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
    assert tickers["UPRO"]["notional_krw"] == pytest.approx(3_000_000, rel=0.01)
    assert tickers["SOXL"]["notional_krw"] == pytest.approx(2_000_000, rel=0.01)

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
