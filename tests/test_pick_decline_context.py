"""EINT-058 추천 종목 하락 구간의 공시·뉴스 동반 여부: 날짜 창, 실패와 '없음' 구분, 뉴스 일부만 확인, 인증 API."""
import pytest

from app.backend import kqs_recommendations, main
from app.backend.pick_context import decline_context, decline_window
from app.backend.stock_risk import classify_disclosure

DAYS = ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
CLOSES = [100, 101, 100, 96, 90, 91, 89, 88]


def _bars():
    return [{"timestamp": d, "open": c, "high": c, "low": c, "close": float(c)} for d, c in zip(DAYS, CLOSES)]


def test_window_is_last_five_sessions_and_finds_worst_day():
    window = decline_window(_bars(), "2026-10-16")
    assert window["start"] == "2026-10-12" and window["end"] == "2026-10-16"
    assert window["return_pct"] == -12.0                      # 10-08 종가 100 → 10-16 종가 88
    assert window["worst_day"] == {"date": "2026-10-13", "pct": -6.25}
    assert decline_window(_bars(), "2026-10-08") is None      # 앞쪽 봉 부족
    assert decline_window(_bars(), "2026-10-17") is None      # 기준일 봉 없음


def test_context_filters_by_date_tags_and_separates_failure_from_none():
    window = decline_window(_bars(), "2026-10-16")
    disclosures = [{"rcept_dt": "20261013", "report_nm": "영업(잠정)실적(공정공시)"},
                   {"rcept_dt": "20261014", "report_nm": "유상증자결정"},
                   {"rcept_dt": "20261002", "report_nm": "기업설명회(IR)개최"}]           # 창 밖
    news = [{"published_at": "2026-10-13T09:10:00+09:00", "title": "실적 부진", "source": "A"},
            {"published_at": "2026-10-17T08:00:00+09:00", "title": "기준일 이후", "source": "B"}]
    ctx = decline_context(window, disclosures=disclosures, news=news, classify=classify_disclosure)
    assert ctx["status"] == "ok" and [d["date"] for d in ctx["disclosures"]] == ["2026-10-14", "2026-10-13"]
    assert ctx["disclosures"][1]["tag"] == "실적" and ctx["disclosures"][0]["tag"]           # 증자는 위험 분류
    assert [n["title"] for n in ctx["news"]] == ["실적 부진"]
    quiet = decline_context(window, disclosures=[], news=[], classify=classify_disclosure)
    assert quiet["disclosures"] == [] and quiet["news"] == []                                  # 확인했고 없음
    failed = decline_context(window, disclosures=None, news=[], disclosure_error="DART 조회 실패: x")
    assert failed["disclosures"] is None and failed["disclosure_error"] and failed["status"] == "ok"
    assert decline_context(window, disclosures=None, news=None)["status"] == "unavailable"
    assert decline_context(None, disclosures=[], news=[])["status"] == "unavailable"


@pytest.mark.asyncio
async def test_endpoint_marks_partial_news_and_requires_login(monkeypatch, client, make_user):
    payload = {"snapshot_id": "snap-d", "engine": {"data_as_of": "2026-10-16"}, "signals": [
        {"action": "entry", "selection_outcome": "selected", "symbol": "005380", "name": "현대차", "market": "KOSPI"}]}
    monkeypatch.setattr(kqs_recommendations, "read_local_recommendations", lambda: payload)
    main._PICK_DECLINE_CACHE.clear()

    async def fake_ohlcv(ticker, start, end, *, adjusted=True, prefer_cached=False):
        return {"bars": _bars()}

    class FakeKis:
        enabled = True

        async def news_titles(self, code):
            # 40건이 모두 구간 시작(10-12)보다 늦다 → 일부만 확인
            return [{"hts_pbnt_titl_cntt": f"현대차 소식 {i}", "iscd1": code, "data_dt": "20261015", "data_tm": "090000",
                     "cntt_usiq_srno": str(i), "dorg": "X"} for i in range(40)]

    monkeypatch.setattr(main, "get_daily_ohlcv", fake_ohlcv)
    monkeypatch.setattr(main, "kis_service", lambda: FakeKis())
    monkeypatch.setattr(main, "_dart_disclosures_between", lambda code, start, end: [{"rcept_dt": "20261013", "report_nm": "영업(잠정)실적(공정공시)"}])
    assert (await client.get("/api/market/pick-decline-context")).status_code == 401
    headers = await make_user("pick-decline@example.com")
    body = (await client.get("/api/market/pick-decline-context", headers=headers)).json()
    item = body["items"]["005380"]
    assert body["available"] and item["status"] == "ok" and item["news_partial"] is True
    assert item["disclosures"][0]["tag"] == "실적" and item["news"]
