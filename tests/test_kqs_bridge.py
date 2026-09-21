import pytest
from datetime import datetime, timedelta, timezone

from app.backend.db import get_db
from app.backend import scheduler
from app.backend.routers import kqs


def test_bridge_summary_redacts_raw_logs_and_remains_read_only(monkeypatch):
    class Response:
        def read(self):
            return b'{"generated_at":"2026-09-17","health":{"ok":true,"bridge":"kqs","read_only":true},"scheduler":{"state":"Ready","last_result_ok":true},"pipeline":{"status":"ok","last":{"steps":{"1":{"rc":0}}}},"db":{"latest_trade_date":"2026-09-16"},"paper":{"positions":{"status_counts":{"OPEN":1}},"audit":{"latest":{"asof":"2026-09-16","selected":0}}},"forward":{},"hashlock":{"summary":{"ok":1}}}'
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(kqs.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    result = kqs._read_bridge_summary()
    assert result["available"] is True
    assert result["read_only"] is True
    assert result["paper"]["open_positions"] == 1
    assert "raw" not in str(result)


def test_review_candidates_extracts_only_latest_selected_entry_due_rows(monkeypatch):
    monkeypatch.setattr(kqs, "_read_bridge_payload", lambda: {
        "health": {"ok": True}, "paper": {"audit": {
            "latest": {"asof": "2026-09-16", "selected": 1, "entry_due": 1},
            "latest_entry_due": [
                {"AsOf": "2026-09-16", "Symbol": "298380", "Name": "에이비엘바이오", "Market": "KOSDAQ", "SignalType": "swing", "SelectionOutcome": "selected", "ExecutionTiming": "next_trading_session", "MarketCapRank": "19", "AverageTradingValue": "100000", "raw": "hide"},
                {"AsOf": "2026-09-16", "Symbol": "000660", "SelectionOutcome": "not_entry_signal"},
            ],
            "latest_signal_rows": [{"AsOf": "2026-09-16", "SelectionOutcome": "regime_gate_blocked"}],
        }},
    })

    result = kqs._read_engine_review_candidates()

    assert result["entry_due_count"] == 1
    assert result["blocked_signal_count"] == 1
    assert result["candidates"] == [{"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "execution_timing": "next_trading_session", "market_cap_rank": "19", "average_trading_value": "100000"}]
    assert "raw" not in str(result)


def test_review_packet_exposes_structured_evidence_without_raw_bridge_data(monkeypatch):
    monkeypatch.setattr(kqs, "_read_bridge_payload", lambda: {
        "health": {"ok": True}, "db": {"latest_trade_date": "2026-09-16", "latest_gap_days": 1, "rows_on_latest_by_market": [{"market": "KOSDAQ", "rows": 1820}]},
        "pipeline": {"status": "ok", "last": {"db_advanced": True}},
        "paper": {"audit": {"latest": {"asof": "2026-09-16"}, "latest_entry_due": [{
            "AsOf": "2026-09-16", "Symbol": "298380", "Name": "에이비엘바이오", "Market": "KOSDAQ", "SignalType": "swing", "SelectionOutcome": "selected", "ScanState": "entry_due_next_session", "ExecutionTiming": "next_trading_session", "MarketCapRank": "19", "MarketCapTier": "150", "AverageTradingValue": "100000", "ConfigFingerprint": "abc123", "raw": "hide",
        }]}},
    })

    result = kqs._read_engine_review_packet("298380.KQ")

    assert result["found"] is True
    assert result["candidate"]["symbol_key"] == "298380"
    assert result["selection_evidence"]["scan_state"] == "entry_due_next_session"
    assert result["market_context"]["market_rows_on_latest"] == 1820
    assert result["engine_context"]["pipeline_db_advanced"] is True
    assert "raw" not in str(result)


@pytest.mark.asyncio
async def test_kqs_status_requires_login(client):
    response = await client.get("/api/kqs/status")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_kqs_status_returns_summary_to_logged_in_user(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_summary", lambda: {"available": True, "read_only": True, "pipeline": {"status": "ok"}})
    response = await client.get("/api/kqs/status", headers=await make_user("kqs-status@example.com"))
    assert response.status_code == 200, response.text
    assert response.json()["read_only"] is True


@pytest.mark.asyncio
async def test_review_candidates_requires_login(client):
    response = await client.get("/api/kqs/review-candidates")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_review_packet_requires_login(client):
    response = await client.get("/api/kqs/review-candidates/298380/packet")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_review_packet_returns_current_candidate_and_own_review(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_engine_review_packet", lambda _symbol: {
        "available": True, "found": True, "read_only": True, "as_of": "2026-09-16",
        "candidate": {"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오"},
        "selection_evidence": {"outcome": "selected"}, "market_context": {}, "engine_context": {}, "disclaimer": "읽기 전용",
    })
    headers = await make_user("kqs-packet@example.com")
    user = await get_db().users.find_one({"email": "kqs-packet@example.com"})
    await get_db().kqs_candidate_reviews.insert_one({"user_id": user["_id"], "as_of": "2026-09-16", "symbol_key": "298380", "symbol": "298380", "name": "에이비엘바이오", "status": "reviewed", "note": "확인", "updated_at": datetime.now(timezone.utc)})

    response = await client.get("/api/kqs/review-candidates/298380/packet", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["review"]["status"] == "reviewed"


@pytest.mark.asyncio
async def test_review_candidates_returns_only_safe_latest_selected_rows(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_engine_review_candidates", lambda: {
        "available": True, "read_only": True, "as_of": "2026-09-16", "latest_selected_count": 1,
        "entry_due_count": 1, "blocked_signal_count": 2,
        "candidates": [{"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "execution_timing": "next_trading_session", "market_cap_rank": "19", "average_trading_value": "100000"}],
        "disclaimer": "읽기 전용",
    })
    response = await client.get("/api/kqs/review-candidates", headers=await make_user("kqs-candidate@example.com"))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["candidates"][0]["symbol"] == "298380"
    assert "raw" not in str(payload)


@pytest.mark.asyncio
async def test_candidate_review_is_user_scoped_and_cannot_modify_unknown_candidate(monkeypatch, client, make_user):
    queue = {
        "available": True, "read_only": True, "as_of": "2026-09-16", "latest_selected_count": 1,
        "entry_due_count": 1, "blocked_signal_count": 0,
        "candidates": [{"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "execution_timing": "next_trading_session", "market_cap_rank": "19", "average_trading_value": "100000"}],
        "disclaimer": "읽기 전용",
    }
    monkeypatch.setattr(kqs, "_read_engine_review_candidates", lambda: {**queue, "candidates": [dict(item) for item in queue["candidates"]]})
    monkeypatch.setattr(kqs, "_read_engine_review_packet", lambda symbol: {
        "available": True, "found": symbol == "298380", "read_only": True, "as_of": "2026-09-16",
        "candidate": {"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "execution_timing": "next_trading_session"},
        "selection_evidence": {"outcome": "selected"}, "market_context": {}, "engine_context": {}, "disclaimer": "읽기 전용",
    })
    headers = await make_user("kqs-review@example.com")

    missing = await client.post("/api/kqs/review-candidates/005930/review", headers=headers, json={"as_of": "2026-09-16", "status": "reviewed", "note": "대상이 아님"})
    assert missing.status_code == 404
    saved = await client.post("/api/kqs/review-candidates/298380/review", headers=headers, json={"as_of": "2026-09-16", "status": "deferred", "note": "실적 확인 후 재검토"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["review"]["status"] == "deferred"
    assert saved.json()["review"]["note"] == "실적 확인 후 재검토"
    assert "주문" in saved.json()["disclaimer"]

    current = await client.get("/api/kqs/review-candidates", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["candidates"][0]["review"]["status"] == "deferred"
    history = await client.get("/api/kqs/review-candidates/reviews", headers=headers)
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["symbol"] == "298380"
    snapshots = await client.get("/api/kqs/review-candidates/review-snapshots", headers=headers)
    assert snapshots.status_code == 200, snapshots.text
    assert snapshots.json()["items"][0]["packet"]["candidate"]["symbol"] == "298380"
    assert "raw" not in str(snapshots.json())


@pytest.mark.asyncio
async def test_candidate_review_history_requires_login(client):
    response = await client.get("/api/kqs/review-candidates/reviews")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_candidate_review_snapshot_history_requires_login(client):
    response = await client.get("/api/kqs/review-candidates/review-snapshots")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_candidate_queue_snapshot_tracks_new_and_removed_candidates(monkeypatch):
    candidate = {"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "execution_timing": "next_trading_session"}
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).astimezone(kqs.KST).date().isoformat()
    await get_db().kqs_candidate_queue_snapshots.insert_one({
        "snapshot_date": yesterday, "captured_at": datetime.now(timezone.utc), "as_of": yesterday,
        "summary": {}, "candidates": [{"symbol": "000660", "symbol_key": "000660", "name": "SK하이닉스", "market": "KOSPI", "signal_type": "swing", "execution_timing": "next_trading_session"}],
        "changes": {"baseline": True, "changed": False, "new": [], "removed": []},
    })
    monkeypatch.setattr(kqs, "_read_engine_review_candidates", lambda: {
        "available": True, "as_of": "2026-09-16", "latest_selected_count": 1, "entry_due_count": 1, "blocked_signal_count": 0,
        "candidates": [candidate],
    })

    saved = await kqs.capture_candidate_queue_snapshot()

    assert saved["stored"] is True
    assert saved["changes"]["new"][0]["symbol"] == "298380"
    assert saved["changes"]["removed"][0]["symbol"] == "000660"


@pytest.mark.asyncio
async def test_candidate_queue_history_returns_user_review_pending_alerts(client, make_user):
    headers = await make_user("kqs-queue-history@example.com")
    user = await get_db().users.find_one({"email": "kqs-queue-history@example.com"})
    today = datetime.now(timezone.utc).astimezone(kqs.KST).date().isoformat()
    candidates = [
        {"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "execution_timing": "next_trading_session"},
        {"symbol": "000660", "symbol_key": "000660", "name": "SK하이닉스", "market": "KOSPI", "signal_type": "swing", "execution_timing": "next_trading_session"},
    ]
    await get_db().kqs_candidate_queue_snapshots.insert_one({
        "snapshot_date": today, "captured_at": datetime.now(timezone.utc), "as_of": "2026-09-16",
        "summary": {"entry_due_count": 2}, "candidates": candidates,
        "changes": {"baseline": False, "changed": True, "new": [candidates[0]], "removed": []},
    })
    await get_db().kqs_candidate_reviews.insert_one({
        "user_id": user["_id"], "as_of": "2026-09-16", "symbol_key": "298380", "symbol": "298380", "name": "에이비엘바이오", "status": "reviewed", "note": "확인", "updated_at": datetime.now(timezone.utc),
    })

    response = await client.get("/api/kqs/candidate-queue/history", headers=headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["review_summary"]["reviewed_count"] == 1
    assert payload["review_summary"]["pending"][0]["symbol"] == "000660"
    assert {item["type"] for item in payload["alerts"]} == {"new_candidate", "review_pending"}


@pytest.mark.asyncio
async def test_candidate_queue_history_requires_login(client):
    response = await client.get("/api/kqs/candidate-queue/history")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_candidate_review_workflow_prioritizes_and_marks_overdue_items(monkeypatch, client, make_user):
    monkeypatch.setenv("KQS_REVIEW_SLA_HOURS", "4")
    monkeypatch.setenv("KQS_REVIEW_DUE_SOON_MINUTES", "60")
    headers = await make_user("kqs-workflow@example.com")
    user = await get_db().users.find_one({"email": "kqs-workflow@example.com"})
    today = datetime.now(timezone.utc).astimezone(kqs.KST).date().isoformat()
    observed_at = datetime.now(timezone.utc) - timedelta(hours=5)
    candidates = [
        {"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "execution_timing": "next_trading_session"},
        {"symbol": "000660", "symbol_key": "000660", "name": "SK하이닉스", "market": "KOSPI", "signal_type": "swing", "execution_timing": "watch"},
    ]
    await get_db().kqs_candidate_queue_snapshots.insert_one({
        "snapshot_date": today, "captured_at": observed_at, "opened_at": observed_at, "as_of": "2026-09-16",
        "summary": {}, "candidates": candidates, "changes": {"baseline": True, "changed": False, "new": [], "removed": []},
    })
    await get_db().kqs_candidate_reviews.insert_one({
        "user_id": user["_id"], "as_of": "2026-09-16", "symbol_key": "000660", "symbol": "000660",
        "name": "SK하이닉스", "status": "deferred", "note": "다음 공시 확인", "updated_at": datetime.now(timezone.utc),
    })

    response = await client.get("/api/kqs/candidate-queue/review-workflow", headers=headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["policy"]["sla_hours"] == 4
    assert payload["summary"] == {"total_count": 2, "reviewed_count": 1, "pending_count": 1,
                                  "high_priority_pending_count": 1, "due_soon_count": 0, "overdue_count": 1}
    assert payload["items"][0]["symbol"] == "298380"
    assert payload["items"][0]["priority"] == "high"
    assert payload["items"][0]["attention"] == "overdue"
    assert payload["items"][1]["attention"] == "completed"
    assert "주문" in payload["disclaimer"]


@pytest.mark.asyncio
async def test_candidate_review_workflow_requires_login(client):
    response = await client.get("/api/kqs/candidate-queue/review-workflow")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_candidate_review_report_selection_quality_and_operations_alerts_are_read_only(client, make_user):
    headers = await make_user("kqs-report@example.com")
    user = await get_db().users.find_one({"email": "kqs-report@example.com"})
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).astimezone(kqs.KST).date().isoformat()
    today = now.astimezone(kqs.KST).date().isoformat()
    candidate = {"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "execution_timing": "next_trading_session"}
    await get_db().kqs_candidate_queue_snapshots.insert_many([
        {"snapshot_date": yesterday, "captured_at": now - timedelta(days=1), "opened_at": now - timedelta(days=1), "as_of": yesterday,
         "summary": {}, "candidates": [candidate], "changes": {"baseline": True, "changed": False, "new": [], "removed": []}},
        {"snapshot_date": today, "captured_at": now, "opened_at": now, "as_of": today,
         "summary": {}, "candidates": [candidate], "changes": {"baseline": False, "changed": False, "new": [], "removed": []}},
    ])
    await get_db().kqs_candidate_reviews.insert_one({
        "user_id": user["_id"], "as_of": today, "symbol_key": "298380", "symbol": "298380", "name": "에이비엘바이오",
        "status": "reviewed", "note": "확인", "updated_at": now,
    })
    await get_db().market_data_alerts.insert_one({
        "fingerprint": "refresh_failure:test", "type": "refresh_failure", "severity": "warning", "title": "일봉 수집 실패",
        "message": "테스트용 경고", "condition_active": True, "updated_at": now,
    })

    report = await client.get("/api/kqs/candidate-queue/review-report?days=7", headers=headers)
    quality = await client.get("/api/kqs/candidate-queue/selection-quality?days=7", headers=headers)
    alerts = await client.get("/api/kqs/operations-alerts", headers=headers)
    exported = await client.get("/api/kqs/candidate-queue/review-report/export?days=7&format=csv", headers=headers)

    assert report.status_code == 200, report.text
    assert report.json()["summary"]["candidate_observations"] == 2
    assert report.json()["summary"]["reviewed_count"] == 1
    assert quality.status_code == 200, quality.text
    assert quality.json()["summary"]["candidate_continuity_rate_pct"] == 100.0
    assert alerts.status_code == 200, alerts.text
    assert any(item["type"] == "market_refresh_failure" for item in alerts.json()["alerts"])
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "candidate_observations" in exported.text
    assert "주문" in report.json()["disclaimer"]


@pytest.mark.asyncio
async def test_candidate_reporting_endpoints_require_login(client):
    for path in ("/api/kqs/candidate-queue/review-report", "/api/kqs/candidate-queue/selection-quality", "/api/kqs/operations-alerts"):
        assert (await client.get(path)).status_code == 401


@pytest.mark.asyncio
async def test_candidate_outcomes_use_only_verified_daily_bars(client, make_user):
    headers = await make_user("kqs-outcome@example.com")
    as_of = "2026-08-03"
    candidate = {"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "execution_timing": "next_trading_session"}
    await get_db().kqs_candidate_queue_snapshots.insert_one({
        "snapshot_date": as_of, "captured_at": datetime.now(timezone.utc), "as_of": as_of, "summary": {}, "candidates": [candidate],
        "changes": {"baseline": True, "changed": False, "new": [], "removed": []},
    })
    stock_bars, benchmark_bars = [], []
    for index in range(21):
        stamp = (datetime(2026, 8, 3, tzinfo=timezone.utc) + timedelta(days=index)).date().isoformat()
        stock_bars.append({"ticker": "298380.KQ", "interval": "1d", "timestamp": stamp, "close": 100 + index})
        benchmark_bars.append({"ticker": "^KS11", "interval": "1d", "timestamp": stamp, "close": 200 + index})
    await get_db().market_ohlcv.insert_many(stock_bars + benchmark_bars)

    captured = await kqs.capture_candidate_outcome_snapshots()
    response = await client.get("/api/kqs/candidate-queue/outcomes?days=365", headers=headers)

    assert captured["stored_count"] == 1
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["status"] == "observed"
    assert row["horizons"]["1"]["return_pct"] == 1.0
    assert row["horizons"]["20"]["return_pct"] == 20.0
    assert response.json()["summary"]["horizons"]["20"]["observed_count"] == 1
    assert response.json()["summary"]["horizons"]["20"]["sample_gate"] == "insufficient_sample"
    decomposition = await client.get("/api/kqs/candidate-queue/performance-decomposition?days=365", headers=headers)
    assert decomposition.status_code == 200, decomposition.text
    assert any(item["dimension"] == "market" and item["value"] == "KOSDAQ" for item in decomposition.json()["items"])
    assert "미래 수익" in response.json()["disclaimer"]


@pytest.mark.asyncio
async def test_operations_alert_acknowledgement_is_personal_and_does_not_resolve_condition(client, make_user):
    headers = await make_user("kqs-ack@example.com")
    now = datetime.now(timezone.utc)
    await get_db().market_data_alerts.insert_one({
        "fingerprint": "refresh_failure:ack", "type": "refresh_failure", "severity": "warning", "title": "일봉 수집 실패",
        "message": "테스트", "condition_active": True, "created_at": now - timedelta(hours=9), "updated_at": now,
    })

    saved = await client.post("/api/kqs/operations-alerts/market:refresh_failure:ack/acknowledgement", headers=headers, json={"status": "acknowledged", "note": "확인 중"})
    listed = await client.get("/api/kqs/operations-alerts", headers=headers)

    assert saved.status_code == 200, saved.text
    item = next(item for item in listed.json()["alerts"] if item["fingerprint"] == "market:refresh_failure:ack")
    assert item["acknowledgement"]["status"] == "acknowledged"
    assert await get_db().market_data_alerts.count_documents({"condition_active": True}) == 1


@pytest.mark.asyncio
async def test_portfolio_reconciliation_compares_only_normalized_symbols(monkeypatch, client, make_user):
    headers = await make_user("kqs-reconciliation@example.com")
    saved = await client.put("/api/portfolio", headers=headers, json={"cash_krw": 0, "holdings": [
        {"ticker": "298380.KQ", "name": "에이비엘바이오", "quantity": 3, "average_price": 56_000, "currency": "KRW", "sector": "바이오"},
        {"ticker": "005930.KS", "name": "삼성전자", "quantity": 1, "average_price": 70_000, "currency": "KRW", "sector": "반도체"},
    ]})
    assert saved.status_code == 200, saved.text
    monkeypatch.setattr(kqs, "_read_engine_positions", lambda: {"available": True, "as_of": "2026-09-16", "positions": [
        {"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "entry_date": "2026-09-16", "entry_price": "56600"},
        {"symbol": "000660", "symbol_key": "000660", "name": "SK하이닉스", "market": "KOSPI", "signal_type": "swing", "entry_date": "2026-09-16", "entry_price": "200000"},
    ]})

    response = await client.get("/api/kqs/portfolio-reconciliation", headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["read_only"] is True
    assert data["summary"] == {"website_holding_count": 2, "engine_open_position_count": 2, "matching_symbol_count": 1, "website_only_count": 1, "engine_only_count": 1}
    assert data["matched"][0]["website"]["ticker"] == "298380.KQ"
    assert data["engine_only"][0]["symbol"] == "000660"
    assert "raw" not in str(data)


@pytest.mark.asyncio
async def test_reconciliation_snapshot_records_prior_day_changes_and_history(monkeypatch, client, make_user):
    headers = await make_user("kqs-history@example.com")
    saved = await client.put("/api/portfolio", headers=headers, json={"cash_krw": 0, "holdings": [
        {"ticker": "298380.KQ", "name": "에이비엘바이오", "quantity": 3, "average_price": 56_000, "currency": "KRW", "sector": "바이오"},
        {"ticker": "005930.KS", "name": "삼성전자", "quantity": 1, "average_price": 70_000, "currency": "KRW", "sector": "반도체"},
    ]})
    assert saved.status_code == 200, saved.text
    user = await get_db().users.find_one({"email": "kqs-history@example.com"})
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).astimezone(kqs.KST).date().isoformat()
    await get_db().kqs_reconciliation_snapshots.insert_one({
        "user_id": user["_id"], "snapshot_date": yesterday, "captured_at": datetime.now(timezone.utc),
        "engine_as_of": yesterday, "summary": {}, "matched_symbols": [],
        "website_only_symbols": ["005930", "298380"], "engine_only_symbols": ["000660"],
        "changes": {"baseline": True, "changed": False, "new": {}, "resolved": {}},
    })
    monkeypatch.setattr(kqs, "_read_engine_positions", lambda: {"available": True, "as_of": "2026-09-16", "positions": [
        {"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "entry_date": "2026-09-16", "entry_price": "56600"},
    ]})

    captured = await client.post("/api/kqs/portfolio-reconciliation/snapshot", headers=headers)
    assert captured.status_code == 200, captured.text
    changes = captured.json()["changes"]
    assert changes["changed"] is True
    assert changes["new"]["matched"] == ["298380"]
    assert changes["resolved"]["website_only"] == ["298380"]
    assert changes["resolved"]["engine_only"] == ["000660"]

    history = await client.get("/api/kqs/portfolio-reconciliation/history?days=7", headers=headers)
    assert history.status_code == 200, history.text
    assert len(history.json()["snapshots"]) == 2
    assert history.json()["change_alert_count"] == 1


@pytest.mark.asyncio
async def test_reconciliation_history_requires_login(client):
    response = await client.get("/api/kqs/portfolio-reconciliation/history")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_daily_scheduler_captures_reconciliation_for_portfolio_users(monkeypatch, client, make_user):
    headers = await make_user("kqs-scheduler@example.com")
    saved = await client.put("/api/portfolio", headers=headers, json={"cash_krw": 0, "holdings": [
        {"ticker": "298380.KQ", "name": "에이비엘바이오", "quantity": 1, "average_price": 56_000, "currency": "KRW", "sector": "바이오"},
    ]})
    assert saved.status_code == 200, saved.text
    monkeypatch.setattr(kqs, "_read_engine_positions", lambda: {"available": True, "as_of": "2026-09-16", "positions": [
        {"symbol": "298380", "symbol_key": "298380", "name": "에이비엘바이오", "market": "KOSDAQ", "signal_type": "swing", "entry_date": "2026-09-16", "entry_price": "56600"},
    ]})
    monkeypatch.setattr(kqs, "_read_engine_review_candidates", lambda: {"available": True, "as_of": "2026-09-16", "latest_selected_count": 0, "entry_due_count": 0, "blocked_signal_count": 0, "candidates": []})

    await scheduler.capture_daily_kqs_reconciliation_snapshots()

    user = await get_db().users.find_one({"email": "kqs-scheduler@example.com"})
    snapshot = await get_db().kqs_reconciliation_snapshots.find_one({"user_id": user["_id"]})
    assert snapshot is not None
    assert snapshot["summary"]["matching_symbol_count"] == 1
    assert await get_db().kqs_candidate_queue_snapshots.count_documents({}) == 1
