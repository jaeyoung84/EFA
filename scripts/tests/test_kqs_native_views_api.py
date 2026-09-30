"""EINT-011 real SQLite publication + real router; fake annotations, no PC/Mongo/AWS."""
import hashlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_kqs_publication_api as fixtures
from app.backend.routers import kqs

PATHS = ("/api/kqs/status", "/api/kqs/review-candidates",
         "/api/kqs/review-candidates/000001/packet", "/api/kqs/candidate-queue/review-workflow")


class NativeViewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ApiTests.asyncSetUp
    authorize = fixtures.ApiTests.authorize
    publish = fixtures.ApiTests.publish

    async def test_current_views_require_auth_before_reads(self):
        for path in PATHS:
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 401)
        self.assertFalse(self.path.exists())

    async def test_status_candidates_packet_share_publication_and_real_reasons(self):
        self.authorize()
        raw = self.publish()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        annotations = SimpleNamespace(find=lambda *_, **__: SimpleNamespace(to_list=AsyncMock(return_value=[])),
                                      find_one=AsyncMock(return_value=None))
        database = {"kqs_native_candidate_reviews": annotations}
        with patch.object(kqs, "_read_bridge_payload", side_effect=AssertionError("PC bridge must not be requested")), \
                patch.object(kqs, "get_db", return_value=database):
            recommendations = (await self.client.get("/api/kqs/recommendations")).json()
            status = await self.client.get("/api/kqs/status")
            candidates = await self.client.get("/api/kqs/review-candidates")
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(candidates.status_code, 200, candidates.text)
            signal = recommendations["signals"][0]
            packet = await self.client.get(f'/api/kqs/review-candidates/{signal["symbol"]}.KS/packet')
            self.assertEqual(packet.status_code, 200, packet.text)
            for response in (status, candidates, packet):
                value = response.json()
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(value["snapshot_id"], recommendations["snapshot_id"])
                self.assertEqual(value["as_of"], recommendations["engine"]["data_as_of"])
                self.assertEqual(value["generated_at"], recommendations["generated_at"])
                self.assertEqual(value["expires_at"], recommendations["expires_at"])
                self.assertEqual(value["engine"]["code_sha256"], recommendations["engine"]["code_sha256"])
            self.assertEqual(packet.json()["selection_evidence"]["reasons"], signal["reasons"])
            self.assertEqual(packet.json()["selection_evidence"]["features"], signal["features"])
            self.assertEqual(candidates.json()["candidates"][0]["signal_id"], signal["signal_id"])
            self.assertIsNone(candidates.json()["blocked_signal_count"])
            self.assertFalse(status.json()["operations"]["verified"])
            self.assertNotIn("scheduler", status.json())
        self.assertEqual(raw["publication"]["snapshot_id"], recommendations["snapshot_id"])
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    async def test_missing_expired_corrupt_and_invalid_source_fail_closed_without_pc(self):
        self.authorize()
        with patch.object(kqs, "_read_bridge_payload", side_effect=AssertionError("No fallback")):
            for path in PATHS:
                self.assertEqual((await self.client.get(path)).status_code, 503)
            self.assertFalse(self.path.exists())
            self.publish(expired=True)
            for path in PATHS:
                self.assertEqual((await self.client.get(path)).status_code, 503)
            self.path.write_bytes(b"synthetic-corrupt-database")
            before = self.path.read_bytes()
            for path in PATHS:
                self.assertEqual((await self.client.get(path)).status_code, 503)
            self.assertEqual(self.path.read_bytes(), before)
            with patch.dict(os.environ, {"KQS_RECOMMENDATION_SOURCE": "typo"}):
                for path in PATHS:
                    self.assertEqual((await self.client.get(path)).status_code, 503)

    async def test_zero_recommendations_is_available_not_pipeline_health(self):
        self.authorize()
        self.runtime["recommendations"] = []
        for row in self.runtime["audit"]:
            if row["selection_outcome"] == "selected":
                row["selection_outcome"] = "not_entry_signal"
        self.runtime["status"] = "no_recommendations"
        self.publish()
        status = await self.client.get("/api/kqs/status")
        candidates = await self.client.get("/api/kqs/review-candidates")
        self.assertTrue(status.json()["available"])
        self.assertEqual(status.json()["status"], "no_recommendations")
        self.assertFalse(status.json()["operations"]["verified"])
        self.assertEqual(candidates.json()["candidates"], [])

    async def test_unknown_candidate_does_not_become_a_recommendation(self):
        self.authorize()
        self.publish()
        response = await self.client.get("/api/kqs/review-candidates/NOTREAL/packet")
        self.assertEqual(response.status_code, 404)

    async def test_native_positions_history_and_capture_are_not_applicable_no_mongo(self):
        self.authorize()
        with patch.object(kqs, "_read_bridge_payload", side_effect=AssertionError("No PC request")):
            for path in ("/api/kqs/portfolio-reconciliation", "/api/kqs/portfolio-reconciliation/history"):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["applicability"], "not_applicable")
                self.assertFalse(response.json()["available"])
                self.assertNotIn("summary", response.json())
            captured = await self.client.post("/api/kqs/portfolio-reconciliation/snapshot")
            self.assertFalse(captured.json()["stored"])
            self.assertEqual(captured.json()["applicability"], "not_applicable")
            await kqs.capture_all_portfolio_reconciliation_snapshots()


if __name__ == "__main__":
    unittest.main()
