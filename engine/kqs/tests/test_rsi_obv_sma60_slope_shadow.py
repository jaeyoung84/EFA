"""Codex 수정: CODX-MA-002 (2026-10-16), 단일 SMA60 상승 전향 가설 회귀 테스트."""

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from kqs_runtime import rsi_obv_sma60_slope_shadow as shadow

ROOT = Path(__file__).resolve().parents[3]


def _sessions(start: date, count: int) -> list[str]:
    days, current = [], start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


class SmaSlopeShadowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.market = Path(self.directory.name) / "market.sqlite3"
        self.days = _sessions(date(2026, 7, 1), 90)
        self.signal_day = self.days[79]

    def build(self, closes: list[float]):
        with sqlite3.connect(self.market) as connection:
            connection.execute("CREATE TABLE daily_prices (trade_date TEXT, symbol TEXT, market TEXT, open REAL, "
                               "high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY (trade_date, symbol))")
            for day, close in zip(self.days, closes):
                connection.execute("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?)",
                                   (day, "000001", "KOSPI", close, close * 1.01, close * 0.99, close, 1_000_000))

    def payload(self):
        return {"engine": {"data_as_of": self.signal_day}, "publication": {"snapshot_id": "kqs-x"},
                "signals": [{"action": "entry", "selection_outcome": "selected", "signal_type": "long_term",
                             "symbol": "000001", "name": "테스트", "market": "KOSPI", "selection_rank": 1},
                            {"action": "entry", "selection_outcome": "selected", "signal_type": "swing",
                             "symbol": "000001", "name": "테스트", "market": "KOSPI", "selection_rank": 1}]}

    def report(self, through=None):
        with mock.patch.object(shadow, "published_payloads", return_value=[self.payload()]):
            return shadow.build_sma_slope_shadow(Path("unused"), self.market, through or self.days[-1])

    def test_preregistration_is_hash_locked_and_costs_match(self):
        self.assertEqual(hashlib.sha256((ROOT / shadow.PREREGISTRATION).read_bytes()).hexdigest(),
                         shadow.PREREGISTRATION_SHA256)
        expected = round((100 * (1 - 0.00245) / (100 * (1 + 0.00065)) - 1) * 100, 4)
        self.assertEqual(shadow.net_return_pct(100, 100), expected)

    def test_rising_sma_enters_at_same_s1_without_changing_the_native_exit(self):
        closes = [80 + i * 0.25 for i in range(80)] + [101, 103, 104, 105, 106, 107, 108, 109, 110, 111]
        self.build(closes)
        report = self.report()
        self.assertEqual(len(report["rows"]), 1)  # swing is ignored
        row = report["rows"][0]
        self.assertTrue(row["filter"]["rising"])
        self.assertEqual(row["arm_a"]["entry_date"], self.days[80])
        self.assertEqual(row["arm_a"], row["arm_b"])
        self.assertEqual(row["status"], "closed_pair")
        self.assertFalse(report["recommendations_changed"])
        self.assertFalse(report["orders_enabled"])

    def test_falling_sma_keeps_cash_and_future_prices_do_not_change_the_filter(self):
        closes = [120 - i * 0.25 for i in range(80)] + [99, 130, 131, 132, 133, 134, 135, 136, 137, 138]
        self.build(closes)
        full = self.report()["rows"][0]
        self.assertFalse(full["filter"]["rising"])
        self.assertEqual(full["arm_b"], {"status": "not_entered", "net_return_pct": 0.0})
        at_s1 = self.report(self.days[80])["rows"][0]
        self.assertEqual(at_s1["filter"], full["filter"])
        self.assertEqual(at_s1["arm_a"]["status"], "open")

    def test_no_future_session_remains_awaiting_s1(self):
        self.build([80 + i * 0.25 for i in range(90)])
        row = self.report(self.signal_day)["rows"][0]
        self.assertEqual(row["status"], "awaiting_s1")
        self.assertIsNone(row["arm_a"])

    def test_sparse_gate_and_final_verdict_are_fixed(self):
        def pair(a, b=None):
            arm_b = ({"status": "not_entered", "net_return_pct": 0.0} if b is None
                     else {"status": "closed", "net_return_pct": b})
            return {"status": "closed_pair", "arm_a": {"status": "closed", "net_return_pct": a},
                    "arm_b": arm_b}

        better = [pair(2.0, 2.0) for _ in range(100)] + [pair(-5.0) for _ in range(25)]
        self.assertEqual(shadow.summarize(better[:99], "2027-01-01")["verdict"], "not_due")
        self.assertEqual(shadow.summarize(better, "2027-01-01")["verdict"], "PASS")
        sparse = [pair(-1.0) for _ in range(100)] + [pair(1.0, 1.0) for _ in range(10)]
        self.assertEqual(shadow.summarize(sparse, shadow.DEADLINE)["verdict"], "REJECT_TOO_SPARSE")


if __name__ == "__main__":
    unittest.main()
