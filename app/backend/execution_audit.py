"""주문 관련 행위의 사용자별 변조 감지 감사 원장.

각 이벤트는 이전 해시를 포함한 SHA-256 체인으로 저장한다. 애플리케이션은 갱신·삭제
함수를 제공하지 않으며, 검증 API는 원장이 바뀌었는지만 알려 준다. 이는 외부 WORM
보관소를 대체하지 않으며, 실거래 전에는 별도 보관소로 복제해야 한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import hashlib
import json
from typing import Any

from pymongo import ReturnDocument

from .db import get_db

_append_locks: dict[str, tuple[asyncio.Lock, int]] = {}
_append_locks_guard = asyncio.Lock()


async def _acquire_user_lock(user_id) -> asyncio.Lock:
    """사용자별 Lock을 참조 카운트로 관리해, 대기자가 없으면 즉시 정리한다."""
    key = str(user_id)
    async with _append_locks_guard:
        lock, count = _append_locks.get(key, (asyncio.Lock(), 0))
        _append_locks[key] = (lock, count + 1)
    return lock


async def _release_user_lock(user_id) -> None:
    key = str(user_id)
    async with _append_locks_guard:
        lock, count = _append_locks[key]
        if count <= 1:
            del _append_locks[key]
        else:
            _append_locks[key] = (lock, count - 1)


def _canonical(value: Any) -> str:
    """ObjectId·datetime을 포함해 실행 환경에 독립적인 해시 입력을 만든다."""
    def default(item: Any) -> str:
        if isinstance(item, datetime):
            # MongoDB가 UTC datetime을 tzinfo 없이 역직렬화하는 경우도 같은 UTC 값으로
            # 해시되도록 처리한다.
            normalized = item.replace(tzinfo=timezone.utc) if item.tzinfo is None else item.astimezone(timezone.utc)
            return normalized.isoformat()
        return str(item)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=default)


def _event_hash(event: dict) -> str:
    signed = {
        "user_id": str(event["user_id"]), "sequence": event["sequence"],
        "action": event["action"], "resource": event["resource"],
        "payload": event["payload"], "occurred_at": event["occurred_at"],
        "previous_hash": event["previous_hash"],
    }
    return hashlib.sha256(_canonical(signed).encode("utf-8")).hexdigest()


async def append_execution_audit(user_id, action: str, resource: str, payload: dict | None = None) -> dict:
    """새 이벤트를 한 번만 추가한다. payload에는 자격증명·토큰을 넣지 않는다."""
    # 단일 백엔드 프로세스에서 같은 사용자의 빠른 연속 요청도 앞 이벤트가 기록된 뒤
    # 다음 해시를 계산하도록 직렬화한다. 다중 워커 실운영은 외부 append-only 저장소가
    # 준비되기 전까지 지원 범위가 아니다.
    lock = await _acquire_user_lock(user_id)
    try:
        async with lock:
            db = get_db()
            counter = await db.execution_audit_counters.find_one_and_update(
                {"user_id": user_id}, {"$inc": {"sequence": 1}}, upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            sequence = int(counter["sequence"])
            previous = await db.execution_audit_events.find_one(
                {"user_id": user_id, "sequence": sequence - 1}, {"event_hash": 1},
            )
            # BSON datetime은 밀리초까지만 보존하므로 해시 전부터 같은 정밀도로 맞춘다.
            occurred_at = datetime.now(timezone.utc)
            occurred_at = occurred_at.replace(microsecond=(occurred_at.microsecond // 1000) * 1000)
            event = {
                "user_id": user_id, "sequence": sequence, "action": action,
                "resource": resource, "payload": payload or {},
                "occurred_at": occurred_at,
                "previous_hash": (previous or {}).get("event_hash", "GENESIS"),
            }
            event["event_hash"] = _event_hash(event)
            await db.execution_audit_events.insert_one(event)
            return event
    finally:
        await _release_user_lock(user_id)


async def verify_execution_audit(user_id) -> dict:
    rows = await get_db().execution_audit_events.find({"user_id": user_id}).sort("sequence", 1).to_list(10_000)
    expected_previous = "GENESIS"
    for expected_sequence, row in enumerate(rows, start=1):
        if row.get("sequence") != expected_sequence or row.get("previous_hash") != expected_previous or row.get("event_hash") != _event_hash(row):
            return {"valid": False, "event_count": len(rows), "failed_sequence": row.get("sequence")}
        expected_previous = row["event_hash"]
    return {"valid": True, "event_count": len(rows), "last_hash": expected_previous if rows else None}


def serialize_execution_audit(event: dict) -> dict:
    return {
        "sequence": event["sequence"], "action": event["action"], "resource": event["resource"],
        "payload": event.get("payload", {}), "occurred_at": event["occurred_at"].isoformat(),
        "previous_hash": event["previous_hash"], "event_hash": event["event_hash"],
    }


async def export_execution_audit_ndjson(user_id) -> tuple[str, dict]:
    """외부 append-only/WORM 보관소 업로드용 검증 가능한 NDJSON을 만든다."""
    verification = await verify_execution_audit(user_id)
    rows = await get_db().execution_audit_events.find({"user_id": user_id}).sort("sequence", 1).to_list(10_000)
    manifest = {"type": "execution_audit_export", "verification": verification, "event_count": len(rows)}
    lines = [_canonical(manifest), *[_canonical(serialize_execution_audit(row)) for row in rows]]
    content = "\n".join(lines) + "\n"
    return content, {"sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), **verification}
