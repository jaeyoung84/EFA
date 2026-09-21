import pandas as pd
import pytest

from app.backend.db import get_db
from app.backend.routers import strategy


def _frame(days=260):
    index = pd.bdate_range("2025-01-01", periods=days)
    close = pd.Series(range(100, 100 + days), index=index, dtype=float)
    return pd.DataFrame({"Open": close - 0.5, "Close": close})


@pytest.mark.asyncio
async def test_revalidation_risk_runbook_and_sandbox_contract_remain_non_executable(client, make_user, monkeypatch):
    monkeypatch.setattr(strategy, "_download_ohlc_frame", lambda ticker, period: _frame())
    headers = await make_user("quant-operations@example.com")
    research = await client.post("/api/strategy/research", headers=headers, json={
        "ticker": "005930.KS", "period": "5y", "fast_candidates": [5], "slow_candidates": [40],
    })
    assert research.status_code == 200, research.text
    portfolio = await client.put("/api/portfolio", headers=headers, json={
        "cash_krw": 1_000_000, "holdings": [{"ticker": "005930.KS", "name": "삼성전자", "quantity": 10, "average_price": 70_000, "currency": "KRW", "sector": "IT"}],
    })
    assert portfolio.status_code == 200, portfolio.text

    revalidated = await client.post(f"/api/strategy/research/{research.json()['research_id']}/revalidate", headers=headers)
    monitor = await client.get("/api/strategy/research/revalidation-monitor", headers=headers)
    risk = await client.get("/api/strategy/integrated-risk", headers=headers)
    runbook = await client.get("/api/strategy/operations-runbook", headers=headers)
    contract = await client.post("/api/strategy/broker-sandbox/contract-check", headers=headers)
    user = await get_db().users.find_one({"email": "quant-operations@example.com"})
    monthly = await strategy.capture_monthly_revalidation_evidence()

    assert revalidated.status_code == 200, revalidated.text
    assert revalidated.json()["automatic_action"] is False
    assert monitor.status_code == 200
    assert monitor.json()["items"][0]["status"] == "current"
    assert risk.status_code == 200
    assert risk.json()["items"][0]["ticker"] == "005930.KS"
    assert "주문도 변경하지 않습니다" in risk.json()["disclaimer"]
    assert runbook.status_code == 200
    assert runbook.json()["outbound_notifications"] is False
    assert contract.status_code == 200
    assert contract.json()["passed"] is True
    assert contract.json()["external_network_used"] is False
    assert contract.json()["live_order_submission_available"] is False
    assert monthly["stored_count"] >= 1
    assert await get_db().order_proposals.count_documents({"user_id": user["_id"]}) == 0
    assert await get_db().paper_fills.count_documents({"user_id": user["_id"]}) == 0
