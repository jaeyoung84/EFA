"""관심종목·리서치 노트: 본인 데이터만, 상장 종목만, 탈퇴 시 함께 삭제."""
import pytest

from app.backend import instrument_catalog
from app.backend.db import get_db

CATALOG = [
    {"ticker": "005930.KS", "code": "005930", "name": "삼성전자", "market": "KOSPI", "source": "test"},
    {"ticker": "010170.KQ", "code": "010170", "name": "대한광통신", "market": "KOSDAQ", "source": "test"},
]


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    from app.backend.routers import research
    monkeypatch.setattr(research, "get_krx_catalog", lambda: CATALOG)
    monkeypatch.setattr(instrument_catalog, "get_krx_catalog", lambda: CATALOG)


@pytest.mark.asyncio
async def test_watchlist_add_list_remove_and_isolation(client, make_user):
    me, other = await make_user("watch-me@example.com"), await make_user("watch-other@example.com")
    added = await client.post("/api/watchlist", headers=me, json={"ticker": "010170.KQ", "memo": "광통신"})
    assert added.status_code == 201 and added.json()["name"] == "대한광통신"
    assert (await client.post("/api/watchlist", headers=me, json={"ticker": "010170.KQ"})).status_code == 409
    assert (await client.post("/api/watchlist", headers=me, json={"ticker": "999999.KS"})).status_code == 422
    assert (await client.post("/api/watchlist", headers=me, json={"ticker": "AAPL"})).status_code == 422
    assert [row["ticker"] for row in (await client.get("/api/watchlist", headers=me)).json()["items"]] == ["010170.KQ"]
    assert (await client.get("/api/watchlist", headers=other)).json()["items"] == []
    assert (await client.delete("/api/watchlist/010170.KQ", headers=other)).status_code == 404
    assert (await client.delete("/api/watchlist/010170.KQ", headers=me)).status_code == 200
    assert (await client.get("/api/watchlist")).status_code == 401


@pytest.mark.asyncio
async def test_notes_crud_search_and_isolation(client, make_user):
    me, other = await make_user("note-me@example.com"), await make_user("note-other@example.com")
    created = await client.post("/api/notes", headers=me, json={
        "title": "광통신 업황", "body": "데이터센터 투자 확대", "tickers": ["010170.KQ"], "tags": ["#통신", "통신", "AI"]})
    assert created.status_code == 201, created.text
    note = created.json()
    assert note["tickers"] == [{"ticker": "010170.KQ", "name": "대한광통신"}] and note["tags"] == ["통신", "AI"]
    await client.post("/api/notes", headers=me, json={"title": "반도체 메모", "body": "메모리 가격"})
    assert [n["title"] for n in (await client.get("/api/notes", headers=me, params={"q": "데이터센터"})).json()["items"]] == ["광통신 업황"]
    assert [n["title"] for n in (await client.get("/api/notes", headers=me, params={"q": "대한광"})).json()["items"]] == ["광통신 업황"]
    assert [n["title"] for n in (await client.get("/api/notes", headers=me, params={"ticker": "010170.KQ"})).json()["items"]] == ["광통신 업황"]
    assert [n["title"] for n in (await client.get("/api/notes", headers=me, params={"tag": "AI"})).json()["items"]] == ["광통신 업황"]
    assert len((await client.get("/api/notes", headers=me, params={"q": ".*"})).json()["items"]) == 0  # 정규식이 아니라 글자로 찾는다
    assert (await client.get("/api/notes", headers=other)).json()["items"] == []

    assert (await client.put(f"/api/notes/{note['id']}", headers=other, json={"title": "x"})).status_code == 404
    updated = await client.put(f"/api/notes/{note['id']}", headers=me, json={"title": "광통신 업황(수정)", "body": "b"})
    assert updated.status_code == 200 and updated.json()["title"] == "광통신 업황(수정)" and updated.json()["tickers"] == []
    assert (await client.put(f"/api/notes/{note['id']}", headers=me, json={"title": "  "})).status_code == 422
    assert (await client.post("/api/notes", headers=me, json={"title": "t", "tickers": ["000000.KS"]})).status_code == 422
    assert (await client.delete(f"/api/notes/{note['id']}", headers=other)).status_code == 404
    assert (await client.delete(f"/api/notes/{note['id']}", headers=me)).status_code == 200
    assert (await client.delete("/api/notes/not-an-id", headers=me)).status_code == 404


@pytest.mark.asyncio
async def test_withdrawal_also_deletes_watchlist_and_notes(client, make_user):
    me = await make_user("bye-research@example.com")
    await client.post("/api/watchlist", headers=me, json={"ticker": "005930.KS"})
    await client.post("/api/notes", headers=me, json={"title": "t"})
    user = await get_db().users.find_one({"email": "bye-research@example.com"})
    assert (await client.request("DELETE", "/api/auth/me", headers=me, json={"confirm": "탈퇴"})).status_code == 200
    assert await get_db().user_watchlist.count_documents({"user_id": user["_id"]}) == 0
    assert await get_db().research_notes.count_documents({"user_id": user["_id"]}) == 0
