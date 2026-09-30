"""레버리지 ETF(UPRO/SOXL) DCA 전략의 매수 중지·매도 트리거·예비비 단계 투입 판정을 위한 순수 함수.

가격·거시지표 조회(yfinance, FRED 등)는 라우터·스케줄러가 담당하고, 이 모듈은 계산된
지표 값만 받아 결정을 재현 가능하게 판정한다. 이 모듈은 주문을 만들거나 전송하지
않으며, 실거래 권한도 생성하지 않는다 — 실제 체결은 기존 모의 주문(paper_fills)
경로에서만 이루어진다.
"""
from __future__ import annotations

from datetime import date


DEFAULT_LEVERAGE_DCA_POLICY = {
    "daily_buy_krw": 5_000_000.0,
    "weights": {"UPRO": 0.6, "SOXL": 0.4},
    "max_position_krw": 100_000_000.0,
    "sell_net_profit_target_krw": 5_000_000.0,
    "capital_gains_tax_rate": 0.22,
    "annual_capital_gains_deduction_krw": 2_500_000.0,
    "sell_fee_rate": 0.002,
    "spx_drawdown_stop_pct": -8.0,
    "vix_stop": 35.0,
    "vix_resume": 30.0,
    "soxl_divergence_stop_pct": -20.0,
    "soxl_divergence_stop_pct_first_entry": -50.0,
    "soxl_own_drawdown_stop_pct": -20.0,
    "resume_ma_days": 50,
    "resume_consecutive_days": 3,
    "reserve_cash_krw": 100_000_000.0,
    "reserve_spx_drawdown_stages_pct": [-20.0, -30.0, -40.0],
    "reserve_vix_trigger": 50.0,
    "reserve_max_fast_drawdown_days": 30,
}


def compute_drawdown_from_high_pct(closes: list[float]) -> float | None:
    """종가 리스트에서 구간 내 최고가 대비 마지막 종가의 낙폭(%)을 계산한다. 음수가 하락."""
    if not closes:
        return None
    high = max(closes)
    if high <= 0:
        return None
    return round((closes[-1] / high - 1) * 100, 4)


def compute_ma_above_streak(closes: list[float], window: int) -> int:
    """마지막 종가부터 거슬러 이동평균 위에 머문 연속 거래일 수를 센다."""
    if window <= 0 or len(closes) < window:
        return 0
    ma_values = [sum(closes[index - window + 1:index + 1]) / window for index in range(window - 1, len(closes))]
    aligned_closes = closes[window - 1:]
    streak = 0
    for close, ma in zip(reversed(aligned_closes), reversed(ma_values)):
        if close > ma:
            streak += 1
        else:
            break
    return streak


def compute_series_trend(values: list[float]) -> str:
    """구간 첫 값 대비 마지막 값의 방향을 'rising'|'falling'|'flat'으로 판정한다."""
    if len(values) < 2:
        return "flat"
    change = values[-1] - values[0]
    if change > 1e-9:
        return "rising"
    if change < -1e-9:
        return "falling"
    return "flat"


def compute_days_since_high(dates: list[str], closes: list[float]) -> int | None:
    """구간 내 최고 종가 시점부터 마지막 종가 시점까지 경과한 달력일 수를 계산한다."""
    if not closes or not dates or len(dates) != len(closes):
        return None
    peak_index = max(range(len(closes)), key=lambda index: closes[index])
    try:
        peak_date = date.fromisoformat(dates[peak_index][:10])
        latest_date = date.fromisoformat(dates[-1][:10])
    except ValueError:
        return None
    return (latest_date - peak_date).days


def evaluate_buy_stop_conditions(*, spx_drawdown_pct: float, vix: float, soxl_drawdown_pct: float,
                                  upro_drawdown_pct: float, policy: dict, has_soxl_position: bool = False) -> dict:
    """주전략(DCA) 매수 가능 여부를 판정한다. 낙폭 값은 52주 고점 대비 퍼센트(-8.0=-8%) 기준이다.

    SOXL 괴리 중지는 첫 진입 전/후로 기준이 다르다. 아직 SOXL을 한 번도 매수하지 않았다면
    괴리가 -50%p 이내(``soxl_divergence_stop_pct_first_entry``)면 진입 기회를 놓치지 않도록
    매수를 허용한다. 이미 SOXL을 보유 중이라면 괴리가 -20%p(``soxl_divergence_stop_pct``)를
    넘어 계속 벌어지고, 동시에 SOXL 자체 낙폭도 -20%(``soxl_own_drawdown_stop_pct``)를 넘으면
    추가 매수(물타기)를 중단한다.
    """
    reasons: list[str] = []
    stop_all = False
    if spx_drawdown_pct <= policy["spx_drawdown_stop_pct"]:
        stop_all = True
        reasons.append(f"S&P 500이 52주 고점 대비 {policy['spx_drawdown_stop_pct']}% 이하로 하락했습니다.")
    if vix >= policy["vix_stop"]:
        stop_all = True
        reasons.append(f"VIX가 {policy['vix_stop']} 이상입니다.")
    divergence_pct = soxl_drawdown_pct - upro_drawdown_pct
    if has_soxl_position:
        divergence_widened = divergence_pct <= policy["soxl_divergence_stop_pct"]
        soxl_own_loss_exceeded = soxl_drawdown_pct <= policy["soxl_own_drawdown_stop_pct"]
        stop_soxl_only = divergence_widened and soxl_own_loss_exceeded
        if stop_soxl_only and not stop_all:
            reasons.append(f"SOXL 낙폭이 UPRO보다 {abs(policy['soxl_divergence_stop_pct'])}%p 이상 벌어지고, "
                            f"SOXL 자체 낙폭도 {policy['soxl_own_drawdown_stop_pct']}%를 넘어 SOXL 매수만 중단합니다.")
    else:
        stop_soxl_only = divergence_pct <= policy["soxl_divergence_stop_pct_first_entry"]
        if stop_soxl_only and not stop_all:
            reasons.append(f"첫 진입 전이지만 SOXL 낙폭이 UPRO보다 {abs(policy['soxl_divergence_stop_pct_first_entry'])}%p 이상 "
                            f"커 SOXL 매수만 중단합니다.")
    return {
        "stop_all": stop_all,
        "stop_soxl_only": stop_soxl_only and not stop_all,
        "divergence_pct": round(divergence_pct, 4),
        "has_soxl_position": has_soxl_position,
        "reasons": reasons,
    }


def evaluate_resume_condition(*, spx_above_ma_consecutive_days: int, vix: float, policy: dict) -> bool:
    """주전략 재개 조건: 50일 이동평균 위 N일 연속 + VIX가 재개 기준 미만."""
    return spx_above_ma_consecutive_days >= policy["resume_consecutive_days"] and vix < policy["vix_resume"]


def resolve_effective_stop_all(*, previously_stopped: bool, raw_stop_all: bool, resumed: bool) -> bool:
    """S&P/VIX발 매수 전면중지에 히스테리시스를 적용한다.

    한 번 중지되면 당일 조건이 풀려도 재개 조건(``evaluate_resume_condition``)을
    충족하기 전까지는 중지 상태를 유지한다. 처음 중지되는 날은 원 신호를 그대로 쓴다.
    """
    if not previously_stopped:
        return raw_stop_all
    return not resumed


def compute_daily_buy_allocation(*, stop_state: dict, current_position_krw: float, policy: dict) -> dict:
    """오늘 매수할 종목별 금액(KRW)을 계산한다. 실제 체결가·수량 변환은 호출부(주문 제안)가 한다."""
    if stop_state["stop_all"]:
        return {"UPRO": 0.0, "SOXL": 0.0, "total_krw": 0.0, "reasons": stop_state["reasons"]}
    remaining_krw = max(0.0, policy["max_position_krw"] - current_position_krw)
    if remaining_krw <= 0:
        return {"UPRO": 0.0, "SOXL": 0.0, "total_krw": 0.0, "reasons": ["최대 누적 매수 한도에 도달했습니다."]}
    weights = policy["weights"]
    if stop_state["stop_soxl_only"]:
        upro_amount = min(policy["daily_buy_krw"], remaining_krw)
        soxl_amount = 0.0
    else:
        upro_amount = min(policy["daily_buy_krw"] * weights["UPRO"], remaining_krw * weights["UPRO"])
        soxl_amount = min(policy["daily_buy_krw"] * weights["SOXL"], remaining_krw * weights["SOXL"])
    return {"UPRO": round(upro_amount, 2), "SOXL": round(soxl_amount, 2),
            "total_krw": round(upro_amount + soxl_amount, 2), "reasons": stop_state["reasons"]}


def evaluate_sell_trigger(*, position_value_krw: float, cost_basis_krw: float, policy: dict) -> dict:
    """세후·매도수수료 차감 후 실수령 기준 매도 트리거 여부를 계산한다."""
    gross_profit_krw = position_value_krw - cost_basis_krw
    if gross_profit_krw <= 0:
        return {"trigger": False, "gross_profit_krw": round(gross_profit_krw, 2), "fee_krw": 0.0, "net_profit_krw": 0.0,
                "target_net_profit_krw": policy["sell_net_profit_target_krw"]}
    fee_krw = position_value_krw * policy["sell_fee_rate"]
    net_profit_krw = gross_profit_krw * (1 - policy["capital_gains_tax_rate"]) - fee_krw
    return {
        "trigger": net_profit_krw >= policy["sell_net_profit_target_krw"],
        "gross_profit_krw": round(gross_profit_krw, 2),
        "fee_krw": round(fee_krw, 2),
        "net_profit_krw": round(net_profit_krw, 2),
        "target_net_profit_krw": policy["sell_net_profit_target_krw"],
    }


def estimate_after_tax_profit(*, market_value_krw: float, cost_basis_krw: float, realized_gain_ytd_krw: float,
                              policy: dict) -> dict:
    """지금 전량 매도한다고 가정한 세후 실순수익(표시용)을 계산한다.

    해외주식 양도소득세 실제 규칙: 연간 양도차익 합계에서 기본공제(250만원)를 뺀 금액의 22%.
    올해 이미 실현한 차익(``realized_gain_ytd_krw``)이 공제를 먼저 쓰고, 이번 매도로 늘어나는 과세표준에
    대해서만 세금을 더한다. 매도수수료(``sell_fee_rate``)는 차익에서 먼저 뺀다. 자동 매도 판단
    (``evaluate_sell_trigger``)은 공제 없이 22%로 보수 계산하므로 이 값과 다를 수 있다.
    """
    gross_profit_krw = market_value_krw - cost_basis_krw
    fee_krw = market_value_krw * policy["sell_fee_rate"] if market_value_krw > 0 else 0.0
    gain_after_fee_krw = gross_profit_krw - fee_krw
    deduction = policy["annual_capital_gains_deduction_krw"]
    rate = policy["capital_gains_tax_rate"]
    taxable_before = max(0.0, realized_gain_ytd_krw - deduction)
    taxable_after = max(0.0, realized_gain_ytd_krw + gain_after_fee_krw - deduction)
    tax_krw = max(0.0, taxable_after - taxable_before) * rate
    return {
        "gross_profit_krw": round(gross_profit_krw, 2), "sell_fee_krw": round(fee_krw, 2),
        "realized_gain_ytd_krw": round(realized_gain_ytd_krw, 2),
        "deduction_remaining_krw": round(max(0.0, deduction - max(0.0, realized_gain_ytd_krw)), 2),
        "estimated_tax_krw": round(tax_krw, 2), "net_profit_after_tax_krw": round(gain_after_fee_krw - tax_krw, 2),
        "return_pct": round(gross_profit_krw / cost_basis_krw * 100, 4) if cost_basis_krw > 0 else None,
        "net_return_pct": round((gain_after_fee_krw - tax_krw) / cost_basis_krw * 100, 4) if cost_basis_krw > 0 else None,
        "tax_rule": f"(연간 양도차익 - 기본공제 {deduction:,.0f}원) x {rate:.0%}, 매도수수료 {policy['sell_fee_rate']:.1%}",
    }


def evaluate_reserve_deployment_stage(*, spx_drawdown_pct: float, fed_funds_trend: str, vix: float,
                                       ten_year_yield_trend: str, soxl_drawdown_pct: float, upro_drawdown_pct: float,
                                       nfp_3m_avg: float | None = None, unemployment_rate: float | None = None,
                                       drawdown_days: int | None = None, policy: dict) -> dict:
    """예비비 단계 투입 판정에 쓰는 거시 지표 점검.

    모든 지표가 충족돼야 투입 후보가 되며, S&P 낙폭 단계(-20/-30/-40%)에 따라 1~3단계로
    나뉜다. 이 판정은 참고용 신호일 뿐이며 예비비 자금 이동이나 주문을 만들지 않는다.
    ``fed_funds_trend``는 "falling"|"flat"|"rising", ``ten_year_yield_trend``는
    "falling"|"rising" 문자열을 받는다.
    """
    divergence_pct = soxl_drawdown_pct - upro_drawdown_pct
    checks = {
        "fed_not_hiking": fed_funds_trend in {"falling", "flat"},
        "vix_extreme": vix >= policy["reserve_vix_trigger"],
        "ten_year_falling": ten_year_yield_trend == "falling",
        "no_sector_specific_collapse": divergence_pct > policy["soxl_divergence_stop_pct"],
        "labor_weak": bool((nfp_3m_avg is not None and nfp_3m_avg < 100_000) or (unemployment_rate is not None and unemployment_rate > 5.0)),
        "fast_drawdown": drawdown_days is None or drawdown_days <= policy["reserve_max_fast_drawdown_days"],
    }
    eligible = all(checks.values())
    stage = 0
    if eligible:
        for index, threshold in enumerate(sorted(policy["reserve_spx_drawdown_stages_pct"], reverse=True), start=1):
            if spx_drawdown_pct <= threshold:
                stage = index
    return {"eligible": eligible, "checks": checks, "divergence_pct": round(divergence_pct, 4), "stage": stage,
            "creates_live_authority": False, "moves_reserve_funds": False}
