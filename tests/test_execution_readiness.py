import pytest

from app.backend.broker_adapter import BrokerOrderRequest, get_broker_adapter
from app.backend.execution_audit import append_execution_audit, verify_execution_audit
from app.backend.execution_controls import execution_environment_status
from app.backend.db import get_db


def test_explicit_sandbox_never_becomes_a_live_adapter(monkeypatch):
    monkeypatch.setenv("BROKER_ADAPTER", "sandbox")
    monkeypatch.setenv("BROKER_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_MODE", "sandbox")
    adapter = get_broker_adapter()
    result = adapter.submit_order(BrokerOrderRequest(ticker="005930.KS", side="buy", quantity=1))
    status = execution_environment_status()
    assert result.accepted is True
    assert result.broker_order_id.startswith("sandbox-")
    assert status["sandbox_order_submission_available"] is True
    assert status["live_adapter"]["live_capable"] is False
    assert status["live_order_submission_available"] is False


@pytest.mark.asyncio
async def test_execution_audit_chain_detects_tampering(make_user):
    headers = await make_user("audit-chain@example.com")
    # 회원가입 헬퍼가 사용자 문서를 만든 뒤, 테스트는 내부 이벤트를 직접 넣어
    # 해시 체인 자체를 외부 주문 흐름과 독립적으로 검증한다.
    token = headers["Authorization"]
    assert token.startswith("Bearer ")
    user = await get_db().users.find_one({"email": "audit-chain@example.com"})
    await append_execution_audit(user["_id"], "paper_permission_changed", "execution_permission", {"paper_trading_enabled": False})
    await append_execution_audit(user["_id"], "paper_proposal_rejected", "order_proposal", {"proposal_id": "example"})
    valid = await verify_execution_audit(user["_id"])
    assert valid["valid"] is True
    assert valid["event_count"] == 2
    await get_db().execution_audit_events.update_one({"user_id": user["_id"], "sequence": 1}, {"$set": {"payload.paper_trading_enabled": True}})
    tampered = await verify_execution_audit(user["_id"])
    assert tampered["valid"] is False
    assert tampered["failed_sequence"] == 1


@pytest.mark.asyncio
async def test_execution_audit_endpoint_is_user_isolated(client, make_user):
    owner = await make_user("audit-owner@example.com")
    other = await make_user("audit-other@example.com")
    changed = await client.put("/api/strategy/execution-permission", json={"paper_trading_enabled": False}, headers=owner)
    assert changed.status_code == 200
    owner_audit = await client.get("/api/strategy/execution-audit", headers=owner)
    other_audit = await client.get("/api/strategy/execution-audit", headers=other)
    assert owner_audit.status_code == 200
    assert owner_audit.json()["verification"]["valid"] is True
    assert len(owner_audit.json()["items"]) == 1
    assert other_audit.json()["items"] == []
