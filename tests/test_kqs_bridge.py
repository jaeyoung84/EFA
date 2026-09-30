import json
import pytest
from datetime import date as date_type, datetime, timedelta, timezone

from app.backend.db import get_db
from app.backend import scheduler
from app.backend.routers import kqs
from bson import ObjectId


@pytest.fixture(autouse=True)
def explicit_legacy_bridge_mode(monkeypatch):
    """These historical bridge regressions deliberately exercise the legacy namespace."""
    monkeypatch.setenv("KQS_RECOMMENDATION_SOURCE", "bridge")


def test_bridge_summary_redacts_raw_logs_and_remains_read_only(monkeypatch):
    monkeypatch.setenv("KQS_RECOMMENDATION_SOURCE", "bridge")
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


def test_bridge_recommendations_reject_oversized_response(monkeypatch):
    monkeypatch.setenv("KQS_RECOMMENDATION_SOURCE", "bridge")
    class Response:
        def read(self, _limit): return b"x" * (512 * 1024 + 1)
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(kqs.urllib.request, "urlopen", lambda *args, **kwargs: Response())

    with pytest.raises(RuntimeError, match="512 KiB"):
        kqs._read_bridge_recommendations()


def test_review_candidates_extracts_only_latest_selected_entry_due_rows(monkeypatch):
    monkeypatch.setenv("KQS_RECOMMENDATION_SOURCE", "bridge")
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
    monkeypatch.setenv("KQS_RECOMMENDATION_SOURCE", "bridge")
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
async def test_kqs_recommendations_require_login(client):
    response = await client.get("/api/kqs/recommendations")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_kqs_recommendations_are_read_only(monkeypatch, client, make_user):
    recommendation = {
        "schema_version": "kqs-recommendations/v1", "generated_at": "2026-09-28T00:00:00+00:00",
        "expires_at": "2026-09-29T00:00:00+00:00", "read_only": True, "selected_entry_count": 1,
        "engine": {"engine_id": "kqs", "run_id": "daily-1", "git_sha": "abc", "data_as_of": "2026-09-26", "dataset_hash": "sha256:data", "dirty": False},
        "signals": [{"signal_id": "s-1", "action": "entry", "side": "buy", "symbol": "298380", "market": "KOSDAQ", "name": "에이비엘바이오", "signal_type": "trend_pullback", "selection_outcome": "selected", "execution_session": "next_session", "selection_rank": 1, "config_fingerprint": "cfg", "audit_row_hash": "row", "reasons": [{"code": "trend", "label": "추세", "evidence": "장기 추세 조건 통과"}]}],
        "disclaimer": "읽기 전용",
    }
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: recommendation)
    headers = await make_user("kqs-recommendations@example.com")
    before = (await get_db().order_proposals.count_documents({}), await get_db().paper_fills.count_documents({}))

    response = await client.get("/api/kqs/recommendations", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == recommendation
    assert before == (await get_db().order_proposals.count_documents({}), await get_db().paper_fills.count_documents({}))


@pytest.mark.asyncio
async def test_kqs_recommendations_fail_closed(monkeypatch, client, make_user):
    def invalid_contract():
        raise RuntimeError("엔진 추천 계약을 검증하지 못했습니다")
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", invalid_contract)
    response = await client.get("/api/kqs/recommendations", headers=await make_user("kqs-invalid@example.com"))
    assert response.status_code == 503
    assert "계약" in response.json()["detail"]


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



def _recommendations_payload(*, signal_id="run-1-entry-1", symbol="298380", market="KOSDAQ"):
    return {
        "schema_version": "kqs-recommendations/v1", "snapshot_id": "synthetic-run-1",
        "generated_at": "2026-09-28T08:00:00+00:00", "expires_at": "2026-09-29T08:00:00+00:00",
        "engine": {"engine_id": "korea-quant-system", "run_id": "run-1", "git_sha": "abc1234", "dirty": False,
                   "data_as_of": "2026-09-26", "dataset_hash": "dataset-v1"},
        "signals": [{
            "signal_id": signal_id, "action": "entry", "side": "buy", "symbol": symbol, "market": market,
            "name": "에이비엘바이오", "signal_type": "swing", "selection_rank": 1, "selection_outcome": "selected",
            "execution_session": "next_trading_session", "config_fingerprint": "config-v1", "audit_row_hash": "audit-1",
            "reasons": [{"code": "trend", "label": "추세 조건 충족", "evidence": "종가가 진입 조건을 충족"}],
        }],
        "selected_entry_count": 1, "read_only": True, "disclaimer": "읽기 전용",
    }


@pytest.mark.asyncio
async def test_record_decision_requires_login(client):
    response = await client.post("/api/kqs/decisions", json={"signal_id": "run-1-entry-1", "decision": "hold"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_training_dataset_is_user_scoped_and_separates_features_from_future_labels(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload())
    headers = await make_user("kqs-dataset-owner@example.com")
    assert (await client.put("/api/kqs/learning-consent", headers=headers, json={
        "consented": True, "acknowledgement": "KQS_LEARNING_DATA_ONLY"})).status_code == 200
    await make_user("kqs-dataset-other@example.com")
    saved = await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "run-1-entry-1", "decision": "hold", "memo": "개인 메모", "evidence_tags": ["valuation"],
    })
    assert saved.status_code == 200, saved.text
    user = await get_db().users.find_one({"email": "kqs-dataset-owner@example.com"})
    decision = await get_db().kqs_paper_decisions.find_one({"user_id": user["_id"]})
    await get_db().kqs_decision_outcomes.insert_one({
        "decision_id": decision["_id"], "user_id": user["_id"], "decision": "hold",
        "status": "observed", "decided_at": decision["decided_at"], "data_as_of": "2026-09-26",
        "horizons": {"20": {"observed": True, "return_pct": 3.5, "excess_return_pct": 1.2, "target_date": "2026-10-26"}},
    })

    denied = await client.get("/api/kqs/decisions/training-dataset")
    assert denied.status_code == 401
    response = await client.get("/api/kqs/decisions/training-dataset", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    manifest, example = [json.loads(line) for line in response.text.splitlines()]
    assert manifest["example_count"] == 1
    assert manifest["observed_20d_count"] == 1
    assert manifest["features_exclude_future_outcomes"] is True
    assert example["features"]["reasons"][0]["code"] == "trend"
    assert manifest["schema_version"] == "kqs-human-decisions/v3"
    assert "evidence_tags" not in example["features"]
    assert example["human_context"]["evidence_tags"] == ["valuation"]
    assert "memo" not in example["human_context"]
    assert example["labels"]["human_decision"] == "hold"
    assert example["labels"]["forward_returns"]["20"]["return_pct"] == 3.5
    assert example["labels"]["forward_returns"]["1"]["observed"] is False
    assert "user_id" not in response.text and "개인 메모" not in response.text

    with_notes = await client.get("/api/kqs/decisions/training-dataset?include_notes=true", headers=headers)
    _, noted_example = [json.loads(line) for line in with_notes.text.splitlines()]
    assert noted_example["human_context"]["memo"] == "개인 메모"
    assert "human_note" not in noted_example["features"]


@pytest.mark.asyncio
async def test_record_decision_rejects_unknown_signal_id(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload())
    headers = await make_user("kqs-decision-unknown@example.com")
    response = await client.post("/api/kqs/decisions", headers=headers, json={"signal_id": "does-not-exist", "decision": "hold"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_record_hold_decision_requires_no_trade_fields(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload())
    headers = await make_user("kqs-decision-hold@example.com")

    response = await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "run-1-entry-1", "decision": "hold", "memo": "아직 확신이 없어 지켜본다", "evidence_tags": ["관망", "변동성높음"],
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "hold"
    assert body["quantity"] is None
    assert body["fill_price"] is None
    assert body["notional_krw"] is None
    assert body["evidence_tags"] == ["관망", "변동성높음"]
    assert body["symbol"] == "298380"
    assert body["market"] == "KOSDAQ"


@pytest.mark.asyncio
async def test_record_buy_decision_requires_quantity_and_price(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload())
    headers = await make_user("kqs-decision-buy-missing@example.com")

    response = await client.post("/api/kqs/decisions", headers=headers, json={"signal_id": "run-1-entry-1", "decision": "buy"})
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_record_buy_decision_stores_manual_fill_and_snapshot(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload())
    headers = await make_user("kqs-decision-buy@example.com")

    response = await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "run-1-entry-1", "decision": "buy", "quantity": 10, "fill_price": 56_000,
        "fee_krw": 840, "tax_krw": 0, "memo": "추세 조건과 시총 순위가 마음에 들어 매수",
        "evidence_tags": ["추세추종", "시총상위"],
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quantity"] == 10
    assert body["fill_price"] == 56_000
    assert body["fee_krw"] == 840
    assert body["notional_krw"] == 560_000
    assert body["data_as_of"] == "2026-09-26"

    user = await get_db().users.find_one({"email": "kqs-decision-buy@example.com"})
    stored = await get_db().kqs_paper_decisions.find_one({"user_id": user["_id"]})
    assert stored["recommendation_snapshot"]["signal_id"] == "run-1-entry-1"
    assert stored["recommendation_snapshot"]["reasons"][0]["code"] == "trend"
    assert stored["engine_snapshot"]["run_id"] == "run-1"

    audit = await client.get("/api/strategy/execution-audit", headers=headers)
    actions = {item["action"] for item in audit.json()["items"]}
    assert "kqs_decision_recorded" in actions


@pytest.mark.asyncio
async def test_decision_ledger_lists_own_history_only(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload())
    mine = await make_user("kqs-decision-mine@example.com")
    other = await make_user("kqs-decision-other@example.com")

    await client.post("/api/kqs/decisions", headers=mine, json={
        "signal_id": "run-1-entry-1", "decision": "buy", "quantity": 5, "fill_price": 50_000,
    })
    await client.post("/api/kqs/decisions", headers=other, json={
        "signal_id": "run-1-entry-1", "decision": "exclude", "memo": "밸류에이션 부담",
    })

    mine_list = await client.get("/api/kqs/decisions", headers=mine)
    assert mine_list.status_code == 200, mine_list.text
    assert len(mine_list.json()["items"]) == 1
    assert mine_list.json()["items"][0]["decision"] == "buy"

    other_list = await client.get("/api/kqs/decisions", headers=other)
    assert len(other_list.json()["items"]) == 1
    assert other_list.json()["items"][0]["decision"] == "exclude"


@pytest.mark.asyncio
async def test_decision_positions_aggregate_buy_and_sell_only(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="run-1-entry-1"))
    headers = await make_user("kqs-decision-positions@example.com")

    await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "run-1-entry-1", "decision": "buy", "quantity": 10, "fill_price": 50_000,
    })
    await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "run-1-entry-1", "decision": "hold", "memo": "추가 매수는 보류",
    })

    positions = await client.get("/api/kqs/decisions/positions", headers=headers)
    assert positions.status_code == 200, positions.text
    items = positions.json()["items"]
    assert len(items) == 1
    assert items[0]["quantity"] == 10
    assert items[0]["average_cost_krw"] == 50_000

    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="run-1-entry-1-sell"))
    await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "run-1-entry-1-sell", "decision": "sell", "quantity": 10, "fill_price": 55_000,
    })

    positions_after = await client.get("/api/kqs/decisions/positions", headers=headers)
    assert positions_after.json()["items"] == []


@pytest.mark.asyncio
async def test_decision_outcomes_await_baseline_close_before_bars_arrive(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload())
    headers = await make_user("kqs-outcome-wait@example.com")
    await client.post("/api/kqs/decisions", headers=headers, json={"signal_id": "run-1-entry-1", "decision": "hold", "memo": "관망"})

    captured = await kqs.capture_kqs_decision_outcomes()
    assert captured["stored_count"] == 1

    response = await client.get("/api/kqs/decisions/outcomes", headers=headers)
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["status"] == "awaiting_baseline_close"
    assert item["horizons"] == {}


@pytest.mark.asyncio
async def test_decision_outcomes_compute_forward_returns_from_verified_bars_only(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload())
    headers = await make_user("kqs-outcome-observed@example.com")
    await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "run-1-entry-1", "decision": "buy", "quantity": 10, "fill_price": 100,
    })

    stock_bars, benchmark_bars = [], []
    for index in range(21):
        stamp = (datetime(2026, 9, 26, tzinfo=timezone.utc) + timedelta(days=index)).date().isoformat()
        stock_bars.append({"ticker": "298380.KQ", "interval": "1d", "timestamp": stamp, "close": 100 + index})
        benchmark_bars.append({"ticker": "^KS11", "interval": "1d", "timestamp": stamp, "close": 200 + index})
    await get_db().market_ohlcv.insert_many(stock_bars + benchmark_bars)

    captured = await kqs.capture_kqs_decision_outcomes()
    assert captured["stored_count"] == 1

    response = await client.get("/api/kqs/decisions/outcomes", headers=headers)
    item = response.json()["items"][0]
    assert item["status"] == "observed"
    assert item["horizons"]["1"]["return_pct"] == 1.0
    assert item["horizons"]["20"]["return_pct"] == 20.0
    assert item["horizons"]["20"]["excess_return_pct"] is not None
    assert item["realized"] is None  # 아직 매도 기록이 없다


@pytest.mark.asyncio
async def test_decision_outcomes_match_buy_and_sell_fifo_and_compute_realized_pnl(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="buy-signal"))
    headers = await make_user("kqs-outcome-realized@example.com")

    buy = await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "buy-signal", "decision": "buy", "quantity": 10, "fill_price": 50_000, "fee_krw": 750, "tax_krw": 0,
    })
    assert buy.status_code == 200, buy.text

    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="sell-signal"))
    sell = await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "sell-signal", "decision": "sell", "quantity": 10, "fill_price": 55_000, "fee_krw": 825, "tax_krw": 100,
        "memo": "목표 수익률 도달", "evidence_tags": ["익절"],
    })
    assert sell.status_code == 200, sell.text

    captured = await kqs.capture_kqs_decision_outcomes()
    assert captured["matched_count"] == 1

    response = await client.get("/api/kqs/decisions/outcomes", headers=headers)
    items = {item["decision"]: item for item in response.json()["items"]}
    realized = items["buy"]["realized"]
    assert realized is not None
    assert realized["gross_pnl_krw"] == 50_000  # (55,000-50,000) * 10
    assert realized["total_cost_krw"] == 1675  # 750 + 825 + 100
    assert realized["net_pnl_krw"] == 48_325
    assert realized["exit_reason"] == "목표 수익률 도달"
    assert realized["exit_evidence_tags"] == ["익절"]
    assert items["sell"]["realized"] is None  # 실현 손익은 매수 쪽 기록에만 채운다


@pytest.mark.asyncio
async def test_decision_outcomes_endpoint_requires_login(client):
    response = await client.get("/api/kqs/decisions/outcomes")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_performance_report_requires_login(client):
    response = await client.get("/api/kqs/decisions/performance-report")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_performance_report_empty_when_no_decisions(client, make_user):
    headers = await make_user("kqs-perf-empty@example.com")
    response = await client.get("/api/kqs/decisions/performance-report", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversion"]["total_decisions"] == 0
    assert body["conversion"]["buy_conversion_rate_pct"] is None
    assert body["equity_curve"]["points"] == []
    assert body["observation_stage"] is True
    assert body["cagr_pct"] is None


@pytest.mark.asyncio
async def test_performance_report_computes_buy_conversion_rate(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="s1"))
    headers = await make_user("kqs-perf-conversion@example.com")
    await client.post("/api/kqs/decisions", headers=headers, json={"signal_id": "s1", "decision": "buy", "quantity": 1, "fill_price": 100})
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="s2"))
    await client.post("/api/kqs/decisions", headers=headers, json={"signal_id": "s2", "decision": "hold"})
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="s3"))
    await client.post("/api/kqs/decisions", headers=headers, json={"signal_id": "s3", "decision": "exclude"})
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="s4"))
    await client.post("/api/kqs/decisions", headers=headers, json={"signal_id": "s4", "decision": "hold"})

    response = await client.get("/api/kqs/decisions/performance-report", headers=headers)
    conversion = response.json()["conversion"]
    assert conversion["total_decisions"] == 4
    assert conversion["buy_count"] == 1
    assert conversion["hold_count"] == 2
    assert conversion["exclude_count"] == 1
    assert conversion["buy_conversion_rate_pct"] == 25.0


@pytest.mark.asyncio
async def test_performance_report_compares_buy_and_hold_post_decision_returns(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(symbol="298380", market="KOSDAQ", signal_id="buy-sig"))
    headers = await make_user("kqs-perf-comparison@example.com")
    await client.post("/api/kqs/decisions", headers=headers, json={"signal_id": "buy-sig", "decision": "buy", "quantity": 5, "fill_price": 100})

    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(symbol="000660", market="KOSPI", signal_id="hold-sig"))
    await client.post("/api/kqs/decisions", headers=headers, json={"signal_id": "hold-sig", "decision": "hold", "memo": "관망"})

    stock_bars, hold_bars, benchmark_bars = [], [], []
    for index in range(21):
        stamp = (datetime(2026, 9, 26, tzinfo=timezone.utc) + timedelta(days=index)).date().isoformat()
        stock_bars.append({"ticker": "298380.KQ", "interval": "1d", "timestamp": stamp, "close": 100 + index})
        hold_bars.append({"ticker": "000660.KS", "interval": "1d", "timestamp": stamp, "close": 200 - index})
        benchmark_bars.append({"ticker": "^KS11", "interval": "1d", "timestamp": stamp, "close": 300})
    await get_db().market_ohlcv.insert_many(stock_bars + hold_bars + benchmark_bars)

    await kqs.capture_kqs_decision_outcomes()
    response = await client.get("/api/kqs/decisions/performance-report", headers=headers)
    comparison = response.json()["post_decision_comparison"]
    assert comparison["buy"]["count"] == 1
    assert comparison["buy"]["avg_return_pct"]["20"] == 20.0
    assert comparison["hold"]["count"] == 1
    assert comparison["hold"]["avg_return_pct"]["20"] == -10.0


@pytest.mark.asyncio
async def test_performance_report_breaks_down_realized_trades_and_builds_equity_curve(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="buy-sig"))
    headers = await make_user("kqs-perf-realized@example.com")
    await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "buy-sig", "decision": "buy", "quantity": 10, "fill_price": 50_000, "fee_krw": 750,
    })
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="sell-sig"))
    await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "sell-sig", "decision": "sell", "quantity": 10, "fill_price": 55_000, "fee_krw": 825,
    })

    await kqs.capture_kqs_decision_outcomes()
    response = await client.get("/api/kqs/decisions/performance-report", headers=headers)
    body = response.json()
    assert body["equity_curve"]["realized_trade_count"] == 1
    assert body["equity_curve"]["points"][-1]["cumulative_net_pnl_krw"] == 48_425  # 50,000 - 750 - 825
    assert body["equity_curve"]["total_cost_krw"] == 1575
    assert body["equity_curve"]["turnover_krw"] == 500_000 + 550_000
    market_row = next(row for row in body["breakdown"]["by_market"] if row["key"] == "KOSDAQ")
    assert market_row["trade_count"] == 1
    assert market_row["win_rate_pct"] == 100.0


@pytest.mark.asyncio
async def test_performance_report_stays_in_observation_stage_under_one_year(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="s1"))
    headers = await make_user("kqs-perf-observation@example.com")
    await client.post("/api/kqs/decisions", headers=headers, json={"signal_id": "s1", "decision": "hold"})

    response = await client.get("/api/kqs/decisions/performance-report", headers=headers)
    body = response.json()
    assert body["observation_stage"] is True
    assert body["cagr_pct"] is None


@pytest.mark.asyncio
async def test_performance_report_computes_cagr_once_span_exceeds_one_year(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="buy-sig"))
    headers = await make_user("kqs-perf-cagr@example.com")
    user = await get_db().users.find_one({"email": "kqs-perf-cagr@example.com"})
    await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "buy-sig", "decision": "buy", "quantity": 10, "fill_price": 50_000,
    })
    await get_db().kqs_paper_decisions.update_one(
        {"user_id": user["_id"], "signal_id": "buy-sig"},
        {"$set": {"decided_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
                  "trade_at": datetime(2025, 1, 1, tzinfo=timezone.utc), "trade_date": "2025-01-01"}},
    )
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="sell-sig"))
    await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "sell-sig", "decision": "sell", "quantity": 10, "fill_price": 60_000,
    })
    await get_db().kqs_paper_decisions.update_one(
        {"user_id": user["_id"], "signal_id": "sell-sig"},
        {"$set": {"decided_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
                  "trade_at": datetime(2026, 6, 1, tzinfo=timezone.utc), "trade_date": "2026-06-01"}},
    )

    await kqs.capture_kqs_decision_outcomes()
    response = await client.get("/api/kqs/decisions/performance-report", headers=headers)
    body = response.json()
    assert body["observation_stage"] is False
    assert body["cagr_pct"] is not None


# ---- EINT-008: 만료된 추천 보유분의 수동 매도·부분매도·lot 기록 ----

def test_lot_builder_allocates_partial_sells_fifo_and_reports_oversold():
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)

    def row(_id, decision, quantity, price, minutes):
        return {"_id": _id, "symbol_key": "298380", "decision": decision, "quantity": quantity,
                "fill_price": price, "decided_at": t0 + timedelta(minutes=minutes)}

    book = kqs._build_kqs_lots([
        row("b1", "buy", 5, 100, 0), row("b2", "buy", 5, 200, 1),
        row("s1", "sell", 7, 300, 2), row("h", "hold", None, None, 3), row("s2", "sell", 4, 300, 4),
    ])
    first, second = book["lots"]["298380"]
    assert [(a["sell"]["_id"], a["quantity"]) for a in first["allocations"]] == [("s1", 5)]
    assert first["remaining"] == 0
    assert [(a["sell"]["_id"], a["quantity"]) for a in second["allocations"]] == [("s1", 2), ("s2", 3)]
    assert second["remaining"] == 0
    assert book["oversold"] == [{"sell_decision_id": "s2", "symbol_key": "298380", "unmatched_quantity": 1}]


async def _buy_then_expire(monkeypatch, client, headers, email, *, quantity=10, price=50_000, fee=1_000):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="buy-sig"))
    bought = await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "buy-sig", "decision": "buy", "quantity": quantity, "fill_price": price, "fee_krw": fee,
    })
    assert bought.status_code == 200, bought.text
    user = await get_db().users.find_one({"email": email})
    await get_db().kqs_paper_decisions.update_one(
        {"user_id": user["_id"], "signal_id": "buy-sig"},
        {"$set": {"recommendation_expires_at": "2026-09-01T08:00:00+00:00",
                  "decided_at": datetime(2026, 8, 31, 1, tzinfo=timezone.utc),
                  "trade_at": datetime(2026, 8, 31, 1, tzinfo=timezone.utc), "trade_date": "2026-08-31"}},
    )

    def expired():
        raise RuntimeError("추천이 만료되었습니다.")
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", expired)
    return user


@pytest.mark.asyncio
async def test_expired_recommendation_holding_can_be_partially_then_fully_sold(monkeypatch, client, make_user):
    email = "kqs-lot-exit@example.com"
    headers = await make_user(email)
    assert (await client.put("/api/kqs/learning-consent", headers=headers, json={
        "consented": True, "acknowledgement": "KQS_LEARNING_DATA_ONLY"})).status_code == 200
    await _buy_then_expire(monkeypatch, client, headers, email)

    positions = (await client.get("/api/kqs/decisions/positions", headers=headers)).json()
    item = positions["items"][0]
    assert item["recommendation_expired"] is True
    assert item["lots"][0]["signal_id"] == "buy-sig"
    assert item["lots"][0]["bought_on"] == "2026-08-31"

    partial = await client.post("/api/kqs/decisions/positions/298380/sell", headers=headers, json={
        "quantity": 4, "fill_price": 55_000, "fee_krw": 400, "trade_date": "2026-09-10", "memo": "일부 익절",
    })
    assert partial.status_code == 200, partial.text
    assert partial.json()["remaining_quantity"] == 6
    assert partial.json()["item"]["record_origin"] == "position_exit"
    assert partial.json()["item"]["signal_id"] is None

    over = await client.post("/api/kqs/decisions/positions/298380/sell", headers=headers, json={"quantity": 7, "fill_price": 60_000})
    assert over.status_code == 409

    await kqs.capture_kqs_decision_outcomes()
    outcomes = {row["decision"]: row for row in (await client.get("/api/kqs/decisions/outcomes", headers=headers)).json()["items"]}
    realized = outcomes["buy"]["realized"]
    assert realized["status"] == "partial"
    assert realized["closed_quantity"] == 4 and realized["remaining_quantity"] == 6
    assert realized["gross_pnl_krw"] == 20_000
    assert realized["total_cost_krw"] == 800  # 매수 수수료 1,000의 4/10 + 매도 400
    assert realized["net_pnl_krw"] == 19_200
    assert realized["net_return_pct"] == 9.6  # 매도분 원가 200,000 기준
    assert realized["holding_days"] == 10
    assert outcomes["sell"]["status"] == "trade_record_only"

    final = await client.post("/api/kqs/decisions/positions/298380/sell", headers=headers, json={
        "quantity": 6, "fill_price": 60_000, "fee_krw": 600, "trade_date": "2026-09-15",
    })
    assert final.status_code == 200, final.text
    assert (await client.get("/api/kqs/decisions/positions", headers=headers)).json()["items"] == []

    await kqs.capture_kqs_decision_outcomes()
    buy_outcome = next(row for row in (await client.get("/api/kqs/decisions/outcomes", headers=headers)).json()["items"]
                       if row["decision"] == "buy")
    realized = buy_outcome["realized"]
    assert realized["status"] == "closed"
    assert len(realized["allocations"]) == 2
    assert realized["gross_pnl_krw"] == 80_000
    assert realized["total_cost_krw"] == 2_000
    assert realized["holding_days"] == 15

    missing = await client.post("/api/kqs/decisions/positions/298380/sell", headers=headers, json={"quantity": 1, "fill_price": 1})
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_position_sell_rejects_future_or_pre_buy_trade_dates(monkeypatch, client, make_user):
    email = "kqs-lot-dates@example.com"
    headers = await make_user(email)
    await _buy_then_expire(monkeypatch, client, headers, email)
    tomorrow = (datetime.now(timezone.utc).astimezone(kqs.KST).date() + timedelta(days=1)).isoformat()
    future = await client.post("/api/kqs/decisions/positions/298380/sell", headers=headers,
                               json={"quantity": 1, "fill_price": 1, "trade_date": tomorrow})
    assert future.status_code == 422
    early = await client.post("/api/kqs/decisions/positions/298380/sell", headers=headers,
                              json={"quantity": 1, "fill_price": 1, "trade_date": "2026-08-30"})
    assert early.status_code == 422
    assert (await client.get("/api/kqs/decisions/positions", headers=headers)).json()["items"][0]["quantity"] == 10


@pytest.mark.asyncio
async def test_recommendation_sell_cannot_exceed_holdings(monkeypatch, client, make_user):
    monkeypatch.setattr(kqs, "_read_bridge_recommendations", lambda: _recommendations_payload(signal_id="sell-sig"))
    headers = await make_user("kqs-lot-oversell@example.com")
    response = await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": "sell-sig", "decision": "sell", "quantity": 1, "fill_price": 100,
    })
    assert response.status_code == 409
    assert (await client.get("/api/kqs/decisions", headers=headers)).json()["items"] == []


@pytest.mark.asyncio
async def test_concurrent_oversell_is_rolled_back_before_acknowledgement(monkeypatch, client, make_user):
    email = "kqs-lot-race@example.com"
    headers = await make_user(email)
    user = await _buy_then_expire(monkeypatch, client, headers, email, quantity=2)
    now = datetime.now(timezone.utc)
    base = {"user_id": user["_id"], "symbol": "298380", "symbol_key": "298380", "decision": "sell",
            "record_origin": "position_exit", "fill_price": 100, "decided_at": now}
    await get_db().kqs_paper_decisions.insert_one({**base, "quantity": 2})
    racing = (await get_db().kqs_paper_decisions.insert_one({**base, "quantity": 1, "decided_at": now + timedelta(seconds=1)})).inserted_id
    with pytest.raises(kqs.HTTPException) as caught:
        await kqs._reject_if_oversold(user["_id"], racing)
    assert caught.value.status_code == 409
    assert await get_db().kqs_paper_decisions.find_one({"_id": racing}) is None


@pytest.mark.asyncio
async def test_position_exits_are_trade_labels_not_judgment_examples(monkeypatch, client, make_user):
    email = "kqs-lot-dataset@example.com"
    headers = await make_user(email)
    assert (await client.put("/api/kqs/learning-consent", headers=headers, json={
        "consented": True, "acknowledgement": "KQS_LEARNING_DATA_ONLY"})).status_code == 200
    await _buy_then_expire(monkeypatch, client, headers, email)
    sold = await client.post("/api/kqs/decisions/positions/298380/sell", headers=headers,
                             json={"quantity": 10, "fill_price": 55_000, "trade_date": "2026-09-10"})
    assert sold.status_code == 200, sold.text
    await kqs.capture_kqs_decision_outcomes()

    exported = await client.get("/api/kqs/decisions/training-dataset", headers=headers)
    lines = [json.loads(line) for line in exported.text.splitlines()]
    assert lines[0]["schema_version"] == "kqs-human-decisions/v3"
    assert lines[0]["position_exit_records_excluded"] == 1
    examples = lines[1:]
    assert [row["labels"]["human_decision"] for row in examples] == ["buy"]
    assert examples[0]["labels"]["realized_trade"]["status"] == "closed"

    report = (await client.get("/api/kqs/decisions/performance-report", headers=headers)).json()
    assert report["conversion"]["total_decisions"] == 1
    assert report["conversion"]["sell_count"] == 0
    assert report["conversion"]["position_exit_count"] == 1
    assert report["equity_curve"]["realized_trade_count"] == 1
    assert report["equity_curve"]["turnover_krw"] == 500_000 + 550_000


# ---- EINT-009: 수동 판단 데이터셋 불변 manifest·학습/holdout 규약 ----

async def _seed_mature_decisions(email, start_index, date_count, per_date=15, grant_learning_consent=True):
    """20일 레이블이 확정된 판단 표본을 기준일마다 per_date건 넣는다(경계 겹침이 없도록 40일 간격)."""
    user = await get_db().users.find_one({"email": email})
    if grant_learning_consent:
        await get_db().kqs_learning_consents.insert_one({
            "consent_id": f"synthetic-consent-{start_index}-{date_count}",
            "user_id": user["_id"], "status": "granted",
            "policy_version": kqs.KQS_LEARNING_CONSENT_POLICY_VERSION,
            "purpose": "offline_learning_only",
            "acknowledgement": kqs.KQS_LEARNING_CONSENT_ACKNOWLEDGEMENT,
            "recorded_at": datetime.now(timezone.utc),
        })
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    decisions, outcomes = [], []
    for day in range(start_index, start_index + date_count):
        as_of = (base + timedelta(days=40 * day)).date().isoformat()
        for index in range(per_date):
            doc = {"user_id": user["_id"], "signal_id": f"s-{day}-{index}", "symbol": "298380", "symbol_key": "298380",
                   "market": "KOSDAQ", "decision": ("buy", "hold", "exclude")[index % 3], "data_as_of": as_of,
                   "recommendation_snapshot": {"signal_id": f"s-{day}-{index}", "symbol": "298380",
                                               "market": "KOSDAQ", "signal_type": "swing", "reasons": []},
                   "engine_snapshot": {"engine_id": "kqs", "data_as_of": as_of},
                   "publication_snapshot_id": f"synthetic-snapshot-{day}",
                   "recommendation_generated_at": f"{as_of}T00:00:00+00:00",
                   "evidence_tags": [], "decided_at": base + timedelta(days=40 * day, minutes=index)}
            decisions.append(doc)
    result = await get_db().kqs_paper_decisions.insert_many(decisions)
    for doc, inserted in zip(decisions, result.inserted_ids):
        target = (date_type.fromisoformat(doc["data_as_of"]) + timedelta(days=28)).isoformat()
        outcomes.append({"decision_id": inserted, "user_id": user["_id"], "decision": doc["decision"], "status": "observed",
                         "data_as_of": doc["data_as_of"], "decided_at": doc["decided_at"],
                         "horizons": {"20": {"observed": True, "return_pct": 1.0, "excess_return_pct": 0.5, "target_date": target}}})
    await get_db().kqs_decision_outcomes.insert_many(outcomes)
    return user


def _metrics(count):
    return {"sample_count": count, "accuracy": 0.5, "balanced_accuracy": 0.5, "average_return_pct": 1.0}


@pytest.mark.asyncio
async def test_dataset_freeze_requires_readiness_and_seals_holdout(client, make_user):
    email = "kqs-ds-freeze@example.com"
    headers = await make_user(email)
    assert (await client.post("/api/kqs/datasets", headers=headers)).status_code == 422
    await _seed_mature_decisions(email, 0, 10)

    readiness = (await client.get("/api/kqs/decisions/training-readiness", headers=headers)).json()
    assert readiness["ready"] is True and readiness["counts"] == {"train": 90, "validation": 30, "test": 30}

    frozen = await client.post("/api/kqs/datasets", headers=headers)
    assert frozen.status_code == 201, frozen.text
    item = frozen.json()["item"]
    assert item["sequence"] == 1 and item["holdout"]["status"] == "sealed"
    assert item["counts"] == {"train": 90, "validation": 30, "test": 30}
    manifest_id = item["manifest_id"]

    train = await client.get(f"/api/kqs/datasets/{manifest_id}/examples?split=train", headers=headers)
    assert train.status_code == 200, train.text
    lines = [json.loads(line) for line in train.text.splitlines()]
    assert lines[0]["hash_verified"] is True and lines[0]["example_count"] == 90 == len(lines) - 1
    assert all(line["dataset_split"] == "train" for line in lines[1:])

    sealed = await client.get(f"/api/kqs/datasets/{manifest_id}/examples?split=test", headers=headers)
    assert sealed.status_code == 409
    legacy = await client.get("/api/kqs/decisions/training-dataset?split=test", headers=headers)
    assert legacy.status_code == 410

    other = await make_user("kqs-ds-other@example.com")
    assert (await client.get(f"/api/kqs/datasets/{manifest_id}/examples?split=train", headers=other)).status_code == 404


@pytest.mark.asyncio
async def test_dataset_drift_after_freeze_fails_closed(client, make_user):
    email = "kqs-ds-drift@example.com"
    headers = await make_user(email)
    user = await _seed_mature_decisions(email, 0, 10)
    manifest_id = (await client.post("/api/kqs/datasets", headers=headers)).json()["item"]["manifest_id"]
    manifest = await get_db().kqs_dataset_manifests.find_one({"manifest_id": manifest_id})
    changed = manifest["examples"]["train"][0]["id"]
    await get_db().kqs_decision_outcomes.update_one({"decision_id": ObjectId(changed)}, {"$set": {"horizons.20.return_pct": 9.9}})
    assert (await client.get(f"/api/kqs/datasets/{manifest_id}/examples?split=validation", headers=headers)).status_code == 200
    drifted = await client.get(f"/api/kqs/datasets/{manifest_id}/examples?split=train", headers=headers)
    assert drifted.status_code == 409
    assert "1건" in drifted.json()["detail"]
    opened = await client.post(f"/api/kqs/datasets/{manifest_id}/holdout", headers=headers, json={
        "model_family": "majority_class_baseline", "model_version": "m1", "validation": _metrics(30)})
    assert opened.status_code == 409  # 개봉 전 전 구간 해시 대조


@pytest.mark.asyncio
async def test_holdout_opens_once_with_preregistration_and_one_test_report(client, make_user):
    email = "kqs-ds-holdout@example.com"
    headers = await make_user(email)
    user = await _seed_mature_decisions(email, 0, 10)
    manifest_id = (await client.post("/api/kqs/datasets", headers=headers)).json()["item"]["manifest_id"]
    model = {"model_family": "logistic_regression", "model_version": "logit-v1", "training_config": {"C": 1.0}}

    exploratory = await client.post("/api/kqs/training-experiments", headers=headers, json={
        "manifest_id": manifest_id, **model, "train": _metrics(90), "validation": _metrics(30)})
    assert exploratory.status_code == 201, exploratory.text
    assert exploratory.json()["item"]["holdout_evaluation"] is False
    early_test = await client.post("/api/kqs/training-experiments", headers=headers, json={
        "manifest_id": manifest_id, **model, "train": _metrics(90), "validation": _metrics(30), "test": _metrics(30)})
    assert early_test.status_code == 409
    wrong_counts = await client.post("/api/kqs/training-experiments", headers=headers, json={
        "manifest_id": manifest_id, **model, "train": _metrics(89), "validation": _metrics(30)})
    assert wrong_counts.status_code == 422

    bad_prereg = await client.post(f"/api/kqs/datasets/{manifest_id}/holdout", headers=headers,
                                   json={**model, "validation": _metrics(29)})
    assert bad_prereg.status_code == 422
    opened = await client.post(f"/api/kqs/datasets/{manifest_id}/holdout", headers=headers, json={**model, "validation": _metrics(30)})
    assert opened.status_code == 201, opened.text
    assert opened.json()["item"]["holdout"]["status"] == "opened"
    again = await client.post(f"/api/kqs/datasets/{manifest_id}/holdout", headers=headers, json={**model, "validation": _metrics(30)})
    assert again.status_code == 409

    test = await client.get(f"/api/kqs/datasets/{manifest_id}/examples?split=test", headers=headers)
    assert test.status_code == 200 and len(test.text.splitlines()) == 31
    assert await get_db().kqs_dataset_access_log.count_documents({"manifest_id": manifest_id, "user_id": user["_id"]}) == 1

    other_model = await client.post("/api/kqs/training-experiments", headers=headers, json={
        "manifest_id": manifest_id, **model, "model_version": "logit-v2", "train": _metrics(90),
        "validation": _metrics(30), "test": _metrics(30)})
    assert other_model.status_code == 409
    final = await client.post("/api/kqs/training-experiments", headers=headers, json={
        "manifest_id": manifest_id, **model, "train": _metrics(90), "validation": _metrics(30), "test": _metrics(30)})
    assert final.status_code == 201, final.text
    assert final.json()["item"]["holdout_evaluation"] is True
    assert final.json()["item"]["preregistration_sha256"] == opened.json()["item"]["holdout"]["preregistration_sha256"]
    repeat = await client.post("/api/kqs/training-experiments", headers=headers, json={
        "manifest_id": manifest_id, **model, "train": _metrics(90), "validation": _metrics(30), "test": _metrics(30)})
    assert repeat.status_code == 409


@pytest.mark.asyncio
async def test_successor_manifest_evaluates_only_new_dates_and_retires_sealed_holdout(client, make_user):
    email = "kqs-ds-successor@example.com"
    headers = await make_user(email)
    await _seed_mature_decisions(email, 0, 10)
    first = (await client.post("/api/kqs/datasets", headers=headers)).json()["item"]
    assert (await client.post("/api/kqs/datasets", headers=headers)).status_code == 422  # 새 기준일 없음

    await _seed_mature_decisions(email, 10, 4)
    second = await client.post("/api/kqs/datasets", headers=headers)
    assert second.status_code == 201, second.text
    second = second.json()["item"]
    assert second["sequence"] == 2 and second["previous_manifest_id"] == first["manifest_id"]
    assert second["boundaries"]["validation"]["first"] > first["boundaries"]["test"]["last"]
    assert second["counts"] == {"train": 150, "validation": 30, "test": 30}

    listed = (await client.get("/api/kqs/datasets", headers=headers)).json()["items"]
    assert [row["holdout"]["status"] for row in listed] == ["sealed", "retired_sealed"]
    retired = await client.post(f"/api/kqs/datasets/{first['manifest_id']}/holdout", headers=headers, json={
        "model_family": "majority_class_baseline", "model_version": "m1", "validation": _metrics(30)})
    assert retired.status_code == 409
