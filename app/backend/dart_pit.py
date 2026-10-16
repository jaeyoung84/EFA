"""EINT-051: DART 정기공시 시점(PIT) 보관 — 접수일·정정 색인과 접수 직후 재무 스냅샷.

왜: DART 재무 API는 정정 뒤 최신 값만 준다. 과거 연구(KR-VQM-v1)는 그래서 '분기 말+45일' 같은 법정 기한으로 공개 시점을
대신했다. 이 모듈은 앞으로의 정기공시를 접수 직후 받아 두어 '그때 알 수 있었던 값'을 남기고, 과거 공시는 접수일·정정 이력만
색인한다(Codex CODX-DIR / kr-value-compare 문서의 '기반 공사').

- 색인(dart_filings): 공시목록 API(list.json, 정기공시 A, 유가 Y·코스닥 K, 정정 포함). 매 실행 최근 7일 + 과거(2015-01~) 몇 달씩.
- 스냅샷(dart_pit_financials): 원본·[기재정정] 정기공시를 **접수 후 7일 안에** 전체 재무제표(fnlttSinglAcntAll, 연결 우선
  없으면 별도)로 받아 접수번호와 함께 저장. 7일이 지난 공시(과거 색인으로 처음 본 것 포함)는 받지 않고 'historical'로 둔다.
- 하루 DART 한도(2만)를 넘지 않게 실행당 호출 수를 제한한다. 값을 만들거나 기존 값을 덮지 않는다. 추천·주문과 무관.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time as _time
from datetime import date, datetime, timedelta, timezone

import requests

try:
    from .db import get_db
except ImportError:
    from db import get_db  # type: ignore

logger = logging.getLogger("jy_investing.dart_pit")
LIST_URL = "https://opendart.fss.or.kr/api/list.json"
FIN_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
FILINGS, SNAPSHOTS, STATE, RUNS = "dart_filings", "dart_pit_financials", "dart_pit_state", "dart_pit_runs"
CORP_CLASSES = ("Y", "K")
BACKFILL_FROM = date(2015, 1, 1)
BACKFILL_MONTHS_PER_RUN = 6
RECENT_DAYS = 7
PIT_MAX_LAG_DAYS = 7
MAX_CALLS_PER_RUN = 1500
SLEEP_SECONDS = 0.15
REPRT = {"Q1": "11013", "H1": "11012", "Q3": "11014", "FY": "11011"}
NAME = re.compile(r"^(?:\[(?P<tag>[^\]]+)\])?\s*(?P<kind>사업보고서|반기보고서|분기보고서)\s*\((?P<y>\d{4})\.(?P<m>\d{2})\)")


class Budget:
    def __init__(self, limit: int):
        self.limit, self.used = limit, 0

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def classify(report_nm: str) -> dict:
    """보고서명 → 종류·기간·정정 여부·재무 API 코드(12월 결산 기준으로만 확정, 그 밖은 None)."""
    m = NAME.match(report_nm.strip())
    if not m:
        return {"kind": None, "period": None, "tag": None, "bsns_year": None, "reprt": None}
    kind, y, mon, tag = m.group("kind"), int(m.group("y")), int(m.group("m")), m.group("tag")
    reprt = {"사업보고서": "FY" if mon == 12 else None, "반기보고서": "H1" if mon == 6 else None,
             "분기보고서": {3: "Q1", 9: "Q3"}.get(mon)}[kind]
    return {"kind": kind, "period": f"{y}.{mon:02d}", "tag": tag, "bsns_year": str(y) if reprt else None, "reprt": reprt}


def _get(url: str, params: dict, budget: Budget) -> dict | None:
    if not budget.take():
        return None
    for attempt in range(3):
        try:
            _time.sleep(SLEEP_SECONDS)
            return requests.get(url, params=params, timeout=30).json()
        except Exception:                      # 일시 오류는 잠시 뒤 재시도
            _time.sleep(2 * (attempt + 1))
    return {"status": "ERR"}


def fetch_list(key: str, start: date, end: date, corp_cls: str, budget: Budget, get=_get) -> tuple[list[dict], bool]:
    """기간·시장의 정기공시 전체(정정 포함). (목록, 완료 여부) — 호출 한도에 걸리면 완료=False."""
    rows, page = [], 1
    while True:
        payload = get(LIST_URL, {"crtfc_key": key, "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                                 "pblntf_ty": "A", "corp_cls": corp_cls, "last_reprt_at": "N", "page_no": page, "page_count": 100}, budget)
        if payload is None:
            return rows, False
        status = payload.get("status")
        if status == "013":                    # 해당 기간 공시 없음
            return rows, True
        if status != "000":
            raise RuntimeError(f"DART list.json {status} {payload.get('message', '')}")
        rows += payload.get("list") or []
        if page >= int(payload.get("total_page") or 1):
            return rows, True
        page += 1


async def upsert_filings(db, rows: list[dict], now: datetime) -> int:
    added = 0
    for r in rows:
        if not (r.get("stock_code") or "").strip():
            continue
        info = classify(r.get("report_nm", ""))
        rd = r.get("rcept_dt", "")
        doc = {"corp_code": r["corp_code"], "corp_name": r.get("corp_name"), "stock_code": r["stock_code"].strip(),
               "corp_cls": r.get("corp_cls"), "report_nm": r.get("report_nm"), "rcept_dt": f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}",
               "rm": r.get("rm"), **info}
        res = await db[FILINGS].update_one({"_id": r["rcept_no"]}, {"$setOnInsert": {**doc, "first_seen_at": now, "pit_status": "pending"}},
                                           upsert=True)
        added += bool(res.upserted_id)
    return added


def snapshot_items(payload: dict) -> list[dict]:
    keep = ("sj_div", "sj_nm", "account_id", "account_nm", "account_detail", "thstrm_nm", "thstrm_amount", "thstrm_add_amount", "currency")
    return [{k: row.get(k) for k in keep} for row in payload.get("list") or []]


def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


async def run(now: datetime | None = None, db=None, get=_get, key: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    key = key or os.getenv("DART_API_KEY", "")
    if not key:
        return {"status": "skipped", "reason": "DART 키 없음"}
    db = db if db is not None else get_db()
    budget = Budget(MAX_CALLS_PER_RUN)
    today = now.astimezone(timezone(timedelta(hours=9))).date()
    result = {"at": now.isoformat(), "recent_added": 0, "backfill_added": 0, "errors": []}
    loop = asyncio.get_running_loop()

    async def aget(url, params, budget_):            # requests 호출은 스레드에서(이벤트 루프를 막지 않게)
        return await loop.run_in_executor(None, get, url, params, budget_)
    try:
        for cls in CORP_CLASSES:                 # 1) 최근 7일
            rows, _ = await loop.run_in_executor(None, fetch_list, key, today - timedelta(days=RECENT_DAYS), today, cls, budget, get)
            result["recent_added"] += await upsert_filings(db, rows, now)
        # 2) 스냅샷을 과거 색인보다 먼저(접수 7일 안 공시가 호출 한도에 밀리지 않게)
        result["snapshots"] = await take_snapshots(db, key, now, budget, aget)
        state = await db[STATE].find_one({"_id": "backfill"}) or {"_id": "backfill", "next_start": BACKFILL_FROM.isoformat()}
        start = date.fromisoformat(state["next_start"])
        for _ in range(BACKFILL_MONTHS_PER_RUN):  # 3) 과거 색인(한 달씩, 두 시장 모두 끝나야 다음 달)
            if start > today:
                break
            end = min(_month_end(start), today)
            done_all = True
            for cls in CORP_CLASSES:
                rows, done = await loop.run_in_executor(None, fetch_list, key, start, end, cls, budget, get)
                result["backfill_added"] += await upsert_filings(db, rows, now)
                done_all &= done
            if not done_all:
                break
            start = end + timedelta(days=1)
            await db[STATE].replace_one({"_id": "backfill"}, {"_id": "backfill", "next_start": start.isoformat(),
                                                              "complete": start > today, "updated_at": now}, upsert=True)
        # 과거 색인으로 새로 본 공시는 다음 스냅샷 단계에서 historical로 정리된다.
        await take_snapshots(db, key, now, budget, aget)
    except Exception as exc:                      # 일부 실패해도 저장된 것은 유지
        result["errors"].append(str(exc)[:200])
    result["calls"] = budget.used
    result["status"] = "ok" if not result["errors"] else "partial"
    await db[RUNS].insert_one({**result, "created_at": datetime.now(timezone.utc)})
    (logger.warning if result["errors"] else logger.info)("DART PIT %s", result)
    return result


async def take_snapshots(db, key: str, now: datetime, budget: Budget, aget) -> dict:
    """대기 중 공시: 접수 7일 안이면 재무 스냅샷(연결 → 없으면 별도), 아니면 historical. 첨부 공시·12월 결산 외는 받지 않음."""
    counts = {"stored": 0, "empty": 0, "historical": 0, "unmapped": 0, "attachment": 0, "budget_left": True}
    today = now.astimezone(timezone(timedelta(hours=9))).date()
    async for f in db[FILINGS].find({"pit_status": "pending"}).sort("rcept_dt", -1):
        lag = (today - date.fromisoformat(f["rcept_dt"])).days
        status = None
        if lag > PIT_MAX_LAG_DAYS:
            status = "historical"
        elif f.get("tag") and "기재정정" not in f["tag"]:
            status = "attachment"                       # [첨부추가]·[첨부정정]: 재무 숫자 변경이 아님
        elif not f.get("reprt"):
            status = "unmapped"                         # 12월 결산이 아니면 API 보고서 코드를 확정할 수 없음
        if status:
            await db[FILINGS].update_one({"_id": f["_id"]}, {"$set": {"pit_status": status}})
            counts[status] += 1
            continue
        stored = False
        for fs in ("CFS", "OFS"):
            payload = await aget(FIN_URL, {"crtfc_key": key, "corp_code": f["corp_code"], "bsns_year": f["bsns_year"],
                                           "reprt_code": REPRT[f["reprt"]], "fs_div": fs}, budget)
            if payload is None:
                counts["budget_left"] = False
                return counts
            items = snapshot_items(payload) if payload.get("status") == "000" else []
            if items:
                body = json.dumps(items, ensure_ascii=False, sort_keys=True).encode()
                await db[SNAPSHOTS].update_one({"_id": f"{f['_id']}:{fs}"}, {"$setOnInsert": {
                    "rcept_no": f["_id"], "corp_code": f["corp_code"], "stock_code": f["stock_code"], "rcept_dt": f["rcept_dt"],
                    "bsns_year": f["bsns_year"], "reprt": f["reprt"], "fs_div": fs, "tag": f.get("tag"), "fetched_at": now,
                    "lag_days": lag, "items": items, "items_sha256": hashlib.sha256(body).hexdigest()}}, upsert=True)
                stored = True
                break
        await db[FILINGS].update_one({"_id": f["_id"]}, {"$set": {"pit_status": "stored" if stored else "empty", "pit_checked_at": now}})
        counts["stored" if stored else "empty"] += 1
    return counts


async def status(db=None) -> dict:
    db = db if db is not None else get_db()
    by_status = {d["_id"]: d["n"] async for d in db[FILINGS].aggregate([{"$group": {"_id": "$pit_status", "n": {"$sum": 1}}}])}
    state = await db[STATE].find_one({"_id": "backfill"}, {"_id": 0}) or {}
    last = await db[RUNS].find_one({}, {"_id": 0}, sort=[("created_at", -1)])
    first = await db[FILINGS].find_one({}, {"rcept_dt": 1}, sort=[("rcept_dt", 1)])
    return {"filings": sum(by_status.values()), "by_pit_status": by_status, "snapshots": await db[SNAPSHOTS].count_documents({}),
            "earliest_rcept_dt": (first or {}).get("rcept_dt"), "backfill": state, "last_run": last}
