"""레버리지 DCA 운영자 전용 변경: 운영자만 설정·실행·초기화, 다른 계정은 운영자 현황을 읽기 전용으로 본다."""
import pytest

from app.backend.routers import strategy
from test_leverage_dca_paper_trading import _async_signals, _mock_quotes

CONFIG = {"enabled": True, "daily_buy_krw": 1_000_000, "max_position_krw": 300_000_000,
          "sell_net_profit_target_krw": 5_000_000, "reserve_enabled": False, "reserve_cash_krw": 100_000_000,
          "alpaca_enabled": False}


@pytest.mark.asyncio
async def test_owner_edits_and_others_only_monitor_owner_data(client, make_user, monkeypatch):
    monkeypatch.setenv("LEVERAGE_DCA_OWNER_EMAIL", "Owner@Example.com")
    monkeypatch.delenv("LEVERAGE_DCA_ALLOWED_EMAILS", raising=False)
    _mock_quotes(monkeypatch)
    monkeypatch.setattr(strategy, "fetch_market_signals", _async_signals())
    owner = await make_user("owner@example.com")
    viewer = await make_user("viewer@example.com")

    saved = await client.put("/api/strategy/leverage-dca/config", headers=owner, json=CONFIG)
    assert saved.status_code == 200, saved.text
    assert (await client.post("/api/strategy/leverage-dca/run-now", headers=owner)).status_code == 200

    # 다른 계정: 변경은 모두 403
    for method, path, kwargs in [("put", "/api/strategy/leverage-dca/config", {"json": {**CONFIG, "enabled": False}}),
                                 ("post", "/api/strategy/leverage-dca/run-now", {}),
                                 ("delete", "/api/strategy/leverage-dca/trades", {})]:
        response = await getattr(client, method)(path, headers=viewer, **kwargs)
        assert response.status_code == 403 and "모니터링 전용" in response.json()["detail"], path

    # 다른 계정: 운영자의 설정·현황·체결을 읽기 전용으로 본다
    owner_status = (await client.get("/api/strategy/leverage-dca/status", headers=owner)).json()
    view_config = (await client.get("/api/strategy/leverage-dca/config", headers=viewer)).json()
    view_status = (await client.get("/api/strategy/leverage-dca/status", headers=viewer)).json()
    view_trades = (await client.get("/api/strategy/leverage-dca/trades", headers=viewer)).json()
    assert view_config["read_only"] is True and view_config["enabled"] is True
    assert view_config["policy"]["daily_buy_krw"] == 1_000_000
    assert view_status["read_only"] is True and view_status["positions"] == owner_status["positions"]
    assert view_trades["read_only"] is True and len(view_trades["items"]) >= 1
    assert owner_status["read_only"] is False
    assert (await client.get("/api/strategy/leverage-dca/config", headers=owner)).json()["enabled"] is True  # 시도한 끄기가 반영되지 않음
    assert (await client.get("/api/strategy/leverage-dca/config")).status_code == 401


@pytest.mark.asyncio
async def test_missing_owner_account_is_reported(client, make_user, monkeypatch):
    monkeypatch.setenv("LEVERAGE_DCA_OWNER_EMAIL", "nobody@example.com")
    viewer = await make_user("viewer2@example.com")
    response = await client.get("/api/strategy/leverage-dca/status", headers=viewer)
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_without_owner_setting_each_account_manages_its_own(client, make_user, monkeypatch):
    monkeypatch.delenv("LEVERAGE_DCA_OWNER_EMAIL", raising=False)
    monkeypatch.delenv("LEVERAGE_DCA_ALLOWED_EMAILS", raising=False)
    headers = await make_user("self-managed@example.com")
    assert (await client.put("/api/strategy/leverage-dca/config", headers=headers, json=CONFIG)).status_code == 200
    assert (await client.get("/api/strategy/leverage-dca/config", headers=headers)).json()["read_only"] is False
