"""KQS human-record safety only. No quotes, schedules, engine execution or DCA."""
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import math
from uuid import uuid4
from zoneinfo import ZoneInfo

from bson import ObjectId
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)


class ManualRecordInput(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    request_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    trade_at: datetime | None = None
    trade_date: date | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_nonfinite_json(cls, value):
        # A raw NaN/Infinity in Pydantic error input otherwise breaks JSONResponse itself.
        def check(item):
            if isinstance(item, float) and not math.isfinite(item):
                raise HTTPException(422, detail="금액·수량 입력에는 NaN/무한대를 사용할 수 없습니다.")
            if isinstance(item, dict):
                for nested in item.values():
                    check(nested)
            elif isinstance(item, list):
                for nested in item:
                    check(nested)
        check(value)
        return value

    @field_validator("trade_at")
    @classmethod
    def aware_trade_time(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("거래 시각에는 시간대(+09:00 또는 Z)가 필요합니다.")
            value = value.astimezone(timezone.utc)
            return value.replace(microsecond=value.microsecond // 1000 * 1000)
        return value


def request_identity(payload, user_id, scope):
    if payload.request_id is None:
        return {}
    body = payload.model_dump(mode="json", exclude={"request_id"})
    fingerprint = hashlib.sha256(json.dumps({"scope": scope, "body": body}, sort_keys=True,
                                           ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    # Mongo's built-in _id uniqueness is the atomic deduplication gate; no sparse-index migration.
    key = hashlib.sha256(json.dumps([str(user_id), payload.request_id]).encode()).hexdigest()
    return {"_id": ObjectId(key[:24]), "request_id": payload.request_id, "request_fingerprint": fingerprint}


async def replay_record(db, payload, user_id, scope):
    identity = request_identity(payload, user_id, scope)
    if not identity:
        return None
    row = await db.kqs_paper_decisions.find_one({"_id": identity["_id"], "user_id": user_id})
    if row and (row.get("request_id") != payload.request_id or row.get("request_fingerprint") != identity["request_fingerprint"]):
        raise HTTPException(409, detail="같은 request_id에 다른 입력을 사용할 수 없습니다.")
    return row


@asynccontextmanager
async def manual_write_lock(db, user_id):
    """No expiry: an expired lease could let two writers commit an oversell."""
    key, token = f"kqs-manual:{user_id}", uuid4().hex
    collection = db.kqs_manual_write_locks
    try:
        await collection.insert_one({"_id": key, "token": token, "acquired_at": datetime.now(timezone.utc)})
    except DuplicateKeyError as exc:
        raise HTTPException(409, detail="다른 수동 기록을 저장 중입니다. 같은 요청 ID로 잠시 후 재시도하세요.") from exc
    try:
        yield
    finally:
        await asyncio.shield(collection.delete_one({"_id": key, "token": token}))


def trade_fields(payload, now):
    at, day = payload.trade_at, payload.trade_date
    if at is not None and at > now:
        raise HTTPException(422, detail="미래 거래 시각은 기록할 수 없습니다.")
    if at is not None and day is not None and at.astimezone(KST).date() != day:
        raise HTTPException(422, detail="거래일과 거래 시각의 KST 날짜가 다릅니다.")
    if at is not None:
        day, origin = at.astimezone(KST).date(), "user_time"
    elif day is not None:
        origin = "user_date_only"
    else:
        at, day, origin = now, now.astimezone(KST).date(), "record_time_default"
    if day > now.astimezone(KST).date():
        raise HTTPException(422, detail="미래 거래일은 기록할 수 없습니다.")
    quantity, price = payload.quantity, payload.fill_price
    if quantity <= 1e-8:
        raise HTTPException(422, detail="수량은 원장 정밀도(1e-8)를 초과해야 합니다.")
    if not math.isfinite(quantity * price) or not math.isfinite(payload.fee_krw + payload.tax_krw):
        raise HTTPException(422, detail="수량·가격·비용의 계산 범위를 초과했습니다.")
    return {"trade_at": at, "trade_date": day.isoformat(), "trade_time_source": origin}


async def audit_without_rollback(callback, *args):
    """The ledger commit must not become a false failure after an audit outage."""
    try:
        await callback(*args)
    except Exception:
        logger.exception("KQS 수동 원장 저장 후 감사 기록 실패; 원장은 보존됩니다.")
