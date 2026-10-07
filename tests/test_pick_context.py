"""추천 판단 근거(합의안 A): 기준일 갭·업종 집중·최근 지분공시. 참고 표시이며 추천 규칙과 무관하다."""
import pytest

from app.backend import kqs_recommendations, main
from app.backend.pick_context import as_of_gap, recent_activity, sector_concentration


def _bar(day, open_, close):
    return {"timestamp": day, "open": open_, "high": max(open_, close), "low": min(open_, close), "close": close}


def test_gap_uses_only_the_observed_as_of_session():
    bars = [_bar("2026-10-02", 100, 100.0), _bar("2026-10-06", 94, 95.0), _bar("2026-10-07", 80, 81.0)]
    gap = as_of_gap(bars, "2026-10-06")
    assert gap["status"] == "warning" and gap["gap_pct"] == -6.0 and gap["previous_date"] == "2026-10-02"
    assert as_of_gap(bars, "2026-10-02")["status"] == "unavailable"          # 전일 봉 없음
    assert as_of_gap(bars, "2026-10-08")["status"] == "unavailable"          # 기준일 봉 없음(다음 날 갭은 예측하지 않음)
    assert as_of_gap([_bar("2026-10-02", 100, 100.0), _bar("2026-10-06", 96, 97.0)], "2026-10-06")["status"] == "ok"


def test_sector_concentration_needs_two_distinct_symbols():
    groups = sector_concentration({"005380": "자동차", "000270": "자동차", "012330": "자동차", "005930": "반도체", "999999": None})
    assert groups == {"자동차": ["000270", "005380", "012330"]}


def test_recent_activity_is_a_neutral_count():
    items = [{"stock_code": "005380", "kind": "treasury_decision", "rcept_date": "20261002"},
             {"stock_code": "005380", "kind": "insider_decrease", "rcept_date": "20261006"},
             {"stock_code": "000270", "kind": "major_increase", "rcept_date": "20261006"}]
    summary = recent_activity(items, "005380")
    assert summary == {"count": 2, "kinds": {"자사주": 1, "임원·주요주주": 1}, "latest_date": "2026-10-06"}
    assert recent_activity(items, "005930") is None


@pytest.mark.asyncio
async def test_pick_context_endpoint_reads_the_publication(monkeypatch, client, make_user):
    payload = {"snapshot_id": "snap-1", "engine": {"data_as_of": "2026-10-06"}, "signals": [
        {"action": "entry", "selection_outcome": "selected", "symbol": "005380", "name": "현대차", "market": "KOSPI"},
        {"action": "entry", "selection_outcome": "selected", "symbol": "000270", "name": "기아", "market": "KOSPI"},
        {"action": "entry", "selection_outcome": "not_selected", "symbol": "111111", "market": "KOSDAQ"},
    ]}
    monkeypatch.setattr(kqs_recommendations, "read_local_recommendations", lambda: payload)
    main._PICK_CONTEXT_CACHE.clear()
    tickers = []

    async def fake_ohlcv(ticker, start, end, *, adjusted=True, prefer_cached=False):
        tickers.append((ticker, prefer_cached))
        return {"bars": [_bar("2026-10-02", 100, 100.0), _bar("2026-10-06", 93 if ticker.startswith("005380") else 99, 95.0)]}

    async def fake_activity():
        return {"items": [{"stock_code": "000270", "kind": "major_increase", "rcept_date": "20261006"}]}

    monkeypatch.setattr(main, "get_daily_ohlcv", fake_ohlcv)
    monkeypatch.setattr(main, "latest_market_activity", fake_activity)
    monkeypatch.setattr(main, "_stock_ksic", lambda code: "30121")
    monkeypatch.setattr(main, "sector_status", lambda code, ksic, rotation: {"sector_name": "자동차"})
    headers = await make_user("pick-context@example.com")
    body = (await client.get("/api/market/pick-context", headers=headers)).json()
    assert body["available"] is True and set(body["items"]) == {"005380", "000270"}
    assert sorted(tickers) == [("000270.KS", True), ("005380.KS", True)]
    assert body["items"]["005380"]["gap"]["status"] == "warning"
    assert body["items"]["000270"]["gap"]["status"] == "ok"
    assert body["items"]["005380"]["same_sector"] == [{"symbol": "000270", "name": "기아"}]
    assert body["items"]["000270"]["recent_activity"]["count"] == 1
    assert body["items"]["005380"]["recent_activity"] is None
