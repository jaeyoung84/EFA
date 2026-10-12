"""포트폴리오 입력 검색: 미국 종목 한글 별칭, 선물·지수·해외 상장 제외, 정확 일치 표시와 순서."""
import io
import json

from app.backend import main
from app.backend import us_search_aliases as us


def test_base_name_and_alias_matching():
    assert us.base_name("Tesla, Inc.") == "tesla" and us.base_name("Amazon.com, Inc.") == "amazon"
    assert [i["ticker"] for i in us.alias_items("테슬라")] == ["TSLA"]
    assert [i["ticker"] for i in us.alias_items("애")] == ["AAPL"]
    assert us.alias_items("델타항공") == []          # 별칭이 입력 속에 있다고 고르지 않는다
    assert us.alias_items("a") == []                 # 영문 한 글자는 별칭 검색 안 함
    assert us.mark_and_rank("버크셔", us.alias_items("버크셔"))[0]["exact"] is True


def test_holding_candidates_drop_futures_indices_and_foreign_listings():
    keep = [{"ticker": "TSLA", "quote_type": "EQUITY"}, {"ticker": "SPY", "quote_type": "ETF"}, {"ticker": "BRK-B", "quote_type": "EQUITY"},
            {"ticker": "005930.KS"}, {"ticker": "247540.KQ"}]
    drop = [{"ticker": "STSLA=F", "quote_type": "FUTURE"}, {"ticker": "^GSPC", "quote_type": "INDEX"}, {"ticker": "AAPL.TO", "quote_type": "EQUITY"},
            {"ticker": "BTC-USD", "quote_type": "CRYPTOCURRENCY"}, {"ticker": "VFIAX", "quote_type": "MUTUALFUND"}]
    assert all(us.holding_candidate(i) for i in keep) and not any(us.holding_candidate(i) for i in drop)


def test_exact_items_first_and_marked():
    items = [{"ticker": "STSLA", "name": "Some Tesla ETF", "quote_type": "ETF"}, {"ticker": "TSLA", "name": "Tesla, Inc.", "quote_type": "EQUITY"},
             {"ticker": "TSLL", "name": "Direxion TSLA Bull", "quote_type": "ETF"}]
    ranked = us.mark_and_rank("tesla", items)
    assert ranked[0]["ticker"] == "TSLA" and ranked[0]["exact"] and not ranked[1]["exact"]
    assert us.mark_and_rank("tsla", items)[0]["ticker"] == "TSLA"


def _yahoo(quotes):
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return lambda *a, **k: Response(json.dumps({"quotes": quotes}).encode())


def test_api_holdings_scope(monkeypatch):
    monkeypatch.setattr(main, "search_krx_instruments", lambda q, limit=12: [])
    monkeypatch.setattr(main.urllib.request, "urlopen", _yahoo([
        {"symbol": "STSLA=F", "shortname": "Tesla Inc Single Stock futures", "quoteType": "FUTURE", "exchange": "OCM"},
        {"symbol": "TSLA", "shortname": "Tesla, Inc.", "quoteType": "EQUITY", "exchange": "NMS"},
        {"symbol": "YTSL.NE", "shortname": "TESLA YIELD SHARES", "quoteType": "ETF", "exchange": "NEO"},
        {"symbol": "TSLL", "shortname": "Direxion Daily TSLA Bull 2X", "quoteType": "ETF", "exchange": "NGM"}]))
    items = main.home_chart_search("tesla", scope="holdings")["items"]
    assert [i["ticker"] for i in items] == ["TSLA", "TSLL"] and items[0]["exact"] is True
    korean = main.home_chart_search("테슬라", scope="holdings")["items"]
    assert korean[0]["ticker"] == "TSLA" and korean[0]["exact"] is True
    general = main.home_chart_search("tesla")["items"]
    assert "STSLA=F" in {i["ticker"] for i in general}          # 일반 차트 검색은 기존처럼 선물도 보여 준다
