"""Isolated actual router tests; no main app, pytest DB fixture or real Mongo access."""

import hashlib
import io
import json
import os
import sys
import subprocess
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine/kqs/tests"))
from test_runtime import synthetic_inputs
from kqs_runtime.recommendations import generate_recommendations
from kqs_publication.store import publish
from kqs_publication.writer import WriterLock
from app.backend.routers import kqs


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "recommendations.sqlite3"
        self.app = FastAPI()
        self.app.include_router(kqs.router)
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.env = patch.dict(os.environ, {"KQS_RECOMMENDATION_SOURCE": "snapshot", "KQS_SNAPSHOT_DB": str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.no_bridge = patch.object(kqs, "_read_legacy_bridge_recommendations", side_effect=AssertionError("No fallback"))
        self.no_bridge.start()
        self.addCleanup(self.no_bridge.stop)
        self.no_db = patch.object(kqs, "get_db", side_effect=AssertionError("GET must not access Mongo"))
        self.no_db.start()
        self.addCleanup(self.no_db.stop)
        # Fixed reviewed KRX session: Sep. 24-25 are Chuseok closures; Sep. 28 is open.
        self.now = datetime(2026, 9, 29, 0, tzinfo=timezone.utc)
        # Readers default to the wall clock; freeze it so fixed publications do not expire mid-suite.
        frozen = self.now + timedelta(minutes=1)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen if tz is None else frozen.astimezone(tz)

        import kqs_publication.contract as engine_contract
        import kqs_publication.store as engine_store
        for module in (engine_store, engine_contract):
            clock = patch.object(module, "datetime", FrozenDatetime)
            clock.start()
            self.addCleanup(clock.stop)
        history, benchmarks, _ = synthetic_inputs()
        offset = (date(2026, 9, 28) - history["Date"].max().date()).days
        history["Date"] += timedelta(days=offset)
        benchmarks = {key: value.set_axis(value.index + timedelta(days=offset)) for key, value in benchmarks.items()}
        self.runtime = generate_recommendations(history, benchmarks, as_of=history["Date"].max().date().isoformat())

    def authorize(self):
        async def user():
            return {"_id": "human-1"}
        self.app.dependency_overrides[kqs.current_user] = user

    def publish(self, expired=False):
        generated = self.now - timedelta(hours=3) if expired else self.now
        with WriterLock(self.path) as lock:
            return publish(self.path, self.runtime, expected_as_of=self.runtime["as_of"], generated_at=generated,
                           expires_at=generated + timedelta(hours=2), writer_lock=lock)

    async def test_recommendations_without_auth_are_rejected_before_read(self):
        response = await self.client.get("/api/kqs/recommendations")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.path.exists())

    async def test_authenticated_get_is_readonly_no_store_and_has_stable_times(self):
        self.authorize()
        payload = self.publish()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        first = await self.client.get("/api/kqs/recommendations")
        second = await self.client.get("/api/kqs/recommendations")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.headers["cache-control"], "no-store")
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()["generated_at"], payload["generated_at"])
        self.assertEqual(first.json()["expires_at"], payload["expires_at"])
        self.assertEqual(first.json()["snapshot_id"], payload["publication"]["snapshot_id"])
        self.assertIn("rsi14", first.json()["signals"][0]["features"])
        self.assertNotIn("runtime_json", first.text)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    async def test_missing_store_fails_closed_without_bridge_or_creation(self):
        self.authorize()
        response = await self.client.get("/api/kqs/recommendations")
        self.assertEqual(response.status_code, 503)
        self.assertIn("스냅샷", response.json()["detail"])
        self.assertFalse(self.path.exists())

    async def test_expired_latest_snapshot_fails_closed(self):
        self.authorize()
        self.publish(expired=True)
        response = await self.client.get("/api/kqs/recommendations")
        self.assertEqual(response.status_code, 503)
        self.assertIn("만료", response.json()["detail"])

    async def test_invalid_source_is_not_a_silent_fallback(self):
        self.authorize()
        with patch.dict(os.environ, {"KQS_RECOMMENDATION_SOURCE": "typo"}):
            response = await self.client.get("/api/kqs/recommendations")
        self.assertEqual(response.status_code, 503)

    async def test_local_contract_inspection_script_reads_same_fixed_snapshot(self):
        payload = self.publish()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        from scripts import check_kqs_recommendation_contract as checker
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["check", "--snapshot-db", str(self.path)]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            returncode = checker.main()
        self.assertEqual(returncode, 0, stderr.getvalue())
        inspected = json.loads(stdout.getvalue())
        self.assertEqual(inspected["generated_at"], payload["generated_at"])
        self.assertEqual(inspected["expires_at"], payload["expires_at"])
        self.assertEqual(inspected["snapshot_id"], payload["publication"]["snapshot_id"])
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    async def test_local_inspection_of_wrong_db_fails_without_rewriting(self):
        import sqlite3
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE wrong_table (value TEXT)")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = subprocess.run([sys.executable, str(ROOT / "scripts/check_kqs_recommendation_contract.py"),
                                 "--snapshot-db", str(self.path)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    async def test_local_inspection_rejects_invalid_source_without_bridge(self):
        from scripts import check_kqs_recommendation_contract as checker
        with patch.dict(os.environ, {"KQS_RECOMMENDATION_SOURCE": "typo"}), \
                patch.object(sys, "argv", ["inspect"]), \
                patch.object(checker.urllib.request, "urlopen", side_effect=AssertionError("No fallback")), \
                patch("sys.stderr"):
            self.assertEqual(checker.main(), 1)
        self.assertFalse(self.path.exists())

    async def test_recommendations_endpoint_cannot_publish(self):
        self.authorize()
        response = await self.client.post("/api/kqs/recommendations", json={})
        self.assertEqual(response.status_code, 405)
        self.assertFalse(self.path.exists())

    async def test_manual_buy_records_current_immutable_evidence_only(self):
        self.authorize()
        payload = self.publish()
        insert = AsyncMock(return_value=SimpleNamespace(inserted_id="decision-1"))
        cursor = SimpleNamespace(to_list=AsyncMock(return_value=[]))
        cursor.sort = lambda *args: cursor
        database = SimpleNamespace(
            kqs_paper_decisions=SimpleNamespace(insert_one=insert, find=lambda *args: cursor, count_documents=AsyncMock(return_value=0)),
            kqs_manual_write_locks=SimpleNamespace(insert_one=AsyncMock(), delete_one=AsyncMock()),
        )
        with patch.object(kqs, "get_db", return_value=database), patch.object(kqs, "append_execution_audit", new_callable=AsyncMock) as audit:
            response = await self.client.post("/api/kqs/decisions", json={
                "signal_id": payload["signals"][0]["signal_id"], "decision": "buy",
                "quantity": 2, "fill_price": 100, "memo": "사용자 수동 판단"})
        self.assertEqual(response.status_code, 200, response.text)
        saved = insert.call_args.args[0]
        self.assertEqual(saved["user_id"], "human-1")
        self.assertEqual(saved["publication_snapshot_id"], payload["publication"]["snapshot_id"])
        self.assertEqual(saved["recommendation_generated_at"], payload["generated_at"])
        self.assertEqual(saved["recommendation_expires_at"], payload["expires_at"])
        self.assertEqual(saved["recommendation_snapshot"]["features"], payload["signals"][0]["features"])
        self.assertEqual(saved["quantity"], 2)
        self.assertEqual(saved["fill_price"], 100)
        self.assertFalse(audit.call_args.args[3]["live_trading_authorized"])

    async def test_expired_signal_or_unknown_id_cannot_be_written_to_manual_ledger(self):
        self.authorize()
        payload = self.publish(expired=True)
        response = await self.client.post("/api/kqs/decisions", json={"signal_id": payload["signals"][0]["signal_id"], "decision": "hold"})
        self.assertEqual(response.status_code, 503)

    async def test_unknown_signal_cannot_be_fabricated(self):
        self.authorize()
        self.publish()
        response = await self.client.post("/api/kqs/decisions", json={"signal_id": "fabricated", "decision": "hold"})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
