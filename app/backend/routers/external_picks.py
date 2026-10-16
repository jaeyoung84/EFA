"""외부 추천 기록 API. 로그인 사용자 본인 명단만 저장·조회·삭제한다(다른 회원에게 보이지 않음)."""
from __future__ import annotations

import asyncio

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

try:
    from .. import external_picks as picks
    from .auth import current_user
except ImportError:  # pragma: no cover
    import external_picks as picks  # type: ignore
    from routers.auth import current_user  # type: ignore

router = APIRouter(prefix="/api/external-picks", tags=["외부 추천 기록"])


class ParseInput(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


class PickItem(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    market: str = Field(pattern=r"^(US|KR)$")
    name: str = Field(default="", max_length=80)


class SaveInput(BaseModel):
    source: str = Field(min_length=1, max_length=60)
    list_name: str = Field(default="", max_length=80)
    as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    items: list[PickItem] = Field(min_length=1, max_length=picks.MAX_TICKERS)
    raw_text: str = Field(default="", max_length=200_000)


@router.post("/parse")
async def parse(payload: ParseInput, user: dict = Depends(current_user)) -> dict:
    """저장 전 미리보기: 붙여 넣은 글·CSV에서 종목을 인식한다(저장하지 않음)."""
    return picks.parse_candidates(payload.text, await picks.us_directory())


@router.post("/parse-pdf")
async def parse_pdf(request: Request, user: dict = Depends(current_user)) -> dict:
    """PDF 본문(application/pdf)을 받아 달별 글자로 나눠 돌려준다(저장하지 않음). 종목 인식은 /parse로 한다."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > picks.PDF_MAX_BYTES:
        raise HTTPException(status_code=413, detail="PDF는 8MB 이하만 올릴 수 있습니다.")
    data = await request.body()
    try:
        sections = await asyncio.to_thread(picks.pdf_sections, data)
    except picks.ExternalPicksError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"sections": sections}


@router.post("")
async def save(payload: SaveInput, user: dict = Depends(current_user)) -> dict:
    try:
        doc = await picks.save_list(user["_id"], source=payload.source, list_name=payload.list_name, as_of=payload.as_of,
                                    items=[item.model_dump() for item in payload.items], raw_text=payload.raw_text)
    except picks.ExternalPicksError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    tickers = {item["ticker"] for item in doc["items"]} | {picks.BENCHMARK[item["market"]] for item in doc["items"]}
    picks.schedule_prices(tickers)
    return {"id": str(doc["_id"]), "count": len(doc["items"]), "as_of": doc["as_of"]}


@router.get("")
async def report(user: dict = Depends(current_user)) -> JSONResponse:
    return JSONResponse(content=await picks.user_report(user["_id"]), headers={"Cache-Control": "no-store"})


@router.delete("/{list_id}")
async def delete(list_id: str, user: dict = Depends(current_user)) -> dict:
    try:
        oid = ObjectId(list_id)
    except (InvalidId, TypeError) as exc:
        raise HTTPException(status_code=404, detail="명단을 찾을 수 없습니다.") from exc
    if not await picks.delete_list(user["_id"], oid):
        raise HTTPException(status_code=404, detail="명단을 찾을 수 없습니다.")
    return {"deleted": True}
