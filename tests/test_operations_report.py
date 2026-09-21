import pytest

from app.backend.execution_audit import append_execution_audit
from app.backend.db import get_db


@pytest.mark.asyncio
async def test_operations_report_summarizes_audit_and_never_exposes_secrets(client, make_user, monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "should-not-appear")
    headers = await make_user("operations-report@example.com")
    user = await get_db().users.find_one({"email": "operations-report@example.com"})
    await append_execution_audit(user["_id"], "paper_order_preflight_passed", "order_proposal", {"latency_ms": 12.5})
    response = await client.get("/api/strategy/operations-report?days=7", headers=headers)
    assert response.status_code == 200
    report = response.json()
    assert report["report_version"] == "quant-operations-report-v1"
    assert report["audit"]["verification"]["valid"] is True
    assert report["audit"]["recent_action_counts"]["paper_order_preflight_passed"] == 1
    assert report["order_quality"]["average_processing_latency_ms"] == 12.5
    assert any(check["name"] == "실거래 주문 차단" and check["passed"] for check in report["safety_checks"])
    assert "should-not-appear" not in response.text

    exported = await client.get("/api/strategy/operations-report/export?days=7", headers=headers)
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/json")
    assert "quant-operations-report-v1" in exported.text
