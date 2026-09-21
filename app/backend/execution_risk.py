"""모의 주문을 위한 사전 거래 위험 한도 계산.

가격·환율 조회와 DB 접근은 라우터의 책임으로 두고, 이 모듈은 원화 평가금액만
받아 주문 수량을 결정한다. 따라서 주문 실행 전에 같은 정책을 재현 가능하게
검증할 수 있다.
"""
from __future__ import annotations

from .risk_engine import evaluate_policy


def _composition(cash_krw: float, positions: list[dict]) -> dict:
    total = cash_krw + sum(float(item["value_krw"]) for item in positions)
    if total <= 0:
        return {"total_value_krw": round(total, 2), "max_position": None, "currency_exposure": {}, "sector_exposure": {}}
    items = ([{"ticker": "CASH", "currency": "KRW", "value_krw": cash_krw}]
             if cash_krw > 0 else []) + positions
    weighted = sorted(
        ({**item, "weight_pct": round(float(item["value_krw"]) / total * 100, 8)} for item in items),
        key=lambda item: item["value_krw"], reverse=True,
    )
    weighted_positions = [item for item in weighted if item["ticker"] != "CASH"]
    currencies: dict[str, float] = {}
    sectors: dict[str, float] = {}
    for item in positions:
        currencies[item["currency"]] = currencies.get(item["currency"], 0.0) + float(item["value_krw"])
        sector = item.get("sector") or "미분류"
        sectors[sector] = sectors.get(sector, 0.0) + float(item["value_krw"])
    if cash_krw > 0:
        currencies["KRW"] = currencies.get("KRW", 0.0) + cash_krw
    return {
        "total_value_krw": round(total, 2),
        "max_position": weighted_positions[0] if weighted_positions else None,
        "currency_exposure": {key: round(value / total * 100, 8) for key, value in currencies.items()},
        "sector_exposure": {key: round(value / total * 100, 8) for key, value in sectors.items()},
    }


def limit_buy_quantity(*, requested_quantity: float, unit_notional_krw: float, cash_krw: float,
                       positions: list[dict], ticker: str, currency: str, sector: str, policy: dict) -> dict:
    """현금·집중도 한도 안에서 승인 가능한 매수 수량을 반환한다.

    MDD/VaR는 주문 하나만으로 신뢰성 있게 재산출할 수 없으므로 여기서는
    사전 거래에 직접 결정 가능한 현금·종목·업종·외화 노출만 강제한다.
    """
    if unit_notional_krw <= 0:
        return {"action": "blocked", "approved_quantity": 0.0, "reasons": ["주문 평가금액이 0 이하입니다."]}

    max_by_cash = max(0.0, cash_krw / unit_notional_krw)
    upper = min(requested_quantity, max_by_cash)

    def projection(quantity: float) -> tuple[dict, list[dict]]:
        projected = [dict(item) for item in positions]
        match = next((item for item in projected if item["ticker"] == ticker), None)
        if match:
            match["value_krw"] = float(match["value_krw"]) + quantity * unit_notional_krw
        else:
            projected.append({"ticker": ticker, "currency": currency, "sector": sector, "value_krw": quantity * unit_notional_krw})
        composition = _composition(cash_krw - quantity * unit_notional_krw, projected)
        alerts = evaluate_policy(composition, {}, policy)
        return composition, alerts

    if upper <= 0:
        return {"action": "blocked", "approved_quantity": 0.0, "reasons": ["모의 계좌의 사용 가능 현금이 부족합니다."]}

    current_composition, current_alerts = projection(0.0)
    if current_alerts:
        return {"action": "blocked", "approved_quantity": 0.0, "reasons": [alert["message"] for alert in current_alerts],
                "composition": current_composition, "policy_alerts": current_alerts}

    full_composition, full_alerts = projection(upper)
    if not full_alerts:
        action = "approved" if upper == requested_quantity else "reduced"
        reasons = [] if action == "approved" else ["모의 계좌 사용 가능 현금 범위로 수량을 줄였습니다."]
        return {"action": action, "approved_quantity": round(upper, 8), "reasons": reasons,
                "composition": full_composition, "policy_alerts": full_alerts}

    low, high = 0.0, upper
    for _ in range(48):
        middle = (low + high) / 2
        _, alerts = projection(middle)
        if alerts:
            high = middle
        else:
            low = middle
    if low <= 1e-8:
        return {"action": "blocked", "approved_quantity": 0.0, "reasons": [alert["message"] for alert in full_alerts],
                "composition": full_composition, "policy_alerts": full_alerts}
    composition, alerts = projection(low)
    reasons = [alert["message"] for alert in full_alerts]
    if requested_quantity > max_by_cash:
        reasons.append("모의 계좌 사용 가능 현금 범위로 수량을 줄였습니다.")
    return {"action": "reduced", "approved_quantity": round(low, 8), "reasons": reasons,
            "composition": composition, "policy_alerts": alerts}
