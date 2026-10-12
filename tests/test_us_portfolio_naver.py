"""미국 보유종목 네이버 뉴스(한글)와 캐시 없는 종목 즉시 수집(Claude Code, 사용자 승인 2026-10-12)."""
import asyncio

import pytest

from app.backend import us_portfolio_updates as updates
from app.backend.db import get_db


def test_naver_query_uses_korean_alias_ticker_for_etf_or_company_name():
    assert updates.naver_query("TSLA", "Tesla, Inc.") == "테슬라"
    assert updates.naver_query("SCHD", "Schwab U.S. Dividend Equity ETF") == "SCHD"
    assert updates.naver_query("RKLB", "Rocket Lab USA, Inc.") == "Rocket Lab USA"


def test_naver_parser_keeps_only_titles_about_the_company():
    rows = [
        {"title": "<b>테슬라</b>, 로보택시 확대", "originallink": "https://www.hankyung.com/a", "link": "https://n.news.naver.com/a",
         "pubDate": "Mon, 12 Oct 2026 14:00:00 +0900"},
        {"title": "TSLA 옵션 거래 급증", "originallink": "", "link": "https://n.news.naver.com/b", "pubDate": "Mon, 12 Oct 2026 13:00:00 +0900"},
        {"title": "코스피 마감 시황", "originallink": "https://x.com/c", "pubDate": "Mon, 12 Oct 2026 12:00:00 +0900"},
        {"title": "테슬라, 로보택시 확대", "originallink": "https://other.com/d", "pubDate": "Mon, 12 Oct 2026 11:00:00 +0900"},
        {"title": "테슬라 신차", "originallink": "javascript:alert(1)", "pubDate": "Mon, 12 Oct 2026 10:00:00 +0900"},
    ]
    items = updates.parse_naver_news(rows, "TSLA", "테슬라")
    assert [i["title"] for i in items] == ["테슬라, 로보택시 확대", "TSLA 옵션 거래 급증"]
    assert items[0]["source"] == "hankyung.com" and items[0]["provider"] == "naver" and items[0]["published_at"].startswith("2026-10-12T05:00")


def test_interleave_alternates_sources():
    assert [i["id"] for i in updates.interleave([{"id": "n1"}, {"id": "n2"}], [{"id": "y1"}], 10)] == ["n1", "y1", "n2"]


class FakeYahoo:
    def __init__(self, fail=False): self.fail = fail
    async def fetch(self, ticker):
        if self.fail:
            raise ValueError("yahoo down")
        return [{"id": "y1", "ticker": ticker, "title": "Tesla headline", "published_at": "2026-10-12T00:00:00+00:00", "market": "US"}]


class FakeNaver:
    disabled_reason = None
    def __init__(self): self.queries = []
    async def search(self, query, display=5, sort="sim"):
        self.queries.append((query, display, sort))
        return [{"title": "테슬라 급등", "originallink": "https://news.example/t", "pubDate": "Mon, 12 Oct 2026 14:00:00 +0900"}]


@pytest.mark.asyncio
async def test_collect_merges_naver_and_yahoo_and_survives_one_failure():
    sec = updates.SecFilings("EFA Quant")      # 이메일 없음 -> SEC는 비활성
    naver = FakeNaver()
    result = await updates.collect_us_tickers({"TSLA": "Tesla, Inc."}, yahoo=FakeYahoo(), sec=sec, naver=naver)
    doc = await get_db()[updates.US_NEWS_CACHE_COLLECTION].find_one({"_id": "TSLA"})
    assert result["news_collected"] == 1 and naver.queries == [("테슬라", 20, "date")]
    assert [i.get("provider", "yahoo") for i in doc["items"]] == ["naver", "yahoo"] and doc["source_counts"] == {"yahoo": 1, "naver": 1}
    await updates.collect_us_tickers({"TSLA": "Tesla, Inc."}, yahoo=FakeYahoo(fail=True), sec=sec, naver=FakeNaver())
    doc = await get_db()[updates.US_NEWS_CACHE_COLLECTION].find_one({"_id": "TSLA"})
    assert doc["error"] is None and "yahoo down" in doc["partial_error"] and len(doc["items"]) == 1


@pytest.mark.asyncio
async def test_missing_cache_triggers_one_background_collection(client, make_user, monkeypatch):
    calls = []

    async def fake_collect(tickers, **kwargs):
        calls.append(dict(tickers))
        await asyncio.sleep(0.05)          # 실제 수집처럼 시간이 걸리는 동안 두 번째 화면 요청이 온다
    monkeypatch.setattr(updates, "collect_us_tickers", fake_collect)
    headers = await make_user("us-immediate@test.com")
    await client.put("/api/portfolio", headers=headers, json={"cash_krw": 0, "holdings": [
        {"ticker": "TSLA", "name": "Tesla, Inc.", "quantity": 1, "average_price": 400, "currency": "USD"}]})
    await client.get("/api/portfolio/news", headers=headers)
    await client.get("/api/portfolio/disclosures", headers=headers)    # 같은 종목은 다시 예약하지 않는다
    await asyncio.sleep(0.2)
    assert calls == [{"TSLA": "Tesla, Inc."}] and not updates._pending_us
