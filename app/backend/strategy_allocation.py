"""저장된 백테스트 스냅샷을 전략 포트폴리오 리서치 배분안으로 변환한다."""
from __future__ import annotations
import math


def _cap_weights(raw: list[float], cap: float) -> list[float]:
    weights = raw[:]
    remaining = set(range(len(weights)))
    while remaining:
        overflow = [index for index in remaining if weights[index] > cap]
        if not overflow:
            break
        excess = sum(weights[index] - cap for index in overflow)
        for index in overflow:
            weights[index] = cap
            remaining.remove(index)
        if not remaining:
            break
        denominator = sum(weights[index] for index in remaining)
        if denominator <= 0:
            break
        for index in remaining:
            weights[index] += excess * weights[index] / denominator
    return weights


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _volatility(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean, right_mean = _mean(left), _mean(right)
    denominator = math.sqrt(sum((item - left_mean) ** 2 for item in left) * sum((item - right_mean) ** 2 for item in right))
    return sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right)) / denominator if denominator else None


def _return_map(strategy: dict) -> dict[str, float]:
    series = strategy.get("snapshot", {}).get("strategy_return_series", [])
    return {str(row["date"]): float(row["return"]) for row in series if isinstance(row, dict) and "date" in row and "return" in row}


def _strategy_id(strategy: dict) -> str:
    return str(strategy.get("_id", ""))


def build_strategy_allocation(strategies: list[dict], max_strategy_pct: float = 40.0) -> dict:
    """Sharpe/MDD 기반의 참고용 위험 예산을 만든다. 주문 또는 보유 장부는 바꾸지 않는다."""
    cap = max(1.0, min(100.0, float(max_strategy_pct))) / 100
    superseded = {str(strategy["parent_id"]) for strategy in strategies if strategy.get("parent_id")}
    eligible = []
    excluded = []
    for strategy in strategies:
        snapshot = strategy.get("snapshot", {})
        metrics = snapshot.get("metrics", {})
        sharpe, mdd = metrics.get("sharpe"), metrics.get("mdd")
        if _strategy_id(strategy) in superseded:
            excluded.append({"strategy_id": _strategy_id(strategy), "name": strategy.get("name", "-"), "reason": "더 최신의 전략 버전이 있어 배분 후보에서 제외했습니다."})
            continue
        if strategy.get("type") != "real_backtest" or sharpe is None or mdd is None:
            excluded.append({"strategy_id": str(strategy.get("_id", "")), "name": strategy.get("name", "-"), "reason": "실제 가격 백테스트의 Sharpe·MDD 스냅샷이 필요합니다."})
            continue
        drawdown_pct = abs(float(mdd)) * 100
        score = max(float(sharpe), 0.05) / max(drawdown_pct, 1.0)
        eligible.append({"strategy": strategy, "score": score, "drawdown_pct": drawdown_pct})
    if not eligible:
        return {"available": False, "reason": "배분안을 만들 수 있는 실제 가격 백테스트가 없습니다.", "items": [], "excluded": excluded}
    total_score = sum(item["score"] for item in eligible)
    # 전략 수가 적으면 40% 같은 상한만으로 100%를 배분할 수 없다. 이때 숨은 현금
    # 가정을 넣지 않고 최소 실행 가능 상한을 결과에 명시한다.
    effective_cap = max(cap, 1 / len(eligible))
    weights = _cap_weights([item["score"] / total_score for item in eligible], effective_cap)
    items = []
    for item, weight in zip(eligible, weights):
        strategy, snapshot = item["strategy"], item["strategy"].get("snapshot", {})
        validation = snapshot.get("benchmark_validation", {})
        items.append({"strategy_id": str(strategy["_id"]), "name": strategy.get("name", "-"), "ticker": strategy.get("ticker", ""),
                      "risk_budget_pct": round(weight * 100, 2), "score": round(item["score"], 6),
                      "sharpe": snapshot.get("metrics", {}).get("sharpe"), "mdd_pct": round(item["drawdown_pct"], 2),
                      "benchmark_beta": validation.get("beta"), "benchmark_ticker": strategy.get("params", {}).get("benchmark_ticker") or strategy.get("ticker", "")})
    hhi = sum((item["risk_budget_pct"] / 100) ** 2 for item in items)
    return_maps = {_id: _return_map(item["strategy"]) for _id, item in ((str(row["strategy"]["_id"]), row) for row in eligible)}
    usable_ids = [key for key, values in return_maps.items() if len(values) >= 20]
    correlations = []
    for index, left_id in enumerate(usable_ids):
        for right_id in usable_ids[index + 1:]:
            common = sorted(set(return_maps[left_id]) & set(return_maps[right_id]))
            value = _correlation([return_maps[left_id][day] for day in common], [return_maps[right_id][day] for day in common])
            if value is not None:
                correlations.append({"left_strategy_id": left_id, "right_strategy_id": right_id, "observations": len(common), "correlation": round(value, 4)})
    risk_parity_items = []
    volatilities = {key: _volatility(list(return_maps[key].values())) for key in usable_ids}
    positive = [key for key in usable_ids if volatilities[key] and volatilities[key] > 0]
    if len(positive) >= 2:
        raw = [(1 / volatilities[key]) for key in positive]
        raw = [item / sum(raw) for item in raw]
        parity_cap = max(cap, 1 / len(positive))
        for key, weight in zip(positive, _cap_weights(raw, parity_cap)):
            source = next(item for item in items if item["strategy_id"] == key)
            risk_parity_items.append({"strategy_id": key, "name": source["name"], "risk_budget_pct": round(weight * 100, 2), "annual_volatility_pct": round(volatilities[key] * 100, 2)})
    return {"available": True, "method": "Sharpe(양수 하한 적용) / MDD 기반 점수 후 전략당 상한 배분입니다. 과거 성과 기반 참고안이며 주문 신호가 아닙니다.",
            "max_strategy_pct": round(cap * 100, 2), "effective_max_strategy_pct": round(effective_cap * 100, 2), "items": items, "excluded": excluded,
            "concentration_hhi": round(hhi, 4), "strategy_return_correlations": correlations,
            "risk_parity_items": risk_parity_items,
            "strategy_return_correlation": "공통 거래일 20일 이상인 저장 전략만 계산합니다." if correlations else "원시 전략 일간 수익률이 충분하지 않아 산출하지 않았습니다."}


def compare_strategy_versions(current: dict, previous: dict, strategies: list[dict], max_strategy_pct: float = 40.0) -> dict:
    """부모·자식 전략의 과거 백테스트 지표와 배분 영향만 비교한다."""
    if current.get("type") != "real_backtest" or previous.get("type") != "real_backtest":
        return {"available": False, "reason": "실제 가격 백테스트 전략 버전만 비교할 수 있습니다."}
    current_metrics = current.get("snapshot", {}).get("metrics", {})
    previous_metrics = previous.get("snapshot", {}).get("metrics", {})
    required = ("total_return", "cagr", "sharpe", "mdd")
    if any(current_metrics.get(key) is None or previous_metrics.get(key) is None for key in required):
        return {"available": False, "reason": "두 버전 모두 백테스트 성과 스냅샷이 필요합니다."}
    metric_changes = {key: round(float(current_metrics[key]) - float(previous_metrics[key]), 4) for key in required}
    current_map, previous_map = _return_map(current), _return_map(previous)
    common = sorted(set(current_map) & set(previous_map))
    correlation = _correlation([current_map[day] for day in common], [previous_map[day] for day in common]) if len(common) >= 20 else None
    current_allocation = build_strategy_allocation(strategies, max_strategy_pct)
    previous_scenario = [strategy for strategy in strategies if _strategy_id(strategy) != _strategy_id(current)]
    previous_allocation = build_strategy_allocation(previous_scenario, max_strategy_pct)
    def budget(allocation, strategy_id):
        return next((item["risk_budget_pct"] for item in allocation.get("items", []) if item["strategy_id"] == strategy_id), None)
    current_budget = budget(current_allocation, _strategy_id(current))
    previous_budget = budget(previous_allocation, _strategy_id(previous))
    improved = metric_changes["sharpe"] >= 0 and metric_changes["mdd"] >= 0 and metric_changes["cagr"] >= 0
    return {"available": True, "current_strategy_id": _strategy_id(current), "previous_strategy_id": _strategy_id(previous),
            "metric_changes": metric_changes, "return_correlation": round(correlation, 4) if correlation is not None else None,
            "return_correlation_observations": len(common),
            "allocation_impact": {"current_risk_budget_pct": current_budget, "previous_risk_budget_pct": previous_budget,
                                  "delta_risk_budget_pct": round(current_budget - previous_budget, 2) if current_budget is not None and previous_budget is not None else None},
            "promotion_assessment": {"status": "candidate" if improved else "review", "automatic_promotion": False,
                                     "reason": "CAGR·Sharpe 개선과 MDD 악화 없음" if improved else "일부 성과 지표가 악화되었거나 동일합니다. 별도 검토가 필요합니다."}}


def build_rebalance_plan(allocation: dict, dashboard: dict, tolerance_pct: float = 2.0) -> dict:
    """현재 모의 계좌 전략별 평가액과 목표 위험 예산의 차이만 계산한다."""
    if not allocation.get("available"):
        return {"available": False, "reason": allocation.get("reason", "목표 전략 배분안이 없습니다."), "items": []}
    if not dashboard.get("available") or dashboard.get("equity_krw") is None:
        return {"available": False, "reason": "현재 모의 계좌 평가가 불가능해 리밸런싱을 계산하지 않았습니다.", "items": []}
    equity = float(dashboard["equity_krw"])
    if equity <= 0:
        return {"available": False, "reason": "모의 계좌 자산이 0 이하입니다.", "items": []}
    tolerance = max(0.0, min(100.0, float(tolerance_pct)))
    actual = {str(row.get("strategy_id", "")): float(row.get("market_value_krw") or 0) for row in dashboard.get("strategy_summary", [])}
    planned_ids = set()
    items = []
    for target in allocation["items"]:
        strategy_id = target["strategy_id"]
        planned_ids.add(strategy_id)
        target_value = equity * float(target["risk_budget_pct"]) / 100
        current_value = actual.get(strategy_id, 0.0)
        delta = target_value - current_value
        delta_pct = delta / equity * 100
        action = "within_tolerance" if abs(delta_pct) <= tolerance else ("increase_review" if delta > 0 else "decrease_review")
        items.append({"strategy_id": strategy_id, "name": target["name"], "target_risk_budget_pct": target["risk_budget_pct"],
                      "current_weight_pct": round(current_value / equity * 100, 2), "target_value_krw": round(target_value, 2),
                      "current_value_krw": round(current_value, 2), "delta_value_krw": round(delta, 2), "delta_weight_pct": round(delta_pct, 2), "action": action})
    for strategy_id, current_value in actual.items():
        if strategy_id not in planned_ids and current_value > 0:
            items.append({"strategy_id": strategy_id, "name": "목표 배분에서 제외된 전략", "target_risk_budget_pct": 0,
                          "current_weight_pct": round(current_value / equity * 100, 2), "target_value_krw": 0,
                          "current_value_krw": round(current_value, 2), "delta_value_krw": round(-current_value, 2),
                          "delta_weight_pct": round(-current_value / equity * 100, 2), "action": "decrease_review"})
    return {"available": True, "equity_krw": round(equity, 2), "tolerance_pct": tolerance,
            "method": "현재 모의 계좌 평가액과 목표 위험 예산의 차이입니다. 검토용 계획이며 주문·모의 체결을 만들지 않습니다.", "items": items}
