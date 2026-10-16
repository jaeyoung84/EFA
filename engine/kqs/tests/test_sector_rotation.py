"""EINT-021/047 sector rotation (RRG): synthetic weekly data, quadrants, skips, equal-weight benchmark, reference view, read-only, atomic file."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from kqs_collection.krx import SCHEMA_SQL
from kqs_collection.sector_rotation import (SectorRotationError, build_sector_rotation, quadrant, rrg_series,
                                            weekly_closes, write_sector_rotation)

AS_OF = date(2026, 9, 30)  # a Wednesday


def trading_days(weeks: int):
    day = AS_OF - timedelta(weeks=weeks)
    while day <= AS_OF:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def market_db(path: Path, series: dict[str, callable], weeks: int = 60) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_SQL)
    rows = []
    for index, day in enumerate(trading_days(weeks)):
        for symbol, price in series.items():
            close = price(index)
            if close is None:
                continue
            rows.append((day.isoformat(), symbol, symbol, "ETF", "", close, close, close, close, 1, 1, 1, 1, "t"))
    connection.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


class SectorRotationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "market.sqlite3"

    def test_weekly_close_is_last_valid_day_of_each_week(self):
        closes = weekly_closes([("2026-09-28", 10), ("2026-09-29", 11), ("2026-09-30", 0), ("2026-10-05", 12)])
        self.assertEqual(closes, {"2026-09-29": 11.0, "2026-10-05": 12.0})  # halted 09-30 skipped

    def test_quadrants(self):
        self.assertEqual([quadrant(101, 101), quadrant(101, 99), quadrant(99, 99), quadrant(99, 101)],
                         ["leading", "weakening", "lagging", "improving"])

    def test_outperformer_leads_underperformer_lags_and_turnaround_improves(self):
        bench = [100 + i * 0.1 for i in range(60)]
        rising = [b * (1 + 0.002 * i * i / 40) for i, b in enumerate(bench)]       # pulling ahead, faster lately
        falling = [b * (1 - 0.002 * i * i / 40) for i, b in enumerate(bench)]      # falling behind, faster lately
        # 오래 뒤처지다 최근 3주 막 반등: 아직 시장보다 약하지만(ratio<100) 힘은 붙는 중(momentum>=100)
        turning = [b * (1 - 0.01 * i) if i <= 56 else b * (1 - 0.56 + 0.012 * (i - 56)) for i, b in enumerate(bench)]
        self.assertEqual(quadrant(*rrg_series(rising, bench)[-1]), "leading")
        self.assertEqual(quadrant(*rrg_series(falling, bench)[-1]), "lagging")
        self.assertEqual(quadrant(*rrg_series(turning, bench)[-1]), "improving")
        self.assertIsNone(rrg_series(rising, bench)[5])  # not enough weeks yet

    def test_build_reads_db_only_and_skips_sectors_without_data(self):
        daily = lambda base, drift: (lambda i: base * (1 + drift * i))
        market_db(self.db, {"252650": daily(15000, 0.0005), "069500": daily(30000, -0.0015),       # 대형주 기준이 더 약함
                            "091160": daily(50000, 0.002), "091170": daily(10000, -0.001),
                            "305720": lambda i: 20000 if i > 200 else None})  # 2차전지: only ~20 weeks of history
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        report = build_sector_rotation(self.db, AS_OF.isoformat())
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        names = {sector["key"]: sector for sector in report["sectors"]}
        self.assertEqual(set(names), {"semis", "banks"})
        self.assertGreater(names["semis"]["relative_12w_pct"], 0)
        self.assertLess(names["banks"]["relative_12w_pct"], 0)
        self.assertEqual(len(names["semis"]["trail"]), report["tail_weeks"])
        self.assertEqual(names["semis"]["trail"][-1]["week"], AS_OF.isoformat())
        reasons = {item["key"]: item["reason"] for item in report["skipped"]}
        self.assertEqual(reasons["battery"], "주간 시세 이력 부족")
        self.assertEqual(reasons["auto"], "기준일 시세 없음")
        self.assertEqual(report["schema"], "kqs-sector-rotation/v1")
        # EINT-047: 동일가중이 기본 기준, KODEX 200은 참고 분면, 1주 절대 등락률, 대형주 쏠림 안내
        self.assertEqual((report["benchmark"]["symbol"], report["reference_benchmark"]["symbol"]), ("252650", "069500"))
        self.assertIsNotNone(names["semis"]["return_1w_pct"])
        self.assertIn(names["banks"]["reference"]["quadrant"], {"leading", "weakening", "lagging", "improving"})
        self.assertGreater(names["banks"]["reference"]["relative_4w_pct"], names["banks"]["relative_4w_pct"])  # 약한 기준 대비 더 좋아 보임
        conc = report["concentration"]
        self.assertLess(conc["gap_pctp"], 0)
        self.assertEqual(conc["direction"], "mega_caps_weaker")
        self.assertEqual(conc["notice"], abs(conc["gap_pctp"]) >= 5)

    def test_without_reference_benchmark_the_view_is_simply_absent(self):
        market_db(self.db, {"252650": lambda i: 15000 + i, "091160": lambda i: 50000 + 3 * i})
        report = build_sector_rotation(self.db, AS_OF.isoformat())
        self.assertIsNone(report["sectors"][0]["reference"])
        self.assertEqual((report["concentration"]["gap_pctp"], report["concentration"]["notice"]), (None, False))

    def test_missing_benchmark_bar_on_session_fails_closed(self):
        market_db(self.db, {"252650": lambda i: 30000, "091160": lambda i: 50000})
        with self.assertRaises(SectorRotationError):
            build_sector_rotation(self.db, (AS_OF + timedelta(days=1)).isoformat())
        with self.assertRaises(SectorRotationError):
            build_sector_rotation(Path(self.directory.name) / "absent.sqlite3", AS_OF.isoformat())

    def test_write_is_atomic_and_world_readable(self):
        market_db(self.db, {"252650": lambda i: 30000 + i, "091160": lambda i: 50000 + 3 * i})
        target = Path(self.directory.name) / "state" / "sector-rotation.json"
        write_sector_rotation(target, build_sector_rotation(self.db, AS_OF.isoformat()))
        self.assertEqual(json.loads(target.read_text())["schema"], "kqs-sector-rotation/v1")
        self.assertEqual(oct(target.stat().st_mode & 0o777), "0o644")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["sector-rotation.json"])


if __name__ == "__main__":
    unittest.main()
