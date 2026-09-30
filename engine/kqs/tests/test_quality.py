"""Market-input quality regressions use only synthetic SQLite and temporary manifests."""

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from test_runtime import create_db
from kqs_collection.cli import main
from kqs_collection.quality import QualityError, inspect_market_inputs, validate_report
from kqs_collection.store import publish


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.market = Path(self.directory.name) / "market.sqlite3"
        self.as_of = create_db(self.market)
        self.observed = datetime.combine(date.fromisoformat(self.as_of) + timedelta(days=1),
                                         datetime.min.time(), tzinfo=ZoneInfo("Asia/Seoul")).replace(hour=7)
        self.manifest = Path(self.directory.name) / "coverage.json"
        self.quality_db = Path(self.directory.name) / "quality.sqlite3"
        self.write_manifest()

    def write_manifest(self, **changes):
        manifest = {
            "schema": "kqs-market-coverage/v1", "source_id": "synthetic-fixture",
            "as_of": self.as_of,
            "markets": {"KOSPI": {"expected_symbol_count": 1}, "KOSDAQ": {"expected_symbol_count": 1}},
            "benchmarks": {"KOSPI": "069500", "KOSDAQ": "229200"},
        }
        manifest.update(changes)
        self.manifest.write_text(json.dumps(manifest))

    def inspect(self):
        return inspect_market_inputs(self.market, as_of=self.as_of, observed_at=self.observed.isoformat(),
                                     coverage_manifest=self.manifest)

    def test_read_only_ready_report_has_calendar_manifest_and_stable_integrity(self):
        before = hashlib.sha256(self.market.read_bytes()).hexdigest()
        first = self.inspect()
        second = self.inspect()
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["calendar"]["calendar_id"], "krx-equity-2026")
        self.assertEqual(first["coverage"]["KOSPI"]["observed_symbol_count"], 1)
        self.assertEqual(first["benchmarks"]["KOSDAQ"]["symbol"], "229200")
        self.assertEqual(before, hashlib.sha256(self.market.read_bytes()).hexdigest())

    def test_missing_or_duplicate_market_coverage_is_rejected_without_writing_market(self):
        self.write_manifest(markets={"KOSPI": {"expected_symbol_count": 2}, "KOSDAQ": {"expected_symbol_count": 1}})
        before = hashlib.sha256(self.market.read_bytes()).hexdigest()
        report = self.inspect()
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("KOSPI" in issue for issue in report["issues"]))
        self.assertEqual(before, hashlib.sha256(self.market.read_bytes()).hexdigest())

    def test_missing_benchmark_and_invalid_numeric_values_are_rejected(self):
        with closing(sqlite3.connect(self.market)) as connection, connection:
            connection.execute("DELETE FROM daily_prices WHERE symbol='229200' AND trade_date=?", (self.as_of,))
            connection.execute("UPDATE daily_prices SET close=0 WHERE symbol='005930' AND trade_date=?", (self.as_of,))
        report = self.inspect()
        self.assertEqual(report["status"], "rejected")
        self.assertTrue(any("benchmark 229200" in issue for issue in report["issues"]))
        self.assertTrue(any("non-positive" in issue for issue in report["issues"]))

    def test_krx_halted_or_untraded_rows_are_valid_bars(self):
        """Real KRX sessions carry ~110 rows with open/high/low 0, zero volume/value, positive close/cap."""
        with closing(sqlite3.connect(self.market)) as connection, connection:
            connection.execute("UPDATE daily_prices SET open=0, high=0, low=0, volume=0, trading_value=0 "
                               "WHERE symbol='005930' AND trade_date=?", (self.as_of,))
        report = self.inspect()
        self.assertEqual(report["status"], "ready", report["issues"])
        self.assertEqual(report["coverage"]["KOSPI"]["no_trade_rows"], 1)
        self.assertEqual(report["coverage"]["KOSPI"]["invalid_numeric_rows"], 0)

    def test_inconsistent_or_partial_bars_are_rejected(self):
        cases = [
            "SET high=low-1",                               # OHLC range inverted
            "SET volume=0",                                 # zero volume but positive prices/value
            "SET open=0",                                   # traded bar without an open
            "SET market_cap=0",                             # no cap
            "SET open=0, high=0, low=0, volume=0",          # halted pattern but value left positive
        ]
        for change in cases:
            with self.subTest(change=change):
                market = Path(self.directory.name) / f"case-{cases.index(change)}.sqlite3"
                create_db(market)
                with closing(sqlite3.connect(market)) as connection, connection:
                    connection.execute(f"UPDATE daily_prices {change} WHERE symbol='005930' AND trade_date=?",
                                       (self.as_of,))
                report = inspect_market_inputs(market, as_of=self.as_of, observed_at=self.observed.isoformat(),
                                               coverage_manifest=self.manifest)
                self.assertEqual(report["status"], "rejected")
                self.assertEqual(report["coverage"]["KOSPI"]["invalid_numeric_rows"], 1)

    def test_stale_session_unknown_calendar_or_bad_manifest_fail_closed(self):
        with self.assertRaisesRegex(QualityError, "latest available KRX session"):
            inspect_market_inputs(self.market, as_of=self.as_of,
                                  observed_at=(self.observed + timedelta(days=2)).isoformat(),
                                  coverage_manifest=self.manifest)
        self.write_manifest(as_of="2026-01-01")
        with self.assertRaisesRegex(QualityError, "as_of"):
            self.inspect()
        self.write_manifest(markets={"KOSPI": {"expected_symbol_count": 1}})
        with self.assertRaisesRegex(QualityError, "exactly KOSPI"):
            self.inspect()

    def test_immutable_quality_store_is_idempotent_and_not_a_market_db(self):
        report = self.inspect()
        first = publish(self.quality_db, report)
        before = hashlib.sha256(self.quality_db.read_bytes()).hexdigest()
        self.assertEqual(publish(self.quality_db, report), first)
        self.assertEqual(before, hashlib.sha256(self.quality_db.read_bytes()).hexdigest())
        with closing(sqlite3.connect(self.quality_db)) as connection:
            for statement in ("UPDATE kqs_market_input_quality_runs SET as_of='2099-01-01'",
                              "DELETE FROM kqs_market_input_quality_runs"):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    connection.execute(statement)
        before_market = hashlib.sha256(self.market.read_bytes()).hexdigest()
        with self.assertRaisesRegex(QualityError, "must not share"):
            publish(self.market, report)
        self.assertEqual(before_market, hashlib.sha256(self.market.read_bytes()).hexdigest())

    def test_report_tampering_or_same_run_different_content_is_rejected(self):
        report = self.inspect()
        changed = json.loads(json.dumps(report))
        changed["coverage"]["KOSPI"]["observed_row_count"] = 99
        with self.assertRaisesRegex(QualityError, "integrity"):
            validate_report(changed)
        publish(self.quality_db, report)
        changed = json.loads(json.dumps(report))
        changed["issues"] = ["different"]
        changed["integrity"]["report_sha256"] = __import__("kqs_collection.quality", fromlist=["canonical_digest"]).canonical_digest(
            {key: value for key, value in changed.items() if key != "integrity"}
        )
        with self.assertRaisesRegex(QualityError, "different evidence"):
            publish(self.quality_db, changed)

    def test_cli_records_only_to_separate_quality_db_and_needs_explicit_arguments(self):
        before = hashlib.sha256(self.market.read_bytes()).hexdigest()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = main(["--krx-db", str(self.market), "--coverage-manifest", str(self.manifest),
                           "--as-of", self.as_of, "--observed-at", self.observed.isoformat(),
                           "--quality-db", str(self.quality_db)])
        self.assertEqual(status, 0)
        self.assertTrue(self.quality_db.is_file())
        self.assertEqual(json.loads(stdout.getvalue())["status"], "ready")
        self.assertEqual(before, hashlib.sha256(self.market.read_bytes()).hexdigest())
        with redirect_stderr(io.StringIO()):
            status = main(["--krx-db", str(self.market), "--coverage-manifest", str(self.manifest),
                           "--as-of", self.as_of, "--observed-at", self.observed.isoformat(),
                           "--quality-db", str(self.market)])
        self.assertEqual(status, 2)

    def test_collection_package_needs_no_pandas_or_runtime_import(self):
        src = Path(__file__).resolve().parents[1] / "src"
        result = __import__("subprocess").run(
            [__import__("sys").executable, "-S", "-c",
             f"import sys; sys.path.insert(0, {str(src)!r}); import kqs_collection.quality; "
             "assert 'pandas' not in sys.modules; assert 'kqs_runtime' not in sys.modules"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
