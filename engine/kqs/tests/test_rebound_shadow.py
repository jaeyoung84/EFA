"""Pre-registered observation B-1 (rebound-confirmed entry): replay, costs, verdict gate, hash lock."""

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from kqs_runtime import rebound_shadow as shadow

ROOT = Path(__file__).resolve().parents[3]


def _sessions(start: date, count: int) -> list[str]:
    days, current = [], start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


class ReboundShadowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.market = Path(self.directory.name) / "market.sqlite3"
        self.days = _sessions(date(2026, 7, 1), 90)
        self.signal_day = self.days[79]

    def build(self, closes: list[float], halted: set[str] = frozenset()):
        with sqlite3.connect(self.market) as connection:
            connection.execute("CREATE TABLE daily_prices (trade_date TEXT, symbol TEXT, market TEXT, open REAL, "
                               "high REAL, low REAL, close REAL, PRIMARY KEY (trade_date, symbol))")
            for day, close in zip(self.days, closes):
                if day in halted:
                    row = (day, "000001", "KOSPI", 0, 0, 0, close)
                else:
                    row = (day, "000001", "KOSPI", close, close * 1.01, close * 0.99, close)
                connection.execute("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?)", row)

    def payload(self, as_of=None):
        return {"engine": {"data_as_of": as_of or self.signal_day}, "publication": {"snapshot_id": "kqs-x"},
                "signals": [{"action": "entry", "selection_outcome": "selected", "signal_type": "swing",
                             "symbol": "000001", "name": "테스트", "market": "KOSPI", "selection_rank": 1},
                            {"action": "entry", "selection_outcome": "selected", "signal_type": "pullback_v4",
                             "symbol": "000001", "name": "테스트", "market": "KOSPI", "selection_rank": 1}]}

    def report(self, through, payloads=None):
        with mock.patch.object(shadow, "published_payloads", return_value=[self.payload()] if payloads is None else payloads):
            return shadow.build_rebound_shadow(Path("unused"), self.market, through)

    def closes(self, after: list[float]) -> list[float]:
        # 80 sessions drifting around 100, a sharp drop into the signal day, then the given path.
        base = [100 + (i % 5) * 0.5 for i in range(75)] + [96, 92, 88, 84, 80]
        return base + after + [after[-1]] * (90 - len(base) - len(after))

    def test_preregistration_document_is_hash_locked(self):
        text = (ROOT / shadow.PREREGISTRATION).read_bytes()
        self.assertEqual(hashlib.sha256(text).hexdigest(), shadow.PREREGISTRATION_SHA256)

    def test_cost_model_matches_the_audit(self):
        self.assertEqual(shadow.net_return_pct(100.0, 100.0), round((0.99735 / 1.00065 - 1) * 100, 4))
        self.assertLess(shadow.net_return_pct(100.0, 100.0), 0)

    def test_confirmed_rebound_enters_at_s2_and_both_arms_exit_on_the_native_rule(self):
        self.build(self.closes([83, 90, 99, 104]))          # S1 close 83 > 80 and above the band, then recovery
        report = self.report(self.days[-1])
        self.assertEqual(len(report["rows"]), 1)             # pullback_v4 pick ignored
        row = report["rows"][0]
        self.assertTrue(row["confirmation"]["confirmed"])
        self.assertEqual((row["arm_a"]["entry_date"], row["arm_b"]["entry_date"]), (self.days[80], self.days[81]))
        self.assertEqual(row["arm_a"]["entry_price"], 83.0)
        self.assertEqual(row["arm_b"]["entry_price"], 90.0)
        self.assertEqual(row["status"], "closed_pair")
        self.assertEqual(row["arm_a"]["exit_date"], self.days[self.days.index(row["arm_a"]["exit_signal_date"]) + 1])
        self.assertEqual(report["summary"]["verdict"], "not_due")
        self.assertEqual(report["preregistration_sha256"], shadow.PREREGISTRATION_SHA256)

    def test_unconfirmed_signal_is_not_entered_and_counts_as_zero(self):
        self.build(self.closes([78, 76, 90, 104]))          # S1 close below the signal close: no confirmation
        row = self.report(self.days[-1])["rows"][0]
        self.assertFalse(row["confirmation"]["confirmed"])
        self.assertEqual(row["arm_b"], {"status": "not_entered", "net_return_pct": 0.0})
        self.assertEqual(row["status"], "closed_pair")

    def test_future_bars_are_never_used(self):
        self.build(self.closes([83, 90, 99, 104]))
        waiting = self.report(self.signal_day)["rows"][0]
        self.assertEqual(waiting["status"], "awaiting_s1")
        after_s1 = self.report(self.days[80])["rows"][0]
        self.assertEqual(after_s1["arm_b"]["status"], "awaiting_s2")
        self.assertEqual(after_s1["arm_a"]["status"], "open")

    def test_halted_entry_is_excluded_and_older_signals_are_out_of_scope(self):
        self.build(self.closes([83, 90, 99, 104]), halted={self.days[80]})
        self.assertEqual(self.report(self.days[-1])["rows"][0]["status"], "excluded")
        early = self.report(self.days[-1], payloads=[])
        self.assertEqual(early["rows"], [])
        with mock.patch.object(shadow, "published_payloads", return_value=[self.payload("2026-10-06")]):
            self.assertEqual(shadow.build_rebound_shadow(Path("unused"), self.market, self.days[-1])["rows"], [])

    def test_verdict_only_when_due_and_bootstrap_is_reproducible(self):
        def pair(a, b):
            return {"status": "closed_pair", "arm_a": {"status": "closed", "net_return_pct": a},
                    "arm_b": {"status": "closed", "net_return_pct": b}}
        better = [pair(1.0, 2.0 + (i % 3) * 0.1) for i in range(100)]
        self.assertEqual(shadow.summarize(better[:99], "2027-01-01")["verdict"], "not_due")
        due = shadow.summarize(better, "2027-01-01")
        self.assertEqual(due["verdict"], "PASS")
        self.assertEqual(due["ci95"], shadow.summarize(better, "2027-01-01")["ci95"])
        worse = [pair(2.0, 1.0 - (i % 3) * 0.1) for i in range(100)]
        self.assertEqual(shadow.summarize(worse, "2027-01-01")["verdict"], "FAIL")
        mixed = [pair(1.0, 1.0 + (1 if i % 2 else -1)) for i in range(100)]
        self.assertEqual(shadow.summarize(mixed, "2027-01-01")["verdict"], "INCONCLUSIVE")
        self.assertEqual(shadow.summarize(better[:5], shadow.DEADLINE)["verdict"], "PASS")   # deadline reached


if __name__ == "__main__":
    unittest.main()
