import pytest

from app.backend import alpaca_broker
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


def test_alpaca_paper_configured_requires_both_env_vars(monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)
    assert alpaca_broker.alpaca_paper_configured() is False
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "key")
    assert alpaca_broker.alpaca_paper_configured() is False
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "secret")
    assert alpaca_broker.alpaca_paper_configured() is True


def test_execute_paper_order_sync_fills_on_limit_leg(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "key")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "secret")
    monkeypatch.setattr(alpaca_broker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(alpaca_broker, "submit_order", lambda **kwargs: {"id": "order-1"})
    monkeypatch.setattr(alpaca_broker, "get_order", lambda order_id: {"status": "filled", "filled_qty": "5", "filled_avg_price": "100.02"})

    result = alpaca_broker.execute_paper_order_sync(ticker="UPRO", side="buy", requested_quantity=5, limit_price=100.05)
    assert result["status"] == "filled"
    assert result["order_type"] == "limit"
    assert result["fill_quantity"] == 5.0
    assert result["external_execution"] is True


def test_execute_paper_order_sync_falls_back_to_market_when_limit_unfilled(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "key")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "secret")
    monkeypatch.setattr(alpaca_broker.time, "sleep", lambda _seconds: None)
    calls = {"submit": 0}

    def _submit(**kwargs):
        calls["submit"] += 1
        return {"id": f"order-{calls['submit']}"}

    def _get_order(order_id):
        if order_id == "order-1":
            return {"status": "new"}
        return {"status": "filled", "filled_qty": "5", "filled_avg_price": "101.10"}

    monkeypatch.setattr(alpaca_broker, "submit_order", _submit)
    monkeypatch.setattr(alpaca_broker, "get_order", _get_order)
    monkeypatch.setattr(alpaca_broker, "cancel_order", lambda order_id: None)

    result = alpaca_broker.execute_paper_order_sync(ticker="UPRO", side="buy", requested_quantity=5, limit_price=100.05)
    assert result["order_type"] == "market"
    assert calls["submit"] == 2


@pytest.mark.asyncio
async def test_leverage_dca_buy_routes_through_alpaca_when_enabled_and_configured(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "key")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "secret")

    def _fake_execute(**kwargs):
        return {"status": "filled", "fill_price": 100.03, "fill_quantity": 30.0,
                "order_type": "limit", "alpaca_order_id": "order-xyz", "external_execution": True}

    monkeypatch.setattr(strategy, "execute_paper_order_sync", _fake_execute)

    async def _fake_to_thread(fn, **kwargs):
        return fn(**kwargs)
    monkeypatch.setattr(strategy.asyncio, "to_thread", _fake_to_thread)

    headers = await make_user("leverage-dca-alpaca@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
        "alpaca_enabled": True,
    }, headers=headers)

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result = run.json()["result"]
    assert result["status"] == "bought"
    assert all(action["status"] == "filled" for action in result["actions"])

    user = await get_db().users.find_one({"email": "leverage-dca-alpaca@example.com"})
    fills = await get_db().paper_fills.find({"user_id": user["_id"]}).to_list(10)
    assert len(fills) == 2
    assert {fill["source"] for fill in fills} == {"leverage_dca_alpaca_paper"}
    assert all(fill["execution_simulation"]["model"] == "alpaca_paper_broker" for fill in fills)


@pytest.mark.asyncio
async def test_leverage_dca_buy_falls_back_to_internal_when_alpaca_not_configured(client, make_user, monkeypatch):
    """alpaca_enabled=True 라도 서버 환경변수가 없으면 자체 모의 체결로 안전하게 동작한다."""
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)

    headers = await make_user("lvdca-alpaca-off@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
        "alpaca_enabled": True,
    }, headers=headers)

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result = run.json()["result"]
    assert result["status"] == "bought"

    user = await get_db().users.find_one({"email": "lvdca-alpaca-off@example.com"})
    fills = await get_db().paper_fills.find({"user_id": user["_id"]}).to_list(10)
    assert {fill["source"] for fill in fills} == {"leverage_dca_auto"}


@pytest.mark.asyncio
async def test_leverage_dca_buy_falls_back_to_internal_when_alpaca_errors(client, make_user, monkeypatch):
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "key")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "secret")

    def _raise(**kwargs):
        raise alpaca_broker.AlpacaPaperError("네트워크 오류")

    monkeypatch.setattr(strategy, "execute_paper_order_sync", _raise)
    async def _fake_to_thread(fn, **kwargs):
        return fn(**kwargs)
    monkeypatch.setattr(strategy.asyncio, "to_thread", _fake_to_thread)

    headers = await make_user("leverage-dca-alpaca-error@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000, "sell_net_profit_target_krw": 5_000_000,
        "alpaca_enabled": True,
    }, headers=headers)

    run = await client.post("/api/strategy/leverage-dca/run-now", headers=headers)
    result = run.json()["result"]
    assert result["status"] == "bought"

    user = await get_db().users.find_one({"email": "leverage-dca-alpaca-error@example.com"})
    fills = await get_db().paper_fills.find({"user_id": user["_id"]}).to_list(10)
    assert {fill["source"] for fill in fills} == {"leverage_dca_auto"}
    assert all(fill["execution_simulation"]["alpaca_fallback_reason"] == "네트워크 오류" for fill in fills)
