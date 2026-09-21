import pandas as pd
import pytest

from app.backend.db import get_db
from app.backend.routers import strategy


def _payload(name: str, sharpe: float, cagr: float, mdd: float, previous_version_id: str = "") -> dict:
    return {
        "type": "real_backtest", "name": name, "ticker": "005930.KS",
        "params": {"fast_ma": 10, "slow_ma": 60, "period": "1y", "transaction_cost_bps": 10, "slippage_bps": 5, "benchmark_ticker": "^KS11"},
        "snapshot": {"metrics": {"total_return": cagr * 2, "cagr": cagr, "sharpe": sharpe, "mdd": mdd}, "benchmark_validation": {"beta": 0.8}, "strategy_return_series": []},
        "previous_version_id": previous_version_id,
    }


def _mock_market(monkeypatch) -> None:
    close = pd.Series(range(1, 121), index=pd.date_range("2025-01-01", periods=120, freq="B"), dtype=float)
    monkeypatch.setattr(strategy, "_download_close_series", lambda ticker, period: close)
    monkeypatch.setattr(strategy, "fetch_quote_cached", lambda ticker: {
        "status": "ok", "price": 100.0, "change_pct": 0.0, "quoted_at": "2026-01-01T00:00:00Z",
    })


async def _approved_strategy(client, make_user, monkeypatch) -> tuple[dict, str]:
    monkeypatch.setenv("EXECUTION_OPERATOR_EMAILS", "operator@example.com")
    headers = await make_user("operator@example.com")
    parent = await client.post("/api/strategy", json=_payload("기준", 1.0, 0.10, -0.20), headers=headers)
    child = await client.post("/api/strategy", json=_payload("승인 전략", 1.2, 0.15, -0.15, parent.json()["id"]), headers=headers)
    strategy_id = child.json()["id"]
    approved = await client.post(f"/api/strategy/strategy-version-promotion/{strategy_id}/approve", json={}, headers=headers)
    assert approved.status_code == 200
    return headers, strategy_id


@pytest.mark.asyncio
async def test_approved_strategy_passes_preflight_and_records_local_sandbox_result(client, make_user, monkeypatch):
    _mock_market(monkeypatch)
    monkeypatch.setenv("PAPER_ORDER_REQUIRE_PROMOTION_APPROVAL", "true")
    monkeypatch.setenv("PAPER_ORDER_BLOCK_PROMOTION_ALERTS", "true")
    monkeypatch.setenv("EXECUTION_MODE", "sandbox")
    monkeypatch.setenv("BROKER_ADAPTER", "sandbox")
    monkeypatch.setenv("BROKER_SANDBOX_ENABLED", "true")
    headers, strategy_id = await _approved_strategy(client, make_user, monkeypatch)

    preflight = await client.get(f"/api/strategy/paper-order-preflight/{strategy_id}", headers=headers)
    assert preflight.status_code == 200
    assert preflight.json()["allowed"] is True
    assert preflight.json()["promotion_approval_required"] is True
    proposal = await client.post(f"/api/strategy/{strategy_id}/proposals", headers=headers)
    assert proposal.status_code == 200
    assert proposal.json()["preflight"]["allowed"] is True
    fill = await client.post(f"/api/strategy/proposals/{proposal.json()['id']}/approve", json={"quantity": 1}, headers=headers)
    assert fill.status_code == 200
    assert fill.json()["sandbox_result"]["attempted"] is True
    assert fill.json()["sandbox_result"]["accepted"] is True
    audit = await client.get("/api/strategy/execution-audit", headers=headers)
    actions = {item["action"] for item in audit.json()["items"]}
    assert {"paper_order_preflight_passed", "paper_sandbox_order_accepted", "paper_proposal_approved"} <= actions


@pytest.mark.asyncio
async def test_promotion_review_alert_blocks_new_paper_order_and_is_audited(client, make_user, monkeypatch):
    _mock_market(monkeypatch)
    monkeypatch.setenv("PAPER_ORDER_REQUIRE_PROMOTION_APPROVAL", "true")
    monkeypatch.setenv("PAPER_ORDER_BLOCK_PROMOTION_ALERTS", "true")
    headers, strategy_id = await _approved_strategy(client, make_user, monkeypatch)
    user = await get_db().users.find_one({"email": "operator@example.com"})
    await get_db().paper_alerts.insert_one({
        "user_id": user["_id"], "fingerprint": "test:promotion-review", "condition_active": True,
        "type": "promotion_review", "title": "재검토 필요", "context": {"strategy_id": strategy_id},
    })
    preflight = await client.get(f"/api/strategy/paper-order-preflight/{strategy_id}", headers=headers)
    assert preflight.json()["allowed"] is False
    assert preflight.json()["reasons"][0]["code"] == "promotion_review_alert"
    blocked = await client.post(f"/api/strategy/{strategy_id}/proposals", headers=headers)
    assert blocked.status_code == 422
    audit = await client.get("/api/strategy/execution-audit", headers=headers)
    assert any(item["action"] == "paper_order_preflight_blocked" for item in audit.json()["items"])
