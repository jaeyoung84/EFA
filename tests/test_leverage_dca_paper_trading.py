import pytest

from app.backend.db import get_db
from app.backend.routers import strategy


def _signals(**overrides) -> dict:
    base = {
        "available": True, "as_of": "2026-09-18",
        "spx_drawdown_pct": -2.0, "vix": 15.0,
        "soxl_drawdown_pct": -5.0, "upro_drawdown_pct": -2.0,
        "spx_above_ma_consecutive_days": 5, "errors": [],
    }
    base.update(overrides)
    return base


def _async_signals(**overrides):
    async def _inner():
        return _signals(**overrides)
    return _inner


def _mock_quotes(monkeypatch, *, upro_price: float = 100.0, soxl_price: float = 20.0, fx_rate: float = 1300.0) -> None:
    prices = {"UPRO": upro_price, "SOXL": soxl_price, "KRW=X": fx_rate}

    def _fetch(ticker: str) -> dict:
        if ticker not in prices:
            return {"status": "error", "reason": f"{ticker} 미지원"}
        return {"status": "ok", "price": prices[ticker], "change_pct": 0.0, "quoted_at": "2026-09-18T00:00:00Z"}

    monkeypatch.setattr(strategy, "fetch_quote_cached", _fetch)


async def _simulate_next_trading_day(email: str) -> None:
    """1일 1회 실행 가드(last_run_date)를 초기화해 다음 거래일 진입을 흉내 낸다."""
    user = await get_db().users.find_one({"email": email})
    await get_db().leverage_dca_configs.update_one({"user_id": user["_id"]}, {"$set": {"last_run_date": "2000-01-01"}})


@pytest.mark.asyncio
async def test_run_now_buys_upro_and_soxl_by_weight_when_not_stopped(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    headers = await make_user("leverage-dca-buy@example.com")

    config = await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)
    assert config.status_code == 200
    assert config.json()["enabled"] is True

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert run.status_code == 200
    result = run.json()["result"]
    assert result["status"] == "bought"
    tickers = {action["ticker"]: action for action in result["actions"]}
    assert tickers["UPRO"]["status"] == "filled"
    assert tickers["SOXL"]["status"] == "filled"
    # 정수 주 단위로 내림하므로 배정 금액과 정확히 일치하지 않는다(가격에 따라 최대 1주 오차).
    assert tickers["UPRO"]["notional_krw"] == pytest.approx(3_000_000, rel=0.05)
    assert tickers["SOXL"]["notional_krw"] == pytest.approx(2_000_000, rel=0.05)
    assert tickers["UPRO"]["quantity"] == float(int(tickers["UPRO"]["quantity"]))
    assert tickers["SOXL"]["quantity"] == float(int(tickers["SOXL"]["quantity"]))

    status = await client.get("/api/strategy/leverage-dca/status", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is True
    positions = {item["ticker"]: item for item in body["positions"]}
    assert positions["UPRO"]["quantity"] > 0
    assert positions["SOXL"]["quantity"] > 0

    audit = await client.get("/api/strategy/execution-audit", headers=headers)
    actions = {item["action"] for item in audit.json()["items"]}
    assert {"leverage_dca_config_updated", "leverage_dca_auto_buy"} <= actions


@pytest.mark.asyncio
async def test_disabled_config_takes_no_action(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    headers = await make_user("leverage-dca-disabled@example.com")

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert run.status_code == 200
    assert run.json()["result"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_spx_drawdown_stop_blocks_all_buys(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals(spx_drawdown_pct=-9.0))
    headers = await make_user("leverage-dca-stop@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result = run.json()["result"]
    assert result["status"] == "no_action"
    assert result["stop_state"]["stop_all"] is True
    assert result["actions"] == []


@pytest.mark.asyncio
async def test_soxl_divergence_stop_routes_full_amount_to_upro(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals(soxl_drawdown_pct=-60.0, upro_drawdown_pct=-5.0))
    headers = await make_user("leverage-dca-soxl-stop@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result = run.json()["result"]
    assert result["status"] == "bought"
    tickers = {action["ticker"] for action in result["actions"]}
    assert tickers == {"UPRO"}


@pytest.mark.asyncio
async def test_sell_trigger_liquidates_position_and_next_cycle_restarts_dca(client, make_user, monkeypatch):
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    headers = await make_user("leverage-dca-sell@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)

    _mock_quotes(monkeypatch, upro_price=100.0, soxl_price=20.0)
    first = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert first.json()["result"]["status"] == "bought"

    # 가격이 크게 올라 세후 실수령 목표를 넘도록 만든다.
    await _simulate_next_trading_day("leverage-dca-sell@example.com")
    _mock_quotes(monkeypatch, upro_price=250.0, soxl_price=60.0)
    second = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result = second.json()["result"]
    assert result["status"] == "sold"
    assert result["sell_trigger"]["trigger"] is True
    assert all(action["status"] == "filled" for action in result["actions"])

    status = await client.get("/api/strategy/leverage-dca/status", headers=headers)
    assert status.json()["positions"] == []

    # 매도 후 원금이 초기화됐으니 다음 거래일 사이클은 다시 매수로 시작한다.
    await _simulate_next_trading_day("leverage-dca-sell@example.com")
    _mock_quotes(monkeypatch, upro_price=100.0, soxl_price=20.0)
    third = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert third.json()["result"]["status"] == "bought"


@pytest.mark.asyncio
async def test_global_kill_switch_blocks_automation(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    headers = await make_user("leverage-dca-killswitch@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)
    monkeypatch.setenv("EXECUTION_GLOBAL_KILL_SWITCH", "true")

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result = run.json()["result"]
    assert result["status"] == "blocked"


@pytest.mark.asyncio
async def test_scheduler_cycle_runs_only_enabled_users(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    enabled_headers = await make_user("leverage-dca-enabled@example.com")
    await make_user("leverage-dca-not-enabled@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=enabled_headers)

    result = await strategy.run_leverage_dca_daily_cycle()
    assert len(result["results"]) == 1
    assert result["results"][0]["status"] == "bought"

    enabled_user = await get_db().users.find_one({"email": "leverage-dca-enabled@example.com"})
    fills = await get_db().paper_fills.find({"user_id": enabled_user["_id"]}).to_list(10)
    assert len(fills) == 2
    assert {fill["source"] for fill in fills} == {"leverage_dca_auto"}


@pytest.mark.asyncio
async def test_stop_all_persists_until_resume_condition_met(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    headers = await make_user("leverage-dca-hysteresis@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)

    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals(spx_drawdown_pct=-9.0))
    first = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert first.json()["result"]["stop_state"]["stop_all"] is True

    user = await get_db().users.find_one({"email": "leverage-dca-hysteresis@example.com"})
    config = await get_db().leverage_dca_configs.find_one({"user_id": user["_id"]})
    assert config["buy_stopped"] is True

    # 낙폭은 회복됐지만 재개 조건(50일선 위 3일 연속)을 아직 못 채워 중지를 유지해야 한다.
    await _simulate_next_trading_day("leverage-dca-hysteresis@example.com")
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals(spx_drawdown_pct=-1.0, spx_above_ma_consecutive_days=1))
    second = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result2 = second.json()["result"]
    assert result2["status"] == "no_action"
    assert result2["stop_state"]["stop_all"] is True
    assert result2["stop_state"]["hysteresis"]["previously_stopped"] is True
    assert result2["stop_state"]["hysteresis"]["resumed"] is False

    # 재개 조건을 채우면 매수를 재개한다.
    await _simulate_next_trading_day("leverage-dca-hysteresis@example.com")
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals(spx_drawdown_pct=-1.0, spx_above_ma_consecutive_days=3))
    third = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result3 = third.json()["result"]
    assert result3["status"] == "bought"
    assert result3["stop_state"]["stop_all"] is False


def _reserve_signals(**overrides) -> dict:
    base = {
        "available": True, "fed_funds_trend": "falling", "ten_year_yield_trend": "falling",
        "nfp_3m_avg": 50_000.0, "unemployment_rate": 4.0, "drawdown_days": 10, "errors": [],
    }
    base.update(overrides)
    return base


def _async_reserve_signals(**overrides):
    async def _inner(spx_dates, spx_closes):
        return _reserve_signals(**overrides)
    return _inner


@pytest.mark.asyncio
async def test_reserve_deploys_stage_one_when_all_indicators_align(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals(
        spx_drawdown_pct=-25.0, vix=55.0, soxl_drawdown_pct=-30.0, upro_drawdown_pct=-25.0, spx_drawdown_days=10,
    ))
    monkeypatch.setattr(strategy, "fetch_reserve_signals", _async_reserve_signals())
    headers = await make_user("leverage-dca-reserve@example.com")
    await client.put("/api/strategy/paper-account", json={"initial_capital_krw": 200_000_000}, headers=headers)
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
        "reserve_enabled": True, "reserve_cash_krw": 100_000_000,
    }, headers=headers)

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result = run.json()["result"]
    reserve = result["reserve"]
    assert reserve["status"] == "deployed"
    assert reserve["deployed_stage"] == 1
    assert reserve["actions"][0]["ticker"] == "UPRO"
    assert reserve["actions"][0]["notional_krw"] == pytest.approx(100_000_000 / 3, rel=0.02)

    status = await client.get("/api/strategy/leverage-dca/status", headers=headers)
    body = status.json()
    assert body["reserve"]["deployed_stage"] == 1
    assert body["reserve"]["positions"][0]["ticker"] == "UPRO"

    # 같은 단계에서는 추가 투입하지 않는다(단계 유지, 다음 거래일에도 동일 판정).
    await _simulate_next_trading_day("leverage-dca-reserve@example.com")
    second = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert second.json()["result"]["reserve"]["status"] == "no_action"
    assert second.json()["result"]["reserve"]["deployed_stage"] == 1


@pytest.mark.asyncio
async def test_reserve_blocked_when_fed_still_hiking(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals(
        spx_drawdown_pct=-25.0, vix=55.0, soxl_drawdown_pct=-30.0, upro_drawdown_pct=-25.0,
    ))
    monkeypatch.setattr(strategy, "fetch_reserve_signals", _async_reserve_signals(fed_funds_trend="rising"))
    headers = await make_user("leverage-dca-reserve-blocked@example.com")
    await client.put("/api/strategy/paper-account", json={"initial_capital_krw": 200_000_000}, headers=headers)
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
        "reserve_enabled": True, "reserve_cash_krw": 100_000_000,
    }, headers=headers)

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    reserve = run.json()["result"]["reserve"]
    assert reserve["status"] == "no_action"
    assert reserve["reserve_result"]["eligible"] is False
    assert reserve["reserve_result"]["checks"]["fed_not_hiking"] is False


@pytest.mark.asyncio
async def test_reserve_disabled_by_default(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    headers = await make_user("leverage-dca-reserve-off@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)
    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert run.json()["result"]["reserve"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_second_run_same_day_is_a_no_op(client, make_user, monkeypatch):
    """같은 거래일 안에서 여러 번 폴링돼도(장마감 창 폭 5~10분 간격) 중복 매수하지 않는다."""
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    headers = await make_user("leverage-dca-once-per-day@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)

    first = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert first.json()["result"]["status"] == "bought"

    second = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert second.json()["result"]["status"] == "already_run_today"

    user = await get_db().users.find_one({"email": "leverage-dca-once-per-day@example.com"})
    fills = await get_db().paper_fills.find({"user_id": user["_id"]}).to_list(10)
    assert len(fills) == 2


@pytest.mark.asyncio
async def test_trades_endpoint_lists_exact_price_and_quantity(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch, upro_price=123.45, soxl_price=67.89)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    headers = await make_user("leverage-dca-trades@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)
    await client.post("/api/strategy/leverage-dca/run-now", headers=headers)

    trades = await client.get("/api/strategy/leverage-dca/trades", headers=headers)
    assert trades.status_code == 200
    items = trades.json()["items"]
    assert len(items) == 2
    tickers = {item["ticker"] for item in items}
    assert tickers == {"UPRO", "SOXL"}
    for item in items:
        assert item["quantity"] > 0
        assert item["fill_price"] > 0
        assert item["notional_krw"] > 0
        assert item["direction"] == "buy"
        assert item["is_reserve"] is False
        assert item["date"]


@pytest.mark.asyncio
async def test_reset_clears_fills_positions_and_logs_but_keeps_config(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    headers = await make_user("leverage-dca-reset@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)
    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert run.json()["result"]["status"] == "bought"

    reset = await client.delete("/api/strategy/leverage-dca/trades", headers=headers)
    assert reset.status_code == 200
    reset_body = reset.json()
    assert reset_body["status"] == "reset"
    assert reset_body["deleted_fills"] == 2
    assert reset_body["deleted_alpaca_fills"] == 0
    assert reset_body["deleted_logs"] == 1

    trades = await client.get("/api/strategy/leverage-dca/trades", headers=headers)
    assert trades.json()["items"] == []

    status = await client.get("/api/strategy/leverage-dca/status", headers=headers)
    body = status.json()
    assert body["positions"] == []
    assert body["last_run_date"] is None
    assert body["latest_log"] is None
    assert body["enabled"] is True  # 설정값은 유지된다

    # 초기화 뒤 last_run_date가 지워졌으므로 같은 날 다시 실행해도 중복 방지에 막히지 않는다
    run_again = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    assert run_again.json()["result"]["status"] == "bought"


@pytest.mark.asyncio
async def test_buy_is_not_capped_by_shared_portfolio_concentration_policy(client, make_user, monkeypatch):
    """레버리지 DCA는 포트폴리오 전반의 종목 집중도 한도(기본 35%)와 별개의 독립 전략이다.

    기본 모의 계좌 현금(1,000만원) 대비 하루 매수 예산이 80%를 넘어도, 실제 체결 가능한
    현금이 충분하면 35% 집중도 한도에 걸려 줄어들지 않고 요청 금액 그대로 체결돼야 한다.
    """
    _mock_quotes(monkeypatch, upro_price=100.0, soxl_price=20.0, fx_rate=1000.0)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals(soxl_drawdown_pct=-60.0, upro_drawdown_pct=-5.0))
    headers = await make_user("lvdca-no-concentration@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 8_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result = run.json()["result"]
    assert result["status"] == "bought"
    actions = {action["ticker"]: action for action in result["actions"]}
    assert actions.keys() == {"UPRO"}  # SOXL 괴리가 -55%p로 첫 진입 완화 기준(-50%p)도 넘어 중단됨
    # 8,000,000원 배정분이 35% 집중도 한도 없이 거의 그대로 체결된다(정수 주 반올림 오차만 존재).
    assert actions["UPRO"]["notional_krw"] == pytest.approx(8_000_000, rel=0.02)


@pytest.mark.asyncio
async def test_soxl_divergence_threshold_tightens_after_first_entry(client, make_user, monkeypatch):
    """첫 진입 전엔 괴리 -50%p 이내면 매수하고, 일단 SOXL을 보유하면 -20%p+자체낙폭 -20% 기준으로 더 보수적으로 바뀐다."""
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals(soxl_drawdown_pct=-30.0, upro_drawdown_pct=-5.0))
    headers = await make_user("leverage-dca-phase@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)

    _mock_quotes(monkeypatch, upro_price=100.0, soxl_price=20.0)
    first = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    first_result = first.json()["result"]
    # 괴리 -25%p는 구 기준(-20%p)이면 막히지만, SOXL 미보유 상태의 완화 기준(-50%p)에서는 매수된다.
    assert {action["ticker"] for action in first_result["actions"]} == {"UPRO", "SOXL"}

    await _simulate_next_trading_day("leverage-dca-phase@example.com")
    second = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    second_result = second.json()["result"]
    # 이제 SOXL을 보유 중이므로 같은 -25%p 괴리(+자체 낙폭 -30% > -20%)면 SOXL 매수만 중단된다.
    assert {action["ticker"] for action in second_result["actions"]} == {"UPRO"}
    assert second_result["stop_state"]["stop_soxl_only"] is True
    assert second_result["stop_state"]["has_soxl_position"] is True


@pytest.mark.asyncio
async def test_allowlist_blocks_other_accounts_when_configured(client, make_user, monkeypatch):
    monkeypatch.setenv("LEVERAGE_DCA_ALLOWED_EMAILS", "owner@example.com")
    owner_headers = await make_user("owner@example.com")
    other_headers = await make_user("intruder@example.com")

    owner_ok = await client.get("/api/strategy/leverage-dca/config", headers=owner_headers)
    assert owner_ok.status_code == 200

    for path, method in [
        ("/api/strategy/leverage-dca/config", "get"),
        ("/api/strategy/leverage-dca/status", "get"),
        ("/api/strategy/leverage-dca/trades", "get"),
        ("/api/strategy/leverage-dca/run-now", "post"),
    ]:
        response = await getattr(client, method)(path, headers=other_headers)
        assert response.status_code == 403, path


@pytest.mark.asyncio
async def test_allowlist_open_by_default(client, make_user, monkeypatch):
    monkeypatch.delenv("LEVERAGE_DCA_ALLOWED_EMAILS", raising=False)
    headers = await make_user("anyone-else@example.com")
    response = await client.get("/api/strategy/leverage-dca/config", headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_buy_quantities_are_whole_shares_not_fractional(client, make_user, monkeypatch):
    """가격이 배정 금액을 딱 나누어떨어지게 하지 않는 실제 상황(예: SOXL) 재현."""
    _mock_quotes(monkeypatch, upro_price=146.88, soxl_price=27.13, fx_rate=1391.5)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    headers = await make_user("leverage-dca-wholeshare@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result = run.json()["result"]
    assert result["status"] == "bought"
    for action in result["actions"]:
        assert action["status"] == "filled"
        assert action["quantity"] == int(action["quantity"]), f"{action['ticker']} quantity {action['quantity']} is not a whole share"
        assert action["quantity"] >= 1

    trades = await client.get("/api/strategy/leverage-dca/trades", headers=headers)
    for item in trades.json()["items"]:
        assert item["quantity"] == int(item["quantity"])


@pytest.mark.asyncio
async def test_leverage_fills_without_proposal_survive_the_proposal_unique_index_on_consecutive_days(client, make_user, monkeypatch):
    """2026-09-30 사고 회귀: 제안 없는 레버리지 체결은 둘째 날부터 proposal_id 유일 인덱스에 막혔다."""
    db = get_db()
    existing = await db.paper_fills.index_information()
    for name in ("proposal_id_1", strategy.PAPER_FILL_PROPOSAL_INDEX):
        if name in existing:
            await db.paper_fills.drop_index(name)
    await db.paper_fills.create_index("proposal_id", unique=True)  # 운영에 남아 있던 옛 인덱스
    await strategy._ensure_paper_fill_proposal_index(db)
    indexes = await db.paper_fills.index_information()
    assert "proposal_id_1" not in indexes
    assert indexes[strategy.PAPER_FILL_PROPOSAL_INDEX]["partialFilterExpression"] == {"proposal_id": {"$type": "objectId"}}

    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    email = "leverage-dca-index@example.com"
    headers = await make_user(email)
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)
    for _ in range(2):
        run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
        assert run.json()["result"]["status"] == "bought"
        assert {a["status"] for a in run.json()["result"]["actions"]} == {"filled"}
        await _simulate_next_trading_day(email)
    user = await db.users.find_one({"email": email})
    assert await db.paper_fills.count_documents({"user_id": user["_id"]}) == 4

    proposal_id = strategy.ObjectId()
    await db.paper_fills.insert_one({"user_id": user["_id"], "proposal_id": proposal_id})
    with pytest.raises(strategy.DuplicateKeyError):
        await db.paper_fills.insert_one({"user_id": user["_id"], "proposal_id": proposal_id})


@pytest.mark.asyncio
async def test_failed_website_fill_does_not_repeat_orders_the_same_day(client, make_user, monkeypatch):
    """자체 체결 저장이 실패해도 오늘 실행은 이미 선점돼, 5분 주기 재시도가 Alpaca 주문을 반복하지 않는다."""
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    mirror_calls = []

    async def _mirror(**kwargs):
        mirror_calls.append(kwargs["ticker"])
        return None
    monkeypatch.setattr(strategy, "_leverage_dca_submit_alpaca_mirror", _mirror)
    original_insert = type(get_db().paper_fills).insert_one

    async def _failing_insert(self, document, *args, **kwargs):
        if document.get("source") == "leverage_dca_auto":
            raise strategy.PyMongoError("simulated duplicate key")
        return await original_insert(self, document, *args, **kwargs)
    monkeypatch.setattr(type(get_db().paper_fills), "insert_one", _failing_insert)

    headers = await make_user("leverage-dca-no-repeat@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)
    first = (await client.post("/api/strategy/leverage-dca/run-now", headers=headers)).json()["result"]
    assert {a["status"] for a in first["actions"]} == {"error"}
    assert first["last_run_date"]
    calls_after_first = len(mirror_calls)
    for _ in range(3):
        again = (await client.post("/api/strategy/leverage-dca/run-now", headers=headers)).json()["result"]
        assert again["status"] == "already_run_today"
    assert len(mirror_calls) == calls_after_first == 2


@pytest.mark.asyncio
async def test_status_shows_cumulative_average_return_and_after_tax_profit(client, make_user, monkeypatch):
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    monkeypatch.setattr(strategy, "alpaca_paper_configured", lambda: False)
    email = "leverage-dca-summary@example.com"
    headers = await make_user(email)
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 50_000_000,
    }, headers=headers)
    _mock_quotes(monkeypatch, upro_price=100.0, soxl_price=20.0, fx_rate=1000.0)
    await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    await _simulate_next_trading_day(email)
    _mock_quotes(monkeypatch, upro_price=200.0, soxl_price=40.0, fx_rate=1000.0)
    await client.post("/api/strategy/leverage-dca/run-now", headers=headers)

    user = await get_db().users.find_one({"email": email})
    fills = await get_db().paper_fills.find({"user_id": user["_id"], "ticker": "UPRO"}).to_list(10)
    quantity = sum(f["quantity"] for f in fills)
    expected_avg = sum(f["notional"] for f in fills) / quantity

    _mock_quotes(monkeypatch, upro_price=250.0, soxl_price=40.0, fx_rate=1000.0)
    summary = (await client.get("/api/strategy/leverage-dca/status", headers=headers)).json()["holding_summary"]
    upro = next(row for row in summary["tickers"] if row["ticker"] == "UPRO")
    assert upro["quantity"] == quantity
    assert upro["average_cost_usd"] == pytest.approx(expected_avg, abs=1e-4)
    assert upro["return_pct"] == pytest.approx((250 - expected_avg) / expected_avg * 100, abs=1e-3)
    totals = summary["totals"]
    assert totals["market_value_krw"] == pytest.approx(sum(r["market_value_krw"] for r in summary["tickers"]))
    tax = summary["after_tax"]
    assert tax["net_profit_after_tax_krw"] == pytest.approx(
        tax["gross_profit_krw"] - tax["sell_fee_krw"] - tax["estimated_tax_krw"], abs=0.02)
    assert tax["gross_profit_krw"] == pytest.approx(totals["unrealized_pnl_krw"], abs=0.02)


@pytest.mark.asyncio
async def test_status_shows_alpaca_account_summary_from_real_positions(client, make_user, monkeypatch):
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    monkeypatch.setattr(strategy, "alpaca_paper_configured", lambda: True)
    monkeypatch.setattr(strategy, "alpaca_list_positions", lambda: [
        {"symbol": "UPRO", "qty": "48", "avg_entry_price": "148.0"}, {"symbol": "AAPL", "qty": "1", "avg_entry_price": "1"}])
    _mock_quotes(monkeypatch, upro_price=150.0, fx_rate=1400.0)
    headers = await make_user("leverage-dca-alpaca-summary@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
    }, headers=headers)
    summary = (await client.get("/api/strategy/leverage-dca/status", headers=headers)).json()["alpaca_holding_summary"]
    assert [row["ticker"] for row in summary["tickers"]] == ["UPRO"]
    upro = summary["tickers"][0]
    assert upro["average_cost_usd"] == 148.0
    assert upro["market_value_krw"] == 150.0 * 48 * 1400.0
    assert upro["return_pct"] == pytest.approx((150 - 148) / 148 * 100, abs=1e-3)
    assert summary["after_tax"]["net_profit_after_tax_krw"] is not None
