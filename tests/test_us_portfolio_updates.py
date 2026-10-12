"""Codex 수정: CODX-USPORT-001 (2026-10-12), 미국 자산·뉴스·SEC 공시 격리 회귀 테스트."""
from datetime import datetime, timezone

import httpx
import pytest

from app.backend import us_portfolio_updates as updates
from app.backend.db import get_db
from app.backend.routers import portfolio


def test_us_equity_and_cost_weighting_exclude_non_equity_symbols():
    holdings = [
        {"ticker": "AAPL", "name": "Apple", "quantity": 2, "average_price": 200, "currency": "USD"},
        {"ticker": "MSFT", "name": "Microsoft", "quantity": 1, "average_price": 100, "currency": "USD"},
        {"ticker": "KRW=X", "name": "환율", "quantity": 1, "average_price": 1400, "currency": "USD"},
        {"ticker": "005930.KS", "name": "삼성전자", "quantity": 1, "average_price": 70_000, "currency": "KRW"},
    ]
    rows = updates.weighted_us_holdings(holdings)
    assert [row["ticker"] for row in rows] == ["AAPL", "MSFT"]
    assert rows[0]["weight_pct"] == 80.0 and rows[0]["market"] == "US"


def test_yahoo_news_parser_rejects_unsafe_links_and_sorts_recent_first():
    payload = {"news": [
        {"uuid": "old", "title": "Apple old", "link": "https://news.example/old", "publisher": "Reuters", "providerPublishTime": 100},
        {"uuid": "bad", "title": "Unsafe", "link": "javascript:alert(1)", "providerPublishTime": 300},
        {"uuid": "other", "title": "Other stock", "link": "https://news.example/other", "providerPublishTime": 400, "relatedTickers": ["MSFT"]},
        {"uuid": "new", "title": "Apple new", "link": "https://news.example/new", "publisher": "AP", "providerPublishTime": 200},
    ]}
    items = updates.parse_yahoo_news(payload, "AAPL")
    assert [item["id"] for item in items] == ["new", "old"]
    assert all(item["market"] == "US" and item["link_kind"] == "original" for item in items)


def sec_payload():
    return {
        "cik": "320193", "name": "Apple Inc.",
        "filings": {"recent": {
            "form": ["10-Q", "UPLOAD", "8-K"],
            "accessionNumber": ["0000320193-26-000001", "x", "0000320193-26-000002"],
            "filingDate": ["2026-08-01", "2026-08-02", "2026-08-03"],
            "primaryDocument": ["aapl-20260630.htm", "x.txt", "aapl-8k.htm"],
            "primaryDocDescription": ["Quarterly report", "Correspondence", "Current report"],
        }},
    }


def test_sec_parser_keeps_investor_forms_and_builds_edgar_links():
    items = updates.parse_sec_filings(sec_payload(), "AAPL")
    assert [item["form"] for item in items] == ["10-Q", "8-K"]
    assert items[0]["url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/aapl-20260630.htm"
    assert items[0]["source"] == "SEC EDGAR"


@pytest.mark.asyncio
async def test_sec_client_uses_contact_user_agent_and_ticker_cik_mapping():
    seen = []

    def handler(request):
        seen.append(request)
        if str(request.url) == updates.SEC_TICKERS_URL:
            return httpx.Response(200, json={"0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."}})
        return httpx.Response(200, json=sec_payload())

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = updates.SecFilings("EFA Quant ops@example.com", http=http)
        items = await client.fetch("AAPL")
    finally:
        await http.aclose()
    assert items[0]["form"] == "10-Q" and len(seen) == 2
    assert seen[0].headers["User-Agent"] == "EFA Quant ops@example.com"
    assert str(seen[1].url).endswith("CIK0000320193.json")


def test_sec_client_stays_disabled_without_contact_email():
    client = updates.SecFilings("EFA Quant")
    assert "연락 이메일" in client.disabled_reason


@pytest.mark.asyncio
async def test_combined_kr_us_valuation_uses_current_fx(monkeypatch):
    async def quotes(_holdings):
        return {
            "005930.KS": {"status": "ok", "price": 70_000, "change_pct": 1, "quoted_at": "2026-10-12T00:00:00Z", "source": "market_data_cache_daily"},
            "AAPL": {"status": "ok", "price": 200, "change_pct": 2, "quoted_at": "2026-10-12T00:00:00Z", "source": "market_data_cache_daily"},
            "KRW=X": {"status": "ok", "price": 1400, "change_pct": 0, "quoted_at": "2026-10-12T00:00:00Z", "source": "market_data_cache_daily"},
        }

    monkeypatch.setattr(portfolio, "_valuation_quotes", quotes)
    monkeypatch.setattr(portfolio, "live_quote_provider_status", lambda: {"stream_session_open": False})
    result = await portfolio._compute_valuation({"cash_krw": 100_000, "holdings": [
        {"ticker": "005930.KS", "name": "삼성전자", "quantity": 2, "average_price": 60_000, "currency": "KRW", "sector": "반도체"},
        {"ticker": "AAPL", "name": "Apple", "quantity": 3, "average_price": 180, "currency": "USD", "sector": "Technology"},
    ]})
    assert result["available"] is True and result["fx_usd_krw"] == 1400
    assert result["market_totals"]["KR"]["value_krw"] == 140_000
    assert result["market_totals"]["US"]["value_krw"] == 840_000
    assert result["value_krw"] == 1_080_000
    assert {position["market"] for position in result["positions"]} == {"KR", "US"}


@pytest.mark.asyncio
async def test_us_news_and_disclosures_api_are_user_scoped_cache_reads(client, make_user):
    assert (await client.get("/api/portfolio/disclosures")).status_code == 401
    headers = await make_user("us-updates@test.com")
    saved = await client.put("/api/portfolio", headers=headers, json={"cash_krw": 10_000, "holdings": [
        {"ticker": "AAPL", "name": "Apple", "quantity": 2, "average_price": 190, "currency": "USD", "sector": "Technology"},
    ]})
    assert saved.status_code == 200
    db = get_db()
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    await db[updates.US_NEWS_CACHE_COLLECTION].insert_one({
        "_id": "AAPL", "name": "Apple", "fetched_at": now, "error": None,
        "items": [{"id": "n1", "ticker": "AAPL", "title": "Apple headline", "source": "Reuters",
                   "published_at": now.isoformat(), "url": "https://news.example/aapl", "link_kind": "original", "market": "US"}],
    })
    await db[updates.SEC_CACHE_COLLECTION].insert_one({
        "_id": "AAPL", "name": "Apple", "fetched_at": now, "error": None,
        "items": [{"id": "f1", "ticker": "AAPL", "company": "Apple Inc.", "form": "10-Q",
                   "title": "Quarterly report", "filed_at": "2026-08-01", "url": "https://www.sec.gov/aapl", "source": "SEC EDGAR", "market": "US"}],
    })
    try:
        news = (await client.get("/api/portfolio/news", headers=headers)).json()
        disclosures = (await client.get("/api/portfolio/disclosures", headers=headers)).json()
    finally:
        await db[updates.US_NEWS_CACHE_COLLECTION].delete_many({})
        await db[updates.SEC_CACHE_COLLECTION].delete_many({})
    assert news["holdings"][0]["ticker"] == "AAPL" and news["items"][0]["market"] == "US"
    assert disclosures["items"][0]["form"] == "10-Q" and disclosures["source"] == "sec_edgar_submissions"


def test_scheduler_registers_us_updates_without_touching_orders():
    from app.backend import scheduler
    source = open(scheduler.__file__).read()
    assert 'id="collect_us_portfolio_updates"' in source
    assert "collect_us_portfolio_updates" in source and "hours=1" in source


@pytest.mark.asyncio
async def test_portfolio_rejects_market_currency_mismatch(client, make_user):
    headers = await make_user("currency-market@test.com")
    us_as_krw = await client.put("/api/portfolio", headers=headers, json={"holdings": [
        {"ticker": "AAPL", "name": "Apple", "quantity": 1, "average_price": 200, "currency": "KRW"},
    ]})
    kr_as_usd = await client.put("/api/portfolio", headers=headers, json={"holdings": [
        {"ticker": "005930.KS", "name": "삼성전자", "quantity": 1, "average_price": 70_000, "currency": "USD"},
    ]})
    assert us_as_krw.status_code == 422 and "USD" in us_as_krw.json()["detail"]
    assert kr_as_usd.status_code == 422 and "KRW" in kr_as_usd.json()["detail"]
