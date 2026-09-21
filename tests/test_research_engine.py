import pandas as pd
import pytest

from app.backend import research_engine
from app.backend.routers import strategy


def _frame(days=260):
    index = pd.bdate_range("2025-01-01", periods=days)
    close = pd.Series(range(100, 100 + days), index=index, dtype=float)
    return pd.DataFrame({"Open": close - 0.5, "Close": close})


def test_research_keeps_final_test_out_of_selection():
    result = research_engine.run_research(_frame(), "TEST", [(5, 40), (10, 60)], 10, 5, walk_forward_folds=2)
    assert result["selection_rule"].startswith("validation")
    assert result["selected"]["final_test"]["days"] > 0
    assert result["split"]["test_start"] > result["split"]["validation_end"]
    assert len(result["walk_forward"]) >= 1
    assert result["point_in_time_audit"]["passed"] is True
    assert len(result["cost_stress"]) == 3
    assert result["statistical_validation"]["available"] is True
    assert result["statistical_validation"]["candidate_count"] == 2
    assert result["paper_candidate_assessment"]["status"] in {"paper_candidate", "review_required"}


def test_research_factor_filter_is_applied_at_close_before_next_open():
    result = research_engine.run_research(
        _frame(), "TEST", [(5, 40)], 10, 5, walk_forward_folds=2,
        factor_filter={"enabled": True, "min_return_20d": 0.01, "max_realized_volatility_20d": 1.0},
    )
    assert result["factor_filter"]["enabled"] is True
    assert result["point_in_time_audit"]["execution_rule"].endswith("t+1일 시가 체결")


@pytest.mark.asyncio
async def test_research_api_saves_per_user_result(monkeypatch, client, make_user):
    monkeypatch.setattr(strategy, "_download_ohlc_frame", lambda ticker, period: _frame())
    headers = await make_user("research@example.com")
    response = await client.post("/api/strategy/research", json={"ticker": "AAPL", "period": "5y", "fast_candidates": [5, 10], "slow_candidates": [40, 60]}, headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["research_id"]
    assert payload["selected"]["run_id"]
    listed = await client.get("/api/strategy/research", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1


@pytest.mark.asyncio
async def test_research_api_records_factor_filter_and_candidate_is_not_order_authority(monkeypatch, client, make_user):
    monkeypatch.setattr(strategy, "_download_ohlc_frame", lambda ticker, period: _frame())
    headers = await make_user("factor-research@example.com")
    response = await client.post("/api/strategy/research", json={
        "ticker": "AAPL", "period": "5y", "fast_candidates": [5], "slow_candidates": [40],
        "factor_filter_enabled": True, "min_return_20d": 0.01, "max_realized_volatility_20d": 1.0,
    }, headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["factor_filter"]["enabled"] is True
    assert payload["point_in_time_audit"]["final_test_excluded_from_selection"] is True
    assert "eligible_for_paper_candidate" in payload["paper_candidate_assessment"]
    assert "order" not in payload["paper_candidate_assessment"]["note"]


@pytest.mark.asyncio
async def test_research_registry_and_same_ticker_ab_comparison_are_read_only(monkeypatch, client, make_user):
    monkeypatch.setattr(strategy, "_download_ohlc_frame", lambda ticker, period: _frame())
    headers = await make_user("research-registry@example.com")
    first = await client.post("/api/strategy/research", json={"ticker": "AAPL", "period": "5y", "fast_candidates": [5], "slow_candidates": [40]}, headers=headers)
    second = await client.post("/api/strategy/research", json={"ticker": "AAPL", "period": "5y", "fast_candidates": [10], "slow_candidates": [60]}, headers=headers)

    registry = await client.get("/api/strategy/research/registry", headers=headers)
    comparison = await client.get(f"/api/strategy/research/compare?control_id={first.json()['research_id']}&challenger_id={second.json()['research_id']}", headers=headers)

    assert registry.status_code == 200, registry.text
    assert len(registry.json()["items"]) == 2
    assert registry.json()["items"][0]["point_in_time_audit_passed"] is True
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()["control"]["ticker"] == "AAPL"
    assert "주문" in comparison.json()["disclaimer"]
