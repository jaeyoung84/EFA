from app.backend.leverage_dca_strategy import (
    DEFAULT_LEVERAGE_DCA_POLICY,
    compute_daily_buy_allocation,
    compute_days_since_high,
    compute_drawdown_from_high_pct,
    compute_ma_above_streak,
    compute_series_trend,
    evaluate_buy_stop_conditions,
    evaluate_reserve_deployment_stage,
    evaluate_resume_condition,
    evaluate_sell_trigger,
    resolve_effective_stop_all,
)


def test_series_trend_rising_falling_flat():
    assert compute_series_trend([4.0, 4.25, 4.5]) == "rising"
    assert compute_series_trend([4.5, 4.25, 4.0]) == "falling"
    assert compute_series_trend([4.5, 4.5, 4.5]) == "flat"
    assert compute_series_trend([4.5]) == "flat"


def test_days_since_high_counts_calendar_days_from_peak():
    dates = ["2026-01-01", "2026-01-10", "2026-01-20"]
    closes = [100.0, 130.0, 90.0]
    assert compute_days_since_high(dates, closes) == 10


def test_days_since_high_zero_when_latest_is_the_peak():
    dates = ["2026-01-01", "2026-01-10"]
    closes = [100.0, 130.0]
    assert compute_days_since_high(dates, closes) == 0


def test_days_since_high_none_on_mismatched_lengths():
    assert compute_days_since_high(["2026-01-01"], [1.0, 2.0]) is None


def test_resolve_effective_stop_all_enters_stop_on_raw_trigger():
    assert resolve_effective_stop_all(previously_stopped=False, raw_stop_all=True, resumed=False) is True
    assert resolve_effective_stop_all(previously_stopped=False, raw_stop_all=False, resumed=False) is False


def test_resolve_effective_stop_all_stays_stopped_until_resumed():
    assert resolve_effective_stop_all(previously_stopped=True, raw_stop_all=False, resumed=False) is True
    assert resolve_effective_stop_all(previously_stopped=True, raw_stop_all=False, resumed=True) is False
    assert resolve_effective_stop_all(previously_stopped=True, raw_stop_all=True, resumed=True) is False


def test_drawdown_from_high_pct_computes_negative_percentage():
    assert compute_drawdown_from_high_pct([100.0, 120.0, 90.0]) == -25.0


def test_drawdown_from_high_pct_zero_when_at_new_high():
    assert compute_drawdown_from_high_pct([80.0, 90.0, 100.0]) == 0.0


def test_drawdown_from_high_pct_empty_input_returns_none():
    assert compute_drawdown_from_high_pct([]) is None


def test_ma_above_streak_counts_trailing_days_above_moving_average():
    closes = [10.0] * 5 + [20.0, 21.0, 22.0]
    assert compute_ma_above_streak(closes, window=5) == 3


def test_ma_above_streak_resets_on_break():
    closes = [10.0] * 5 + [20.0, 9.0, 22.0]
    assert compute_ma_above_streak(closes, window=5) == 1


def test_ma_above_streak_zero_when_insufficient_history():
    assert compute_ma_above_streak([1.0, 2.0], window=5) == 0


def test_stop_all_when_spx_drawdown_breaches_threshold():
    state = evaluate_buy_stop_conditions(spx_drawdown_pct=-9.0, vix=15.0, soxl_drawdown_pct=-10.0,
                                          upro_drawdown_pct=-9.0, policy=DEFAULT_LEVERAGE_DCA_POLICY)
    assert state["stop_all"] is True
    assert state["stop_soxl_only"] is False
    assert state["reasons"]


def test_stop_all_when_vix_breaches_threshold():
    state = evaluate_buy_stop_conditions(spx_drawdown_pct=-1.0, vix=40.0, soxl_drawdown_pct=-5.0,
                                          upro_drawdown_pct=-4.0, policy=DEFAULT_LEVERAGE_DCA_POLICY)
    assert state["stop_all"] is True


def test_soxl_only_stop_when_divergence_breaches_without_overall_stop():
    state = evaluate_buy_stop_conditions(spx_drawdown_pct=-2.0, vix=15.0, soxl_drawdown_pct=-60.0,
                                          upro_drawdown_pct=-7.0, policy=DEFAULT_LEVERAGE_DCA_POLICY)
    assert state["stop_all"] is False
    assert state["stop_soxl_only"] is True
    assert state["divergence_pct"] == -53.0


def test_no_stop_under_normal_conditions():
    state = evaluate_buy_stop_conditions(spx_drawdown_pct=-2.0, vix=15.0, soxl_drawdown_pct=-5.0,
                                          upro_drawdown_pct=-2.0, policy=DEFAULT_LEVERAGE_DCA_POLICY)
    assert state["stop_all"] is False
    assert state["stop_soxl_only"] is False
    assert state["reasons"] == []


def test_soxl_first_entry_allows_buy_within_relaxed_threshold():
    """SOXL을 한 번도 매수하지 않았다면 괴리가 -20%p를 넘어도 -50%p 이내면 진입을 허용한다."""
    state = evaluate_buy_stop_conditions(spx_drawdown_pct=-2.0, vix=15.0, soxl_drawdown_pct=-35.0,
                                          upro_drawdown_pct=-5.0, policy=DEFAULT_LEVERAGE_DCA_POLICY,
                                          has_soxl_position=False)
    assert state["divergence_pct"] == -30.0
    assert state["stop_all"] is False
    assert state["stop_soxl_only"] is False


def test_soxl_first_entry_still_stops_beyond_relaxed_threshold():
    state = evaluate_buy_stop_conditions(spx_drawdown_pct=-2.0, vix=15.0, soxl_drawdown_pct=-60.0,
                                          upro_drawdown_pct=-5.0, policy=DEFAULT_LEVERAGE_DCA_POLICY,
                                          has_soxl_position=False)
    assert state["divergence_pct"] == -55.0
    assert state["stop_soxl_only"] is True


def test_soxl_after_entry_stops_on_my_position_loss_not_on_drawdown():
    """2026-10-08 사용자 결정: SOXL 보유 중에는 내 SOXL 평가손익률이 -20% 이하일 때만 추가 매수를 멈춘다."""
    common = dict(spx_drawdown_pct=-2.0, vix=15.0, soxl_drawdown_pct=-47.0, upro_drawdown_pct=-1.5,
                  policy=DEFAULT_LEVERAGE_DCA_POLICY, has_soxl_position=True)
    # 고점 대비 -47%·괴리 -45.5%p여도 내 평가손익률이 -5%면 계속 산다(예전 규칙이면 중단).
    state = evaluate_buy_stop_conditions(**common, soxl_position_return_pct=-5.0)
    assert state["stop_soxl_only"] is False and state["reasons"] == []
    # 내 평가손익률이 -20% 이하이면 SOXL만 중단한다.
    state = evaluate_buy_stop_conditions(**common, soxl_position_return_pct=-20.0)
    assert state["stop_soxl_only"] is True and state["stop_all"] is False
    assert "평가손익률이 -20.00%" in state["reasons"][0]
    assert state["soxl_position_return_pct"] == -20.0
    # 평가손익률을 계산하지 못하면 중단하지 않는다(시세 오류는 실행 단계에서 따로 막는다).
    assert evaluate_buy_stop_conditions(**common, soxl_position_return_pct=None)["stop_soxl_only"] is False
    # 보유 중에도 괴리 -50%p 이하이면 첫 매수 전과 똑같이 중단한다(평가손익률이 괜찮아도).
    collapsed = dict(common, soxl_drawdown_pct=-55.0, upro_drawdown_pct=-2.0)
    state = evaluate_buy_stop_conditions(**collapsed, soxl_position_return_pct=3.0)
    assert state["stop_soxl_only"] is True and "50.0%p" in state["reasons"][0]
    both = evaluate_buy_stop_conditions(**collapsed, soxl_position_return_pct=-25.0)
    assert both["stop_soxl_only"] is True and len(both["reasons"]) == 2


def test_resume_condition_requires_both_ma_streak_and_low_vix():
    policy = DEFAULT_LEVERAGE_DCA_POLICY
    assert evaluate_resume_condition(spx_above_ma_consecutive_days=3, vix=25.0, policy=policy) is True
    assert evaluate_resume_condition(spx_above_ma_consecutive_days=2, vix=25.0, policy=policy) is False
    assert evaluate_resume_condition(spx_above_ma_consecutive_days=3, vix=31.0, policy=policy) is False


def test_daily_buy_allocation_splits_by_weight_when_not_stopped():
    stop_state = {"stop_all": False, "stop_soxl_only": False, "reasons": []}
    allocation = compute_daily_buy_allocation(stop_state=stop_state, current_position_krw=0.0,
                                               policy=DEFAULT_LEVERAGE_DCA_POLICY)
    assert allocation["UPRO"] == 3_000_000.0
    assert allocation["SOXL"] == 2_000_000.0
    assert allocation["total_krw"] == 5_000_000.0


def test_daily_buy_allocation_zero_when_stopped():
    stop_state = {"stop_all": True, "stop_soxl_only": False, "reasons": ["S&P 500이 -8% 이하"]}
    allocation = compute_daily_buy_allocation(stop_state=stop_state, current_position_krw=0.0,
                                               policy=DEFAULT_LEVERAGE_DCA_POLICY)
    assert allocation["UPRO"] == 0.0
    assert allocation["SOXL"] == 0.0
    assert allocation["total_krw"] == 0.0


def test_daily_buy_allocation_soxl_only_stop_routes_full_amount_to_upro():
    stop_state = {"stop_all": False, "stop_soxl_only": True, "reasons": ["SOXL 괴리"]}
    allocation = compute_daily_buy_allocation(stop_state=stop_state, current_position_krw=0.0,
                                               policy=DEFAULT_LEVERAGE_DCA_POLICY)
    assert allocation["UPRO"] == 5_000_000.0
    assert allocation["SOXL"] == 0.0


def test_daily_buy_allocation_respects_max_position_cap():
    stop_state = {"stop_all": False, "stop_soxl_only": False, "reasons": []}
    allocation = compute_daily_buy_allocation(
        stop_state=stop_state, current_position_krw=99_000_000.0, policy=DEFAULT_LEVERAGE_DCA_POLICY,
    )
    assert allocation["total_krw"] == 1_000_000.0
    assert allocation["UPRO"] == 600_000.0
    assert allocation["SOXL"] == 400_000.0


def test_daily_buy_allocation_blocked_at_position_cap():
    stop_state = {"stop_all": False, "stop_soxl_only": False, "reasons": []}
    allocation = compute_daily_buy_allocation(
        stop_state=stop_state, current_position_krw=100_000_000.0, policy=DEFAULT_LEVERAGE_DCA_POLICY,
    )
    assert allocation["total_krw"] == 0.0
    assert "한도" in allocation["reasons"][0]


def test_sell_trigger_fires_when_net_profit_meets_target():
    result = evaluate_sell_trigger(position_value_krw=55_000_000.0, cost_basis_krw=48_000_000.0,
                                    policy=DEFAULT_LEVERAGE_DCA_POLICY)
    assert result["gross_profit_krw"] == 7_000_000.0
    assert result["fee_krw"] == 110_000.0
    assert result["net_profit_krw"] == 5_350_000.0
    assert result["trigger"] is True


def test_sell_trigger_does_not_fire_below_target():
    result = evaluate_sell_trigger(position_value_krw=51_000_000.0, cost_basis_krw=50_000_000.0,
                                    policy=DEFAULT_LEVERAGE_DCA_POLICY)
    assert result["trigger"] is False


def test_sell_trigger_no_profit_short_circuits_without_fee():
    result = evaluate_sell_trigger(position_value_krw=40_000_000.0, cost_basis_krw=45_000_000.0,
                                    policy=DEFAULT_LEVERAGE_DCA_POLICY)
    assert result["gross_profit_krw"] == -5_000_000.0
    assert result["fee_krw"] == 0.0
    assert result["trigger"] is False


def test_reserve_deployment_requires_all_checks_and_maps_drawdown_to_stage():
    result = evaluate_reserve_deployment_stage(
        spx_drawdown_pct=-31.0, fed_funds_trend="falling", vix=55.0, ten_year_yield_trend="falling",
        soxl_drawdown_pct=-40.0, upro_drawdown_pct=-30.0, nfp_3m_avg=80_000, unemployment_rate=None,
        drawdown_days=20, policy=DEFAULT_LEVERAGE_DCA_POLICY,
    )
    assert result["eligible"] is True
    assert result["stage"] == 2
    assert result["creates_live_authority"] is False
    assert result["moves_reserve_funds"] is False


def test_reserve_deployment_blocked_when_fed_is_hiking():
    result = evaluate_reserve_deployment_stage(
        spx_drawdown_pct=-31.0, fed_funds_trend="rising", vix=55.0, ten_year_yield_trend="falling",
        soxl_drawdown_pct=-40.0, upro_drawdown_pct=-30.0, nfp_3m_avg=80_000, unemployment_rate=None,
        drawdown_days=20, policy=DEFAULT_LEVERAGE_DCA_POLICY,
    )
    assert result["eligible"] is False
    assert result["stage"] == 0
    assert result["checks"]["fed_not_hiking"] is False


def test_reserve_deployment_blocked_when_soxl_specific_collapse():
    result = evaluate_reserve_deployment_stage(
        spx_drawdown_pct=-25.0, fed_funds_trend="flat", vix=55.0, ten_year_yield_trend="falling",
        soxl_drawdown_pct=-70.0, upro_drawdown_pct=-25.0, nfp_3m_avg=80_000, unemployment_rate=None,
        drawdown_days=20, policy=DEFAULT_LEVERAGE_DCA_POLICY,
    )
    assert result["checks"]["no_sector_specific_collapse"] is False
    assert result["eligible"] is False


def test_after_tax_profit_applies_annual_deduction_then_22_percent():
    from app.backend.leverage_dca_strategy import DEFAULT_LEVERAGE_DCA_POLICY, estimate_after_tax_profit
    policy = dict(DEFAULT_LEVERAGE_DCA_POLICY)
    # 평가 11,000,000 / 원가 10,000,000 → 차익 1,000,000, 수수료 22,000 → 공제(250만) 이내라 세금 0
    small = estimate_after_tax_profit(market_value_krw=11_000_000, cost_basis_krw=10_000_000, realized_gain_ytd_krw=0, policy=policy)
    assert small["estimated_tax_krw"] == 0
    assert small["net_profit_after_tax_krw"] == 978_000
    assert small["return_pct"] == 10.0
    # 차익 5,000,000, 수수료 30,000 → 과세표준 4,970,000-2,500,000 = 2,470,000 x 22% = 543,400
    large = estimate_after_tax_profit(market_value_krw=15_000_000, cost_basis_krw=10_000_000, realized_gain_ytd_krw=0, policy=policy)
    assert large["estimated_tax_krw"] == 543_400
    assert large["net_profit_after_tax_krw"] == 5_000_000 - 30_000 - 543_400
    # 올해 이미 300만원을 실현했으면 공제가 소진돼 이번 차익 전액(수수료 차감 후)에 22%
    used = estimate_after_tax_profit(market_value_krw=11_000_000, cost_basis_krw=10_000_000, realized_gain_ytd_krw=3_000_000, policy=policy)
    assert used["deduction_remaining_krw"] == 0
    assert used["estimated_tax_krw"] == round(978_000 * 0.22, 2)
    # 손실이면 세금 없음, 수수료만 추가 손실
    loss = estimate_after_tax_profit(market_value_krw=9_000_000, cost_basis_krw=10_000_000, realized_gain_ytd_krw=0, policy=policy)
    assert loss["estimated_tax_krw"] == 0
    assert loss["net_profit_after_tax_krw"] == -1_018_000
