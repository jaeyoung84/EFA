"""Daily pipeline, run log, health check and backup: fake KRX transport and temporary files only."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from krx_fixture import FakeKrx, available_time, build_market
from kqs_collection.krx import KrxOpenApiClient
from kqs_ops.backup import backup_databases, prune, verify_backup
from kqs_ops.paths import state_paths
from kqs_ops.runlog import read_runs
from kqs_ops.status import check_health
from kqs_publication.attempts import read_attempts
from kqs_publication.store import read_latest
from kqs_publication.writer import WriterLock
from kqs_runtime.pipeline import run_daily_pipeline

KEY = "fixture-secret-key-value"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.state = root / "state"
        self.market = root / "market" / "krx_daily.sqlite3"
        self.market.parent.mkdir()
        self.as_of, self.rows = build_market(self.market)
        self.now = available_time(self.as_of)
        self.paths = state_paths(self.state)

    def client(self, rows=None):
        return KrxOpenApiClient(auth_key=KEY, transport=FakeKrx(self.rows if rows is None else rows),
                                sleep=lambda _: None)

    def run_pipeline(self, rows=None, now=None, **kwargs):
        return run_daily_pipeline(state_dir=self.state, market_db=self.market, client=self.client(rows),
                                  now=now or self.now, **kwargs)

    def test_end_to_end_publishes_logs_and_is_healthy(self):
        run = self.run_pipeline()
        self.assertEqual(run["status"], "published", run.get("error"))
        self.assertEqual(run["session"], self.as_of)
        self.assertEqual(run["steps"]["quality"]["status"], "ready")
        payload = read_latest(self.paths["snapshot_db"], now=self.now + timedelta(minutes=1))
        self.assertEqual(payload["engine"]["data_as_of"], self.as_of)
        # EINT-031(v4): 만료 = 다음 세션 자료 공개 기대(그다음 거래일 08:00 KST) + 유예 3시간, 24시간 상한 없음.
        from kqs_publication.calendar import calendar_for_session
        calendar = calendar_for_session(date.fromisoformat(self.as_of))
        next_session = calendar.next_session(date.fromisoformat(self.as_of))
        self.assertEqual(datetime.fromisoformat(payload["expires_at"]),
                         calendar.availability_at(next_session) + calendar.expiry_grace)
        self.assertEqual(payload["publication"]["calendar"]["policy"], "krx-session-expiry/v4")
        self.assertEqual(json.loads(self.paths["status_file"].read_text())["status"], "published")
        self.assertEqual([r["status"] for r in read_runs(self.paths["pipeline_runs_db"])], ["published"])
        self.assertEqual([a["status"] for a in read_attempts(self.paths["attempts_db"])], ["published"])
        self.assertTrue(check_health(self.state, now=self.now + timedelta(minutes=5))["healthy"])
        catalog = json.loads(self.paths["instrument_catalog"].read_text())
        self.assertEqual((catalog["schema"], catalog["as_of"]), ("kqs-instrument-catalog/v1", self.as_of))
        self.assertEqual({item["symbol"] for item in catalog["items"]}, {"005930", "035420"})  # stocks only, no ETFs
        self.assertEqual(run["steps"]["instrument_catalog"]["status"], "written")
        outcomes = json.loads(self.paths["recommendation_outcomes"].read_text())  # EINT-019
        self.assertEqual((outcomes["schema"], outcomes["through"], outcomes["source_count"]),
                         ("kqs-recommendation-outcomes/v1", self.as_of, 1))
        self.assertTrue(all(row["status"] == "awaiting_entry" for row in outcomes["rows"]))  # nothing after as_of yet
        self.assertEqual(run["steps"]["recommendation_outcomes"]["status"], "written")
        # EINT-033: signal funnel (observation only) — stage counts from the stored audit, rolling history.
        self.assertEqual(run["steps"]["signal_funnel"]["status"], "written", run["steps"]["signal_funnel"])
        funnel = json.loads(self.paths["signal_funnel"].read_text())
        self.assertEqual((funnel["schema"], funnel["as_of"], funnel["recommendation"]), ("kqs-signal-funnel/v1", self.as_of, False))
        self.assertEqual([row["as_of"] for row in funnel["history"]], [self.as_of])
        self.assertEqual(funnel["today"]["swing"]["selected"], sum(1 for s in payload["signals"] if s["signal_type"] == "swing"))
        self.assertEqual(run["steps"]["rebound_shadow"]["status"], "written")
        # EINT-021: the fixture market has no sector-ETF history, so the step fails closed and is only recorded
        self.assertIn(run["steps"]["sector_rotation"]["status"], {"written", "failed"})
        self.assertTrue(all(KEY not in path.read_bytes().decode("latin-1") for path in self.state.rglob("*")
                            if path.is_file()))

    def test_rerun_is_idempotent_and_every_run_is_logged(self):
        self.run_pipeline()
        again = self.run_pipeline(now=self.now + timedelta(minutes=30))
        self.assertEqual(again["status"], "already_published")
        # EINT-028: 이미 게시된 거래일이면 수집·검사·게시를 다시 하지 않고 바로 끝낸다(KRX 호출 없음).
        self.assertEqual(again["steps"], {"skipped": {"reason": "session_already_published"}})
        with closing(sqlite3.connect(self.paths["snapshot_db"])) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM kqs_recommendation_snapshots").fetchone()[0], 1)
        self.assertEqual(len(read_runs(self.paths["pipeline_runs_db"])), 2)

    def test_eint028_attempts_after_success_do_not_call_krx_but_failures_retry(self):
        """08:20 실패 → 08:50 성공 → 09:30은 KRX를 부르지 않고 끝난다. refresh는 건너뛰지 않는다."""
        first_client = KrxOpenApiClient(auth_key=KEY, transport=FakeKrx({}), sleep=lambda _: None)
        failed = run_daily_pipeline(state_dir=self.state, market_db=self.market, client=first_client, now=self.now)
        self.assertEqual(failed["status"], "collect_failed")
        self.assertTrue(first_client.transport.urls)                      # 실패한 시도는 KRX를 불렀다
        ok = self.run_pipeline(now=self.now + timedelta(hours=1))
        self.assertEqual(ok["status"], "published", ok.get("error"))
        later_transport = FakeKrx(self.rows)
        later = run_daily_pipeline(state_dir=self.state, market_db=self.market, now=self.now + timedelta(hours=2),
                                   client=KrxOpenApiClient(auth_key=KEY, transport=later_transport, sleep=lambda _: None))
        self.assertEqual(later["status"], "already_published")
        self.assertEqual(later_transport.urls, [])                        # 성공 뒤 남은 시도는 KRX 호출 0회
        forced = self.run_pipeline(now=self.now + timedelta(hours=3), refresh=True)
        self.assertNotIn("skipped", forced["steps"])

    def test_provider_not_ready_is_collect_failed_and_alerts_after_grace(self):
        run = self.run_pipeline(rows={})
        self.assertEqual(run["status"], "collect_failed")
        self.assertFalse(self.paths["snapshot_db"].exists())
        self.assertTrue(check_health(self.state, now=self.now)["healthy"])
        late = check_health(self.state, now=self.now + timedelta(hours=12))
        self.assertFalse(late["healthy"])
        self.assertEqual(late["last_run_status"], "collect_failed")
        self.assertEqual(late["expected_session"], self.as_of)

    def test_provider_truncation_is_rejected_before_quality(self):
        self.market.unlink()
        self.as_of, self.rows = build_market(self.market, previous_counts={"KOSPI": 400, "KOSDAQ": 1, "ETF": 2})
        run = self.run_pipeline()
        self.assertEqual(run["status"], "coverage_rejected")
        self.assertIn("provider truncation", run["error"])
        self.assertFalse(self.paths["snapshot_db"].exists())

    def test_bad_bars_are_recorded_as_rejected_quality_and_not_published(self):
        rows = {m: [dict(r) for r in v] for m, v in self.rows.items()}
        rows["KOSPI"][0]["TDD_HGPRC"] = "1"
        run = self.run_pipeline(rows=rows)
        self.assertEqual(run["status"], "quality_rejected")
        self.assertEqual(run["steps"]["quality"]["status"], "rejected")
        self.assertFalse(self.paths["snapshot_db"].exists())
        with closing(sqlite3.connect(self.paths["quality_db"])) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM kqs_market_input_quality_runs").fetchone()[0], 1)

    def test_busy_writer_is_reported(self):
        with WriterLock(self.paths["snapshot_db"]):
            run = self.run_pipeline()
        self.assertEqual(run["status"], "writer_busy")

    def test_market_db_must_not_share_a_state_file(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            run_daily_pipeline(state_dir=self.state, market_db=self.paths["quality_db"], client=self.client(),
                               now=self.now)

    def test_backup_copies_are_consistent_verifiable_and_rotated(self):
        self.run_pipeline()
        backups = Path(self.directory.name) / "backups"
        sources = {"market": self.market, "quality": self.paths["quality_db"],
                   "snapshots": self.paths["snapshot_db"], "attempts": self.paths["attempts_db"],
                   "pipeline_runs": self.paths["pipeline_runs_db"], "absent": Path(self.directory.name) / "none"}
        manifests = [backup_databases(sources, backups, now=self.now + timedelta(hours=hour), keep=2)
                     for hour in range(3)]
        latest = manifests[-1]
        self.assertEqual(latest["missing"], ["absent"])
        self.assertEqual(set(latest["files"]), {"market", "quality", "snapshots", "attempts", "pipeline_runs"})
        self.assertTrue(all(entry["integrity"] == "ok" for entry in latest["files"].values()))
        self.assertEqual(len([p for p in backups.iterdir() if p.is_dir()]), 2)
        target = backups / latest["directory"]
        self.assertTrue(verify_backup(target)["ok"])
        restored = read_latest(target / "snapshots.sqlite3", now=self.now + timedelta(minutes=1))
        self.assertEqual(restored["engine"]["data_as_of"], self.as_of)
        with (target / "quality.sqlite3").open("r+b") as handle:
            handle.seek(200)
            handle.write(b"corrupted")
        self.assertFalse(verify_backup(target)["ok"])
        (backups / "not-ours").mkdir()
        self.assertEqual(prune(backups, keep=1), [manifests[1]["directory"]])
        self.assertTrue((backups / "not-ours").is_dir())


if __name__ == "__main__":
    unittest.main()
