"""중복 티커·잘못된 티커 입력이 422로 거부되는지 확인한다."""


async def test_duplicate_ticker_returns_422(client, make_user):
    headers = await make_user("dup-ticker@example.com")
    resp = await client.put(
        "/api/portfolio",
        json={
            "cash_krw": 0,
            "holdings": [
                {"ticker": "AAPL", "name": "Apple", "quantity": 1, "average_price": 100, "currency": "USD"},
                {"ticker": "aapl", "name": "Apple 중복", "quantity": 2, "average_price": 90, "currency": "USD"},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_malformed_ticker_returns_422(client, make_user):
    headers = await make_user("bad-ticker@example.com")
    resp = await client.put(
        "/api/portfolio",
        json={
            "cash_krw": 0,
            "holdings": [
                {"ticker": "bad ticker!!", "name": "잘못된 티커", "quantity": 1, "average_price": 100, "currency": "USD"},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_strategy_backtest_malformed_ticker_returns_422(client):
    resp = await client.post(
        "/api/strategy/backtest",
        json={"ticker": "bad ticker!!", "fast_ma": 20, "slow_ma": 60, "period": "2y"},
    )
    assert resp.status_code == 422


async def test_strategy_backtest_invalid_period_returns_422(client):
    resp = await client.post(
        "/api/strategy/backtest",
        json={"ticker": "AAPL", "fast_ma": 20, "slow_ma": 60, "period": "10y"},
    )
    assert resp.status_code == 422


async def test_portfolio_risk_invalid_period_returns_422(client, make_user):
    headers = await make_user("bad-period@example.com")
    await client.put(
        "/api/portfolio",
        json={"cash_krw": 100000, "holdings": []},
        headers=headers,
    )
    resp = await client.get("/api/portfolio/risk?period=10y", headers=headers)
    assert resp.status_code == 422
