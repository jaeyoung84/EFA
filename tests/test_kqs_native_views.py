"""EINT-011 authenticated web flow: synthetic publication and disposable test Mongo only."""
from datetime import date, datetime, timedelta, timezone
import hashlib

import pytest

from app.backend.db import get_db
from app.backend.routers import kqs
from engine.kqs.tests.test_runtime import synthetic_inputs
from kqs_runtime.recommendations import generate_recommendations
from kqs_publication.store import publish
from kqs_publication.writer import WriterLock


@pytest.fixture
def native_publication(monkeypatch, tmp_path):
    import kqs_publication.contract as contract
    import kqs_publication.store as store
    now = datetime(2026, 9, 29, 0, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            result = now + timedelta(minutes=1)
            return result if tz is None else result.astimezone(tz)
    monkeypatch.setattr(contract, "datetime", Clock)
    monkeypatch.setattr(store, "datetime", Clock)
    history, benchmarks, _ = synthetic_inputs()
    offset = (date(2026, 9, 28) - history["Date"].max().date()).days
    history["Date"] += timedelta(days=offset)
    benchmarks = {key: value.set_axis(value.index + timedelta(days=offset)) for key, value in benchmarks.items()}
    runtime = generate_recommendations(history, benchmarks, as_of="2026-09-28")
    path = tmp_path / "recommendations.sqlite3"
    with WriterLock(path) as lock:
        publish(path, runtime, expected_as_of="2026-09-28", generated_at=now,
                expires_at=now + timedelta(hours=2), writer_lock=lock)
    monkeypatch.setenv("KQS_RECOMMENDATION_SOURCE", "snapshot")
    monkeypatch.setenv("KQS_SNAPSHOT_DB", str(path))
    def no_network(*_, **__):
        raise AssertionError("Native views must never request the PC bridge")
    monkeypatch.setattr(kqs.urllib.request, "urlopen", no_network)
    return kqs.read_local_recommendations(), path


@pytest.mark.asyncio
async def test_native_review_history_is_separate_and_current_snapshot_scoped(native_publication, client, make_user):
    payload, path = native_publication
    headers = await make_user("native-review@example.com")
    user = await get_db().users.find_one({"email": "native-review@example.com"})
    signal = payload["signals"][0]
    old = {"user_id": user["_id"], "as_of": payload["engine"]["data_as_of"], "symbol_key": signal["symbol"],
           "symbol": signal["symbol"], "name": signal["name"], "status": "reviewed", "note": "legacy memo",
           "updated_at": datetime.now(timezone.utc)}
    await get_db().kqs_candidate_reviews.insert_one(dict(old))
    await get_db().kqs_native_candidate_reviews.insert_one({**old, "publication_snapshot_id": "old-native-snapshot"})
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    queue = await client.get("/api/kqs/review-candidates", headers=headers)
    assert queue.status_code == 200, queue.text
    assert queue.json()["candidates"][0]["review"] is None
    body = {"as_of": payload["engine"]["data_as_of"], "status": "deferred", "note": "native memo"}
    recorded = await client.post(f'/api/kqs/review-candidates/{signal["symbol"]}/review', headers=headers, json=body)
    assert recorded.status_code == 200, recorded.text
    native = await get_db().kqs_native_candidate_reviews.find_one({"user_id": user["_id"]})
    assert native["publication_snapshot_id"] == payload["snapshot_id"]
    assert native["signal_id"] == signal["signal_id"]
    assert (await get_db().kqs_candidate_reviews.find_one({"user_id": user["_id"]}))["note"] == "legacy memo"
    packet = await client.get(f'/api/kqs/review-candidates/{signal["symbol"]}/packet', headers=headers)
    assert packet.json()["review"]["note"] == "native memo"
    snapshots = (await client.get("/api/kqs/review-candidates/review-snapshots", headers=headers)).json()
    assert snapshots["items"][0]["packet"]["snapshot_id"] == payload["snapshot_id"]
    assert snapshots["items"][0]["packet"]["selection_evidence"]["reasons"] == signal["reasons"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    for name in ("order_proposals", "paper_fills", "kqs_paper_decisions"):
        assert await get_db()[name].count_documents({}) == 0


@pytest.mark.asyncio
async def test_native_current_workflow_is_not_legacy_or_old_snapshot(native_publication, client, make_user):
    payload, _ = native_publication
    headers = await make_user("native-workflow@example.com")
    await get_db().kqs_candidate_queue_snapshots.insert_one({"snapshot_date": "2026-09-29", "as_of": "2026-09-28",
                                                           "candidates": [{"symbol_key": "LEGACY", "symbol": "LEGACY"}]})
    response = await client.get("/api/kqs/candidate-queue/review-workflow", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["snapshot_id"] == payload["snapshot_id"]
    assert {item["symbol"] for item in response.json()["items"]} == {item["symbol"] for item in payload["signals"]}
    assert await get_db().kqs_native_candidate_queue_snapshots.count_documents({}) == 0


@pytest.mark.asyncio
async def test_native_queue_capture_keeps_legacy_history_untouched(native_publication, client, make_user):
    payload, _ = native_publication
    headers = await make_user("native-capture@example.com")
    day = datetime.now(kqs.KST).date().isoformat()
    await get_db().kqs_candidate_queue_snapshots.insert_one({"snapshot_date": day, "as_of": "2026-09-23", "candidates": []})
    captured = await client.post("/api/kqs/candidate-queue/snapshot", headers=headers)
    assert captured.status_code == 200, captured.text
    assert captured.json()["stored"] is True
    assert captured.json()["snapshot_id"] == payload["snapshot_id"]
    assert captured.json()["source"] == "snapshot"
    assert (await get_db().kqs_candidate_queue_snapshots.find_one({"snapshot_date": day}))["as_of"] == "2026-09-23"
    history = await client.get("/api/kqs/candidate-queue/history", headers=headers)
    assert history.json()["snapshots"][0]["as_of"] == payload["engine"]["data_as_of"]
    assert history.json()["snapshots"][0]["snapshot_id"] == payload["snapshot_id"]


@pytest.mark.asyncio
async def test_native_review_is_personal_not_a_trade(native_publication, client, make_user):
    payload, _ = native_publication
    first = await make_user("native-first@example.com")
    second = await make_user("native-second@example.com")
    signal = payload["signals"][0]
    path = f'/api/kqs/review-candidates/{signal["symbol"]}/review'
    body = {"as_of": payload["engine"]["data_as_of"], "status": "reviewed", "note": "personal"}
    assert (await client.post(path, headers=first, json=body)).status_code == 200
    queue = await client.get("/api/kqs/review-candidates", headers=second)
    assert queue.json()["candidates"][0]["review"] is None
    wrong = await client.post(path, headers=first, json={**body, "as_of": "2026-09-23"})
    assert wrong.status_code == 409
    assert await get_db().kqs_paper_decisions.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "snapshot", "bridge"], ids=["default-native", "explicit-native", "explicit-bridge"])
async def test_scheduler_candidate_price_targets_follow_selected_source(monkeypatch, source):
    """Read targets only: no schedules, quotes, provider requests or DCA execution."""
    from app.backend import scheduler

    if source is None:
        monkeypatch.delenv("KQS_RECOMMENDATION_SOURCE", raising=False)
    else:
        monkeypatch.setenv("KQS_RECOMMENDATION_SOURCE", source)
    db = get_db()
    await db.kqs_native_candidate_queue_snapshots.insert_one({"candidates": [
        {"symbol": "000001", "market": "KOSPI"},
        {"symbol": "000002", "market": "kosdaq"},
        {"symbol": "000001", "market": "KOSPI"},  # deduplication
    ]})
    await db.kqs_candidate_queue_snapshots.insert_one({"candidates": [
        {"symbol": "000004", "market": "KOSPI"},
        {"symbol": "000005", "market": "KOSDAQ"},
    ]})
    await db.portfolios.insert_one({"holdings": [{"ticker": "MSFT", "currency": "USD"}]})
    await db.strategies.insert_one({"type": "real_backtest", "ticker": "AAPL"})
    await db.kqs_paper_decisions.insert_one({"symbol": "000003", "market": "KOSPI"})
    names = ("kqs_native_candidate_queue_snapshots", "kqs_candidate_queue_snapshots",
             "portfolios", "strategies", "kqs_paper_decisions")
    before = {name: await db[name].find({}).to_list(None) for name in names}

    def forbidden(*args, **kwargs):
        raise AssertionError("Collecting price targets must not execute a schedule, DCA or provider")
    for name in ("start_scheduler", "fetch_quote_cached", "get_daily_ohlcv", "run_leverage_dca_daily_cycle"):
        monkeypatch.setattr(scheduler, name, forbidden)
    monkeypatch.setattr(kqs.urllib.request, "urlopen", forbidden)

    targets = await scheduler.collect_active_tickers()
    selected = {"000004.KS", "000005.KQ"} if source == "bridge" else {"000001.KS", "000002.KQ"}
    assert targets == selected | {"MSFT", "AAPL", "000003.KS", "KRW=X"}
    after = {name: await db[name].find({}).to_list(None) for name in names}
    assert before == after
    for name in ("order_proposals", "paper_fills", "market_data_refresh_jobs", "market_ohlcv"):
        assert await db[name].count_documents({}) == 0


@pytest.mark.asyncio
async def test_scheduler_invalid_candidate_source_fails_without_legacy_fallback(monkeypatch):
    from fastapi import HTTPException
    from app.backend import scheduler

    monkeypatch.setenv("KQS_RECOMMENDATION_SOURCE", "invalid-source")
    db = get_db()
    await db.kqs_candidate_queue_snapshots.insert_one({"candidates": [{"symbol": "000004", "market": "KOSPI"}]})
    before = await db.kqs_candidate_queue_snapshots.find({}).to_list(None)
    with pytest.raises(HTTPException) as failure:
        await scheduler.collect_active_tickers()
    assert failure.value.status_code == 503
    assert await db.kqs_candidate_queue_snapshots.find({}).to_list(None) == before
