"""로그인 없이 개인 데이터 API에 접근하면 401을 반환하는지 확인한다."""


async def test_get_portfolio_requires_auth(client):
    resp = await client.get("/api/portfolio")
    assert resp.status_code == 401


async def test_put_portfolio_requires_auth(client):
    resp = await client.put("/api/portfolio", json={"cash_krw": 0, "holdings": []})
    assert resp.status_code == 401


async def test_portfolio_valuation_requires_auth(client):
    resp = await client.get("/api/portfolio/valuation")
    assert resp.status_code == 401


async def test_portfolio_risk_requires_auth(client):
    resp = await client.get("/api/portfolio/risk")
    assert resp.status_code == 401


async def test_strategy_list_requires_auth(client):
    resp = await client.get("/api/strategy")
    assert resp.status_code == 401


async def test_strategy_save_requires_auth(client):
    resp = await client.post("/api/strategy", json={"type": "lean", "name": "x", "symbol": "samsung"})
    assert resp.status_code == 401


async def test_strategy_fills_requires_auth(client):
    resp = await client.get("/api/strategy/fills")
    assert resp.status_code == 401


async def test_strategy_backtest_preview_does_not_require_auth(client, monkeypatch):
    """미저장 백테스트 미리보기는 로그인 없이도 가능해야 한다(저장만 인증 필요)."""
    import pandas as pd
    from app.backend.routers import strategy

    dates = pd.date_range("2024-01-02", periods=220, freq="B")
    close = pd.Series([100 + index * 0.2 + (index % 9) for index in range(len(dates))], index=dates)
    monkeypatch.setattr(strategy, "_download_ohlc_frame", lambda _ticker, _period: pd.DataFrame({"Open": close, "Close": close}))
    resp = await client.post(
        "/api/strategy/backtest",
        json={"ticker": "AAPL", "fast_ma": 20, "slow_ma": 60, "period": "1y"},
    )
    assert resp.status_code == 200
