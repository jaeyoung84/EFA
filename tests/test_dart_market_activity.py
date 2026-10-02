from datetime import datetime, timezone

import pytest

from app.backend import dart_market_activity as activity
from app.backend.db import get_db
from app.backend import scheduler


@pytest.mark.parametrize("title,expected", [
    ("주요사항보고서(자기주식 취득 결정)", "treasury_decision"),
    ("자기주식취득 신탁계약 체결 결정", "treasury_trust"),
    ("자기주식 취득 결과보고서", "treasury_result"),
    ("임원ㆍ주요주주 특정증권등 소유상황보고서", "insider"),
    ("주식등의 대량보유상황보고서", "major"),
    ("자기주식 처분 결정", None),
])
def test_activity_classifier_is_narrow(title, expected):
    assert activity.classify_disclosure(title) == expected


def test_ownership_activity_shows_change_not_unverified_trade_type():
    row = {"corp_name": "테스트", "stock_code": "123456", "rcept_dt": "20261002", "rcept_no": "1",
           "report_nm": "임원ㆍ주요주주 특정증권등 소유상황보고서", "flr_nm": "홍길동"}
    item = activity._activity_item(row, "insider", {
        "repror": "홍길동", "isu_exctv_ofcps": "이사", "sp_stock_lmp_cnt": "1,000",
        "sp_stock_lmp_irds_cnt": "100", "sp_stock_lmp_rate": "1.2", "sp_stock_lmp_irds_rate": "0.1",
    })
    assert item["kind"] == "insider_increase"
    assert item["holding_count"] == 1000 and item["change_count"] == 100
    assert item["dart_url"].endswith("rcpNo=1")


@pytest.mark.asyncio
async def test_daily_snapshot_is_idempotent_and_public_api_reads_stored_data(monkeypatch, client):
    calls = []

    def fake_list(snapshot_date, disclosure_type):
        calls.append(disclosure_type)
        return [{
            "corp_code": "00126380", "corp_name": "테스트", "stock_code": "123456",
            "rcept_dt": "20261002", "rcept_no": "20261002000001",
            "report_nm": "임원ㆍ주요주주 특정증권등 소유상황보고서", "flr_nm": "홍길동",
        }] if disclosure_type == "D" else [{
            "corp_code": "00126380", "corp_name": "테스트", "stock_code": "123456",
            "rcept_dt": "20261002", "rcept_no": "20261002000002",
            "report_nm": "주요사항보고서(자기주식 취득 결정)", "flr_nm": "테스트",
        }]

    monkeypatch.setattr(activity, "_list_disclosures", fake_list)
    monkeypatch.setattr(activity, "_detail_for", lambda *args: {
        "repror": "홍길동", "sp_stock_lmp_cnt": "1000", "sp_stock_lmp_irds_cnt": "10",
        "sp_stock_lmp_rate": "1.0", "sp_stock_lmp_irds_rate": "0.1",
    })
    now = datetime(2026, 10, 2, 11, tzinfo=timezone.utc)  # 20:00 KST
    first = await activity.collect_market_activity(now)
    second = await activity.collect_market_activity(now)

    assert first["status"] == second["status"] == "success"
    assert calls == ["B", "D"]
    assert first["counts"] == {"treasury": 1, "insider_increase": 1, "insider_decrease": 0, "major": 0}
    response = await client.get("/api/dart/market-activity")
    assert response.status_code == 200
    assert response.json()["snapshot_date"] == "2026-10-02"
    assert len(response.json()["items"]) == 2


def test_market_activity_runs_after_close_at_8pm_kst():
    assert scheduler.DART_MARKET_ACTIVITY_HOUR_KST == 20
    assert scheduler.DART_MARKET_ACTIVITY_MINUTE_KST == 0
