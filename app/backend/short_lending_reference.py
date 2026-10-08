"""Codex 수정: CODX-SL-001 (2026-10-08), 추천과 분리된 공매도·대차 참고자료 관측.

추천 게시본/선정 규칙/개인 원장에는 쓰지 않는다. 수집 시각의 원자료와 요약을
게시 ID·종목·수집 슬롯별로 첫 기록 그대로 보존해 후일 탐색 자료로만 사용한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import statistics
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import get_db
from .kis_quotes import kis_service
from .kqs_recommendations import read_local_recommendations

SCHEMA = "kqs-short-lending-reference/v1"
COLLECTION = "kqs_short_lending_references"
KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("jy_investing.short_lending_reference")
CODE = re.compile(r"^[0-9A-Z]{6}$")


async def _short_history(service, code: str, start: str, end: str) -> dict:
    payload = await service._rest_get(
        "/uapi/domestic-stock/v1/quotations/daily-short-sale", "FHPST04830000",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
         "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end},
    )
    if str(payload.get("rt_cd")) != "0" or not isinstance(payload.get("output2"), list):
        raise RuntimeError("KIS 공매도 일별추이 응답 오류")
    return {"output1": payload.get("output1"), "output2": payload["output2"]}


async def _loan_history(service, code: str, start: str, end: str) -> dict:
    payload = await service._rest_get(
        "/uapi/domestic-stock/v1/quotations/daily-loan-trans", "HHPST074500C0",
        {"MRKT_DIV_CLS_CODE": "3", "MKSC_SHRN_ISCD": code,
         "START_DATE": start, "END_DATE": end, "CTS": ""},
    )
    if str(payload.get("rt_cd")) != "0" or not isinstance(payload.get("output1"), list):
        raise RuntimeError("KIS 대차거래 일별추이 응답 오류")
    return {"output1": payload["output1"]}


def _number(value: object) -> float | None:
    try:
        result = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _day(value: object) -> str | None:
    raw = str(value or "").replace("-", "")
    if not re.fullmatch(r"\d{8}", raw):
        return None
    try:
        return date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}").isoformat()
    except ValueError:
        return None


def _hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def short_rows(payload: dict, as_of: str) -> list[dict]:
    """KIS의 일별 공매도 거래대금 비중. 미래·중복·비정상 행은 사용하지 않는다."""
    by_day: dict[str, dict] = {}
    for raw in payload.get("output2") or []:
        if not isinstance(raw, dict):
            continue
        day = _day(raw.get("stck_bsop_date"))
        if not day or day > as_of:
            continue
        short_value = _number(raw.get("ssts_tr_pbmn"))
        total_value = _number(raw.get("acml_tr_pbmn"))
        reported = _number(raw.get("ssts_tr_pbmn_rlim"))
        if short_value is not None and total_value is not None and total_value > 0:
            ratio = 100 * short_value / total_value
        else:
            ratio = reported
        if ratio is None or not 0 <= ratio <= 100:
            continue
        by_day[day] = {"date": day, "short_value": short_value, "total_value": total_value,
                       "ratio_pct": round(ratio, 6)}
    return [by_day[day] for day in sorted(by_day)][-90:]


def loan_rows(payload: dict, as_of: str) -> list[dict]:
    """KIS 대차 잔고주수와 체결·상환주수. 금액은 주가에 흔들려 방향 판정에 쓰지 않는다."""
    by_day: dict[str, dict] = {}
    for raw in payload.get("output1") or []:
        if not isinstance(raw, dict):
            continue
        day = _day(raw.get("bsop_date"))
        balance = _number(raw.get("rmnd_stcn"))
        if not day or day > as_of or balance is None or balance < 0:
            continue
        by_day[day] = {"date": day, "balance_shares": int(balance),
                       "new_shares": _number(raw.get("new_stcn")),
                       "returned_shares": _number(raw.get("rdmp_stcn"))}
    return [by_day[day] for day in sorted(by_day)][-90:]


def short_summary(rows: list[dict]) -> dict:
    recent = rows[-60:]
    result = {"status": "insufficient" if len(recent) < 60 else "ok",
              "sessions": len(recent), "as_of": rows[-1]["date"] if rows else None,
              "direction_60": None, "volatility_20_pp": None, "blocks_20_pct": None}
    if len(recent) < 60:
        return result
    values = [row["ratio_pct"] for row in recent]
    blocks = [round(statistics.mean(values[i:i + 20]), 3) for i in (0, 20, 40)]
    direction = "increase" if blocks[0] < blocks[1] < blocks[2] else (
        "decrease" if blocks[0] > blocks[1] > blocks[2] else "mixed")
    result.update(direction_60=direction, blocks_20_pct=blocks,
                  volatility_20_pp=round(statistics.pstdev(values[-20:]), 3),
                  current_20_pct=blocks[-1])
    return result


def loan_summary(rows: list[dict]) -> dict:
    recent = rows[-60:]
    result = {"status": "insufficient" if len(recent) < 60 else "ok",
              "sessions": len(recent), "as_of": rows[-1]["date"] if rows else None,
              "direction_60": None, "balance_shares": rows[-1]["balance_shares"] if rows else None,
              "change_pct_60": None}
    if len(recent) < 60:
        return result
    old, new = recent[0]["balance_shares"], recent[-1]["balance_shares"]
    result.update(direction_60="increase" if new > old else "decrease" if new < old else "flat",
                  change_pct_60=round(100 * (new / old - 1), 3) if old else None,
                  first_balance_shares=old)
    return result


def _selected(payload: dict) -> dict[str, dict]:
    selected = {}
    for signal in payload.get("signals") or []:
        code = str(signal.get("symbol") or "").upper()
        if signal.get("action") == "entry" and signal.get("selection_outcome") == "selected" and CODE.fullmatch(code):
            row = selected.setdefault(code, {"symbol": code, "market": signal.get("market"),
                                             "name": signal.get("name") or code, "signal_ids": []})
            row["signal_ids"].append(signal["signal_id"])
    return selected


async def collect_short_lending_references(*, slot: str = "preopen", now: datetime | None = None,
                                           payload: dict | None = None, service=None, collection=None) -> dict:
    """현재 날짜에 새로 게시된 추천만 관측. 오전/늦은 재시도는 서로 다른 불변 기록이다."""
    if slot not in {"preopen", "late"}:
        raise ValueError("unknown collection slot")
    current = (now or datetime.now(timezone.utc)).astimezone(KST)
    payload = payload or await asyncio.to_thread(read_local_recommendations)
    generated_at = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00"))
    if generated_at.astimezone(KST).date() != current.date():
        return {"status": "no_new_publication", "count": 0}
    snapshot_id = str(payload["snapshot_id"])
    as_of = str(payload["engine"]["data_as_of"])[:10]
    selected = _selected(payload)
    if not selected:
        return {"status": "no_picks", "snapshot_id": snapshot_id, "count": 0}
    if service is None:
        service = kis_service()
    if collection is None:
        collection = get_db()[COLLECTION]
    await collection.create_index([("snapshot_id", 1), ("symbol", 1)])
    start = (date.fromisoformat(as_of) - timedelta(days=145)).strftime("%Y%m%d")
    end = as_of.replace("-", "")
    recorded = 0
    for code, pick in selected.items():
        key = f"{snapshot_id}:{slot}:{code}"
        if await collection.find_one({"_id": key}, {"_id": 1}):
            continue
        if slot == "late" and await collection.find_one(
            {"snapshot_id": snapshot_id, "symbol": code, "available_before_open": True,
             "short.status": "ok", "loan.status": "ok"}, {"_id": 1}
        ):
            continue
        started = datetime.now(timezone.utc)
        sources = {}
        for kind, fetch, normalizer, summarizer in (
            ("short", _short_history, short_rows, short_summary),
            ("loan", _loan_history, loan_rows, loan_summary),
        ):
            try:
                raw = await fetch(service, code, start, end)
                rows = normalizer(raw, as_of)
                sources[kind] = {**summarizer(rows), "rows": rows, "raw_sha256": _hash(raw),
                                 "source": "KIS", "fetched_at": datetime.now(timezone.utc)}
            except Exception as exc:
                logger.warning("%s %s 수집 실패: %s", code, kind, type(exc).__name__)
                sources[kind] = {"status": "unavailable", "as_of": None, "rows": [],
                                 "source": "KIS", "error_type": type(exc).__name__}
        completed = datetime.now(timezone.utc)
        document = {"_id": key, "schema": SCHEMA, "snapshot_id": snapshot_id, "slot": slot, **pick,
                    "recommendation_as_of": as_of, "recommendation_generated_at": generated_at,
                    "captured_started_at": started, "captured_at": completed,
                    "available_before_open": completed.astimezone(KST).date() == current.date()
                                             and completed.astimezone(KST).time() < time(9, 0),
                    **sources}
        await collection.update_one({"_id": key}, {"$setOnInsert": document}, upsert=True)
        recorded += 1
    return {"status": "recorded", "snapshot_id": snapshot_id, "count": recorded}


async def read_short_lending_references(payload: dict | None = None, collection=None) -> dict:
    """웹은 저장된 관측만 읽는다. GET은 KIS를 호출하거나 과거 값을 다시 계산하지 않는다."""
    payload = payload or await asyncio.to_thread(read_local_recommendations)
    selected = _selected(payload)
    if collection is None:
        collection = get_db()[COLLECTION]
    documents = await collection.find({"snapshot_id": str(payload["snapshot_id"])},
                                      {"_id": 0}).to_list(length=40)
    items = {}
    for code in selected:
        candidates = [row for row in documents if row.get("symbol") == code and row.get("schema") == SCHEMA]
        candidates.sort(key=lambda row: (row.get("short", {}).get("status") == "ok"
                                         and row.get("loan", {}).get("status") == "ok",
                                         row.get("captured_at") or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        if not candidates:
            items[code] = {"status": "pending"}
            continue
        chosen = candidates[0]
        items[code] = {"status": "available" if chosen["short"]["status"] == chosen["loan"]["status"] == "ok" else "partial",
                       "captured_at": chosen["captured_at"], "available_before_open": chosen["available_before_open"],
                       "short": {k: v for k, v in chosen["short"].items() if k not in {"rows", "raw_sha256"}},
                       "loan": {k: v for k, v in chosen["loan"].items() if k not in {"rows", "raw_sha256"}}}
    return {"available": True, "schema": SCHEMA, "snapshot_id": payload["snapshot_id"], "items": items,
            "note": "추천·순위·청산에 반영되지 않는 참고자료입니다. KIS 공매도 거래비중은 순보유잔고가 아니며 대차잔고는 공매도 대기량이 아닙니다. 각 수집 시각과 자료 기준일을 확인하세요."}
