"""EINT-014/015 regressions: learning quality and consent are synthetic-only."""
import pytest

from app.backend.db import get_db
from app.backend.routers import kqs
from test_kqs_bridge import _metrics, _seed_mature_decisions


async def _set_consent(client, headers, consented: bool):
    return await client.put("/api/kqs/learning-consent", headers=headers, json={
        "consented": consented, "acknowledgement": "KQS_LEARNING_DATA_ONLY",
    })


@pytest.mark.asyncio
async def test_learning_consent_defaults_to_denied_then_explicit_opt_in_enables_only_v3_freeze(client, make_user):
    email = "learning-consent-default@example.com"
    headers = await make_user(email)
    await _seed_mature_decisions(email, 0, 10, grant_learning_consent=False)

    readiness = await client.get("/api/kqs/decisions/training-readiness", headers=headers)
    assert readiness.status_code == 200
    payload = readiness.json()
    assert payload["learning_consent"]["active"] is False
    assert payload["data_quality"]["ready"] is True
    assert payload["ready"] is False
    assert (await client.get("/api/kqs/decisions/training-dataset", headers=headers)).status_code == 409
    assert (await client.post("/api/kqs/datasets", headers=headers)).status_code == 422

    bad = await client.put("/api/kqs/learning-consent", headers=headers, json={"consented": True, "acknowledgement": "wrong"})
    assert bad.status_code == 422
    granted = await _set_consent(client, headers, True)
    assert granted.status_code == 200 and granted.json()["current"]["active"] is True

    ready = (await client.get("/api/kqs/decisions/training-readiness", headers=headers)).json()
    assert ready["ready"] is True
    frozen = await client.post("/api/kqs/datasets", headers=headers)
    assert frozen.status_code == 201, frozen.text
    assert frozen.json()["item"]["learning_consent"]["status"] == "granted"


@pytest.mark.asyncio
async def test_quality_gate_blocks_one_class_and_reports_concentration_without_training(client, make_user):
    email = "learning-quality-one-class@example.com"
    headers = await make_user(email)
    user = await _seed_mature_decisions(email, 0, 10)
    await get_db().kqs_paper_decisions.update_many({"user_id": user["_id"]}, {"$set": {"decision": "buy"}})
    await get_db().kqs_decision_outcomes.update_many({"user_id": user["_id"]}, {"$set": {"decision": "buy"}})

    readiness = (await client.get("/api/kqs/decisions/training-readiness", headers=headers)).json()
    quality = readiness["data_quality"]
    assert readiness["ready"] is False
    assert quality["ready"] is False
    assert quality["label_counts"] == {"buy": 150}
    assert any("레이블" in reason for reason in quality["blocking_reasons"])
    assert (await client.post("/api/kqs/datasets", headers=headers)).status_code == 422
    assert await get_db().kqs_dataset_manifests.count_documents({}) == 0


@pytest.mark.asyncio
async def test_revocation_blocks_future_v3_exports_holdout_and_experiment_without_rewriting_manifest(client, make_user):
    email = "learning-consent-revoke@example.com"
    headers = await make_user(email)
    await _seed_mature_decisions(email, 0, 10)
    frozen = await client.post("/api/kqs/datasets", headers=headers)
    assert frozen.status_code == 201, frozen.text
    manifest_id = frozen.json()["item"]["manifest_id"]
    before = await get_db().kqs_dataset_manifests.find_one({"manifest_id": manifest_id})
    assert (await _set_consent(client, headers, False)).status_code == 200

    assert (await client.get(f"/api/kqs/datasets/{manifest_id}/examples?split=train", headers=headers)).status_code == 409
    assert (await client.get("/api/kqs/decisions/training-dataset", headers=headers)).status_code == 409
    blocked_holdout = await client.post(f"/api/kqs/datasets/{manifest_id}/holdout", headers=headers, json={
        "model_family": "majority_class_baseline", "model_version": "blocked", "validation": _metrics(30),
    })
    assert blocked_holdout.status_code == 409
    blocked_experiment = await client.post("/api/kqs/training-experiments", headers=headers, json={
        "manifest_id": manifest_id, "model_family": "majority_class_baseline", "model_version": "blocked",
        "train": _metrics(90), "validation": _metrics(30),
    })
    assert blocked_experiment.status_code == 409
    after = await get_db().kqs_dataset_manifests.find_one({"manifest_id": manifest_id})
    assert after["manifest_sha256"] == before["manifest_sha256"]
    consent = (await client.get("/api/kqs/learning-consent", headers=headers)).json()
    assert consent["current"]["status"] == "revoked"
    assert [item["status"] for item in consent["history"]] == ["revoked", "granted"]
