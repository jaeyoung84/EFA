"""Ported KRX collector and coverage derivation: fake transport, temporary SQLite, no network."""

import os
import sqlite3
import tempfile
import unittest
import urllib.error
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from krx_fixture import FakeKrx, build_market
from kqs_collection.coverage import allowed_change, build_coverage_manifest, write_manifest
from kqs_collection.krx import KrxApiError, KrxOpenApiClient, MarketStore, collect_session
from kqs_collection.quality import QualityError
from kqs_publication.calendar import CalendarError

KEY = "fixture-secret-key-value"


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.market = Path(self.directory.name) / "market.sqlite3"
        self.as_of, self.rows = build_market(self.market)
        self.session = date.fromisoformat(self.as_of)
        self.fake = FakeKrx(self.rows)
        self.client = KrxOpenApiClient(auth_key=KEY, transport=self.fake, sleep=lambda _: None)

    def counts(self):
        with closing(sqlite3.connect(self.market)) as connection:
            return {row[0]: row[1] for row in connection.execute(
                "SELECT market, row_count FROM collection_log WHERE trade_date=?", (self.as_of,))}

    def test_collects_reviewed_session_and_skips_when_already_logged(self):
        result = collect_session(self.client, MarketStore(self.market), self.session)
        self.assertEqual({m: v["status"] for m, v in result["markets"].items()},
                         {"KOSPI": "collected", "KOSDAQ": "collected", "ETF": "collected"})
        self.assertEqual(self.counts(), {"KOSPI": 1, "KOSDAQ": 1, "ETF": 2})
        self.assertTrue(all(h["AUTH_KEY"] == KEY for h in self.fake.headers))
        requested = len(self.fake.urls)
        again = collect_session(self.client, MarketStore(self.market), self.session)
        self.assertEqual({v["status"] for v in again["markets"].values()}, {"skipped"})
        self.assertEqual(len(self.fake.urls), requested)
        collect_session(self.client, MarketStore(self.market), self.session, refresh=True)
        self.assertEqual(len(self.fake.urls), requested + 3)
        self.assertEqual(self.counts(), {"KOSPI": 1, "KOSDAQ": 1, "ETF": 2})

    def test_closure_or_uncovered_year_is_never_requested(self):
        for closed in (date(2026, 9, 24), date(2026, 9, 26), date(2027, 2, 9), date(2028, 1, 3)):
            with self.subTest(day=closed), self.assertRaises(CalendarError):
                collect_session(self.client, MarketStore(self.market), closed)
        self.assertEqual(self.fake.urls, [])

    def test_zero_rows_for_a_session_is_a_failure_and_writes_nothing(self):
        empty = KrxOpenApiClient(auth_key=KEY, transport=FakeKrx({}), sleep=lambda _: None)
        with self.assertRaisesRegex(KrxApiError, "no KOSPI rows"):
            collect_session(empty, MarketStore(self.market), self.session)
        self.assertEqual(self.counts(), {})

    def test_row_normalization_rejects_wrong_date_negative_or_bad_symbol(self):
        cases = [("BAS_DD", "20200101", "returned"), ("ACC_TRDVOL", "-1", "Negative"), ("ISU_CD", "12", "symbol")]
        for field, value, message in cases:
            broken = {m: [dict(r) for r in rows] for m, rows in self.rows.items()}
            broken["KOSPI"][0][field] = value
            client = KrxOpenApiClient(auth_key=KEY, transport=FakeKrx(broken), sleep=lambda _: None)
            with self.subTest(field=field), self.assertRaisesRegex(KrxApiError, message):
                collect_session(client, MarketStore(self.market), self.session)
        self.assertEqual(self.counts(), {})

    def test_etf_rows_without_close_are_skipped(self):
        rows = {m: [dict(r) for r in v] for m, v in self.rows.items()}
        rows["ETF"].append({**rows["ETF"][0], "ISU_CD": "999999", "TDD_CLSPRC": ""})
        client = KrxOpenApiClient(auth_key=KEY, transport=FakeKrx(rows), sleep=lambda _: None)
        collect_session(client, MarketStore(self.market), self.session)
        self.assertEqual(self.counts()["ETF"], 2)

    def test_d31_key_source_priority_and_key_never_in_repr_or_errors(self):
        env_file = Path(self.directory.name) / ".env"
        env_file.write_text('# comment\nOTHER=1\nKRX_AUTH_KEY="from-dotenv"\n')
        with patch.dict(os.environ, {"KRX_AUTH_KEY": ""}):
            self.assertEqual(KrxOpenApiClient.from_env(env_file=env_file).auth_key, "from-dotenv")
        with patch.dict(os.environ, {"KRX_AUTH_KEY": "from-environment"}):
            self.assertEqual(KrxOpenApiClient.from_env(env_file=env_file).auth_key, "from-environment")
        with patch.dict(os.environ, {"KRX_AUTH_KEY": ""}), self.assertRaisesRegex(ValueError, "KRX_AUTH_KEY"):
            KrxOpenApiClient.from_env(env_file=Path(self.directory.name) / "missing.env")
        client = KrxOpenApiClient(auth_key=KEY, retries=1, sleep=lambda _: None)
        self.assertNotIn(KEY, repr(client))
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(KEY)):
            with self.assertRaises(KrxApiError) as caught:
                client.fetch_market_day("KOSPI", self.session)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_legacy_two_market_schema_is_refused_not_migrated(self):
        legacy = Path(self.directory.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute("CREATE TABLE collection_log (trade_date TEXT, market TEXT CHECK (market IN ('KOSPI','KOSDAQ')), "
                               "row_count INTEGER, collected_at TEXT)")
        with self.assertRaisesRegex(ValueError, "Legacy"):
            collect_session(self.client, MarketStore(legacy), self.session)


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.market = Path(self.directory.name) / "market.sqlite3"

    def collect(self, **kwargs):
        as_of, rows = build_market(self.market, **kwargs)
        client = KrxOpenApiClient(auth_key=KEY, transport=FakeKrx(rows), sleep=lambda _: None)
        collect_session(client, MarketStore(self.market), date.fromisoformat(as_of))
        return as_of

    def test_manifest_uses_collection_counts_and_is_deterministic(self):
        as_of = self.collect()
        manifest = build_coverage_manifest(self.market, as_of)
        self.assertEqual(manifest["markets"], {"KOSPI": {"expected_symbol_count": 1},
                                               "KOSDAQ": {"expected_symbol_count": 1}})
        self.assertEqual(manifest["source_id"], "krx-openapi-collection-log/v1")
        path = Path(self.directory.name) / "coverage" / "c.json"
        write_manifest(path, manifest)
        first = path.read_bytes()
        write_manifest(path, build_coverage_manifest(self.market, as_of))
        self.assertEqual(first, path.read_bytes())

    def test_large_count_change_from_previous_session_is_rejected(self):
        as_of = self.collect(previous_counts={"KOSPI": 1, "KOSDAQ": 900, "ETF": 2})
        with self.assertRaisesRegex(QualityError, "provider truncation"):
            build_coverage_manifest(self.market, as_of)

    def test_band_matches_observed_real_day_to_day_changes(self):
        self.assertEqual(allowed_change(942), 10)
        self.assertEqual(allowed_change(1821), 19)
        self.assertEqual(allowed_change(100), 5)

    def test_missing_collection_record_is_rejected(self):
        as_of, _ = build_market(self.market)
        with self.assertRaisesRegex(QualityError, "no positive collection"):
            build_coverage_manifest(self.market, as_of)


if __name__ == "__main__":
    unittest.main()
