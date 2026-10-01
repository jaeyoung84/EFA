"""Single-writer publication worker regressions: synthetic SQLite and temporary files only."""

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_runtime import create_db
from kqs_collection.quality import inspect_market_inputs
from kqs_collection.store import publish as record_quality
from kqs_publication.attempts import read_attempts
from kqs_publication.store import read_latest
from kqs_publication.writer import WriterLock
from kqs_runtime import cli as preview_cli
from kqs_runtime.worker import main, run_publication


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.market = root / "market.sqlite3"
        self.snapshot = root / "recommendations.sqlite3"
        self.quality_db = root / "quality.sqlite3"
        self.attempts = root / "attempts.sqlite3"
        self.manifest = root / "coverage.json"
        self.as_of = create_db(self.market)
        self.generated = datetime.combine(date.fromisoformat(self.as_of) + timedelta(days=1), datetime.min.time(),
                                          tzinfo=ZoneInfo("Asia/Seoul")).replace(hour=7)
        self.expiry = self.generated + timedelta(hours=4)
        self.write_manifest()

    def write_manifest(self, kospi_count=1, as_of=None):
        self.manifest.write_text(json.dumps({
            "schema": "kqs-market-coverage/v1", "source_id": "synthetic-fixture", "as_of": as_of or self.as_of,
            "markets": {"KOSPI": {"expected_symbol_count": kospi_count}, "KOSDAQ": {"expected_symbol_count": 1}},
            "benchmarks": {"KOSPI": "069500", "KOSDAQ": "229200"},
        }))

    def recorded_quality(self) -> str:
        report = inspect_market_inputs(self.market, as_of=self.as_of, observed_at=self.generated.isoformat(),
                                       coverage_manifest=self.manifest)
        return record_quality(self.quality_db, report)["run_id"]

    def run_worker(self, quality_run_id, **overrides):
        params = dict(krx_db=self.market, history_start="2025-01-01", as_of=self.as_of,
                      expected_as_of=self.as_of, expires_at=self.expiry, snapshot_db=self.snapshot,
                      quality_db=self.quality_db, quality_run_id=quality_run_id,
                      coverage_manifest=self.manifest, attempts_db=self.attempts, now=self.generated)
        params.update(overrides)
        return run_publication(**params)

    def test_ready_recorded_quality_run_publishes_and_logs_attempt(self):
        before = hashlib.sha256(self.market.read_bytes()).hexdigest()
        result = self.run_worker(self.recorded_quality())
        self.assertEqual(result["attempt"]["status"], "published")
        self.assertEqual(result["payload"]["schema_version"], "kqs-recommendations/v2")
        self.assertEqual(read_latest(self.snapshot, now=self.generated + timedelta(minutes=1)), result["payload"])
        self.assertEqual(result["attempt"]["snapshot_id"], result["payload"]["publication"]["snapshot_id"])
        self.assertEqual(before, hashlib.sha256(self.market.read_bytes()).hexdigest())
        [logged] = read_attempts(self.attempts)
        self.assertEqual(logged["status"], "published")
        self.assertIs(logged["orders_enabled"], False)

    def test_rerun_is_idempotent_and_keeps_first_expiry(self):
        run_id = self.recorded_quality()
        first = self.run_worker(run_id)
        second = self.run_worker(run_id, now=self.generated + timedelta(hours=1),
                                 expires_at=self.expiry + timedelta(hours=1))
        self.assertEqual(second["attempt"]["status"], "already_published")
        self.assertEqual(first["payload"], second["payload"])
        with closing(sqlite3.connect(self.snapshot)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM kqs_recommendation_snapshots").fetchone()[0], 1)
        self.assertEqual([row["status"] for row in read_attempts(self.attempts)], ["published", "already_published"])

    def test_unrecorded_or_rejected_quality_run_blocks_publication(self):
        result = self.run_worker("0" * 64)
        self.assertEqual(result["attempt"]["status"], "blocked_quality")
        self.assertFalse(self.snapshot.exists())
        self.write_manifest(kospi_count=2)
        rejected = self.recorded_quality()
        result = self.run_worker(rejected)
        self.assertEqual(result["attempt"]["status"], "blocked_quality")
        self.assertIn("not ready", result["attempt"]["error"])
        self.assertFalse(self.snapshot.exists())
        self.assertEqual([row["status"] for row in read_attempts(self.attempts)], ["blocked_quality"] * 2)

    def test_market_change_after_quality_run_blocks_publication(self):
        run_id = self.recorded_quality()
        with closing(sqlite3.connect(self.market)) as connection, connection:
            connection.execute("UPDATE daily_prices SET close=close+1 WHERE symbol='005930' AND trade_date=?",
                               (self.as_of,))
        result = self.run_worker(run_id)
        self.assertEqual(result["attempt"]["status"], "blocked_quality")
        self.assertIn("no longer match", result["attempt"]["error"])
        self.assertFalse(self.snapshot.exists())

    def test_quality_run_for_another_session_blocks_publication(self):
        run_id = self.recorded_quality()
        result = self.run_worker(run_id, as_of="2026-01-02", expected_as_of="2026-01-02")
        self.assertEqual(result["attempt"]["status"], "blocked_quality")
        self.assertIn("as_of", result["attempt"]["error"])
        self.assertFalse(self.snapshot.exists())

    def test_busy_writer_is_logged_and_does_not_publish(self):
        run_id = self.recorded_quality()
        with WriterLock(self.snapshot):
            result = self.run_worker(run_id)
        self.assertEqual(result["attempt"]["status"], "writer_busy")
        self.assertIsNone(result["payload"])
        self.assertFalse(self.snapshot.exists())
        self.assertEqual(self.run_worker(run_id)["attempt"]["status"], "published")

    def test_publication_failure_is_logged_and_releases_the_lock(self):
        run_id = self.recorded_quality()
        result = self.run_worker(run_id, expected_as_of="2026-01-02")
        self.assertEqual(result["attempt"]["status"], "failed")
        self.assertIsNotNone(result["attempt"]["run_id"])
        self.assertFalse(self.snapshot.exists())
        with WriterLock(self.snapshot) as lock:
            self.assertTrue(lock.held)

    def test_all_databases_must_be_separate_files(self):
        run_id = self.recorded_quality()
        for name, value in (("snapshot_db", self.market), ("attempts_db", self.quality_db),
                            ("quality_db", self.snapshot), ("attempts_db", self.snapshot)):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "separate"):
                self.run_worker(run_id, **{name: value})
        self.assertFalse(self.attempts.exists())

    def test_attempt_log_is_immutable_and_isolated(self):
        self.run_worker(self.recorded_quality())
        with closing(sqlite3.connect(self.attempts)) as connection:
            for statement in ("UPDATE kqs_publication_worker_attempts SET status='published'",
                              "DELETE FROM kqs_publication_worker_attempts"):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    connection.execute(statement)
        with self.assertRaisesRegex(ValueError, "separate"):
            self.run_worker(self.recorded_quality(), attempts_db=self.quality_db)

    def test_cli_exit_codes_and_preview_cannot_publish(self):
        run_id = self.recorded_quality()
        args = ["--krx-db", str(self.market), "--history-start", "2025-01-01", "--as-of", self.as_of,
                "--expected-as-of", self.as_of, "--expires-at", self.expiry.isoformat(),
                "--snapshot-db", str(self.snapshot), "--quality-db", str(self.quality_db),
                "--quality-run-id", run_id, "--coverage-manifest", str(self.manifest),
                "--attempts-db", str(self.attempts)]
        with WriterLock(self.snapshot), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 3)
        stdout = io.StringIO()
        with patch("kqs_runtime.worker.datetime") as clock, redirect_stdout(stdout):
            clock.now.return_value = self.generated
            status = main(args)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["attempt"]["status"], "published")
        blocked = [*args]
        blocked[blocked.index("--quality-run-id") + 1] = "0" * 64
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(blocked), 2)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            preview_cli.main(["--krx-db", str(self.market), "--history-start", "2025-01-01",
                              "--as-of", self.as_of, "--snapshot-db", str(self.snapshot)])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
