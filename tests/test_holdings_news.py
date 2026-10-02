"""내 보유종목 주요 뉴스: 비중 정렬, 한투 뉴스 관련성 필터(실응답 형태), 원문 링크 매칭, 캐시 수집·읽기, API.

한투·네이버에는 접속하지 않는다(가짜 서비스·MockTransport).
"""
from datetime import datetime, timezone

import httpx
import pytest

from app.backend import holdings_news as hn
from app.backend import kis_quotes
from app.backend.db import get_db
from app.backend.kis_quotes import KisQuotes


def kis_row(title, codes, *, dt="20261002", tm="101500", dorg="연합뉴스", srno=None):
    row = {"hts_pbnt_titl_cntt": title, "data_dt": dt, "data_tm": tm, "dorg": dorg,
           "cntt_usiq_srno": srno or f"{dt}{tm}{abs(hash(title)) % 10**8}", "news_ofer_entp_code": "2"}
    for index, code in enumerate(codes, start=1):
        row[f"iscd{index}"] = code
    return row


@pytest.fixture(autouse=True)
def _reset():
    hn._pending.clear()
    yield
    kis_quotes._set_service_for_tests(None)


def test_weighted_holdings_orders_by_value_and_falls_back_to_cost():
    holdings = [
        {"ticker": "000660.KS", "name": "SK하이닉스", "quantity": 10, "average_price": 100_000},
        {"ticker": "034020.KS", "name": "두산에너빌리티", "quantity": 100, "average_price": 20_000},
        {"ticker": "263750.KQ", "name": "펄어비스", "quantity": 10, "average_price": 30_000},
        {"ticker": "AAPL", "name": "Apple", "quantity": 5, "average_price": 200},
    ]
    rows = hn.weighted_holdings(holdings, {"000660.KS": 300_000.0, "034020.KS": 25_000.0})
    assert [r["code"] for r in rows] == ["000660", "034020", "263750"]       # 300만 > 250만 > 30만(취득가)
    assert [r["basis"] for r in rows] == ["market", "market", "cost"]
    assert round(sum(r["weight_pct"] for r in rows)) == 100 and rows[0]["weight_pct"] == 51.72


def test_relevant_news_keeps_stock_news_and_drops_ranking_lists():
    rows = [
        kis_row("SK하이닉스, HBM4 양산 돌입", ["000660"], tm="101500"),
        kis_row("“‘삼전닉스’ 지금 팔면 후회한다”", ["005930", "000660"], tm="194000"),       # 첫 관련 종목이 다름 → 제외
        kis_row("주식대차거래 잔고감소 상위종목(코스피)", ["000660"], tm="180516"),         # 순위표 → 제외
        kis_row("반도체 장비 수주 확대", ["000660", "1", "2", "3", "4", "5"], tm="090000"),  # 관련 종목 6개 → 제외
        kis_row("외국인, SK하이닉스 3일째 순매수", ["005930", "000660"], tm="140000"),       # 제목에 종목명 → 유지
        kis_row("SK하이닉스, HBM4 양산 돌입", ["000660"], tm="101600", dorg="다른통신"),     # 같은 제목 → 하나만
        kis_row("무관한 기사", ["005930"], tm="150000"),
    ]
    items = hn.relevant_news(rows, "000660", "SK하이닉스")
    assert [i["title"] for i in items] == ["외국인, SK하이닉스 3일째 순매수", "SK하이닉스, HBM4 양산 돌입"]   # 최신순
    assert items[0]["published_at"] == "2026-10-02T14:00:00+09:00" and items[0]["source"] == "연합뉴스"


def test_best_link_matches_title_and_rejects_unsafe_or_distant():
    title, when = "SK하이닉스, HBM4 양산 돌입", "2026-10-02T10:15:00+09:00"
    candidates = [
        {"title": "<b>삼성전자</b> 실적 발표", "originallink": "https://a.example/1", "pubDate": "Fri, 02 Oct 2026 10:20:00 +0900"},
        {"title": "<b>SK하이닉스</b>, HBM4 양산 돌입", "originallink": "https://news.example/hbm4",
         "link": "https://n.news.naver.com/x", "pubDate": "Fri, 02 Oct 2026 10:16:00 +0900"},
    ]
    assert hn.best_link(title, when, candidates) == "https://news.example/hbm4"
    assert hn.best_link(title, when, [{**candidates[1], "originallink": "javascript:alert(1)"}]) == "https://n.news.naver.com/x"
    assert hn.best_link(title, when, [{**candidates[1], "pubDate": "Mon, 21 Sep 2026 10:00:00 +0900"}]) is None
    assert hn.best_link(title, when, candidates[:1]) is None


@pytest.mark.asyncio
async def test_naver_search_without_permission_disables_itself():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"errorCode": "024"})

    naver = hn.NaverNewsSearch("id", "secret", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await naver.search("제목") == [] and "권한 없음" in naver.disabled_reason
    assert await naver.search("다른 제목") == [] and len(calls) == 1


class FakeKis(KisQuotes):
    def __init__(self, rows_by_code):
        super().__init__("key", "secret")
        self.rows_by_code, self.calls = rows_by_code, []

    async def news_titles(self, code):
        self.calls.append(code)
        if code == "999999":
            raise kis_quotes.KisError("EGW00201 초당 거래건수를 초과하였습니다.")
        return self.rows_by_code.get(code, [])


def naver_transport(calls):
    def handler(request):
        calls.append(request.url.params["query"])
        return httpx.Response(200, json={"items": [{"title": "<b>SK하이닉스</b>, HBM4 양산 돌입",
                                                    "originallink": "https://news.example/hbm4",
                                                    "pubDate": "Fri, 02 Oct 2026 10:16:00 +0900"}]})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_collect_caches_news_resolves_links_once_and_records_errors(monkeypatch):
    service = FakeKis({"000660": [kis_row("SK하이닉스, HBM4 양산 돌입", ["000660"], srno="S1"),
                                  kis_row("SK하이닉스 목표가 상향", ["000660"], srno="S2", tm="090000")]})
    kis_quotes._set_service_for_tests(service)
    naver_calls = []
    real_search = hn.NaverNewsSearch
    monkeypatch.setattr(hn, "NaverNewsSearch", lambda: real_search("id", "secret", http=naver_transport(naver_calls)))
    db = get_db()
    try:
        result = await hn.collect_codes({"000660": "SK하이닉스", "999999": "오류종목"})
        assert result["collected"] == 1 and result["failed"] == 1
        doc = await db[hn.CACHE_COLLECTION].find_one({"_id": "000660"})
        assert [i["id"] for i in doc["items"]] == ["S1", "S2"]
        assert doc["items"][0]["url"] == "https://news.example/hbm4" and doc["items"][0]["link_kind"] == "original"
        assert doc["items"][1]["url"] == "https://m.stock.naver.com/domestic/stock/000660/news"   # 제목 불일치 → 종목 뉴스 목록
        assert "EGW00201" in (await db[hn.CACHE_COLLECTION].find_one({"_id": "999999"}))["error"]
        await hn.collect_codes({"000660": "SK하이닉스"})
        assert len(naver_calls) == 2                       # 두 번째 수집은 링크를 다시 찾지 않는다
        assert service.calls.count("000660") == 2
    finally:
        await db[hn.CACHE_COLLECTION].delete_many({})
        await db[hn.LINK_COLLECTION].delete_many({})


@pytest.mark.asyncio
async def test_collect_skips_outside_hours_and_without_keys(monkeypatch):
    class Late(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 2, 23, 30, tzinfo=hn.KST)

    monkeypatch.setattr(hn, "datetime", Late)
    assert (await hn.collect_holdings_news())["skipped"] == "수집 시간 외"
    kis_quotes._set_service_for_tests(KisQuotes("", ""))
    assert (await hn.collect_codes({"000660": "x"}))["skipped"] == "한투 키 없음"


@pytest.mark.asyncio
async def test_news_api_reads_cache_by_weight_and_schedules_new_holdings(client, make_user, monkeypatch):
    assert (await client.get("/api/portfolio/news")).status_code == 401
    headers = await make_user("news@test.com")
    put = await client.put("/api/portfolio", headers=headers, json={"cash_krw": 0, "holdings": [
        {"ticker": "263750.KQ", "name": "펄어비스", "quantity": 10, "average_price": 30_000, "currency": "KRW"},
        {"ticker": "000660.KS", "name": "SK하이닉스", "quantity": 10, "average_price": 300_000, "currency": "KRW"},
        {"ticker": "034020.KS", "name": "두산에너빌리티", "quantity": 10, "average_price": 20_000, "currency": "KRW"},
    ]})
    assert put.status_code == 200, put.text
    db = get_db()
    now = datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)
    item = lambda code, n, t: {"id": f"{code}-{n}", "code": code, "title": f"{code} 뉴스 {n}", "source": "연합뉴스",  # noqa: E731
                               "published_at": t, "url": "https://news.example/a", "link_kind": "original"}
    await db[hn.CACHE_COLLECTION].insert_many([
        {"_id": "263750", "name": "펄어비스", "fetched_at": now, "error": None,
         "items": [item("263750", 1, "2026-10-02T11:00:00+09:00")]},
        {"_id": "000660", "name": "SK하이닉스", "fetched_at": now, "error": None,
         "items": [item("000660", i, f"2026-10-02T1{i}:00:00+09:00") for i in range(5)]},
    ])
    scheduled = []
    monkeypatch.setattr(hn, "schedule_missing", lambda codes: scheduled.append(codes))
    try:
        data = (await client.get("/api/portfolio/news", headers=headers)).json()
    finally:
        await db[hn.CACHE_COLLECTION].delete_many({})
    assert [h["code"] for h in data["holdings"]] == ["000660", "263750", "034020"]
    assert [i["code"] for i in data["items"]] == ["000660"] * 3 + ["263750"]          # 비중 순, 종목당 3건
    assert data["items"][0]["name"] == "SK하이닉스" and data["items"][0]["weight_pct"] > 80
    assert data["pending"] == ["034020"] and scheduled == [{"034020": "두산에너빌리티"}]
    assert data["fetched_at"].startswith("2026-10-02T01:00")


def test_scheduler_registers_the_news_job_within_5_to_10_minutes():
    from app.backend import scheduler
    assert 5 <= scheduler.HOLDINGS_NEWS_INTERVAL_MINUTES <= 10
    source = (scheduler.__file__ and open(scheduler.__file__).read())
    assert 'id="collect_holdings_news"' in source and "max_instances=1" in source
