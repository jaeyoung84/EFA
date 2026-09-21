from bson import ObjectId

from app.backend.strategy_allocation import build_rebalance_plan, build_strategy_allocation, compare_strategy_versions


def _strategy(name, sharpe, mdd):
    return {"_id": ObjectId(), "name": name, "type": "real_backtest", "ticker": "TEST", "params": {"benchmark_ticker": "^KS11"},
            "snapshot": {"metrics": {"sharpe": sharpe, "mdd": mdd, "total_return": sharpe / 10, "cagr": sharpe / 20}, "benchmark_validation": {"beta": 0.8},
                         "strategy_return_series": [{"date": f"2026-01-{day:02d}", "return": (day % 4 - 1.5) / 100} for day in range(1, 25)]}}


def test_strategy_allocation_caps_risk_budget_and_excludes_incomplete_items():
    result = build_strategy_allocation([_strategy("A", 2.0, -0.1), _strategy("B", 0.2, -0.3), _strategy("C", 0.5, -0.2), {"_id": ObjectId(), "name": "가상", "type": "virtual_backtest", "snapshot": {}}], 40)
    assert result["available"] is True
    assert round(sum(item["risk_budget_pct"] for item in result["items"]), 2) == 100.0
    assert max(item["risk_budget_pct"] for item in result["items"]) <= 40
    assert len(result["excluded"]) == 1
    assert len(result["strategy_return_correlations"]) == 3
    assert len(result["risk_parity_items"]) == 3


def test_rebalance_plan_is_read_only_difference_with_tolerance():
    allocation = build_strategy_allocation([_strategy("A", 2.0, -0.1), _strategy("B", 1.0, -0.2), _strategy("C", 0.5, -0.2)], 40)
    dashboard = {"available": True, "equity_krw": 1_000_000, "strategy_summary": [{"strategy_id": allocation["items"][0]["strategy_id"], "market_value_krw": 50_000}]}
    plan = build_rebalance_plan(allocation, dashboard, 2)
    assert plan["available"] is True
    assert all("review" in item["action"] or item["action"] == "within_tolerance" for item in plan["items"])
    assert plan["method"].startswith("현재 모의 계좌")


def test_version_comparison_excludes_superseded_parent_and_never_auto_promotes():
    previous = _strategy("이전", 1.0, -0.20)
    current = _strategy("새 버전", 1.2, -0.15)
    current["parent_id"] = previous["_id"]
    result = compare_strategy_versions(current, previous, [previous, current], 40)
    assert result["available"] is True
    assert result["promotion_assessment"]["status"] == "candidate"
    assert result["promotion_assessment"]["automatic_promotion"] is False
    allocation = build_strategy_allocation([previous, current], 40)
    assert allocation["items"][0]["strategy_id"] == str(current["_id"])
