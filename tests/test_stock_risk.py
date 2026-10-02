"""EINT-025: 추천 종목 위험 공시·거래소 지정 상태 — 공시 제목 분류, 한투 상태 필드, 요약, API(로그인·캐시·확인 실패)."""
from datetime import date

import pytest

from app.backend import kis_quotes, main
from app.backend.kis_quotes import KisQuotes
from app.backend.stock_risk import classify_disclosure, market_status_flags, risk_summary


@pytest.fixture(autouse=True)
def _reset():
    main._RISK_CACHE.clear()
    yield
    main._RISK_CACHE.clear()
    kis_quotes._set_service_for_tests(None)


@pytest.mark.parametrize("title,expected", [
    ("상장폐지사유발생", ("high", "상장폐지 관련")),
    ("주권매매거래정지", ("high", "매매거래 정지")),
    ("주권매매거래정지해제", None),
    ("관리종목지정해제", None),
    ("횡령ㆍ배임혐의발생", ("high", "횡령·배임")),
    ("[기재정정]주요사항보고서(전환사채권발행결정)", ("caution", "전환사채(CB) 발행")),
    ("주요사항보고서(신주인수권부사채권발행결정)", ("caution", "신주인수권부사채(BW) 발행")),
    ("주요사항보고서(유상증자결정)", ("caution", "유상증자")),
    ("주요사항보고서(유무상증자결정)", ("caution", "유상증자")),
    ("주요사항보고서(무상증자결정)", None),
    ("최대주주 변경", ("caution", "최대주주 변경 관련")),
    ("[기재정정]최대주주변경을수반하는주식담보제공계약체결", ("caution", "최대주주 변경 관련")),
    ("기타시장안내(관리종목지정우려종목)              (시가총액 20억원 미만)", ("high", "관리종목 지정 우려")),
    ("주권매매거래정지              (주식의 병합, 분할 등 전자등록 변경)", ("caution", "매매거래 정지(병합·분할)")),
    ("유상증자결정(종속회사의주요경영사항)", None),
    ("[첨부정정]주요사항보고서(전환사채권발행결정)", ("caution", "전환사채(CB) 발행")),
    ("최대주주등소유주식변동신고서", None),
    ("임원ㆍ주요주주특정증권등소유상황보고서", None),
    ("기업설명회(IR)개최", None),
    ("감사보고서제출", None),
])
def test_classify_disclosure(title, expected):
    assert classify_disclosure(title) == expected


def test_corrected_filings_count_once_and_titles_are_tidy():
    from app.backend.stock_risk import disclosure_flags
    flags = disclosure_flags([
        {"report_nm": "[첨부정정]주요사항보고서(전환사채권발행결정)", "rcept_dt": "20260928", "rcept_no": "2"},
        {"report_nm": "주요사항보고서(전환사채권발행결정)", "rcept_dt": "20260928", "rcept_no": "1"},
        {"report_nm": "주요사항보고서(전환사채권발행결정)", "rcept_dt": "20260910", "rcept_no": "0"},
        {"report_nm": "기타시장안내(관리종목지정우려종목)     (시가총액)", "rcept_dt": "20260915", "rcept_no": "3"},
    ])
    assert [(f["label"], f["date"]) for f in flags] == [("전환사채(CB) 발행", "2026-09-28"), ("전환사채(CB) 발행", "2026-09-10"),
                                                        ("관리종목 지정 우려", "2026-09-15")]
    assert flags[2]["title"] == "기타시장안내(관리종목지정우려종목) (시가총액)"


def test_market_status_flags_from_kis_fields():
    normal = {"iscd_stat_cls_code": "55", "mrkt_warn_cls_code": "00", "short_over_yn": "N", "invt_caful_yn": "N",
              "temp_stop_yn": "N", "sltr_yn": "N", "mang_issu_cls_code": "N"}
    assert market_status_flags(normal) == []
    warned = market_status_flags({**normal, "iscd_stat_cls_code": "53", "mrkt_warn_cls_code": "02"})
    assert [(f["severity"], f["label"]) for f in warned] == [("high", "투자경고")]
    caution = market_status_flags({**normal, "mrkt_warn_cls_code": "01", "short_over_yn": "Y"})
    assert sorted(f["label"] for f in caution) == ["단기과열", "투자주의"] and {f["severity"] for f in caution} == {"caution"}
    managed = market_status_flags({**normal, "iscd_stat_cls_code": "51", "mang_issu_cls_code": "Y"})
    assert [f["label"] for f in managed] == ["관리종목"]


def test_risk_summary_levels_order_and_unchecked_sources():
    disclosures = [
        {"report_nm": "주요사항보고서(유상증자결정)", "rcept_dt": "20260910", "rcept_no": "20260910000001"},
        {"report_nm": "기업설명회(IR)개최", "rcept_dt": "20260920", "rcept_no": "2"},
        {"report_nm": "횡령ㆍ배임혐의발생", "rcept_dt": "20260915", "rcept_no": "3"},
    ]
    result = risk_summary("123456", disclosures=disclosures, status_output={"mrkt_warn_cls_code": "01"}, errors={},
                          checked_on=date(2026, 10, 2))
    assert result["level"] == "high" and result["checked"] == ["dart", "krx"]
    assert [f["label"] for f in result["flags"]] == ["횡령·배임", "투자주의", "유상증자"]
    assert result["flags"][2]["url"].endswith("rcpNo=20260910000001") and result["flags"][2]["date"] == "2026-09-10"
    clean = risk_summary("005930", disclosures=[], status_output=None, errors={"krx": "x"}, checked_on=date(2026, 10, 2))
    assert clean["level"] == "none" and clean["checked"] == ["dart"] and clean["errors"] == {"krx": "x"}


@pytest.mark.asyncio
async def test_stock_risk_api_login_cache_and_failures(client, make_user, monkeypatch):
    assert (await client.get("/api/market/stock-risk?symbols=005930")).status_code == 401
    headers = await make_user("risk@test.com")
    dart_calls = []

    def fake_dart(code, today):
        dart_calls.append(code)
        if code == "000002":
            raise RuntimeError("DART 응답 020: 요청 제한")
        return [{"report_nm": "주요사항보고서(전환사채권발행결정)", "rcept_dt": "20260925", "rcept_no": "9"}] if code == "000001" else []

    class FakeKis(KisQuotes):
        async def stock_status(self, ticker):
            return {"iscd_stat_cls_code": "53" if ticker.startswith("000001") else "55", "mrkt_warn_cls_code": "00"}

    monkeypatch.setattr(main, "_dart_recent_disclosures", fake_dart)
    kis_quotes._set_service_for_tests(FakeKis("key", "secret"))
    response = await client.get("/api/market/stock-risk?symbols=000001,000002,005930,bad!", headers=headers)
    assert response.status_code == 200
    items = {item["symbol"]: item for item in response.json()["items"]}
    assert set(items) == {"000001", "000002", "005930"}
    assert items["000001"]["level"] == "high" and [f["label"] for f in items["000001"]["flags"]] == ["투자경고", "전환사채(CB) 발행"]
    assert items["000002"]["checked"] == ["krx"] and "요청 제한" in items["000002"]["errors"]["dart"]
    assert items["005930"]["level"] == "none" and items["005930"]["checked"] == ["dart", "krx"]
    await client.get("/api/market/stock-risk?symbols=000001,000002,005930", headers=headers)
    assert dart_calls.count("000001") == 1 and dart_calls.count("000002") == 2     # 성공은 캐시, 실패는 다시 시도


@pytest.mark.asyncio
async def test_stock_risk_without_kis_keys_reports_unchecked_status(client, make_user, monkeypatch):
    headers = await make_user("risk2@test.com")
    monkeypatch.setattr(main, "_dart_recent_disclosures", lambda code, today: [])
    kis_quotes._set_service_for_tests(KisQuotes("", ""))
    item = (await client.get("/api/market/stock-risk?symbols=005930", headers=headers)).json()["items"][0]
    assert item["level"] == "none" and item["checked"] == ["dart"] and "한투 키가 없어" in item["errors"]["krx"]


@pytest.mark.asyncio
async def test_kis_stock_status_reuses_quote_response():
    import httpx
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 86400})
        return httpx.Response(200, json={"rt_cd": "0", "output": {"stck_prpr": "100", "prdy_vrss": "0", "prdy_ctrt": "0",
                                                                   "iscd_stat_cls_code": "54"}})

    service = KisQuotes("k", "s", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await service.quote("005930.KS")
    status = await service.stock_status("005930.KS")
    assert status["iscd_stat_cls_code"] == "54" and sum(p.endswith("inquire-price") for p in calls) == 1
