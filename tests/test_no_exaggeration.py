"""시세 조회 실패 시 평가·리스크 수치를 꾸며내지 않고 명시적으로 제외하는지 확인한다."""

from app.backend.routers import portfolio
from app.backend.db import get_db
import time

BOGUS_TICKER = "ZZZZINVALIDTICKER"


async def test_valuation_excludes_unpriceable_ticker_instead_of_faking_it(client, make_user, monkeypatch):
    monkeypatch.setattr(
        portfolio,
        "fetch_quote_cached",
        lambda _ticker: {"status": "error", "reason": "테스트용 시세 조회 실패"},
    )
    headers = await make_user("no-exaggerate-valuation@example.com")
    await client.put(
        "/api/portfolio",
        json={
            "cash_krw": 100_000,
            "holdings": [
                {"ticker": BOGUS_TICKER, "name": "존재하지 않는 종목", "quantity": 1, "average_price": 1000, "currency": "USD"},
            ],
        },
        headers=headers,
    )

    resp = await client.get("/api/portfolio/valuation", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["value_krw"] is None
    assert data["profit_krw"] is None
    assert len(data["excluded"]) == 1
    assert data["excluded"][0]["ticker"] == BOGUS_TICKER


async def test_risk_reports_unavailable_when_only_holding_has_no_price_history(client, make_user, monkeypatch):
    monkeypatch.setattr(
        portfolio,
        "fetch_quote_cached",
        lambda _ticker: {"status": "error", "reason": "테스트용 시세 조회 실패"},
    )
    headers = await make_user("no-exaggerate-risk@example.com")
    await client.put(
        "/api/portfolio",
        json={
            "cash_krw": 0,
            "holdings": [
                {"ticker": BOGUS_TICKER, "name": "존재하지 않는 종목", "quantity": 1, "average_price": 1000, "currency": "USD"},
            ],
        },
        headers=headers,
    )

    resp = await client.get("/api/portfolio/risk?period=1y", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert "var_95_pct" not in data


async def test_valuation_uses_saved_daily_cache_before_yahoo(client, make_user, monkeypatch):
    headers = await make_user("cached-valuation@example.com")
    await get_db().market_ohlcv.insert_many([
        {"ticker": "005930.KS", "interval": "1d", "timestamp": "2026-09-09", "close": 70_000},
        {"ticker": "005930.KS", "interval": "1d", "timestamp": "2026-09-10", "close": 71_000},
    ])
    monkeypatch.setattr(portfolio, "fetch_quote_cached", lambda _ticker: (_ for _ in ()).throw(AssertionError("캐시가 먼저 사용되어야 합니다")))
    await client.put("/api/portfolio", json={"cash_krw": 0, "holdings": [
        {"ticker": "005930.KS", "name": "삼성전자", "quantity": 2, "average_price": 70_000, "currency": "KRW"},
    ]}, headers=headers)

    response = await client.get("/api/portfolio/valuation", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is True
    assert data["source"] == "market_data_cache_daily"
    assert data["positions"][0]["price"] == 71_000


async def test_valuation_returns_timeout_reason_when_live_quote_is_slow(client, make_user, monkeypatch):
    headers = await make_user("bounded-valuation@example.com")
    monkeypatch.setattr(portfolio, "QUOTE_FETCH_TIMEOUT_SECONDS", 0.01)

    def slow_quote(_ticker):
        time.sleep(0.05)
        return {"status": "ok", "price": 100, "change_pct": 0, "quoted_at": "2026-01-01T00:00:00+00:00"}

    monkeypatch.setattr(portfolio, "fetch_quote_cached", slow_quote)
    await client.put("/api/portfolio", json={"cash_krw": 0, "holdings": [
        {"ticker": "AAPL", "name": "Apple", "quantity": 1, "average_price": 100, "currency": "USD"},
    ]}, headers=headers)

    response = await client.get("/api/portfolio/valuation", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is False
    assert any("0.01초" in item["reason"] for item in data["excluded"])
