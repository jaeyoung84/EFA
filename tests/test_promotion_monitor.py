from datetime import datetime, timedelta, timezone

import pytest

from app.backend.db import get_db


def _payload(name: str, sharpe: float, cagr: float, mdd: float, previous_version_id: str = "") -> dict:
    return {
        "type": "real_backtest", "name": name, "ticker": "005930.KS",
        "params": {"fast_ma": 10, "slow_ma": 60, "period": "2y", "transaction_cost_bps": 10, "slippage_bps": 5, "benchmark_ticker": "^KS11"},
        "snapshot": {"metrics": {"total_return": cagr * 2, "cagr": cagr, "sharpe": sharpe, "mdd": mdd}, "benchmark_validation": {"beta": 0.8}, "strategy_return_series": []},
        "previous_version_id": previous_version_id,
    }


@pytest.mark.asyncio
async def test_approved_strategy_monitor_creates_daily_snapshot_and_review_alert(client, make_user, monkeypatch):
    monkeypatch.setenv("EXECUTION_OPERATOR_EMAILS", "operator@example.com")
    headers = await make_user("operator@example.com")
    parent = await client.post("/api/strategy", json=_payload("기준", 1.0, 0.10, -0.20), headers=headers)
    child = await client.post("/api/strategy", json=_payload("승인 대상", 1.2, 0.15, -0.15, parent.json()["id"]), headers=headers)
    strategy_id = child.json()["id"]
    approved = await client.post(f"/api/strategy/strategy-version-promotion/{strategy_id}/approve", json={}, headers=headers)
    assert approved.status_code == 200

    await get_db().strategy_promotion_states.update_one(
        {"user_id": (await get_db().users.find_one({"email": "operator@example.com"}))["_id"]},
        {"$set": {"approved_at": datetime.now(timezone.utc) - timedelta(days=31)}},
    )
    monitor = await client.get("/api/strategy/promotion-monitor", headers=headers)
    assert monitor.status_code == 200
    assert monitor.json()["records"][0]["review_due"] is True
    assert monitor.json()["records"][0]["status"] == "waiting_for_paper_fills"
    assert any(item["type"] == "promotion_review" for item in monitor.json()["findings"])

    snapshot = await client.post("/api/strategy/paper-dashboard/snapshot", headers=headers)
    assert snapshot.status_code == 200
    assert snapshot.json()["promotion_monitor"]["approved_strategy_count"] == 1
    refreshed = await client.post("/api/strategy/promotion-monitor/refresh", headers=headers)
    assert refreshed.status_code == 200
    alerts = await client.get("/api/strategy/alerts?status=active", headers=headers)
    assert any(item["type"] == "promotion_review" for item in alerts.json()["items"])
