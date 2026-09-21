from datetime import date, datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.backend.db import get_db
from app.backend.routers import strategy


@pytest.mark.asyncio
async def test_paper_dashboard_reports_cash_realized_unrealized_and_equity(client, make_user, monkeypatch):
    headers = await make_user("paper-dashboard@example.com")
    user = await get_db().users.find_one({"email": "paper-dashboard@example.com"})
    strategy_id = ObjectId()
    await get_db().strategies.insert_one({"_id": strategy_id, "user_id": user["_id"], "name": "대시보드 전략"})
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await get_db().paper_accounts.insert_one({"user_id": user["_id"], "initial_capital_krw": 1_000})
    await get_db().paper_fills.insert_many([
        {"user_id": user["_id"], "strategy_id": strategy_id, "ticker": "005930.KS", "direction": "buy", "quantity": 2,
         "fill_price": 100, "notional": 200, "notional_krw": 200, "currency": "KRW", "sector": "반도체", "filled_at": start},
        {"user_id": user["_id"], "strategy_id": strategy_id, "ticker": "005930.KS", "direction": "sell", "quantity": 1,
         "fill_price": 150, "notional": 150, "notional_krw": 150, "currency": "KRW", "sector": "반도체", "filled_at": start + timedelta(days=1)},
    ])
    monkeypatch.setattr(strategy, "fetch_quote_cached", lambda ticker: {
        "status": "ok", "price": 120.0, "change_pct": 0.0, "quoted_at": "2026-01-03T00:00:00Z",
    })

    response = await client.get("/api/strategy/paper-dashboard", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is True
    assert data["cash_krw"] == 950
    assert data["realized_pnl_krw"] == 50
    assert data["unrealized_pnl_krw"] == 20
    assert data["equity_krw"] == 1_070
    assert data["strategy_summary"][0]["strategy_name"] == "대시보드 전략"
    assert data["positions"][0]["quantity"] == 1

    captured = await client.post("/api/strategy/paper-dashboard/snapshot", headers=headers)
    assert captured.status_code == 200
    current_date = date.fromisoformat(captured.json()["snapshot_date"])
    await get_db().paper_daily_snapshots.insert_one({
        "user_id": user["_id"], "snapshot_date": (current_date - timedelta(days=1)).isoformat(),
        "captured_at": start, "available": True, "equity_krw": 1_000,
    })
    history = await client.get("/api/strategy/paper-dashboard/history?period=1m", headers=headers)
    assert history.status_code == 200
    assert history.json()["valid_snapshot_count"] == 2
    assert history.json()["metrics"]["period_return_pct"] == 7

    report = await client.get("/api/strategy/paper-report?period=all", headers=headers)
    assert report.status_code == 200
    assert report.json()["report_version"] == "paper-research-report-v1"
    assert report.json()["audit"]["fill_count"] == 2
    csv_export = await client.get("/api/strategy/paper-report/export?period=all&format=csv", headers=headers)
    assert csv_export.status_code == 200
    assert "section,recorded_at,field,value" in csv_export.text
    json_export = await client.get("/api/strategy/paper-report/export?period=all&format=json", headers=headers)
    assert json_export.status_code == 200
    assert json_export.json()["period"] == "all"
