"""EINT-045 시장 전체 신호 밀도(>=9) 그림자 관측: 밀도 정의, 청산, 포트폴리오, D0·판정 시점, 판정 문장, 첫 기록 고정."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd

from kqs_runtime import signal_density_shadow as shadow

ROOT = Path(__file__).resolve().parents[3]


def _sessions(start: date, count: int) -> list[str]:
    days, current = [], start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _audit(n_entries: int) -> dict:
    rows = [{"symbol": f"{i:06d}", "state": "entry_due_next_session", "signal_type": "swing" if i % 2 else "long_term"}
            for i in range(n_entries)]
    rows += [{"symbol": "999999", "state": "regime_gate_blocked", "signal_type": "swing"},
             {"symbol": "888888", "state": "entry_due_next_session", "signal_type": "pullback_v4"},
             {"symbol": "777777", "state": "no_signal", "signal_type": None}]
    return {"audit": rows}


class DensityTests(unittest.TestCase):
    def test_preregistration_is_hash_locked(self):
        self.assertEqual(hashlib.sha256((ROOT / shadow.PREREGISTRATION).read_bytes()).hexdigest(), shadow.PREREGISTRATION_SHA256)

    def test_raw_density_counts_only_reversion_entry_rows(self):
        self.assertEqual(shadow.raw_density(_audit(9)), 9)
        self.assertEqual(shadow.raw_density({}), 0)

    def test_add_months_and_judgment_timing(self):
        self.assertEqual(shadow.add_months("2026-01-31", 1), "2026-02-28")
        days = _sessions(date(2026, 11, 2), 900)
        need = {d: (100 if d >= "2028-12-15" else 50) for d in days}
        cutoff, kind = shadow.judgment_cutoff("2026-11-02", days, need)
        self.assertEqual((cutoff[:7], kind), ("2028-12", "due"))            # 24개월 뒤라도 100건이 찬 달의 월말
        cutoff, kind = shadow.judgment_cutoff("2026-11-02", days, {d: 10 for d in days})
        self.assertEqual((cutoff[:7], kind), ("2029-11", "deadline"))       # 36개월 시한
        self.assertEqual(shadow.judgment_cutoff("2026-11-02", days[:300], need), (None, "not_due"))

    def test_verdict_rules(self):
        base = {"b_closed": 120, "retention": 0.4, "a_cagr": 0.03, "b_cagr": 0.06, "a_daily_mdd": -0.2, "b_daily_mdd": -0.1,
                "cagr_diff_ci95": [0.005, 0.05]}
        self.assertEqual(shadow.verdict(base, "due"), "PASS_CANDIDATE")
        self.assertEqual(shadow.verdict({**base, "retention": 0.1}, "due"), "FAIL")
        self.assertEqual(shadow.verdict({**base, "b_daily_mdd": -0.3}, "due"), "FAIL")
        self.assertEqual(shadow.verdict({**base, "cagr_diff_ci95": [-0.01, 0.05]}, "due"), "INCONCLUSIVE")
        self.assertEqual(shadow.verdict({**base, "b_closed": 40}, "deadline"), "REJECT_TOO_SPARSE")

    def test_portfolio_slots_cap_and_marking(self):
        days = _sessions(date(2026, 11, 2), 5)
        trade = lambda sym, order, closes, exit_date=None, exit_price=None: {  # noqa: E731
            "symbol": sym, "order": order, "entry_date": days[0], "entry_price": 100.0, "exit_date": exit_date,
            "exit_price": exit_price, "closes": dict(zip(days, closes))}
        trades = [trade(f"S{i}", (days[0], 0, i), [100] * 5) for i in range(5)]
        trades[0] = trade("S0", (days[0], 0, 0), [110] * 5, days[3], 120.0)
        eq = shadow.simulate(trades, days)
        self.assertEqual(len(eq), 5)
        held_slice = 1 / 15 / (100 * (1 + shadow.BUY_COST))                    # 하루 3개만 진입
        self.assertAlmostEqual(eq.iloc[1], 1 - 3 / 15 + held_slice * (110 + 100 + 100))
        self.assertGreater(eq.iloc[-1], eq.iloc[1])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        self.market, self.snap = root / "market.sqlite3", root / "snap.sqlite3"
        self.days = _sessions(date(2026, 7, 1), 100)
        self.signal_day = self.days[80]
        closes = [100 - i * 0.2 for i in range(81)] + [86, 90, 95, 100, 104, 108, 110, 111, 112, 113,
                                                        114, 115, 116, 117, 118, 119, 120, 121, 122]
        with sqlite3.connect(self.market) as c:
            c.execute("CREATE TABLE daily_prices (trade_date TEXT, symbol TEXT, market TEXT, open REAL, high REAL, low REAL, "
                      "close REAL, volume REAL, PRIMARY KEY (trade_date, symbol))")
            for day, close in zip(self.days, closes):
                c.execute("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?)", (day, "000001", "KOSPI", close, close * 1.01,
                                                                                close * 0.99, close, 1_000_000))
        with sqlite3.connect(self.snap) as c:
            c.execute("CREATE TABLE kqs_recommendation_snapshots (data_as_of TEXT, generated_at TEXT, runtime_json TEXT)")
            c.execute("INSERT INTO kqs_recommendation_snapshots VALUES (?,?,?)", (self.signal_day, "t1", json.dumps(_audit(12))))

    def payload(self):
        sig = lambda item: {"action": "entry", "selection_outcome": "selected", "signal_type": item, "symbol": "000001",  # noqa: E731
                            "name": "테스트", "market": "KOSPI", "selection_rank": 1}
        return {"engine": {"data_as_of": self.signal_day}, "publication": {"snapshot_id": "x"},
                "signals": [sig("swing"), sig("long_term"), {**sig("pullback_v4")}]}

    def build(self, through, previous=None):
        with mock.patch.object(shadow, "published_payloads", return_value=[self.payload()]):
            return shadow.build_signal_density_shadow(self.snap, self.market, through, previous)

    def test_d0_density_rows_and_exits(self):
        report = self.build(self.days[-1], {"d0": self.signal_day})
        self.assertEqual(report["d0"], self.signal_day)
        self.assertEqual(report["days"], [{"as_of": self.signal_day, "density": 12}])
        self.assertEqual([r["item"] for r in report["rows"]], ["swing", "long_term"])        # pullback_v4 제외
        for row in report["rows"]:
            self.assertTrue(row["b_enters"])
            self.assertEqual(row["arm"]["entry_date"], self.days[81])
            self.assertEqual(row["arm"]["status"], "closed")
            self.assertNotIn("closes", row["arm"])
        self.assertEqual(report["status"], "not_due")
        self.assertFalse(report["recommendations_changed"] or report["orders_enabled"])

    def test_first_run_sets_d0_and_first_density_is_kept(self):
        first = self.build(self.signal_day)
        self.assertEqual(first["d0"], self.signal_day)
        with sqlite3.connect(self.snap) as c:                    # 같은 날 재게시가 와도 처음 기록한 밀도 유지
            c.execute("UPDATE kqs_recommendation_snapshots SET runtime_json = ?", (json.dumps(_audit(3)),))
        again = self.build(self.days[-1], first)
        self.assertEqual(again["days"][0]["density"], 12)
        self.assertEqual(again["d0"], self.signal_day)


if __name__ == "__main__":
    unittest.main()
