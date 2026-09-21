"""포트폴리오 위험 한도와 변동성 목표를 계산하는 순수 함수."""
from __future__ import annotations


DEFAULT_RISK_POLICY = {
    "max_position_pct": 35.0,
    "max_sector_pct": 50.0,
    "max_foreign_currency_pct": 60.0,
    "max_var_95_pct": 3.0,
    "max_mdd_pct": 30.0,
    "max_daily_loss_pct": 3.0,
    "target_annual_volatility_pct": 15.0,
}


def evaluate_policy(composition: dict, risk: dict, policy: dict) -> list[dict]:
    alerts: list[dict] = []
    maximum = composition.get("max_position")
    if maximum and maximum.get("ticker") != "CASH" and maximum.get("weight_pct", 0) > policy["max_position_pct"]:
        alerts.append({"code": "max_position", "severity": "warning", "actual_pct": maximum["weight_pct"],
                       "limit_pct": policy["max_position_pct"], "message": f"{maximum['ticker']} 비중이 종목 한도를 초과했습니다."})
    for sector, weight in composition.get("sector_exposure", {}).items():
        if sector != "미분류" and weight > policy["max_sector_pct"]:
            alerts.append({"code": "max_sector", "severity": "warning", "sector": sector, "actual_pct": weight,
                           "limit_pct": policy["max_sector_pct"], "message": f"{sector} 업종 비중이 한도를 초과했습니다."})
    foreign = sum(weight for currency, weight in composition.get("currency_exposure", {}).items() if currency != "KRW")
    if foreign > policy["max_foreign_currency_pct"]:
        alerts.append({"code": "foreign_currency", "severity": "warning", "actual_pct": round(foreign, 2),
                       "limit_pct": policy["max_foreign_currency_pct"], "message": "외화 자산 비중이 한도를 초과했습니다."})
    for key, code, label in (("var_95_pct", "var_95", "일일 VaR"), ("mdd_pct", "mdd", "최대 낙폭")):
        value = risk.get(key)
        limit = policy["max_var_95_pct" if key == "var_95_pct" else "max_mdd_pct"]
        if value is not None and value > limit:
            alerts.append({"code": code, "severity": "warning", "actual_pct": value, "limit_pct": limit,
                           "message": f"{label}이 정책 한도를 초과했습니다."})
    daily_loss = risk.get("daily_loss_pct")
    if daily_loss is not None and daily_loss > policy["max_daily_loss_pct"]:
        alerts.append({"code": "daily_loss", "severity": "critical", "actual_pct": daily_loss,
                       "limit_pct": policy["max_daily_loss_pct"], "message": "일일 모의 계좌 손실이 정책 한도를 초과했습니다."})
    return alerts


def volatility_target(annual_volatility_pct: float | None, policy: dict) -> dict:
    if annual_volatility_pct is None or annual_volatility_pct <= 0:
        return {"available": False, "reason": "연환산 변동성이 없어 목표 비중을 계산할 수 없습니다."}
    scale = min(1.0, policy["target_annual_volatility_pct"] / annual_volatility_pct)
    return {"available": True, "target_annual_volatility_pct": policy["target_annual_volatility_pct"],
            "current_annual_volatility_pct": annual_volatility_pct, "risky_asset_scale": round(scale, 4),
            "cash_buffer_pct": round((1 - scale) * 100, 2)}
