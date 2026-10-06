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

# Codex 수정: DART-001 (2026-10-06), 최근 7일 보존·빈 수집·실패 회귀.
def _treasury_row(day: str, suffix: str) -> dict:
    return {
        "corp_name": "테스트", "stock_code": "123456",
        "rcept_dt": day.replace("-", ""), "rcept_no": f"{day.replace('-', '')}{suffix}",
        "report_nm": "주요사항보고서(자기주식 취득 결정)", "flr_nm": "테스트",
    }


@pytest.mark.asyncio
async def test_recent_seven_days_keep_earlier_disclosures_after_empty_or_failed_day(monkeypatch):
    def fake_list(snapshot_date, disclosure_type):
        day = snapshot_date.isoformat()
        if day == "2026-10-04" and disclosure_type == "B":
            raise RuntimeError("temporary DART failure")
        if disclosure_type != "B":
            return []
        if day == "2026-10-02":
            return [_treasury_row(day, "000001")]
        if day == "2026-10-06":
            return [_treasury_row(day, "000002")]
        return []

    monkeypatch.setattr(activity, "_list_disclosures", fake_list)
    for day in (2, 3, 4, 6):
        await activity.collect_market_activity(datetime(2026, 10, day, 11, tzinfo=timezone.utc))

    report = await activity.latest_market_activity(datetime(2026, 10, 6, 12, tzinfo=timezone.utc))
    assert report["window_start"] == "2026-09-30"
    assert report["snapshot_date"] == "2026-10-06"
    assert [item["rcept_no"] for item in report["items"]] == ["20261006000002", "20261002000001"]
    assert report["counts"]["treasury"] == 2

    later = await activity.latest_market_activity(datetime(2026, 10, 9, 12, tzinfo=timezone.utc))
    assert [item["rcept_no"] for item in later["items"]] == ["20261006000002"]
    expired = await activity.latest_market_activity(datetime(2026, 10, 13, 12, tzinfo=timezone.utc))
    assert expired["status"] == "pending"
    assert expired["items"] == []


@pytest.mark.asyncio
async def test_manual_refresh_preserves_same_day_snapshot_until_new_items(monkeypatch):
    phase = {"value": "first"}

    def fake_list(snapshot_date, disclosure_type):
        if disclosure_type != "B":
            return []
        if phase["value"] == "failed":
            raise RuntimeError("temporary DART failure")
        if phase["value"] == "empty":
            return []
        suffix = "000001" if phase["value"] == "first" else "000002"
        return [_treasury_row(snapshot_date.isoformat(), suffix)]

    monkeypatch.setattr(activity, "_list_disclosures", fake_list)
    now = datetime(2026, 10, 6, 11, tzinfo=timezone.utc)
    first = await activity.collect_market_activity(now)
    phase["value"] = "empty"
    empty = await activity.collect_market_activity(now, refresh=True)
    assert empty["refresh_status"] == "no_new_items"
    assert empty["items"] == first["items"]

    phase["value"] = "failed"
    failed = await activity.collect_market_activity(now, refresh=True)
    assert failed["status"] == "failed"
    stored = await get_db()[activity.SNAPSHOT_COLLECTION].find_one({"snapshot_date": "2026-10-06"})
    assert stored["status"] == "success"
    assert stored["items"] == first["items"]

    phase["value"] = "new"
    updated = await activity.collect_market_activity(now, refresh=True)
    assert updated["status"] == "success"
    assert [item["rcept_no"] for item in updated["items"]] == ["20261006000002"]
    report = await activity.latest_market_activity(now)
    assert [item["rcept_no"] for item in report["items"]] == ["20261006000002"]


@pytest.mark.asyncio
async def test_evening_collection_rechecks_morning_snapshot(monkeypatch):
    collection = get_db()[activity.SNAPSHOT_COLLECTION]
    await collection.update_one(
        {"snapshot_date": "2026-10-06"},
        {"$set": {
            "snapshot_date": "2026-10-06",
            "status": "success",
            "collected_at": datetime(2026, 10, 6, 1, tzinfo=timezone.utc),
            "items": [{"rcept_no": "20261006000001"}],
        }},
        upsert=True,
    )
    calls = []

    def fake_list(snapshot_date, disclosure_type):
        calls.append(disclosure_type)
        return [_treasury_row(snapshot_date.isoformat(), "000002")] if disclosure_type == "B" else []

    monkeypatch.setattr(activity, "_list_disclosures", fake_list)
    before_close = await activity.collect_market_activity(datetime(2026, 10, 6, 10, tzinfo=timezone.utc))
    assert before_close["items"] == [{"rcept_no": "20261006000001"}]
    assert calls == []

    after_close = await activity.collect_market_activity(datetime(2026, 10, 6, 11, tzinfo=timezone.utc))
    assert calls == ["B", "D"]
    assert [item["rcept_no"] for item in after_close["items"]] == ["20261006000002"]
    stored = await collection.find_one({"snapshot_date": "2026-10-06"})
