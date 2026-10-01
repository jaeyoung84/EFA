"""관심종목과 리서치 노트: 로그인 회원 본인만 보고 고치는 개인 메모 기능.

관심종목은 국내 상장 목록(엔진이 게시한 KRX 목록)에 있는 종목만 담는다. 시세는 화면이
기존 /api/market/snapshot으로 따로 조회하며, 여기서는 주문·알림·자동매매를 만들지 않는다.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

try:
    from ..db import get_db
    from ..instrument_catalog import get_krx_catalog
    from .auth import current_user
except ImportError:  # Allows `uvicorn main:app` from app/backend.
    from db import get_db  # type: ignore
    from instrument_catalog import get_krx_catalog  # type: ignore
    from routers.auth import current_user  # type: ignore

router = APIRouter(prefix="/api", tags=["관심종목·리서치 노트"])

MAX_WATCHLIST = 50
MAX_NOTES = 1000
TICKER_PATTERN = r"^[0-9A-Z]{6}\.(KS|KQ)$"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


async def _ensure_indexes() -> None:
    db = get_db()
    await db.user_watchlist.create_index([("user_id", 1), ("ticker", 1)], unique=True)
    await db.research_notes.create_index([("user_id", 1), ("updated_at", -1)])


def _listed(ticker: str) -> dict:
    for item in get_krx_catalog():
        if item.get("ticker") == ticker:
            return item
    raise HTTPException(status_code=422, detail="현재 국내 상장 목록에 없는 종목입니다. 종목명으로 검색해 고르세요.")


# ── 관심종목 ──────────────────────────────────────────────

class WatchlistInput(BaseModel):
    ticker: str = Field(pattern=TICKER_PATTERN)
    memo: str = Field(default="", max_length=200)


def _watch_row(document: dict) -> dict:
    return {"ticker": document["ticker"], "code": document["code"], "name": document["name"],
            "market": document.get("market", ""), "memo": document.get("memo", ""),
            "added_at": _iso(document.get("added_at"))}


@router.get("/watchlist")
async def list_watchlist(user: dict = Depends(current_user)) -> dict:
    rows = await get_db().user_watchlist.find({"user_id": user["_id"]}).sort("added_at", 1).to_list(MAX_WATCHLIST)
    return {"items": [_watch_row(row) for row in rows], "limit": MAX_WATCHLIST}


@router.post("/watchlist", status_code=status.HTTP_201_CREATED)
async def add_watchlist(payload: WatchlistInput, user: dict = Depends(current_user)) -> dict:
    await _ensure_indexes()
    item = _listed(payload.ticker)
    db = get_db()
    if await db.user_watchlist.count_documents({"user_id": user["_id"]}) >= MAX_WATCHLIST:
        raise HTTPException(status_code=409, detail=f"관심종목은 최대 {MAX_WATCHLIST}개까지 담을 수 있습니다.")
    document = {"user_id": user["_id"], "ticker": item["ticker"], "code": item.get("code", item["ticker"][:6]),
                "name": item.get("name", item["ticker"]), "market": item.get("market", ""),
                "memo": payload.memo.strip(), "added_at": _now()}
    try:
        await db.user_watchlist.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="이미 관심종목에 있습니다.") from exc
    return _watch_row(document)


@router.delete("/watchlist/{ticker}")
async def remove_watchlist(ticker: str, user: dict = Depends(current_user)) -> dict:
    result = await get_db().user_watchlist.delete_one({"user_id": user["_id"], "ticker": ticker})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="관심종목에 없는 종목입니다.")
    return {"deleted": True}


# ── 리서치 노트 ───────────────────────────────────────────

class NoteInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=20_000)
    tickers: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=10)


def _clean_tags(values: list[str]) -> list[str]:
    seen, tags = set(), []
    for value in values:
        tag = value.strip().lstrip("#")[:30]
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def _note_tickers(values: list[str]) -> list[dict]:
    linked, seen = [], set()
    for ticker in values:
        if ticker in seen:
            continue
        if not re.fullmatch(TICKER_PATTERN, ticker):
            raise HTTPException(status_code=422, detail="노트에 연결할 종목은 종목명으로 검색해 고르세요.")
        item = _listed(ticker)
        seen.add(ticker)
        linked.append({"ticker": item["ticker"], "name": item.get("name", ticker)})
    return linked


def _note_row(document: dict) -> dict:
    return {"id": str(document["_id"]), "title": document["title"], "body": document.get("body", ""),
            "tickers": document.get("tickers", []), "tags": document.get("tags", []),
            "created_at": _iso(document.get("created_at")), "updated_at": _iso(document.get("updated_at"))}


def _note_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=404, detail="노트를 찾을 수 없습니다.") from exc


@router.get("/notes")
async def list_notes(q: str = Query(default="", max_length=60), ticker: str = Query(default="", max_length=12),
                     tag: str = Query(default="", max_length=30), user: dict = Depends(current_user)) -> dict:
    query: dict = {"user_id": user["_id"]}
    if q.strip():
        pattern = re.escape(q.strip())
        query["$or"] = [{"title": {"$regex": pattern, "$options": "i"}}, {"body": {"$regex": pattern, "$options": "i"}},
                        {"tickers.name": {"$regex": pattern, "$options": "i"}}]
    if ticker:
        query["tickers.ticker"] = ticker
    if tag:
        query["tags"] = tag.lstrip("#")
    rows = await get_db().research_notes.find(query).sort("updated_at", -1).to_list(200)
    return {"items": [_note_row(row) for row in rows]}


@router.post("/notes", status_code=status.HTTP_201_CREATED)
async def create_note(payload: NoteInput, user: dict = Depends(current_user)) -> dict:
    await _ensure_indexes()
    db = get_db()
    if await db.research_notes.count_documents({"user_id": user["_id"]}) >= MAX_NOTES:
        raise HTTPException(status_code=409, detail=f"리서치 노트는 최대 {MAX_NOTES}개까지 저장할 수 있습니다.")
    now = _now()
    document = {"user_id": user["_id"], "title": payload.title.strip(), "body": payload.body,
                "tickers": _note_tickers(payload.tickers), "tags": _clean_tags(payload.tags),
                "created_at": now, "updated_at": now}
    if not document["title"]:
        raise HTTPException(status_code=422, detail="제목을 입력하세요.")
    result = await db.research_notes.insert_one(document)
    document["_id"] = result.inserted_id
    return _note_row(document)


@router.put("/notes/{note_id}")
async def update_note(note_id: str, payload: NoteInput, user: dict = Depends(current_user)) -> dict:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="제목을 입력하세요.")
    document = await get_db().research_notes.find_one_and_update(
        {"_id": _note_id(note_id), "user_id": user["_id"]},
        {"$set": {"title": title, "body": payload.body, "tickers": _note_tickers(payload.tickers),
                  "tags": _clean_tags(payload.tags), "updated_at": _now()}},
        return_document=ReturnDocument.AFTER)
    if not document:
        raise HTTPException(status_code=404, detail="노트를 찾을 수 없습니다.")
    return _note_row(document)


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str, user: dict = Depends(current_user)) -> dict:
    result = await get_db().research_notes.delete_one({"_id": _note_id(note_id), "user_id": user["_id"]})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="노트를 찾을 수 없습니다.")
    return {"deleted": True}
