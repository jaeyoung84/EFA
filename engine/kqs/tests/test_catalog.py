"""EINT-018 instrument catalog: KOSPI/KOSDAQ stocks of one session, read-only, atomic file."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from kqs_collection.catalog import CatalogError, build_instrument_catalog, write_instrument_catalog
from kqs_collection.krx import SCHEMA_SQL


def _db(path: Path, rows):
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_SQL)
    connection.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


def row(day, symbol, name, market, cap):
    return (day, symbol, name, market, "섹션", 1, 1, 1, 1, 1, 1, cap, 1, "t")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "market.sqlite3"
        _db(self.db, [row("2026-09-30", "005930", "삼성전자", "KOSPI", 900), row("2026-09-30", "086520", "에코프로", "KOSDAQ", 50),
                      row("2026-09-30", "069500", "KODEX 200", "ETF", 5000), row("2026-09-29", "016170", "상장폐지사", "KOSPI", 10)])

    def test_only_current_session_stocks_ordered_by_market_cap(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        catalog = build_instrument_catalog(self.db, "2026-09-30")
        self.assertEqual([(i["symbol"], i["market"]) for i in catalog["items"]], [("005930", "KOSPI"), ("086520", "KOSDAQ")])
        self.assertEqual(catalog["count"], 2)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)  # read-only

    def test_missing_session_or_db_fails_closed(self):
        with self.assertRaises(CatalogError):
            build_instrument_catalog(self.db, "2026-09-28")
        with self.assertRaises(CatalogError):
            build_instrument_catalog(Path(self.directory.name) / "absent.sqlite3", "2026-09-30")

    def test_write_is_atomic_and_world_readable(self):
        target = Path(self.directory.name) / "state" / "instrument-catalog.json"
        write_instrument_catalog(target, build_instrument_catalog(self.db, "2026-09-30"))
        self.assertEqual(json.loads(target.read_text())["count"], 2)
        self.assertEqual(oct(target.stat().st_mode & 0o777), "0o644")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["instrument-catalog.json"])


if __name__ == "__main__":
    unittest.main()
