import json

import pytest


@pytest.mark.asyncio
async def test_secret_reference_status_never_exposes_values(client, make_user, monkeypatch):
    monkeypatch.setenv("BROKER_SECRET_SOURCE", "external_reference")
    monkeypatch.setenv("BROKER_API_KEY_REF", "vault://quant/broker-key")
    monkeypatch.setenv("BROKER_API_SECRET_REF", "vault://quant/broker-secret")
    monkeypatch.setenv("BROKER_ACCOUNT_ID_REF", "vault://quant/account")
    headers = await make_user("secret-status@example.com")
    response = await client.get("/api/strategy/execution-status", headers=headers)
    assert response.status_code == 200
    assert response.json()["broker_secret_source"] == "external_reference"
    assert response.json()["broker_secret_references_configured"] is True
    assert "vault://" not in response.text


@pytest.mark.asyncio
async def test_two_distinct_operators_approve_readiness_only(client, make_user, monkeypatch):
    emails = ["requester@example.com", "reviewer1@example.com", "reviewer2@example.com"]
    monkeypatch.setenv("EXECUTION_OPERATOR_EMAILS", ",".join(emails))
    requester, reviewer1, reviewer2 = [await make_user(email) for email in emails]
    created = await client.post("/api/strategy/execution-operations/requests", json={"purpose": "worm_archive_review"}, headers=requester)
    assert created.status_code == 200
    request_id = created.json()["request_id"]
    self_approval = await client.post(f"/api/strategy/execution-operations/requests/{request_id}/approve", headers=requester)
    assert self_approval.status_code == 409
    first = await client.post(f"/api/strategy/execution-operations/requests/{request_id}/approve", headers=reviewer1)
    assert first.json()["status"] == "pending"
    second = await client.post(f"/api/strategy/execution-operations/requests/{request_id}/approve", headers=reviewer2)
    assert second.json()["status"] == "approved_for_review"
    assert second.json()["creates_live_authority"] is False


@pytest.mark.asyncio
async def test_audit_export_has_valid_manifest_and_digest(client, make_user):
    headers = await make_user("audit-export@example.com")
    await client.put("/api/strategy/execution-permission", json={"paper_trading_enabled": False}, headers=headers)
    response = await client.get("/api/strategy/execution-audit/export", headers=headers)
    assert response.status_code == 200
    assert response.headers["x-audit-chain-valid"] == "true"
    assert len(response.headers["x-audit-sha256"]) == 64
    manifest = json.loads(response.text.splitlines()[0])
    assert manifest["type"] == "execution_audit_export"
    assert manifest["verification"]["valid"] is True


@pytest.mark.asyncio
async def test_recovery_and_live_integration_readiness_never_enable_live_orders(client, make_user):
    headers = await make_user("readiness-board@example.com")
    recovery = await client.get("/api/strategy/recovery-readiness", headers=headers)
    live = await client.get("/api/strategy/live-integration-readiness", headers=headers)

    assert recovery.status_code == 200, recovery.text
    assert recovery.json()["automatic_restore"] is False
    assert any(item["name"] == "백업 스크립트" for item in recovery.json()["checks"])
    assert live.status_code == 200, live.text
    assert live.json()["live_order_submission_available"] is False
    assert any(item["name"] == "실거래 주문 전송" and not item["passed"] for item in live.json()["gates"])


@pytest.mark.asyncio
async def test_decision_records_and_monthly_report_do_not_change_execution_authority(client, make_user):
    headers = await make_user("decision-log@example.com")
    created = await client.post("/api/strategy/decision-records", headers=headers, json={
        "target_type": "candidate", "target_id": "298380:2026-09-16", "decision": "review_required", "note": "표본 축적 후 재검토",
    })
    listed = await client.get("/api/strategy/decision-records", headers=headers)
    monthly = await client.get("/api/strategy/monthly-operations-report", headers=headers)
    lineage = await client.get("/api/strategy/data-lineage", headers=headers)

    assert created.status_code == 200, created.text
    assert created.json()["decision"] == "review_required"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["target_id"] == "298380:2026-09-16"
    assert monthly.status_code == 200, monthly.text
    assert monthly.json()["live_integration"]["live_order_submission_available"] is False
    assert "자동 실행" in monthly.json()["disclaimer"]
    assert lineage.status_code == 200, lineage.text
    assert lineage.json()["summary"]["ticker_count"] == 2
    assert "변경하지 않습니다" in lineage.json()["disclaimer"]
