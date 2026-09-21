from datetime import datetime, timedelta, timezone

import pytest

from app.backend.db import get_db
from app.backend.routers import strategy


def test_paper_execution_simulation_keeps_limit_and_partial_fill_local():
    unfilled = strategy._simulate_paper_execution(side="buy", quote_price=101, order_type="limit", limit_price=100, requested_quantity=10, max_fill_quantity=None)
    partial = strategy._simulate_paper_execution(side="buy", quote_price=100, order_type="market", limit_price=None, requested_quantity=10, max_fill_quantity=3)
    assert unfilled["status"] == "unfilled"
    assert partial["status"] == "partially_filled"
    assert partial["fill_quantity"] == 3
    assert partial["external_execution"] is False


@pytest.mark.asyncio
async def test_factor_universe_screen_uses_stored_same_date_bars_only(client, make_user):
    headers = await make_user("universe-screen@example.com")
    rows = []
    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    for ticker, base in (("005930.KS", 100), ("000660.KS", 200)):
        for index in range(25):
            stamp = (start + timedelta(days=index)).date().isoformat()
            rows.append({"ticker": ticker, "interval": "1d", "timestamp": stamp, "close": base + index,
                         "volume": 1_000 + index, "fingerprint": f"{ticker}-{index}", "source": "fixture", "dataset_version": "fixture-v1"})
    await get_db().market_ohlcv.insert_many(rows)
    response = await client.post("/api/strategy/research/universe-screen", headers=headers, json={
        "tickers": ["005930.KS", "000660.KS"], "as_of": (start + timedelta(days=24)).date().isoformat(),
        "minimum_average_volume_20d": 900,
    })
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 2
    assert response.json()["items"][0]["eligible_for_research"] is True
    assert "주문" in response.json()["disclaimer"]


@pytest.mark.asyncio
async def test_operations_observability_and_security_design_remain_read_only(client, make_user):
    headers = await make_user("observability@example.com")
    board = await client.get("/api/strategy/operations-runbook", headers=headers)
    readiness = await client.get("/api/strategy/live-integration-readiness", headers=headers)
    assert board.status_code == 200, board.text
    assert "system_observability" in board.json()
    assert board.json()["outbound_notifications"] is False
    assert readiness.status_code == 200
    assert any(item["name"] == "실브로커 보안 설계" and item["passed"] for item in readiness.json()["gates"])
