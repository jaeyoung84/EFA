"""EINT-049 금일 소부장: 네이버 테마 3개 합집합, 거래소 등락률 사용, 중복 종목 병합, 정렬·전체 보기, 캐시·지연, API."""
import pytest

from app.backend import main, sobujang


def row(code, name, pct, sosok="1", end="stock", direction="RISING", value="1,000", cap="5,000"):
    return {"itemCode": code, "stockName": name, "stockEndType": end, "sosok": sosok, "closePrice": "106,200",
            "compareToPreviousClosePrice": "3,500", "compareToPreviousPrice": {"name": direction},
            "fluctuationsRatio": pct, "accumulatedTradingValue": value, "marketValue": cap}


THEMES = {12: [row("240810", "원익IPS", "3.41"), row("042700", "한미반도체", "-1.20", sosok="0", direction="FALLING"),
               row("000001", "ETF", "9.9", end="etf")],
          14: [row("240810", "원익IPS", "3.41"), row("357780", "솔브레인", "0.00", direction="EVEN")],
          608: [row("222800", "심텍", "5.5", value="9,999")]}


def fake_fetch(url):
    no = int(url.split("/theme/")[1].split("?")[0])
    return {"stocks": THEMES[no], "totalCount": len(THEMES[no])}


@pytest.fixture(autouse=True)
def _reset():
    sobujang._reset_cache_for_tests()
    yield
    sobujang._reset_cache_for_tests()


def test_merge_uses_exchange_pct_merges_duplicates_and_excludes_non_stocks():
    data = sobujang.sobujang_ranking(0, fetch=fake_fetch, now=0)
    assert [i["name"] for i in data["items"]] == ["심텍", "원익IPS", "솔브레인", "한미반도체"]
    wonik = next(i for i in data["items"] if i["code"] == "240810")
    assert wonik["change_pct"] == 3.41 and wonik["ticker"] == "240810.KQ" and wonik["themes"] == ["반도체 장비", "반도체 재료/부품"]
    hanmi = next(i for i in data["items"] if i["code"] == "042700")
    assert hanmi["ticker"] == "042700.KS" and hanmi["change"] == -3500 and hanmi["market"] == "KOSPI"
    assert data["universe_size"] == 4 and data["summary"]["up"] == 2 and data["summary"]["down"] == 1 and data["summary"]["flat"] == 1
    assert [i["rank"] for i in data["items"]] == [1, 2, 3, 4]


def test_limit_cache_and_stale_fallback():
    assert len(sobujang.sobujang_ranking(2, fetch=fake_fetch, now=0)["items"]) == 2
    calls = []
    counting = lambda url: calls.append(url) or fake_fetch(url)  # noqa: E731
    assert sobujang.sobujang_ranking(20, fetch=counting, now=10)["cached"] is True and calls == []
    broken = lambda url: (_ for _ in ()).throw(OSError("down"))  # noqa: E731
    stale = sobujang.sobujang_ranking(20, fetch=broken, now=100)
    assert stale["stale"] is True and stale["items"]
    with pytest.raises(sobujang.SobujangError):
        sobujang.sobujang_ranking(20, fetch=broken, now=1000)


def test_endpoint_returns_full_list_and_503_on_failure(monkeypatch):
    monkeypatch.setattr(sobujang, "_fetch_json", fake_fetch)
    response = main.market_top_sobujang(0)
    import json
    body = json.loads(response.body)
    assert response.status_code == 200 and body["universe_size"] == 4 and len(body["items"]) == 4
    sobujang._reset_cache_for_tests()
    monkeypatch.setattr(sobujang, "_fetch_json", lambda url: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(main.HTTPException) as err:
        main.market_top_sobujang(20)
    assert err.value.status_code == 503
