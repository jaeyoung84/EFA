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
