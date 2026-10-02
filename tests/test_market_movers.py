"""금일 상승종목(코스피·코스닥 전 종목): 네이버 순위 파싱, 30초 캐시, 실패 시 이전 값/503, 거래대금 필터."""
import pytest

from app.backend import main, market_movers as mm


def _row(code, name, price, pct, value_million, cap_eok, kind="stock", direction="RISING", change="100"):
    return {"itemCode": code, "stockName": name, "stockEndType": kind, "closePrice": f"{price:,}",
            "compareToPreviousClosePrice": change, "compareToPreviousPrice": {"name": direction},
            "fluctuationsRatio": f"{pct:.2f}", "accumulatedTradingVolume": "1,000",
            "accumulatedTradingValue": f"{value_million:,}", "marketValue": f"{cap_eok:,}",
            "localTradedAt": "2026-10-02T10:22:17+09:00"}


PAYLOAD = {"marketStatus": "OPEN", "totalCount": 1824, "stocks": [
    _row("217590", "티엠씨", 18440, 29.95, 93694, 4443, direction="UPPER_LIMIT", change="4,250"),
    _row("069500", "KODEX 200", 111520, 31.0, 999999, 99999, kind="etf"),     # ETF 제외
    _row("000001", "소형주", 1200, 25.10, 300, 120),                            # 거래대금 3억
    _row("005930", "삼성전자", 276000, 2.79, 3000000, 16000000),
]}


@pytest.fixture(autouse=True)
def fresh_cache():
    mm._reset_cache_for_tests()
    yield
    mm._reset_cache_for_tests()


def test_parse_keeps_stocks_only_in_won_units():
    data = mm.parse_up_ranking(PAYLOAD, "KOSDAQ")
    assert [i["code"] for i in data["items"]] == ["217590", "000001", "005930"]
    top = data["items"][0]
    assert (top["ticker"], top["limit_up"], top["change"]) == ("217590.KQ", True, 4250.0)
    assert top["trading_value_krw"] == 93_694_000_000 and top["market_cap_krw"] == 444_300_000_000
    assert data["as_of"] == "2026-10-02T10:22:17+09:00" and data["market_status"] == "OPEN"


def test_cache_filter_and_stale_fallback():
    calls = []
    clock = [1000.0]
    def fetch(market):
        calls.append(market)
        if len(calls) > 1:
            raise OSError("blocked")
        return PAYLOAD
    first = mm.top_gainers("KOSPI", 20, fetch=fetch, clock=lambda: clock[0])
    assert [i["rank"] for i in first["items"]] == [1, 2, 3] and first["cached"] is False
    liquid = mm.top_gainers("KOSPI", 20, 1_000_000_000, fetch=fetch, clock=lambda: clock[0])
    assert [i["name"] for i in liquid["items"]] == ["티엠씨", "삼성전자"] and liquid["cached"] is True and len(calls) == 1
    clock[0] += 60  # 캐시 만료 → 가져오기 실패 → 10분 이내 이전 값을 '지연'으로
    stale = mm.top_gainers("KOSPI", 20, fetch=fetch, clock=lambda: clock[0])
    assert stale["stale"] is True and len(stale["items"]) == 3
    clock[0] += 700  # 이전 값도 오래됨 → 실패
    with pytest.raises(mm.MarketMoversError):
        mm.top_gainers("KOSPI", 20, fetch=fetch, clock=lambda: clock[0])
    with pytest.raises(ValueError):
        mm.top_gainers("NYSE", 20, fetch=fetch)


@pytest.mark.asyncio
async def test_endpoint_per_market_and_failure(client, monkeypatch):
    monkeypatch.setattr(mm, "_fetch", lambda market: PAYLOAD)
    data = (await client.get("/api/market/top-gainers", params={"market": "kosdaq", "limit": 2})).json()
    assert data["market"] == "KOSDAQ" and [i["ticker"] for i in data["items"]] == ["217590.KQ", "000001.KQ"]
    assert (await client.get("/api/market/top-gainers", params={"market": "NYSE"})).status_code == 422
    mm._reset_cache_for_tests()
    monkeypatch.setattr(mm, "_fetch", lambda market: (_ for _ in ()).throw(OSError("timeout")))
    failed = await client.get("/api/market/top-gainers", params={"market": "KOSPI"})
    assert failed.status_code == 503 and "데이터 없음" in failed.json()["detail"]


@pytest.mark.asyncio
async def test_portfolio_valuation_fetches_up_to_20_quotes_10_at_a_time(monkeypatch):
    import asyncio
    from app.backend.routers import portfolio

    running, peak, seen = [0], [0], []

    async def cached(tickers):
        return {}

    async def bounded(ticker):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        await asyncio.sleep(0.01)
        running[0] -= 1
        seen.append(ticker)
        return {"status": "ok", "price": 100.0, "change_pct": 0.0, "quoted_at": "t", "source": "test"}

    monkeypatch.setattr(portfolio, "_cached_daily_quotes", cached)
    monkeypatch.setattr(portfolio, "_bounded_quote", bounded)
    holdings = [{"ticker": f"{i:06d}.KS", "currency": "KRW"} for i in range(25)]
    quotes = await portfolio._valuation_quotes(holdings)
    assert len(seen) == 20 and peak[0] <= 10
    skipped = [q for q in quotes.values() if q["status"] == "error"]
    assert len(skipped) == 5 and "20종목까지" in skipped[0]["reason"]
