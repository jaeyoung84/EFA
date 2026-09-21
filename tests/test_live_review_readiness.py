from datetime import datetime, timedelta, timezone

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


async def _approved_strategy(client, make_user, monkeypatch) -> tuple[dict, object, object]:
    monkeypatch.setenv("EXECUTION_OPERATOR_EMAILS", "operator@example.com")
    headers = await make_user("operator@example.com")
    parent = await client.post("/api/strategy", json=_payload("기준", 1.0, 0.10, -0.20), headers=headers)
    child = await client.post("/api/strategy", json=_payload("장기 검증", 1.2, 0.15, -0.15, parent.json()["id"]), headers=headers)
    strategy_id = child.json()["id"]
    assert (await client.post(f"/api/strategy/strategy-version-promotion/{strategy_id}/approve", json={}, headers=headers)).status_code == 200
    user = await get_db().users.find_one({"email": "operator@example.com"})
    state = await get_db().strategy_promotion_states.find_one({"user_id": user["_id"]})
    return headers, user["_id"], state["strategy_id"]


async def _seed_monitoring_data(user_id, strategy_id, values: list[float]) -> None:
    now = datetime.now(timezone.utc)
    await get_db().paper_accounts.insert_one({"user_id": user_id, "initial_capital_krw": 1_000})
    await get_db().paper_fills.insert_one({
        "user_id": user_id, "strategy_id": strategy_id, "ticker": "005930.KS", "direction": "buy", "quantity": 1,
        "fill_price": 100, "notional": 100, "notional_krw": 100, "currency": "KRW", "sector": "반도체",
        "risk_control": {"action": "approved"}, "filled_at": now - timedelta(days=len(values) + 1),
    })
    for index, value in enumerate(values):
        captured_at = now - timedelta(days=len(values) - index)
        await get_db().strategy_promotion_monitor_snapshots.insert_one({
            "user_id": user_id, "strategy_id": strategy_id, "snapshot_date": captured_at.date().isoformat(), "captured_at": captured_at,
            "status": "monitoring", "paper_return_proxy_pct": value,
        })
    await get_db().order_proposals.insert_one({
        "user_id": user_id, "strategy_id": strategy_id, "ticker": "005930.KS", "direction": "buy", "status": "approved", "created_at": now - timedelta(days=1),
    })


@pytest.mark.asyncio
async def test_long_paper_run_becomes_review_candidate_but_never_live_authority(client, make_user, monkeypatch):
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MIN_TRADING_DAYS", "5")
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MIN_ORDER_DECISIONS", "1")
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MAX_MDD_PCT", "100")
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MAX_PERFORMANCE_GAP_PCT", "100")
    monkeypatch.setattr(strategy, "fetch_quote_cached", lambda ticker: {"status": "ok", "price": 100.0, "change_pct": 0, "quoted_at": "2026-01-01T00:00:00Z"})
    headers, user_id, strategy_id = await _approved_strategy(client, make_user, monkeypatch)
    await _seed_monitoring_data(user_id, strategy_id, [0.0, 1.0, 0.5, 1.5, 2.0])

    response = await client.get("/api/strategy/live-review-readiness", headers=headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["status"] == "live_review_candidate"
    assert item["candidate_for_live_review"] is True
    assert item["metrics"]["observed_trading_days"] == 5
    assert item["creates_live_authority"] is False
    assert response.json()["live_trading_authorized"] is False


@pytest.mark.asyncio
async def test_long_paper_run_with_excess_mdd_requires_review_and_creates_alert(client, make_user, monkeypatch):
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MIN_TRADING_DAYS", "5")
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MIN_ORDER_DECISIONS", "1")
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MAX_MDD_PCT", "1")
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MAX_PERFORMANCE_GAP_PCT", "100")
    monkeypatch.setattr(strategy, "fetch_quote_cached", lambda ticker: {"status": "ok", "price": 100.0, "change_pct": 0, "quoted_at": "2026-01-01T00:00:00Z"})
    headers, user_id, strategy_id = await _approved_strategy(client, make_user, monkeypatch)
    await _seed_monitoring_data(user_id, strategy_id, [0.0, 4.0, -4.0, 2.0, 3.0])

    response = await client.get("/api/strategy/live-review-readiness", headers=headers)
    assert response.json()["items"][0]["status"] == "review_required"
    refreshed = await client.post("/api/strategy/alerts/refresh", headers=headers)
    assert refreshed.status_code == 200
    alerts = await client.get("/api/strategy/alerts?status=active", headers=headers)
    assert any(item["type"] == "promotion_live_review" for item in alerts.json()["items"])


@pytest.mark.asyncio
async def test_strategy_governance_board_only_classifies_human_review_candidates(client, make_user, monkeypatch):
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MIN_TRADING_DAYS", "5")
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MIN_ORDER_DECISIONS", "1")
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MAX_MDD_PCT", "100")
    monkeypatch.setenv("PAPER_LIVE_REVIEW_MAX_PERFORMANCE_GAP_PCT", "100")
    monkeypatch.setattr(strategy, "fetch_quote_cached", lambda ticker: {"status": "ok", "price": 100.0, "change_pct": 0, "quoted_at": "2026-01-01T00:00:00Z"})
    headers, user_id, strategy_id = await _approved_strategy(client, make_user, monkeypatch)
    await _seed_monitoring_data(user_id, strategy_id, [0.0, 1.0, 0.5, 1.5, 2.0])

    response = await client.get("/api/strategy/governance-board", headers=headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"]["live_review_candidate_count"] == 1
    assert payload["items"][0]["recommendation"] == "human_review_candidate"
    assert payload["live_trading_authorized"] is False
    assert "주문 제출" in payload["disclaimer"]
