from datetime import date, datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.backend.db import get_db
from app.backend.routers import strategy


@pytest.mark.asyncio
async def test_risk_daily_loss_and_signal_alerts_have_resolution_history(client, make_user, monkeypatch):
    headers = await make_user("paper-alerts@example.com")
    user = await get_db().users.find_one({"email": "paper-alerts@example.com"})
    strategy_id = ObjectId()
    proposal_id = ObjectId()
    now = datetime.now(timezone.utc)
    await get_db().paper_accounts.insert_one({"user_id": user["_id"], "initial_capital_krw": 1_000})
    await get_db().paper_fills.insert_one({"user_id": user["_id"], "strategy_id": strategy_id, "ticker": "005930.KS", "direction": "buy",
        "quantity": 5, "fill_price": 100, "notional": 500, "notional_krw": 500, "currency": "KRW", "sector": "반도체", "filled_at": now})
    await get_db().paper_daily_snapshots.insert_one({"user_id": user["_id"], "snapshot_date": (date.today() - timedelta(days=1)).isoformat(),
        "captured_at": now - timedelta(days=1), "available": True, "equity_krw": 1_000})
    await get_db().order_proposals.insert_one({"_id": proposal_id, "user_id": user["_id"], "strategy_id": strategy_id,
        "ticker": "005930.KS", "direction": "buy", "basis": "test", "reference_price": 80, "status": "pending", "created_at": now})
    monkeypatch.setattr(strategy, "fetch_quote_cached", lambda ticker: {"status": "ok", "price": 80.0, "change_pct": 0, "quoted_at": now.isoformat()})
    await client.put("/api/portfolio/risk-policy", json={"max_position_pct": 100, "max_sector_pct": 100, "max_foreign_currency_pct": 100,
        "max_var_95_pct": 100, "max_mdd_pct": 100, "max_daily_loss_pct": 3, "target_annual_volatility_pct": 15}, headers=headers)

    refreshed = await client.post("/api/strategy/alerts/refresh", headers=headers)
    assert refreshed.status_code == 200
    alerts = await client.get("/api/strategy/alerts", headers=headers)
    assert {item["type"] for item in alerts.json()["items"]} == {"risk_limit", "strategy_signal"}
    daily = next(item for item in alerts.json()["items"] if item["context"].get("code") == "daily_loss")
    deferred = await client.post(f"/api/strategy/alerts/{daily['id']}/resolve", json={"action": "deferred", "note": "내일 재검토"}, headers=headers)
    assert deferred.json()["status"] == "deferred"
    assert deferred.json()["events"][-1]["note"] == "내일 재검토"

    await get_db().order_proposals.update_one({"_id": proposal_id}, {"$set": {"status": "approved"}})
    monkeypatch.setattr(strategy, "fetch_quote_cached", lambda ticker: {"status": "ok", "price": 120.0, "change_pct": 0, "quoted_at": now.isoformat()})
    await client.post("/api/strategy/alerts/refresh", headers=headers)
    active = await client.get("/api/strategy/alerts", headers=headers)
    assert active.json()["items"] == []


@pytest.mark.asyncio
async def test_performance_drawdown_is_an_alert_not_an_order_action(client, make_user, monkeypatch):
    headers = await make_user("performance-alert@example.com")
    user = await get_db().users.find_one({"email": "performance-alert@example.com"})
    now = datetime.now(timezone.utc)
    await get_db().paper_accounts.insert_one({"user_id": user["_id"], "initial_capital_krw": 1_000})
    await get_db().paper_fills.insert_one({"user_id": user["_id"], "strategy_id": ObjectId(), "ticker": "005930.KS", "direction": "buy", "quantity": 1, "fill_price": 100, "notional": 100, "notional_krw": 100, "currency": "KRW", "sector": "반도체", "filled_at": now})
    await get_db().paper_daily_snapshots.insert_many([
        {"user_id": user["_id"], "snapshot_date": (date.today() - timedelta(days=2)).isoformat(), "captured_at": now - timedelta(days=2), "available": True, "equity_krw": 1_000},
        {"user_id": user["_id"], "snapshot_date": (date.today() - timedelta(days=1)).isoformat(), "captured_at": now - timedelta(days=1), "available": True, "equity_krw": 800},
    ])
    monkeypatch.setattr(strategy, "fetch_quote_cached", lambda ticker: {"status": "ok", "price": 100.0, "change_pct": 0, "quoted_at": now.isoformat()})
    refreshed = await client.post("/api/strategy/alerts/refresh", headers=headers)
    assert refreshed.status_code == 200
    alerts = await client.get("/api/strategy/alerts", headers=headers)
    performance = [item for item in alerts.json()["items"] if item["type"] == "strategy_performance"]
    assert any(item["context"].get("mdd_pct") == 20.0 for item in performance)
    assert any(item["context"].get("return_pct") == -20.0 for item in performance)
