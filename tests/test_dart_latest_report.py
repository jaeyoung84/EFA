"""DART 재무 분석: 가장 최근 공시 자동 선택, 연결 우선, 분기·반기 누적 손익, ROE 연환산."""
import io
import json
import urllib.parse
from datetime import date

import pytest

from app.backend import main


def _row(fs, sj, name, cur, prior, cur_add=None, prior_add=None):
    return {"fs_div": fs, "sj_div": sj, "account_nm": name, "rcept_no": "20260814000123",
            "thstrm_dt": "2026.01.01 ~ 2026.06.30" if sj == "IS" else "2026.06.30 현재",
            "frmtrm_dt": "2025.01.01 ~ 2025.06.30" if sj == "IS" else "2025.12.31 현재",
            "thstrm_amount": f"{cur:,}", "frmtrm_amount": f"{prior:,}",
            "thstrm_add_amount": None if cur_add is None else f"{cur_add:,}",
            "frmtrm_add_amount": None if prior_add is None else f"{prior_add:,}"}


HALF = [  # 별도(OFS) 행이 먼저 와도 연결(CFS)을 써야 한다
    _row("OFS", "IS", "매출액", 1, 1, 1, 1),
    _row("CFS", "BS", "자산총계", 1000, 900), _row("CFS", "BS", "부채총계", 400, 380),
    _row("CFS", "BS", "자본총계", 600, 520), _row("CFS", "BS", "유동자산", 300, 280), _row("CFS", "BS", "유동부채", 200, 190),
    _row("CFS", "IS", "매출액", 170, 75, 300, 150),       # 2분기 3개월 170, 상반기 누적 300
    _row("CFS", "IS", "영업이익", 40, 10, 60, 20),
    _row("CFS", "IS", "당기순이익", 30, 8, 30, 16),
]


@pytest.fixture
def dart(monkeypatch):
    calls = []
    available = {("2026", "11012"): HALF, ("2026", "11013"): HALF, ("2025", "11011"): [
        _row("CFS", "BS", "자산총계", 900, 800), _row("CFS", "BS", "자본총계", 520, 500),
        _row("CFS", "IS", "매출액", 500, 400), _row("CFS", "IS", "당기순이익", 52, 40)]}

    def fake_urlopen(url, timeout=0):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        calls.append((query["bsns_year"], query["reprt_code"]))
        rows = available.get((query["bsns_year"], query["reprt_code"]))
        body = {"status": "000", "message": "정상", "list": rows} if rows else {"status": "013", "message": "조회된 데이타가 없습니다."}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(main.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(main, "_dart_api_key", lambda: "test-key")
    monkeypatch.setattr(main, "_fetch_company_detail", lambda corp: {"corp_name": "테스트전자", "corp_cls": "Y"})

    class Today(main.datetime):
        @classmethod
        def now(cls, tz=None):
            return main.datetime(2026, 10, 1, 9, 0, tzinfo=tz)
    monkeypatch.setattr(main, "datetime", Today)
    return calls


def test_candidates_skip_unfinished_periods_and_go_newest_first():
    assert main._dart_report_candidates(date(2026, 10, 1), None, None)[:4] == [
        ("2026", "11014"), ("2026", "11012"), ("2026", "11013"), ("2025", "11011")]
    assert main._dart_report_candidates(date(2026, 5, 1), None, None)[:2] == [("2026", "11013"), ("2025", "11011")]
    assert main._dart_report_candidates(date(2026, 10, 1), "2025", None)[0] == ("2025", "11011")
    assert main._dart_report_candidates(date(2026, 10, 1), None, "11011")[0] == ("2025", "11011")
    assert main._dart_report_candidates(date(2026, 10, 1), "2024", "11012") == [("2024", "11012")]


@pytest.mark.asyncio
async def test_default_uses_latest_filed_report_with_cumulative_income(client, dart):
    response = await client.post("/api/dart/financial-analysis", json={"corp_code": "00126380"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert dart[:2] == [("2026", "11014"), ("2026", "11012")]  # 3분기는 아직 없음 → 반기
    report = data["report"]
    assert (report["label"], report["mode"], report["annualized"], report["months"]) == ("2026년 반기", "latest", True, 6)
    assert report["dart_url"].endswith("rcpNo=20260814000123") and report["prior_label"] == "2025년 반기 누적"
    assert data["financials"]["is_consolidated"] is True
    assert data["ratios"]["revenue_growth"] == pytest.approx(100.0)        # 누적 300 vs 전년 동기 누적 150
    assert data["ratios"]["op_margin"] == pytest.approx(20.0)              # 60 / 300
    assert data["ratios"]["roe"] == pytest.approx(30 * 2 / ((600 + 520) / 2) * 100)  # 반기 순이익 ×2 연환산
    assert data["bsns_year"] == "2026" and "2026년 반기" in json.dumps(data["analysis"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_explicit_annual_report_is_not_annualized(client, dart):
    data = (await client.post("/api/dart/financial-analysis", json={"corp_code": "00126380", "bsns_year": "2025", "reprt_code": "11011"})).json()
    assert dart == [("2025", "11011")]
    assert (data["report"]["label"], data["report"]["annualized"], data["report"]["mode"]) == ("2025년 연간", False, "selected")
    assert data["ratios"]["roe"] == pytest.approx(52 / ((520 + 500) / 2) * 100)


@pytest.mark.asyncio
async def test_missing_report_explains_what_was_checked(client, dart):
    response = await client.post("/api/dart/financial-analysis", json={"corp_code": "00126380", "bsns_year": "2024", "reprt_code": "11012"})
    assert response.status_code == 404 and "2024년 반기" in response.json()["detail"]


@pytest.mark.parametrize("ratio,points", [(30.0, 20), (50.0, 20), (80.0, 16), (150.0, 10), (250.0, 5), (450.0, 0), (None, 10)])
def test_debt_ratio_score_shows_the_real_ratio(ratio, points):
    """'항목별 평가'에 부호를 뒤집은 값(-30%)이 아니라 실제 부채비율이 보여야 한다. 점수 구간은 그대로다."""
    _, breakdown = main._score_financial_health({"debt_equity_ratio": ratio})
    assert breakdown["부채비율"]["score"] == points and breakdown["부채비율"]["value"] == ratio
    assert "note" not in breakdown["부채비율"]


def test_capital_impairment_gets_zero_points_and_a_plain_warning():
    """자본총계가 음수면 부채비율이 음수가 된다. 이전에는 이게 '매우 낮은 부채'로 만점(20)을 받았다."""
    _, breakdown = main._score_financial_health({"debt_equity_ratio": -350.0})
    assert breakdown["부채비율"] == {"score": 0.0, "max": 20, "value": -350.0, "note": "자본잠식"}
    analysis = main._generate_dart_analysis({"corp_name": "테스트"}, "KOSDAQ", {"debt_equity_ratio": -350.0}, 30.0, "D", "2026")
    text = json.dumps(analysis, ensure_ascii=False)
    assert "완전자본잠식" in text and "매우 낮은 수준" not in text
