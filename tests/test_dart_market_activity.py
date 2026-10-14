from datetime import datetime, timedelta, timezone

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
    # Claude Code 수정 (2026-10-14, 사용자 승인): 고정 날짜(2026-10-02)는 읽기 API의 최근 7일 창(실제 오늘 기준)을
    # 벗어나 2026-10-10부터 실패했다. 수집 시각을 '어제 20:00 KST'로 둔다: 7일 창 안이고, 저장 시각(실제 오늘)과
    # 날짜가 달라 같은 날 20시 전 저장분을 다시 받는 정기 재확인 규칙이 실행 시각에 따라 끼어들지 않는다.
    now = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)  # 20:00 KST
    day = now.astimezone(activity.KST).strftime("%Y%m%d")

    def fake_list(snapshot_date, disclosure_type):
        calls.append(disclosure_type)
        return [{
            "corp_code": "00126380", "corp_name": "테스트", "stock_code": "123456",
            "rcept_dt": day, "rcept_no": f"{day}000001",
            "report_nm": "임원ㆍ주요주주 특정증권등 소유상황보고서", "flr_nm": "홍길동",
        }] if disclosure_type == "D" else [{
            "corp_code": "00126380", "corp_name": "테스트", "stock_code": "123456",
            "rcept_dt": day, "rcept_no": f"{day}000002",
            "report_nm": "주요사항보고서(자기주식 취득 결정)", "flr_nm": "테스트",
        }]

    monkeypatch.setattr(activity, "_list_disclosures", fake_list)
    monkeypatch.setattr(activity, "_detail_for", lambda *args: {
        "repror": "홍길동", "sp_stock_lmp_cnt": "1000", "sp_stock_lmp_irds_cnt": "10",
        "sp_stock_lmp_rate": "1.0", "sp_stock_lmp_irds_rate": "0.1",
    })
    first = await activity.collect_market_activity(now)
    second = await activity.collect_market_activity(now)

    assert first["status"] == second["status"] == "success"
    assert calls == ["B", "D"]
    assert first["counts"] == {"treasury": 1, "insider_increase": 1, "insider_decrease": 0, "major": 0}
    response = await client.get("/api/dart/market-activity")
    assert response.status_code == 200
    assert response.json()["snapshot_date"] == now.astimezone(activity.KST).date().isoformat()
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


# 시장 구분(2026-10-06, 사용자 승인): 새 수집은 DART corp_cls, 예전 수집본은 KRX 상장 목록으로 채운다.
def test_market_comes_from_dart_corp_cls():
    base = {"corp_name": "테스트", "stock_code": "123456", "rcept_dt": "20261006", "rcept_no": "1",
            "report_nm": "주요사항보고서(자기주식 취득 결정)"}
    assert activity._activity_item({**base, "corp_cls": "Y"}, "treasury_decision")["market"] == "KOSPI"
    assert activity._activity_item({**base, "corp_cls": "K"}, "treasury_decision")["market"] == "KOSDAQ"
    assert activity._activity_item({**base, "corp_cls": "E", "stock_code": ""}, "treasury_decision")["market"] == "기타"


@pytest.mark.asyncio
async def test_recent_report_fills_missing_market_and_keeps_newest_first(monkeypatch):
    from app.backend import instrument_catalog
    monkeypatch.setattr(instrument_catalog, "get_kqs_catalog", lambda: [
        {"code": "111111", "exchange": "KOSPI"}, {"code": "222222", "exchange": "KOSDAQ"},
    ])
    collection = get_db()[activity.SNAPSHOT_COLLECTION]
    stored = {
        "2026-10-02": [{"rcept_no": "20261002000001", "rcept_date": "20261002", "stock_code": "222222", "kind": "treasury_decision"}],
        "2026-10-06": [
            {"rcept_no": "20261006000001", "rcept_date": "20261006", "stock_code": "111111", "kind": "treasury_decision"},
            {"rcept_no": "20261006000002", "rcept_date": "20261006", "stock_code": "333333", "kind": "major_increase",
             "market": "KONEX"},
        ],
    }
    for day, items in stored.items():
        await collection.update_one({"snapshot_date": day}, {"$set": {
            "snapshot_date": day, "status": "success", "items": items,
            "collected_at": datetime(2026, 10, int(day[-2:]), 11, tzinfo=timezone.utc)}}, upsert=True)

    report = await activity.latest_market_activity(datetime(2026, 10, 6, 12, tzinfo=timezone.utc))
    assert [item["rcept_no"] for item in report["items"]] == ["20261006000002", "20261006000001", "20261002000001"]
    assert [item["market"] for item in report["items"]] == ["KONEX", "KOSPI", "KOSDAQ"]
    assert report["market_counts"] == {"KOSPI": 1, "KOSDAQ": 1, "KONEX": 1, "기타": 0}
    assert report["counts"]["treasury"] == 2   # 기존 counts 형식은 그대로
