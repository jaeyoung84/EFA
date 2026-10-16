"""EINT-051 DART 시점 보관: 보고서명 해석, 색인(상장사만·첫 기록 유지), 접수 7일 안 스냅샷·그 밖 historical, 첨부·비12월, 호출 한도, 과거 색인 진행."""
from datetime import datetime, timezone

import pytest

from app.backend import dart_pit as pit
from app.backend.db import get_db


def test_classify_report_names():
    assert pit.classify("반기보고서 (2026.06)")[ "reprt"] == "H1"
    c = pit.classify("[기재정정]사업보고서 (2025.12)")
    assert (c["reprt"], c["bsns_year"], c["tag"]) == ("FY", "2025", "기재정정")
    assert pit.classify("분기보고서 (2026.03)")["reprt"] == "Q1" and pit.classify("분기보고서 (2026.09)")["reprt"] == "Q3"
    assert pit.classify("분기보고서 (2026.06)")["reprt"] is None          # 비12월 결산은 코드 미확정
    assert pit.classify("주요사항보고서")["kind"] is None


def filing(no, dt, name, stock="005930", corp="00126380"):
    return {"corp_code": corp, "corp_name": "회사", "stock_code": stock, "corp_cls": "Y", "report_nm": name, "rcept_no": no, "rcept_dt": dt, "rm": ""}


class FakeDart:
    def __init__(self, lists, fin_status="000"):
        self.lists, self.fin_status, self.calls = lists, fin_status, []

    def __call__(self, url, params, budget):
        if not budget.take():
            return None
        self.calls.append((url, dict(params)))
        if url == pit.LIST_URL:
            key = (params["bgn_de"], params["corp_cls"])
            rows = self.lists.get(key, [])
            return {"status": "000" if rows else "013", "list": rows, "total_page": 1}
        if params["fs_div"] == "CFS" and self.fin_status != "000":
            return {"status": "013"}
        return {"status": "000", "list": [{"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "100"}]}


@pytest.mark.asyncio
async def test_run_indexes_snapshots_recent_and_marks_old_historical(monkeypatch):
    db = get_db()
    now = datetime(2026, 10, 16, 10, 30, tzinfo=timezone.utc)          # 19:30 KST
    monkeypatch.setattr(pit, "BACKFILL_FROM", pit.date(2026, 9, 1))
    monkeypatch.setattr(pit, "SLEEP_SECONDS", 0)
    recent = [filing("20261014000001", "20261014", "반기보고서 (2026.06)"),
              filing("20261015000002", "20261015", "[첨부추가]반기보고서 (2026.06)"),
              filing("20261015000003", "20261015", "분기보고서 (2026.06)"),
              filing("20261015000004", "20261015", "반기보고서 (2026.06)", stock="")]           # 비상장은 색인 안 함
    old = [filing("20260914000009", "20260914", "반기보고서 (2026.06)", corp="00000009", stock="000009")]
    fake = FakeDart({("20261009", "Y"): recent, ("20260901", "Y"): old}, fin_status="013")      # 연결 없음 → 별도
    result = await pit.run(now, db=db, get=fake, key="k")
    assert result["status"] == "ok" and result["recent_added"] == 3 and result["backfill_added"] == 1
    status = {d["_id"]: d["pit_status"] async for d in db[pit.FILINGS].find({})}
    assert status == {"20261014000001": "stored", "20261015000002": "attachment", "20261015000003": "unmapped", "20260914000009": "historical"}
    snap = await db[pit.SNAPSHOTS].find_one({"rcept_no": "20261014000001"})
    assert snap["fs_div"] == "OFS" and snap["lag_days"] == 2 and snap["items"][0]["account_nm"] == "매출액" and len(snap["items_sha256"]) == 64
    state = await db[pit.STATE].find_one({"_id": "backfill"})
    assert state["next_start"] == "2026-10-17" and state["complete"] is True
    again = await pit.run(now, db=db, get=FakeDart({("20261009", "Y"): recent}), key="k")   # 같은 공시는 첫 기록 그대로
    assert again["recent_added"] == 0 and await db[pit.SNAPSHOTS].count_documents({}) == 1


@pytest.mark.asyncio
async def test_budget_stops_cleanly_and_status_endpoint(client, monkeypatch):
    db = get_db()
    monkeypatch.setattr(pit, "MAX_CALLS_PER_RUN", 1)
    monkeypatch.setattr(pit, "SLEEP_SECONDS", 0)
    now = datetime(2026, 10, 16, 10, 30, tzinfo=timezone.utc)
    fake = FakeDart({("20261009", "Y"): [filing("20261014000001", "20261014", "반기보고서 (2026.06)")]})
    result = await pit.run(now, db=db, get=fake, key="k")
    assert result["calls"] == 1 and result["status"] == "ok"
    assert (await db[pit.FILINGS].find_one({"_id": "20261014000001"}))["pit_status"] == "pending"   # 한도로 미처리 → 다음 실행
    body = (await client.get("/api/market/dart-pit-status")).json()
    assert body["filings"] == 1 and body["by_pit_status"] == {"pending": 1} and body["last_run"]["calls"] == 1
