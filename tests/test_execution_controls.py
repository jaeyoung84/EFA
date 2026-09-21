import pytest

from app.backend.execution_controls import execution_environment_status, paper_submission_allowed
from app.backend.db import get_db


def test_live_execution_stays_unavailable_without_installed_adapter(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_TRADING_KILL_SWITCH", "false")
    monkeypatch.setenv("EXECUTION_GLOBAL_KILL_SWITCH", "false")
    monkeypatch.setenv("BROKER_API_KEY", "key")
    monkeypatch.setenv("BROKER_API_SECRET", "secret")
    monkeypatch.setenv("BROKER_ACCOUNT_ID", "account")
    status = execution_environment_status()
    assert status["live_credentials_configured"] is True
    assert status["live_adapter"]["ready"] is False
    assert status["live_order_submission_available"] is False
    assert status["secrets_exposed"] is False


@pytest.mark.asyncio
async def test_user_can_disable_paper_submission_without_creating_live_permission(client, make_user):
    headers = await make_user("execution-controls@example.com")
    initial = await client.get("/api/strategy/execution-status", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["user_permission"]["paper_trading_enabled"] is True
    assert "BROKER_API_SECRET" not in initial.text
    updated = await client.put("/api/strategy/execution-permission", json={"paper_trading_enabled": False}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["live_trading_authorized"] is False
    user = await get_db().users.find_one({"email": "execution-controls@example.com"})
    allowed, reason = await paper_submission_allowed(user["_id"])
    assert allowed is False
    assert "모의 주문 권한" in reason
