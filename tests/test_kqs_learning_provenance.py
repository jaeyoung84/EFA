"""EINT-013 leakage/provenance regressions; disposable test Mongo and synthetic rows only."""
from datetime import datetime, timezone
import json

import pytest
from bson import ObjectId

from app.backend.db import get_db
from app.backend.routers import kqs
from test_kqs_bridge import _seed_mature_decisions, _metrics


async def _legacy_v2_manifest(user, decisions, outcomes_by_id):
    """Synthetic immutable historical shape; does not call a real archive or holdout."""
    partition = kqs._kqs_training_partition(decisions, outcomes_by_id)
    by_id = {row["_id"]: row for row in decisions}
    examples = {}
    for kind in kqs._KQS_SPLITS:
        ordered = sorted(partition["splits"][kind], key=lambda _id: (
            str(by_id[_id]["data_as_of"]), str(_id)))
        examples[kind] = [{"id": str(_id), "as_of": str(by_id[_id]["data_as_of"])[:10],
                           "sha256": kqs._kqs_example_sha256(by_id[_id], outcomes_by_id.get(_id, {}))}
                          for _id in ordered]
    body = {
        "schema_version": kqs.KQS_DATASET_MANIFEST_SCHEMA,
        "example_schema": kqs.KQS_DATASET_EXAMPLE_SCHEMA_V2,
        "sequence": 1, "previous_manifest_id": None, "previous_manifest_sha256": None,
        "policy": partition["policy"], "boundaries": partition["boundaries"],
        "counts": partition["counts"], "excluded_overlap_count": partition["excluded_overlap_count"],
        "examples": examples, "dataset_sha256": kqs._canonical_sha256(examples),
        "hash_scope": "features at decision time + human_decision + observed 20-day label; notes and realized P&L excluded",
    }
    row = {"manifest_id": "synthetic-historical-v2", "user_id": user["_id"],
           "created_at": datetime.now(timezone.utc), **body,
           "manifest_sha256": kqs._canonical_sha256(body)}
    await get_db().kqs_dataset_manifests.insert_one(row)
    return row


@pytest.mark.asyncio
async def test_v3_freeze_keeps_human_tag_out_of_features_and_binds_engine_provenance(client, make_user):
    email = "learning-v3@example.com"
    headers = await make_user(email)
    user = await _seed_mature_decisions(email, 0, 10)
    one = await get_db().kqs_paper_decisions.find_one({"user_id": user["_id"]})
    await get_db().kqs_paper_decisions.update_one({"_id": one["_id"]},
        {"$set": {"evidence_tags": ["buy"], "memo": "나중 판단", "quantity": 9, "fill_price": 77}})
    frozen = await client.post("/api/kqs/datasets", headers=headers)
    assert frozen.status_code == 201, frozen.text
    item = frozen.json()["item"]
    assert item["example_schema"] == "kqs-human-decisions/v3"
    assert item["feature_schema"] == "kqs-engine-features/v1"
    assert item["feature_schema_sha256"] == kqs._canonical_sha256(kqs.KQS_FEATURE_SCHEMA_V3)
    train = await client.get(f'/api/kqs/datasets/{item["manifest_id"]}/examples?split=train&include_notes=true',
                             headers=headers)
    assert train.status_code == 200, train.text
    header, *rows = [json.loads(line) for line in train.text.splitlines()]
    assert header["training_eligible"] is True and header["hash_verified"] is True
    tagged = next(row for row in rows if row["example_id"] == str(one["_id"]))
    assert set(tagged["features"]) == set(kqs.KQS_FEATURE_SCHEMA_V3["fields"])
    assert "buy" not in json.dumps(tagged["features"])
    assert tagged["human_context"]["evidence_tags"] == ["buy"]
    assert tagged["human_context"]["memo"] == "나중 판단"
    assert tagged["human_context"]["quantity"] == 9
    assert tagged["labels"]["human_decision"] == "buy"
    assert tagged["feature_provenance"]["publication_snapshot_id"] == one["publication_snapshot_id"]
    assert not ({"evidence_tags", "human_note", "memo", "quantity", "fill_price", "realized_trade"}
                & tagged["features"].keys())


@pytest.mark.asyncio
async def test_v3_human_annotation_is_separate_but_engine_source_drift_blocks_export(client, make_user):
    email = "learning-drift@example.com"
    headers = await make_user(email)
    user = await _seed_mature_decisions(email, 0, 10)
    frozen = await client.post("/api/kqs/datasets", headers=headers)
    assert frozen.status_code == 201
    manifest_id = frozen.json()["item"]["manifest_id"]
    manifest = await get_db().kqs_dataset_manifests.find_one({"manifest_id": manifest_id})
    decision_id = ObjectId(manifest["examples"]["train"][0]["id"])
    await get_db().kqs_paper_decisions.update_one({"_id": decision_id}, {"$set": {"evidence_tags": ["human-only"]}})
    still_valid = await client.get(f"/api/kqs/datasets/{manifest_id}/examples?split=train", headers=headers)
    assert still_valid.status_code == 200, still_valid.text
    await get_db().kqs_paper_decisions.update_one(
        {"_id": decision_id}, {"$set": {"recommendation_snapshot.signal_type": "tampered"}})
    drifted = await client.get(f"/api/kqs/datasets/{manifest_id}/examples?split=train", headers=headers)
    assert drifted.status_code == 409
    opening = await client.post(f"/api/kqs/datasets/{manifest_id}/holdout", headers=headers,
                                json={"model_family": "majority_class_baseline", "model_version": "m1",
                                      "validation": _metrics(30)})
    assert opening.status_code == 409
    assert await get_db().kqs_dataset_holdout_openings.count_documents({}) == 0


@pytest.mark.asyncio
async def test_missing_publication_provenance_blocks_readiness_and_freeze(client, make_user):
    email = "learning-incomplete@example.com"
    headers = await make_user(email)
    user = await _seed_mature_decisions(email, 0, 10)
    one = await get_db().kqs_paper_decisions.find_one({"user_id": user["_id"]})
    await get_db().kqs_paper_decisions.update_one({"_id": one["_id"]},
                                                   {"$unset": {"publication_snapshot_id": ""}})
    ready = await client.get("/api/kqs/decisions/training-readiness", headers=headers)
    assert ready.status_code == 200 and ready.json()["ready"] is False
    assert ready.json()["invalid_feature_provenance_count"] == 1
    frozen = await client.post("/api/kqs/datasets", headers=headers)
    assert frozen.status_code == 422
    assert await get_db().kqs_dataset_manifests.count_documents({}) == 0


@pytest.mark.asyncio
async def test_existing_v2_manifest_hashes_remain_readable_but_new_training_is_blocked(client, make_user):
    email = "learning-old-v2@example.com"
    headers = await make_user(email)
    user = await _seed_mature_decisions(email, 0, 10)
    decisions, outcomes = await kqs._kqs_user_dataset_rows(user["_id"])
    old = await _legacy_v2_manifest(user, decisions, outcomes)
    original_sha = old["manifest_sha256"]
    response = await client.get(f'/api/kqs/datasets/{old["manifest_id"]}/examples?split=train', headers=headers)
    assert response.status_code == 200, response.text
    header, *rows = [json.loads(line) for line in response.text.splitlines()]
    assert header["schema_version"] == "kqs-human-decisions/v2"
    assert header["training_eligible"] is False and header["legacy_feature_leakage_warning"] is True
    assert "evidence_tags" in rows[0]["features"]
    assert (await client.post(f'/api/kqs/datasets/{old["manifest_id"]}/holdout', headers=headers,
                              json={"model_family": "majority_class_baseline", "model_version": "m1",
                                    "validation": _metrics(30)})).status_code == 409
    assert (await client.post("/api/kqs/training-experiments", headers=headers,
                              json={"manifest_id": old["manifest_id"], "model_family": "majority_class_baseline",
                                    "model_version": "m1", "train": _metrics(90),
                                    "validation": _metrics(30)})).status_code == 409
    stored = await get_db().kqs_dataset_manifests.find_one({"manifest_id": old["manifest_id"]})
    assert stored["manifest_sha256"] == original_sha and "feature_schema" not in stored
    assert await get_db().kqs_dataset_holdout_openings.count_documents({}) == 0
    assert await get_db().kqs_training_experiments.count_documents({}) == 0


@pytest.mark.asyncio
async def test_v3_successor_to_v2_keeps_previous_boundary_and_retires_old_holdout(client, make_user):
    email = "learning-successor@example.com"
    headers = await make_user(email)
    user = await _seed_mature_decisions(email, 0, 10)
    decisions, outcomes = await kqs._kqs_user_dataset_rows(user["_id"])
    old = await _legacy_v2_manifest(user, decisions, outcomes)
    await _seed_mature_decisions(email, 10, 4)
    response = await client.post("/api/kqs/datasets", headers=headers)
    assert response.status_code == 201, response.text
    new = response.json()["item"]
    assert new["example_schema"] == "kqs-human-decisions/v3"
    assert new["previous_manifest_id"] == old["manifest_id"]
    assert new["boundaries"]["validation"]["first"] > old["boundaries"]["test"]["last"]
    listed = (await client.get("/api/kqs/datasets", headers=headers)).json()["items"]
    assert [item["holdout"]["status"] for item in listed] == ["sealed", "retired_sealed"]
    assert (await get_db().kqs_dataset_manifests.find_one({"manifest_id": old["manifest_id"]}))["manifest_sha256"] == old["manifest_sha256"]


@pytest.mark.asyncio
async def test_opened_v2_holdout_is_preserved_for_audit_but_cannot_be_reopened(client, make_user):
    email = "learning-old-opened@example.com"
    headers = await make_user(email)
    user = await _seed_mature_decisions(email, 0, 10)
    decisions, outcomes = await kqs._kqs_user_dataset_rows(user["_id"])
    old = await _legacy_v2_manifest(user, decisions, outcomes)
    await get_db().kqs_dataset_holdout_openings.insert_one({
        "manifest_id": old["manifest_id"], "user_id": user["_id"],
        "opened_at": datetime.now(timezone.utc),
        "preregistration": {"model_family": "majority_class_baseline", "model_version": "historical"},
        "preregistration_sha256": "synthetic-old-opening"})
    response = await client.get(f'/api/kqs/datasets/{old["manifest_id"]}/examples?split=test', headers=headers)
    assert response.status_code == 200, response.text
    header = json.loads(response.text.splitlines()[0])
    assert header["schema_version"] == "kqs-human-decisions/v2"
    assert header["holdout_status"] == "opened"
    assert header["training_eligible"] is False
    assert await get_db().kqs_dataset_access_log.count_documents({"manifest_id": old["manifest_id"]}) == 1
    assert (await client.post(f'/api/kqs/datasets/{old["manifest_id"]}/holdout', headers=headers,
                              json={"model_family": "majority_class_baseline", "model_version": "new",
                                    "validation": _metrics(30)})).status_code == 409
    assert await get_db().kqs_dataset_holdout_openings.count_documents({"manifest_id": old["manifest_id"]}) == 1
