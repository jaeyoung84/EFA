"""Daily, read-only OpenDART market activity snapshots.

This module collects only public disclosures after the Korean market close.  It
does not create recommendations, orders, paper fills, or user-ledger entries.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import get_db

logger = logging.getLogger("jy_investing.dart_market_activity")
KST = ZoneInfo("Asia/Seoul")
DART_API_URL = "https://opendart.fss.or.kr/api"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
SNAPSHOT_COLLECTION = "dart_market_activity_snapshots"
_MAX_DART_PAGES = 30
RECENT_DAYS = 7  # Codex 수정: DART-001 (2026-10-06), 최근 7일 수집본 표시.


def _dart_api_key() -> str:
    key = (os.getenv("DART_API_KEY") or os.getenv("OPENDART_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("DART_API_KEY 또는 OPENDART_API_KEY가 설정되지 않았습니다.")
    return key


def _compact(value: object) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "").replace("ㆍ", "").replace("·", ""), flags=re.UNICODE)


def _number(value: object) -> int | float | None:
    text = str(value or "").replace(",", "").strip()
    if not text or text in {"-", "N/A"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def classify_disclosure(report_name: object) -> str | None:
    """Return a narrow public-disclosure category; unrelated filings stay out."""
    title = _compact(report_name)
    if "자기주식취득" in title:
        if "신탁계약" in title:
            return "treasury_trust"
        if "결과보고서" in title:
            return "treasury_result"
        if "결정" in title:
            return "treasury_decision"
    if "임원주요주주특정증권등소유상황보고서" in title:
        return "insider"
    if "주식등의대량보유상황보고서" in title:
        return "major"
    return None


def _dart_json(path: str, **params: object) -> dict:
    query = urllib.parse.urlencode({"crtfc_key": _dart_api_key(), **params})
    with urllib.request.urlopen(f"{DART_API_URL}/{path}.json?{query}", timeout=12) as response:
        data = json.loads(response.read())
    status = data.get("status")
    if status not in {"000", "013"}:
        raise RuntimeError(f"DART 응답 {status}: {data.get('message', '')}")
    return data


def _list_disclosures(snapshot_date: date, disclosure_type: str) -> list[dict]:
    params = {
        "bgn_de": snapshot_date.strftime("%Y%m%d"),
        "end_de": snapshot_date.strftime("%Y%m%d"),
        "pblntf_ty": disclosure_type,
        "last_reprt_at": "Y",
        "page_no": 1,
        "page_count": 100,
        "sort": "date",
        "sort_mth": "desc",
    }
    first = _dart_json("list", **params)
    if first.get("status") == "013":
        return []
    rows = list(first.get("list") or [])
    total_pages = min(int(first.get("total_page") or 1), _MAX_DART_PAGES)
    for page in range(2, total_pages + 1):
        rows.extend((_dart_json("list", **{**params, "page_no": page}).get("list") or []))
    return rows


def _detail_for(corp_code: str, rcept_no: str, kind: str) -> dict:
    endpoint = "elestock" if kind == "insider" else "majorstock"
    rows = _dart_json(endpoint, corp_code=corp_code).get("list") or []
    return next((row for row in rows if str(row.get("rcept_no")) == rcept_no), {})


def _activity_item(row: dict, kind: str, detail: dict | None = None) -> dict:
    detail = detail or {}
    delta = _number(detail.get("sp_stock_lmp_irds_cnt") or detail.get("stkqy_irds"))
    holding_count = _number(detail.get("sp_stock_lmp_cnt") or detail.get("stkqy"))
    holding_rate = _number(detail.get("sp_stock_lmp_rate") or detail.get("stkrt"))
    rate_delta = _number(detail.get("sp_stock_lmp_irds_rate") or detail.get("stkrt_irds"))
    if kind == "insider":
        kind = "insider_increase" if (delta or 0) > 0 else "insider_decrease" if (delta or 0) < 0 else "insider_change"
    elif kind == "major":
        kind = "major_increase" if (delta or 0) > 0 else "major_decrease" if (delta or 0) < 0 else "major_change"
    receipt = str(row.get("rcept_no") or detail.get("rcept_no") or "")
    return {
        "kind": kind,
        "corp_name": row.get("corp_name") or detail.get("corp_name") or "-",
        "stock_code": row.get("stock_code") or "",
        "rcept_date": row.get("rcept_dt") or detail.get("rcept_dt") or "",
        "rcept_no": receipt,
        "report_name": row.get("report_nm") or "",
        "reporter": detail.get("repror") or detail.get("reporter") or row.get("flr_nm") or "",
        "relationship": "임원·주요주주" if kind.startswith("insider") else "5% 이상·대량보유자" if kind.startswith("major") else "회사",
        "position": detail.get("isu_exctv_ofcps") or detail.get("isu_exctv_rgist_at") or "",
        "holding_count": holding_count,
        "change_count": delta,
        "holding_rate": holding_rate,
        "change_rate": rate_delta,
        "report_reason": detail.get("report_resn") or "",
        "dart_url": DART_VIEW_URL.format(rcept_no=receipt) if receipt else None,
    }


async def _enrich(row: dict, kind: str, gate: asyncio.Semaphore) -> dict:
    if kind not in {"insider", "major"}:
        return _activity_item(row, kind)
    try:
        async with gate:
            detail = await asyncio.to_thread(_detail_for, str(row.get("corp_code") or ""), str(row.get("rcept_no") or ""), kind)
    except Exception as exc:
        logger.warning("DART 지분변동 상세 조회 실패: %s", exc)
        detail = {}
    return _activity_item(row, kind, detail)


def _counts(items: list[dict]) -> dict[str, int]:
    return {
        "treasury": sum(item["kind"].startswith("treasury") for item in items),
        "insider_increase": sum(item["kind"] == "insider_increase" for item in items),
        "insider_decrease": sum(item["kind"] == "insider_decrease" for item in items),
        "major": sum(item["kind"].startswith("major") for item in items),
    }


async def ensure_market_activity_indexes() -> None:
    db = get_db()
    await db[SNAPSHOT_COLLECTION].create_index("snapshot_date", unique=True)
    await db[SNAPSHOT_COLLECTION].create_index([("collected_at", -1)])


# Codex 수정: DART-001 (2026-10-06), 당일 재수집과 최근 7일 공시 보존.
async def collect_market_activity(now: datetime | None = None, *, refresh: bool = False) -> dict:
    """Collect one KST day's filings; a failed or empty refresh cannot erase a populated snapshot."""
    current = (now or datetime.now(timezone.utc)).astimezone(KST)
    snapshot_date = current.date().isoformat()
    collection = get_db()[SNAPSHOT_COLLECTION]
    existing = await collection.find_one({"snapshot_date": snapshot_date}, {"_id": 0})
    previous_success = bool(existing and existing.get("status") == "success")
    # Codex 수정: DART-001, 장 마감 전 수동 수집은 20:00 정기 수집을 막지 않는다.
    collected_at = existing.get("collected_at") if existing else None
    # MongoDB 기본 디코딩은 UTC 날짜를 naive datetime으로 반환한다.
    if isinstance(collected_at, datetime) and collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    collected_kst = collected_at.astimezone(KST) if isinstance(collected_at, datetime) else None
    scheduled_recheck = (
        previous_success
        and collected_kst is not None
        and collected_kst.date() == current.date()
        and collected_kst.time() < time(20, 0)
        and current.time() >= time(20, 0)
    )
    if previous_success and existing.get("items") and not (refresh or scheduled_recheck):
        return existing

    started_at = datetime.now(timezone.utc)
    if not previous_success:
        await collection.update_one(
            {"snapshot_date": snapshot_date},
            {"$set": {"snapshot_date": snapshot_date, "started_at": started_at, "status": "running"}},
            upsert=True,
        )
    try:
        rows = await asyncio.to_thread(_list_disclosures, current.date(), "B")
        rows += await asyncio.to_thread(_list_disclosures, current.date(), "D")
        selected: list[tuple[dict, str]] = []
        seen: set[str] = set()
        for row in rows:
            kind = classify_disclosure(row.get("report_nm"))
            receipt = str(row.get("rcept_no") or "")
            if kind and receipt and receipt not in seen:
                selected.append((row, kind))
                seen.add(receipt)
        gate = asyncio.Semaphore(4)
        items = await asyncio.gather(*(_enrich(row, kind, gate) for row, kind in selected))
        items.sort(key=lambda item: (item.get("rcept_date") or "", item.get("rcept_no") or ""), reverse=True)
        result = {
            "snapshot_date": snapshot_date,
            "status": "success",
            "started_at": started_at,
            "collected_at": datetime.now(timezone.utc),
            "items": items,
            "counts": _counts(items),
            "source": "OpenDART 공시정보",
            "note": "평일 20:00 KST에 수집한 읽기 전용 공시입니다. 보유수량 증감은 매수·매도를 단정하지 않습니다.",
        }
    except Exception as exc:
        logger.warning("DART 시장활동 수집 실패", exc_info=True)
        result = {
            "snapshot_date": snapshot_date,
            "status": "failed",
            "started_at": started_at,
            "collected_at": datetime.now(timezone.utc),
            "items": [],
            "counts": _counts([]),
            "source": "OpenDART 공시정보",
            "error": str(exc),
            "note": "DART 수집에 실패했습니다. 다음 예정 수집에서 다시 확인합니다.",
        }
        if not previous_success:
            await collection.update_one({"snapshot_date": snapshot_date}, {"$set": result}, upsert=True)
        return result

    if previous_success and existing.get("items") and not items:
        return {**existing, "refresh_status": "no_new_items", "refresh_attempted_at": result["collected_at"]}
    await collection.update_one({"snapshot_date": snapshot_date}, {"$set": result}, upsert=True)
    return result


async def latest_market_activity(now: datetime | None = None) -> dict:
    """Combine successful snapshots in the last seven KST calendar days, without querying DART."""
    today = (now or datetime.now(timezone.utc)).astimezone(KST).date()
    window_start = (today - timedelta(days=RECENT_DAYS - 1)).isoformat()
    documents = await get_db()[SNAPSHOT_COLLECTION].find(
        {"status": "success", "snapshot_date": {"$gte": window_start, "$lte": today.isoformat()}},
        {"_id": 0},
    ).sort("snapshot_date", -1).to_list(length=RECENT_DAYS)
    if documents:
        items: list[dict] = []
        seen: set[str] = set()
        for document in documents:
            for item in document.get("items") or []:
                receipt = str(item.get("rcept_no") or "")
                if receipt and receipt in seen:
                    continue
                if receipt:
                    seen.add(receipt)
                items.append(item)
        items.sort(key=lambda item: (item.get("rcept_date") or "", item.get("rcept_no") or ""), reverse=True)
        latest_with_items = next((document["snapshot_date"] for document in documents if document.get("items")), None)
        return {
            "status": "success",
            "snapshot_date": documents[0]["snapshot_date"],
            "latest_disclosure_date": latest_with_items,
            "window_start": window_start,
            "window_end": today.isoformat(),
            "collected_at": documents[0].get("collected_at"),
            "items": items,
            "counts": _counts(items),
            "source": "OpenDART 공시정보",
            "note": "최근 7일 수집 공시입니다. 새 수집이 0건이거나 실패해도 기간 안의 기존 공시를 유지합니다. 보유수량 증감은 매수·매도를 단정하지 않습니다.",
        }
    return {
        "status": "pending",
        "snapshot_date": None,
        "window_start": window_start,
        "window_end": today.isoformat(),
        "items": [],
        "counts": _counts([]),
        "source": "OpenDART 공시정보",
        "note": "최근 7일 수집된 공시가 없습니다. 페이지를 열어도 즉시 DART를 호출하지 않습니다.",
    }
