"""EINT-027: 추천 종목 권장 매수 수량(변동성 기반 포지션 사이징, 참고 계산·주문 없음).

권장 수량 = (총자산 × 1회 손실 한도%) ÷ 주당 위험 단위. 종목당 최대 비중(리스크 정책)을 넘지 않게 줄이고,
현금으로 살 수 있는 수량도 함께 보여 준다.

주당 위험 단위
- Pullback v4: 검증된 손절 폭 1.5×ATR(엔진이 게시한 ATR 그대로).
- 평균회귀 2종(Bollinger-RSI·RSI-OBV): 가격 손절이 없는 전략이다(D-3). 사이징만을 위해 '2×ATR 하락'을 위험
  단위로 가정한다. 청산 규칙이 아니며 화면에 그렇게 적는다. ATR(14, Wilder)은 한투 일봉으로 엔진과 같은 식으로 계산한다.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

RISK_PCT_DEFAULT = 1.0
RISK_PCT_MIN, RISK_PCT_MAX = 0.25, 3.0
V4_STOP_ATR_MULTIPLE = 1.5
REVERSION_RISK_ATR_MULTIPLE = 2.0
ATR_PERIOD = 14


def clamp_risk_pct(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return RISK_PCT_DEFAULT
    if number != number:
        return RISK_PCT_DEFAULT
    return min(RISK_PCT_MAX, max(RISK_PCT_MIN, number))


def wilder_atr(bars: list[dict], period: int = ATR_PERIOD) -> float | None:
    """엔진(pullback_v4.compute_indicators)과 같은 ATR: TR의 ewm(alpha=1/period, adjust=False), 처음 period개 이후만 유효.

    bars는 오래된 날짜부터. 첫 날 TR은 고가-저가(전일 종가가 없으므로).
    """
    if len(bars) < period:
        return None
    alpha = 1.0 / period
    atr, previous_close = None, None
    for bar in bars:
        high, low = bar["high"], bar["low"]
        tr = high - low if previous_close is None else max(high - low, abs(high - previous_close), abs(low - previous_close))
        atr = tr if atr is None else (1 - alpha) * atr + alpha * tr
        previous_close = bar["close"]
    return atr if atr and atr > 0 else None


def daily_window(as_of: str) -> tuple[str, str]:
    """ATR(14) 계산에 충분한 일봉 구간(기준일까지 약 4개월)."""
    end = date.fromisoformat(as_of)
    return (end - timedelta(days=130)).strftime("%Y%m%d"), end.strftime("%Y%m%d")


def risk_unit(item: str, *, v4_atr: float | None, atr14: float | None) -> dict | None:
    """주당 위험 단위와 그 근거. 계산할 수 없으면 None."""
    if item == "pullback_v4":
        if not v4_atr or v4_atr <= 0:
            return None
        return {"per_share": V4_STOP_ATR_MULTIPLE * v4_atr, "atr": v4_atr, "basis": "v4_stop",
                "label": f"손절 폭 {V4_STOP_ATR_MULTIPLE:g}×ATR(검증된 규칙)"}
    if item in ("swing", "long_term"):
        if not atr14 or atr14 <= 0:
            return None
        return {"per_share": REVERSION_RISK_ATR_MULTIPLE * atr14, "atr": atr14, "basis": "reversion_atr",
                "label": f"가격 손절이 없는 전략이라 {REVERSION_RISK_ATR_MULTIPLE:g}×ATR 하락을 위험 단위로 가정(청산 규칙 아님)"}
    return None


def size_position(*, equity: float, cash: float | None, price: float, unit: float,
                  risk_pct: float, max_position_pct: float) -> dict:
    """권장 수량. 위험 한도와 종목당 최대 비중 중 작은 쪽, 그리고 현금으로 살 수 있는 수량."""
    risk_budget = equity * risk_pct / 100
    qty_by_risk = math.floor(risk_budget / unit) if unit > 0 else 0
    qty_by_weight = math.floor(equity * max_position_pct / 100 / price) if price > 0 else 0
    quantity = max(0, min(qty_by_risk, qty_by_weight))
    capped_by = "max_position" if qty_by_weight < qty_by_risk else None
    qty_by_cash = math.floor(cash / price) if cash is not None and price > 0 else None
    amount = quantity * price
    return {
        "quantity": quantity, "amount_krw": round(amount), "weight_pct": round(amount / equity * 100, 2) if equity else None,
        "risk_krw": round(quantity * unit), "risk_budget_krw": round(risk_budget), "capped_by": capped_by,
        "cash_quantity": qty_by_cash, "cash_short": qty_by_cash is not None and qty_by_cash < quantity,
    }
