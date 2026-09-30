"""EINT-012: synthetic publications and disposable Mongo; no real orders, prices or DCA."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock

import pytest

from app.backend.db import get_db
from app.backend.routers import kqs
from test_kqs_native_views import native_publication


def buy_body(publication, request_id="manual-buy-0001", **extra):
    return {"signal_id": publication["signals"][0]["signal_id"], "decision": "buy",
            "quantity": 10, "fill_price": 100, "request_id": request_id, **extra}


async def bought(client, headers, publication, **extra):
    body = buy_body(publication, **extra)
    response = await client.post("/api/kqs/decisions", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


@pytest.mark.asyncio
async def test_retry_is_same_record_even_after_expiry_and_conflicts_do_not_write(native_publication, client, make_user, monkeypatch):
    publication, _ = native_publication
    headers = await make_user("manual-retry@example.com")
    first, body = await bought(client, headers, publication)
    def expired():
        raise RuntimeError("expired")
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", expired)
    retry = await client.post("/api/kqs/decisions", headers=headers, json=body)
    assert retry.status_code == 200
    assert retry.json() == first
    changed = await client.post("/api/kqs/decisions", headers=headers, json={**body, "quantity": 11})
    assert changed.status_code == 409
    assert await get_db().kqs_paper_decisions.count_documents({}) == 1
    assert await get_db().kqs_manual_write_locks.count_documents({}) == 0


@pytest.mark.asyncio
async def test_request_id_is_user_scoped_and_auth_required(native_publication, client, make_user):
    publication, _ = native_publication
    first = await make_user("manual-first@example.com")
    second = await make_user("manual-second@example.com")
    one, body = await bought(client, first, publication)
    two, _ = await bought(client, second, publication)
    assert one["id"] != two["id"]
    assert (await client.post("/api/kqs/decisions", json=body)).status_code == 401
    assert await get_db().kqs_paper_decisions.count_documents({}) == 2


@pytest.mark.asyncio
async def test_full_position_exit_retry_replays_when_no_lots_remain(native_publication, client, make_user):
    publication, _ = native_publication
    headers = await make_user("manual-sell-retry@example.com")
    await bought(client, headers, publication)
    symbol = publication["signals"][0]["symbol"]
    path = f"/api/kqs/decisions/positions/{symbol}/sell"
    body = {"quantity": 10, "fill_price": 120, "request_id": "manual-exit-0001"}
    first = await client.post(path, headers=headers, json=body)
    assert first.status_code == 200, first.text
    retry = await client.post(path, headers=headers, json=body)
    assert retry.json() == first.json()
    assert first.json()["remaining_quantity"] == 0
    assert (await client.get("/api/kqs/decisions/positions", headers=headers)).json()["items"] == []
    assert (await client.post(path, headers=headers, json={**body, "fill_price": 121})).status_code == 409
    assert await get_db().kqs_paper_decisions.count_documents({}) == 2


@pytest.mark.asyncio
async def test_concurrent_duplicate_buys_and_distinct_oversells_are_safe(native_publication, client, make_user):
    publication, _ = native_publication
    headers = await make_user("manual-concurrent@example.com")
    body = buy_body(publication)
    responses = await asyncio.gather(*(client.post("/api/kqs/decisions", headers=headers, json=body) for _ in range(2)))
    assert {response.status_code for response in responses} <= {200, 409}
    assert any(response.status_code == 200 for response in responses)
    assert await get_db().kqs_paper_decisions.count_documents({}) == 1
    retry = await client.post("/api/kqs/decisions", headers=headers, json=body)
    assert retry.status_code == 200
    symbol = publication["signals"][0]["symbol"]
    path = f"/api/kqs/decisions/positions/{symbol}/sell"
    responses = await asyncio.gather(*(client.post(path, headers=headers, json={
        "quantity": 7, "fill_price": 120, "request_id": f"manual-race-{i:04d}"}) for i in range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    positions = (await client.get("/api/kqs/decisions/positions", headers=headers)).json()
    assert positions["items"][0]["quantity"] == 3
    assert positions["data_issues"] == []
    assert await get_db().kqs_manual_write_locks.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"trade_at": "2026-09-29T12:00:00"},  # timezone missing
    {"trade_at": "2099-01-01T00:00:00Z"},
    {"trade_at": "2026-09-29T01:00:00Z", "trade_date": "2026-09-28"},
    {"trade_at": "2026-09-28T00:00:00Z"},  # before publication
    {"quantity": float("inf")}, {"fee_krw": float("nan")},
    {"quantity": 1e308, "fill_price": 1e308}, {"request_id": "bad id"}, {"quantity": 1e-9},
])
async def test_bad_trade_time_or_nonfinite_money_never_writes(native_publication, client, make_user, change):
    publication, _ = native_publication
    headers = await make_user("manual-invalid@example.com")
    response = await client.post("/api/kqs/decisions", headers={**headers, "Content-Type": "application/json"},
                                 content=json.dumps(buy_body(publication, **change)))
    assert response.status_code == 422, response.text
    assert await get_db().kqs_paper_decisions.count_documents({}) == 0
    assert await get_db().kqs_manual_write_locks.count_documents({}) == 0


@pytest.mark.asyncio
async def test_trade_time_record_time_and_date_only_are_distinct(native_publication, client, make_user):
    publication, _ = native_publication
    headers = await make_user("manual-times@example.com")
    at = datetime.now(timezone.utc) - timedelta(minutes=2)
    first, _ = await bought(client, headers, publication, trade_at=at.isoformat())
    assert first["trade_time_source"] == "user_time"
    assert first["trade_at"] != first["recorded_at"]
    assert first["recorded_at"] == first["decided_at"]
    today = datetime.now(kqs.KST).date().isoformat()
    path = f'/api/kqs/decisions/positions/{publication["signals"][0]["symbol"]}/sell'
    response = await client.post(path, headers=headers, json={
        "quantity": 1, "fill_price": 120, "trade_date": today, "request_id": "manual-date-only"})
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["trade_time_source"] == "user_date_only"
    assert item["trade_at"] is None and item["trade_date"] == today


@pytest.mark.asyncio
async def test_backdated_sell_cannot_consume_later_buys(native_publication, client, make_user):
    publication, _ = native_publication
    headers = await make_user("manual-chronology@example.com")
    first, _ = await bought(client, headers, publication)
    yesterday = (datetime.now(kqs.KST) - timedelta(days=1)).date().isoformat()
    path = f'/api/kqs/decisions/positions/{publication["signals"][0]["symbol"]}/sell'
    response = await client.post(path, headers=headers, json={"quantity": 1, "fill_price": 120,
                                                            "trade_date": yesterday, "request_id": "manual-backdated"})
    assert response.status_code == 422, response.text
    assert (await client.get("/api/kqs/decisions/positions", headers=headers)).json()["items"][0]["quantity"] == 10
    assert await get_db().kqs_paper_decisions.count_documents({}) == 1


@pytest.mark.asyncio
async def test_audit_failure_does_not_turn_commit_into_duplicate_retry(native_publication, client, make_user, monkeypatch):
    publication, _ = native_publication
    headers = await make_user("manual-audit@example.com")
    audit = AsyncMock(side_effect=RuntimeError("synthetic audit outage"))
    monkeypatch.setattr(kqs, "append_execution_audit", audit)
    first, body = await bought(client, headers, publication)
    retry = await client.post("/api/kqs/decisions", headers=headers, json=body)
    assert retry.status_code == 200 and retry.json() == first
    assert audit.await_count == 1
    assert await get_db().kqs_paper_decisions.count_documents({}) == 1
    assert await get_db().kqs_manual_write_locks.count_documents({}) == 0


@pytest.mark.asyncio
async def test_failed_validation_releases_lock_and_stale_lock_fails_closed(native_publication, client, make_user, monkeypatch):
    publication, _ = native_publication
    headers = await make_user("manual-lock@example.com")
    user = await get_db().users.find_one({"email": "manual-lock@example.com"})
    with monkeypatch.context() as patch:
        patch.setattr(kqs, "trade_fields", lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
        with pytest.raises(RuntimeError):
            await client.post("/api/kqs/decisions", headers=headers, json=buy_body(publication))
    assert await get_db().kqs_manual_write_locks.count_documents({}) == 0
    await get_db().kqs_manual_write_locks.insert_one({"_id": f'kqs-manual:{user["_id"]}', "token": "abandoned"})
    response = await client.post("/api/kqs/decisions", headers=headers, json=buy_body(publication))
    assert response.status_code == 409
    assert await get_db().kqs_paper_decisions.count_documents({}) == 0
    assert await get_db().kqs_manual_write_locks.count_documents({}) == 1


@pytest.mark.asyncio
async def test_existing_bad_ledger_is_not_silently_repaired(native_publication, client, make_user):
    publication, _ = native_publication
    headers = await make_user("manual-old-error@example.com")
    user = await get_db().users.find_one({"email": "manual-old-error@example.com"})
    symbol = publication["signals"][0]["symbol"]
    result = await get_db().kqs_paper_decisions.insert_one({"user_id": user["_id"], "symbol_key": symbol,
        "symbol": symbol, "decision": "sell", "quantity": 2, "fill_price": 100,
        "decided_at": datetime.now(timezone.utc) - timedelta(days=1)})
    response = await client.post("/api/kqs/decisions", headers=headers, json=buy_body(publication))
    assert response.status_code == 409
    assert await get_db().kqs_paper_decisions.find_one({"_id": result.inserted_id}) is not None
    assert await get_db().kqs_paper_decisions.count_documents({}) == 1


@pytest.mark.asyncio
async def test_same_day_earlier_exact_time_cannot_reorder_fifo(native_publication, client, make_user):
    publication, _ = native_publication
    headers = await make_user("manual-intraday@example.com")
    at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await bought(client, headers, publication, trade_at=at.isoformat())
    path = f'/api/kqs/decisions/positions/{publication["signals"][0]["symbol"]}/sell'
    response = await client.post(path, headers=headers, json={
        "quantity": 1, "fill_price": 120, "trade_at": (at - timedelta(seconds=1)).isoformat(),
        "request_id": "manual-intraday-sell"})
    assert response.status_code == 422, response.text
    assert await get_db().kqs_paper_decisions.count_documents({}) == 1
    assert await get_db().kqs_manual_write_locks.count_documents({}) == 0


@pytest.mark.asyncio
async def test_account_trade_limit_rejects_before_insert(native_publication, client, make_user, monkeypatch):
    publication, _ = native_publication
    headers = await make_user("manual-limit@example.com")
    db = get_db()
    collection_type = type(db.kqs_paper_decisions)
    original = collection_type.count_documents

    async def at_limit(collection, query, *args, **kwargs):
        if collection.name == "kqs_paper_decisions":
            assert query["decision"] == {"$in": ["buy", "sell"]}
            assert "user_id" in query
            return 10_000
        return await original(collection, query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(collection_type, "count_documents", at_limit)
        response = await client.post("/api/kqs/decisions", headers=headers, json=buy_body(publication))
    assert response.status_code == 409, response.text
    assert await db.kqs_paper_decisions.count_documents({}) == 0
    assert await db.kqs_manual_write_locks.count_documents({}) == 0
