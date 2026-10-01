from app.backend import instrument_catalog
from app.backend import main


SAMPLE_KIND_HTML = """
<html><body><table>
<tr><th>회사명</th><th>시장구분</th><th>종목코드</th></tr>
<tr><td>삼성전자</td><td>유가</td><td>005930</td></tr>
<tr><td>카카오</td><td>유가</td><td>035720</td></tr>
<tr><td>카카오게임즈</td><td>코스닥</td><td>293490</td></tr>
<tr><td>코넥스예제</td><td>코넥스</td><td>123456</td></tr>
</table></body></html>
""".encode("euc-kr")


def test_kind_catalog_parser_maps_market_suffixes():
    rows = instrument_catalog._parse_kind_html(SAMPLE_KIND_HTML)
    assert [(row["name"], row["ticker"]) for row in rows] == [
        ("삼성전자", "005930.KS"),
        ("카카오", "035720.KS"),
        ("카카오게임즈", "293490.KQ"),
    ]


def test_company_search_prefers_exact_name(monkeypatch):
    monkeypatch.setattr(
        instrument_catalog,
        "get_krx_catalog",
        lambda: instrument_catalog._parse_kind_html(SAMPLE_KIND_HTML),
    )
    rows = instrument_catalog.search_krx_instruments("카카오")
    assert [row["ticker"] for row in rows] == ["035720.KS", "293490.KQ"]


def test_chart_search_deduplicates_krx_and_static_alias(monkeypatch):
    monkeypatch.setattr(
        main,
        "search_krx_instruments",
        lambda query, limit=12: [{
            "ticker": "005930.KS", "code": "005930", "name": "삼성전자",
            "exchange": "KOSPI", "type": "EQUITY", "source": "KRX_KIND",
        }],
    )
    monkeypatch.setattr(main.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    response = main.home_chart_search("삼성전자")
    assert [row["ticker"] for row in response["items"]] == ["005930.KS"]
    assert response["items"][0]["source"] == "KRX_KIND"


# ── EINT-018: engine-published KRX listing as the primary name-search source ──
import io
import json as _json
import os
import time as _time

import pytest
from fastapi import HTTPException


def _write_kqs_catalog(path, items, as_of="2026-09-30"):
    path.write_text(_json.dumps({"schema": "kqs-instrument-catalog/v1", "as_of": as_of, "count": len(items),
                                 "items_sha256": "x", "items": items}, ensure_ascii=False), encoding="utf-8")


KQS_ITEMS = [
    {"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "section": "", "market_cap": 900},
    {"symbol": "035420", "name": "NAVER", "market": "KOSPI", "section": "", "market_cap": 300},
    {"symbol": "086520", "name": "에코프로", "market": "KOSDAQ", "section": "", "market_cap": 120},
    {"symbol": "247540", "name": "에코프로비엠", "market": "KOSDAQ", "section": "", "market_cap": 200},
    {"symbol": "010170", "name": "대한광통신", "market": "KOSPI", "section": "", "market_cap": 5},
]


@pytest.fixture
def kqs_catalog(tmp_path, monkeypatch):
    path = tmp_path / "instrument-catalog.json"
    _write_kqs_catalog(path, KQS_ITEMS)
    monkeypatch.setenv("KQS_INSTRUMENT_CATALOG", str(path))
    instrument_catalog._reset_catalog_cache_for_tests()
    def kind_blocked():
        raise AssertionError("KIND must not be used when the engine catalog exists")
    monkeypatch.setattr(instrument_catalog, "_download_catalog", kind_blocked)
    yield path
    instrument_catalog._reset_catalog_cache_for_tests()


def test_engine_catalog_is_primary_with_market_suffix_alias_and_cap_order(kqs_catalog):
    rows = instrument_catalog.search_krx_instruments("에코프로")
    assert [(row["name"], row["ticker"]) for row in rows] == [("에코프로", "086520.KQ"), ("에코프로비엠", "247540.KQ")]
    assert instrument_catalog.search_krx_instruments("네이버")[0]["ticker"] == "035420.KS"
    assert instrument_catalog.search_krx_instruments("대한광통신")[0]["source"] == "KQS_KRX_DAILY"
    assert instrument_catalog.resolve_krx_name("네이버")["code"] == "035420"
    assert instrument_catalog.resolve_krx_name("에코") is None  # ambiguous partial match is not guessed


def test_engine_catalog_reloads_when_the_file_changes(kqs_catalog):
    assert instrument_catalog.search_krx_instruments("신규상장") == []
    _write_kqs_catalog(kqs_catalog, KQS_ITEMS + [{"symbol": "999990", "name": "신규상장", "market": "KOSDAQ",
                                                  "section": "", "market_cap": 1}], as_of="2026-10-01")
    stamp = _time.time() + 5
    os.utime(kqs_catalog, (stamp, stamp))
    assert instrument_catalog.search_krx_instruments("신규상장")[0]["ticker"] == "999990.KQ"


@pytest.mark.asyncio
async def test_instruments_endpoint_and_name_resolution(kqs_catalog, client):
    data = (await client.get("/api/market/instruments", params={"q": "삼성"})).json()
    assert data["items"][0] == {"ticker": "005930.KS", "code": "005930", "name": "삼성전자", "market": "KOSPI",
                                "source": "KQS_KRX_DAILY"}
    assert data["as_of"] == "2026-09-30"
    assert main._resolve_ticker_or_name("대한광통신") == "010170.KS"
    assert main._resolve_ticker_or_name("AAPL") == "AAPL"
    assert main._resolve_ticker_or_name("005930.KS") == "005930.KS"
    with pytest.raises(HTTPException) as caught:
        main._resolve_ticker_or_name("없는회사이름")
    assert caught.value.status_code == 404


def test_dart_search_drops_delisted_and_uses_listing_market(kqs_catalog, monkeypatch):
    monkeypatch.setattr(main, "_load_dart_corp_codes", lambda: [
        {"corp_code": "1", "corp_name": "에코프로", "stock_code": "086520", "modify_date": ""},
        {"corp_code": "2", "corp_name": "에코프로옛상장", "stock_code": "123123", "modify_date": ""},
        {"corp_code": "3", "corp_name": "NAVER", "stock_code": "035420", "modify_date": ""},
    ])
    monkeypatch.setattr(main, "_resolve_krx_yahoo_ticker", lambda code: (_ for _ in ()).throw(AssertionError("no Yahoo probing")))
    result = main.dart_company_search(main.DartCompanySearchRequest(company_name="에코프로", limit=10))
    assert [(r["corp_name"], r["ticker"], r["market"]) for r in result["results"]] == [("에코프로", "086520.KQ", "KOSDAQ")]
    naver = main.dart_company_search(main.DartCompanySearchRequest(company_name="네이버", limit=10))
    assert [r["corp_name"] for r in naver["results"]] == ["NAVER"]


def test_dart_invalid_key_is_reported_in_plain_language(monkeypatch):
    main._load_dart_corp_codes.cache_clear()
    monkeypatch.setenv("DART_API_KEY", "x" * 40)
    xml = '<?xml version="1.0"?><result><status>010</status><message>등록되지 않은 인증키입니다.</message></result>'.encode()
    monkeypatch.setattr(main.urllib.request, "urlopen", lambda *_a, **_k: io.BytesIO(xml))
    with pytest.raises(HTTPException) as caught:
        main._load_dart_corp_codes()
    main._load_dart_corp_codes.cache_clear()
    assert caught.value.status_code == 503
    assert "DART 인증키를 확인" in caught.value.detail and "<result>" not in caught.value.detail


def test_peer_rows_resolve_names_and_report_unknown(kqs_catalog, monkeypatch):
    class Ticker:
        def __init__(self, symbol): self.info = {"marketCap": 1e12, "revenueGrowth": 0.1}
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", Ticker)
    result = main.industry_peer(main.PeerRequest(tickers={"대한광통신": "", "없는회사이름": "", "엔비디아": "NVDA"}))
    rows = {row["company"]: row for row in result["rows"]}
    assert rows["대한광통신"]["ticker"] == "010170.KS"
    assert "찾지 못했습니다" in rows["없는회사이름"]["error"]
    assert rows["엔비디아"]["ticker"] == "NVDA"


def test_ranking_endpoints_reuse_results_for_a_minute(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_market_top_ranked_components",
                        lambda comps, limit, universe: calls.append(universe) or {"items": [], "universe": universe})
    main._ranking_cache.clear()
    first, second = main.market_top_gainers(20), main.market_top_gainers(20)
    assert (first["cached"], second["cached"], len(calls)) == (False, True, 1)
    main.market_top_sobujang(20)
    assert len(calls) == 2
    main._ranking_cache.clear()
