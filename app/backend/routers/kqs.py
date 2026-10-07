"""통합 KQS 추천·사용자 수동 판단 API. Legacy PC bridge는 명시적 호환 모드다."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import statistics
import urllib.error
import urllib.request
from collections import Counter
from uuid import uuid4
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError
from fastapi.responses import JSONResponse

try:
    from ..db import get_db
    from ..kqs_manual_records import ManualRecordInput, request_identity, replay_record, manual_write_lock, trade_fields, audit_without_rollback
    from ..execution_audit import append_execution_audit
    from ..kqs_contract import KqsContractError, validate_recommendation_payload
    from ..kqs_recommendations import (read_local_recommendations, recommendation_source,
                                      publication_status, publication_candidates, publication_packet,
                                      native_positions_not_applicable)
    from ..kqs_strategy_board import board_items, holding_signals, read_outcome_report, read_rebound_shadow
    from ..kqs_pipeline_health import pipeline_health
    from .auth import current_user
except ImportError:
    from db import get_db  # type: ignore
    from kqs_manual_records import ManualRecordInput, request_identity, replay_record, manual_write_lock, trade_fields, audit_without_rollback
    from execution_audit import append_execution_audit  # type: ignore
    from kqs_contract import KqsContractError, validate_recommendation_payload  # type: ignore
    from kqs_recommendations import (read_local_recommendations, recommendation_source,
                                    publication_status, publication_candidates, publication_packet,
                                    native_positions_not_applicable)  # type: ignore
    from kqs_strategy_board import board_items, holding_signals, read_outcome_report, read_rebound_shadow  # type: ignore
    from kqs_pipeline_health import pipeline_health  # type: ignore
    from routers.auth import current_user  # type: ignore


router = APIRouter(prefix="/api/kqs", tags=["퀀트 추천·수동 판단"])
KST = ZoneInfo("Asia/Seoul")


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read an integer setting without allowing an unsafe operational range."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _korean_date_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc).astimezone(KST).date() - timedelta(days=days - 1)).isoformat()


class CandidateReviewInput(BaseModel):
    as_of: str = Field(min_length=10, max_length=10)
    status: Literal["reviewed", "deferred"]
    note: str = Field(default="", max_length=500)


class OperationsAlertAcknowledgementInput(BaseModel):
    status: Literal["acknowledged", "deferred"]
    note: str = Field(default="", max_length=500)


class KqsExperimentSplitMetrics(BaseModel):
    sample_count: int = Field(ge=0, le=1_000_000)
    accuracy: float | None = Field(default=None, ge=0, le=1)
    balanced_accuracy: float | None = Field(default=None, ge=0, le=1)
    average_return_pct: float | None = Field(default=None, ge=-100, le=10_000)


class KqsLearningConsentInput(BaseModel):
    """Explicit user choice for future offline learning only; it never authorizes trading."""
    consented: bool
    acknowledgement: Literal["KQS_LEARNING_DATA_ONLY"]


class KqsTrainingExperimentInput(BaseModel):
    manifest_id: str = Field(min_length=1, max_length=80)
    model_family: Literal["majority_class_baseline", "logistic_regression", "tree_ensemble", "external_offline"]
    model_version: str = Field(min_length=1, max_length=80)
    training_config: dict[str, str | int | float | bool] = Field(default_factory=dict)
    train: KqsExperimentSplitMetrics
    validation: KqsExperimentSplitMetrics
    test: KqsExperimentSplitMetrics | None = None
    note: str = Field(default="", max_length=1_000)


def _bridge_url() -> str:
    return os.getenv("KQS_BRIDGE_URL", "http://127.0.0.1:18100").rstrip("/")


def _read_bridge_payload() -> dict:
    """브리지 원문은 이 모듈 안에서만 읽고, 라우트에는 축약본만 반환한다."""
    request = urllib.request.Request(f"{_bridge_url()}/api/all", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"퀀트 엔진 브리지에 연결하지 못했습니다: {exc}") from exc


def _read_bridge_recommendations() -> dict:
    """하위 호환 함수명: 기본은 불변 로컬 게시, legacy bridge는 명시적으로만 선택한다."""
    source = recommendation_source()
    if source == "snapshot":
        return read_local_recommendations()
    if source != "bridge":
        raise RuntimeError("KQS_RECOMMENDATION_SOURCE는 snapshot 또는 bridge여야 합니다.")
    return _read_legacy_bridge_recommendations()


def _read_legacy_bridge_recommendations() -> dict:
    """명시적인 legacy 호환 모드. 자동 fallback이나 엔진 재실행은 없다."""
    max_bytes = 512 * 1024
    request = urllib.request.Request(
        f"{_bridge_url()}/api/v1/recommendations", headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise KqsContractError("추천 응답이 512 KiB 제한을 넘었습니다.")
        payload = json.loads(raw.decode("utf-8"))
        return validate_recommendation_payload(payload)
    except (urllib.error.URLError, TimeoutError, ValueError, KqsContractError) as exc:
        raise RuntimeError(f"엔진 추천 계약을 검증하지 못했습니다: {exc}") from exc


def _read_bridge_summary() -> dict:
    """Compatibility name; native mode never requests the PC bridge."""
    if recommendation_source() == "snapshot":
        return publication_status(read_local_recommendations())
    payload = _read_bridge_payload()
    health, scheduler = payload.get("health", {}), payload.get("scheduler", {})
    pipeline, db, paper = payload.get("pipeline", {}), payload.get("db", {}), payload.get("paper", {})
    forward, hashlock = payload.get("forward", {}), payload.get("hashlock", {})
    last = pipeline.get("last", {})
    return {
        "available": bool(health.get("ok")), "bridge": health.get("bridge"), "generated_at": payload.get("generated_at"),
        "read_only": bool(health.get("read_only")),
        "scheduler": {key: scheduler.get(key) for key in ("state", "last_run", "last_result_ok", "next_run", "missed_runs")},
        "pipeline": {"status": pipeline.get("status"), "last_start": last.get("start_kst"), "last_end": last.get("end_kst"),
                     "duration_s": last.get("duration_s"), "db_advanced": last.get("db_advanced"),
                     "step_results": {key: value.get("rc") for key, value in last.get("steps", {}).items()},
                     "skip_count": pipeline.get("skip_count"), "fatal_count": pipeline.get("fatal_count")},
        "market_data": {key: db.get(key) for key in ("latest_trade_date", "rows_on_latest", "latest_gap_days")},
        "paper": {"open_positions": (paper.get("positions", {}).get("status_counts", {}) or {}).get("OPEN", 0),
                  "latest_selected": paper.get("audit", {}).get("latest", {}).get("selected", 0),
                  "latest_as_of": paper.get("audit", {}).get("latest", {}).get("asof")},
        "forward": {"v2": forward.get("v2", {}).get("last", {}).get("judge"),
                    "v4": forward.get("v4", {}).get("last", {}).get("judge")},
        "hashlock": hashlock.get("summary", {}),
        "disclaimer": "Ubuntu-26.04 엔진의 읽기 전용 운영 요약입니다. 홈페이지에서 파이프라인·전략·주문을 실행하거나 변경하지 않습니다.",
    }


def _symbol_key(value: object) -> str:
    """KRX 코드와 홈페이지 티커의 접미사를 제거해 비교 키를 만든다."""
    symbol = str(value or "").strip().upper()
    return symbol.split(".", 1)[0]


def _read_engine_positions() -> dict:
    """브리지 원문에서 열린 모의 포지션에 필요한 필드만 추출한다.

    원문에는 파이프라인 로그와 연구 자료가 함께 있으므로 이 함수의 반환값만
    홈페이지 API 경계를 통과하게 한다.
    """
    if recommendation_source() == "snapshot":
        return native_positions_not_applicable()
    payload = _read_bridge_payload()
    paper = payload.get("paper", {}) or {}
    raw_positions = (paper.get("positions", {}) or {}).get("items", []) or []
    positions = []
    for item in raw_positions[:100]:
        if str(item.get("Status", "")).upper() != "OPEN":
            continue
        symbol = str(item.get("Symbol", "")).strip()
        key = _symbol_key(symbol)
        if not key:
            continue
        positions.append({
            "symbol": symbol,
            "symbol_key": key,
            "name": str(item.get("Name", "")).strip(),
            "market": str(item.get("Market", "")).strip(),
            "signal_type": str(item.get("SignalType", "")).strip(),
            "entry_date": str(item.get("EntryDate", "")).strip() or None,
            "entry_price": item.get("EntryPrice"),
        })
    latest = (paper.get("audit", {}) or {}).get("latest", {}) or {}
    return {
        "available": bool((payload.get("health", {}) or {}).get("ok")),
        "as_of": latest.get("asof"),
        "positions": positions,
    }


def _read_engine_review_candidates() -> dict:
    """현재 추천과 같은 source의 유효한 selected 진입 후보만 반환한다."""
    if recommendation_source() == "snapshot":
        return publication_candidates(read_local_recommendations())
    payload = _read_bridge_payload()
    paper = payload.get("paper", {}) or {}
    audit = paper.get("audit", {}) or {}
    latest = audit.get("latest", {}) or {}
    as_of = str(latest.get("asof") or "")
    entry_due = _latest_selected_audit_rows(audit, as_of)
    candidates: list[dict] = []
    seen: set[str] = set()
    for item in entry_due[:20]:
        if str(item.get("SelectionOutcome") or "").lower() != "selected":
            continue
        symbol = str(item.get("Symbol") or "").strip()
        key = _symbol_key(symbol)
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append({
            "symbol": symbol,
            "symbol_key": key,
            "name": str(item.get("Name") or "").strip(),
            "market": str(item.get("Market") or "").strip(),
            "signal_type": str(item.get("SignalType") or "").strip(),
            "execution_timing": str(item.get("ExecutionTiming") or "").strip(),
            "market_cap_rank": item.get("MarketCapRank"),
            "average_trading_value": item.get("AverageTradingValue"),
        })
    blocked = [
        item for item in (audit.get("latest_signal_rows", []) or [])
        if str(item.get("AsOf") or as_of) == as_of and str(item.get("SelectionOutcome") or "") == "regime_gate_blocked"
    ]
    return {
        "available": bool((payload.get("health", {}) or {}).get("ok")),
        "read_only": True,
        "as_of": as_of or None,
        "latest_selected_count": latest.get("selected", 0),
        "entry_due_count": latest.get("entry_due", len(candidates)),
        "blocked_signal_count": len(blocked),
        "candidates": candidates,
        "disclaimer": "엔진의 최신 감사 파일에서 다음 거래일 진입 대상으로 선택된 항목만 보여주는 연구용 검토 큐입니다. 매수 제안·주문·포트폴리오 반영을 수행하지 않습니다.",
    }


def _latest_selected_audit_rows(audit: dict, as_of: str) -> list[dict]:
    """브리지 버전 차이를 흡수해 최신 기준일의 selected 행만 반환한다."""
    entry_due = audit.get("latest_entry_due", []) or []
    if entry_due:
        return entry_due
    return [
        item for item in (audit.get("selected_rows", []) or [])
        if str(item.get("AsOf") or "") == as_of and str(item.get("SelectionOutcome") or "").lower() == "selected"
    ]


def _read_engine_review_packet(symbol: str) -> dict:
    """현재 추천과 같은 source의 후보 근거를 반환한다."""
    if recommendation_source() == "snapshot":
        return publication_packet(read_local_recommendations(), symbol)
    payload = _read_bridge_payload()
    paper = payload.get("paper", {}) or {}
    audit = paper.get("audit", {}) or {}
    latest = audit.get("latest", {}) or {}
    as_of = str(latest.get("asof") or "")
    symbol_key = _symbol_key(symbol)
    row = next((
        item for item in _latest_selected_audit_rows(audit, as_of)
        if str(item.get("SelectionOutcome") or "").lower() == "selected" and _symbol_key(item.get("Symbol")) == symbol_key
    ), None)
    available = bool((payload.get("health", {}) or {}).get("ok"))
    if not available or not row:
        return {"available": available, "found": False, "as_of": as_of or None}
    market = str(row.get("Market") or "").strip()
    db = payload.get("db", {}) or {}
    market_rows = (db.get("rows_on_latest_by_market", []) or [])
    market_row = next((item for item in market_rows if str(item.get("market") or "") == market), {})
    pipeline = payload.get("pipeline", {}) or {}
    last = pipeline.get("last", {}) or {}
    return {
        "available": True,
        "found": True,
        "read_only": True,
        "packet_version": "kqs-candidate-review-v1",
        "as_of": as_of or None,
        "candidate": {
            "symbol": str(row.get("Symbol") or "").strip(), "symbol_key": symbol_key,
            "name": str(row.get("Name") or "").strip(), "market": market,
            "signal_type": str(row.get("SignalType") or "").strip(),
            "execution_timing": str(row.get("ExecutionTiming") or "").strip(),
        },
        "selection_evidence": {
            "outcome": str(row.get("SelectionOutcome") or "").strip(),
            "scan_state": str(row.get("ScanState") or "").strip(),
            "market_cap_rank": row.get("MarketCapRank"),
            "market_cap_tier": row.get("MarketCapTier"),
            "average_trading_value": row.get("AverageTradingValue"),
        },
        "market_context": {
            "db_latest_trade_date": db.get("latest_trade_date"),
            "market_rows_on_latest": market_row.get("rows"),
            "market_data_gap_days": db.get("latest_gap_days"),
        },
        "engine_context": {
            "pipeline_status": pipeline.get("status"),
            "pipeline_db_advanced": last.get("db_advanced"),
            "configuration_fingerprint": str(row.get("ConfigFingerprint") or "").strip() or None,
        },
        "disclaimer": "최신 엔진 감사 행에서 추출한 검토용 정형 근거입니다. 가격 목표·수익 예측·매매 추천이 아니며 주문·포트폴리오를 변경하지 않습니다.",
    }



def _request_source() -> str:
    try:
        return recommendation_source()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _candidate_collection(db, name: str):
    """Separate native observation/review history; never migrate or relabel legacy rows."""
    prefix = "kqs_native_" if _request_source() == "snapshot" else "kqs_"
    return db[prefix + name.removeprefix("kqs_")]


def _serialize_candidate_review(document: dict) -> dict:
    return {
        "source": document.get("source", "bridge"),
        "publication_snapshot_id": document.get("publication_snapshot_id"),
        "symbol_key": document["symbol_key"],
        "symbol": document["symbol"],
        "name": document.get("name", ""),
        "as_of": document["as_of"],
        "status": document["status"],
        "note": document.get("note", ""),
        "updated_at": document["updated_at"].isoformat() if document.get("updated_at") else None,
    }


def _serialize_candidate_review_snapshot(document: dict) -> dict:
    return {
        "as_of": document["as_of"],
        "symbol_key": document["symbol_key"],
        "symbol": document["symbol"],
        "name": document.get("name", ""),
        "status": document["status"],
        "note": document.get("note", ""),
        "captured_at": document["captured_at"].isoformat() if document.get("captured_at") else None,
        "packet": document["packet"],
    }


async def _candidate_queue_for_user(user_id) -> dict:
    """현재 후보에 해당 사용자만 볼 수 있는 검토 상태를 병합한다."""
    queue = await asyncio.to_thread(_read_engine_review_candidates)
    candidates = queue.get("candidates", [])
    as_of = queue.get("as_of")
    if not as_of or not candidates:
        queue["review_summary"] = {"reviewed_count": 0, "deferred_count": 0, "pending_count": len(candidates)}
        return queue
    keys = [candidate["symbol_key"] for candidate in candidates]
    documents = await _candidate_collection(get_db(), "kqs_candidate_reviews").find(
        {"user_id": user_id, "as_of": as_of, "symbol_key": {"$in": keys},
         **({"publication_snapshot_id": queue["snapshot_id"]} if queue.get("source") == "snapshot" else {})}, {"_id": 0, "user_id": 0},
    ).to_list(len(keys))
    reviews = {review["symbol_key"]: _serialize_candidate_review(review) for review in documents}
    for candidate in candidates:
        candidate["review"] = reviews.get(candidate["symbol_key"])
    queue["review_summary"] = {
        "reviewed_count": sum(review["status"] == "reviewed" for review in reviews.values()),
        "deferred_count": sum(review["status"] == "deferred" for review in reviews.values()),
        "pending_count": len(candidates) - len(reviews),
    }
    return queue


def _snapshot_queue_candidates(candidates: list[dict]) -> list[dict]:
    """일별 이력에는 후보 식별·분류에 필요한 안전한 필드만 남긴다."""
    return [{
        "symbol": item["symbol"], "symbol_key": item["symbol_key"], "name": item.get("name", ""),
        "market": item.get("market", ""), "signal_type": item.get("signal_type", ""),
        "execution_timing": item.get("execution_timing", ""),
    } for item in candidates]


def _candidate_queue_changes(candidates: list[dict], previous: dict | None) -> dict:
    current = {item["symbol_key"]: item for item in candidates}
    if not previous:
        return {"baseline": True, "changed": False, "new": [], "removed": []}
    prior = {item["symbol_key"]: item for item in previous.get("candidates", [])}
    return {
        "baseline": False,
        "changed": set(current) != set(prior),
        "new": [current[key] for key in sorted(set(current) - set(prior))],
        "removed": [prior[key] for key in sorted(set(prior) - set(current))],
    }


async def capture_candidate_queue_snapshot(queue: dict | None = None) -> dict:
    """후보 큐 전역 연구 상태를 KST 하루 한 건으로 저장한다."""
    if queue is None:
        queue = await asyncio.to_thread(_read_engine_review_candidates)
    if not queue.get("available"):
        return {"stored": False, "reason": "외부 엔진을 확인하지 못해 후보 큐 스냅샷을 저장하지 않았습니다."}
    now = datetime.now(timezone.utc)
    snapshot_date = now.astimezone(KST).date().isoformat()
    db = get_db()
    previous = await _candidate_collection(db, "kqs_candidate_queue_snapshots").find_one(
        {"snapshot_date": {"$lt": snapshot_date}}, sort=[("snapshot_date", -1)],
    )
    candidates = _snapshot_queue_candidates(queue.get("candidates", []))
    existing = await _candidate_collection(db, "kqs_candidate_queue_snapshots").find_one(
        {"snapshot_date": snapshot_date}, {"opened_at": 1, "captured_at": 1},
    )
    # An operator refresh must not silently reset the review-SLA clock.
    opened_at = (existing or {}).get("opened_at") or (existing or {}).get("captured_at") or now
    document = {
        "snapshot_date": snapshot_date, "captured_at": now, "opened_at": opened_at, "as_of": queue.get("as_of"),
        "source": queue.get("source", recommendation_source()), "snapshot_id": queue.get("snapshot_id"),
        "generated_at": queue.get("generated_at"), "expires_at": queue.get("expires_at"),
        "engine": queue.get("engine"),
        "summary": {key: queue.get(key, 0) for key in ("latest_selected_count", "entry_due_count", "blocked_signal_count")},
        "candidates": candidates, "changes": _candidate_queue_changes(candidates, previous),
    }
    await _candidate_collection(db, "kqs_candidate_queue_snapshots").update_one(
        {"snapshot_date": snapshot_date}, {"$set": document}, upsert=True,
    )
    return {"stored": True, **document}


def _serialize_candidate_queue_snapshot(document: dict) -> dict:
    return {
        "source": document.get("source", "bridge"), "snapshot_id": document.get("snapshot_id"),
        "generated_at": document.get("generated_at"), "expires_at": document.get("expires_at"),
        "snapshot_date": document["snapshot_date"],
        "captured_at": document["captured_at"].isoformat() if document.get("captured_at") else None,
        "opened_at": (document.get("opened_at") or document.get("captured_at")).isoformat() if (document.get("opened_at") or document.get("captured_at")) else None,
        "as_of": document.get("as_of"), "summary": document.get("summary", {}),
        "candidates": document.get("candidates", []), "changes": document.get("changes", {}),
    }


def _build_candidate_review_workflow(snapshot: dict, reviews: list[dict], now: datetime | None = None) -> dict:
    """Build a per-user review work queue; it cannot affect orders or positions."""
    now = now or datetime.now(timezone.utc)
    sla_hours = _bounded_env_int("KQS_REVIEW_SLA_HOURS", 4, 1, 72)
    due_soon_minutes = _bounded_env_int("KQS_REVIEW_DUE_SOON_MINUTES", 60, 5, 720)
    opened_at = snapshot.get("opened_at") or snapshot.get("captured_at") or now
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    deadline_at = opened_at + timedelta(hours=sla_hours)
    review_by_symbol = {review.get("symbol_key"): review for review in reviews}
    summary = {"total_count": 0, "reviewed_count": 0, "pending_count": 0,
               "high_priority_pending_count": 0, "due_soon_count": 0, "overdue_count": 0}
    items = []
    for candidate in snapshot.get("candidates", []):
        symbol_key = candidate.get("symbol_key") or candidate.get("symbol")
        review = review_by_symbol.get(symbol_key)
        priority = "high" if candidate.get("execution_timing") == "next_trading_session" else "normal"
        if review:
            attention = "completed"
        elif now > deadline_at:
            attention = "overdue"
        elif deadline_at - now <= timedelta(minutes=due_soon_minutes):
            attention = "due_soon"
        else:
            attention = "open"
        summary["total_count"] += 1
        if review:
            summary["reviewed_count"] += 1
        else:
            summary["pending_count"] += 1
            if priority == "high":
                summary["high_priority_pending_count"] += 1
            if attention == "due_soon":
                summary["due_soon_count"] += 1
            elif attention == "overdue":
                summary["overdue_count"] += 1
        items.append({
            "symbol": candidate.get("symbol"), "symbol_key": symbol_key, "name": candidate.get("name"),
            "market": candidate.get("market"), "signal_type": candidate.get("signal_type"),
            "execution_timing": candidate.get("execution_timing"), "priority": priority, "attention": attention,
            "review": _serialize_candidate_review(review) if review else None,
            "deadline_at": deadline_at.isoformat(),
        })
    attention_order = {"overdue": 0, "due_soon": 1, "open": 2, "completed": 3}
    items.sort(key=lambda item: (attention_order[item["attention"]], 0 if item["priority"] == "high" else 1, item["name"] or ""))
    return {
        "opened_at": opened_at.isoformat(), "deadline_at": deadline_at.isoformat(),
        "policy": {"sla_hours": sla_hours, "due_soon_minutes": due_soon_minutes,
                   "high_priority_execution_timing": "next_trading_session"},
        "summary": summary, "items": items,
    }


async def build_candidate_review_report(user_id, days: int = 7) -> dict:
    """Summarize a user's candidate-review evidence without inferring investment performance."""
    period_days = max(1, min(365, int(days)))
    cutoff = _korean_date_days_ago(period_days)
    db = get_db()
    snapshots = await _candidate_collection(db, "kqs_candidate_queue_snapshots").find(
        {"snapshot_date": {"$gte": cutoff}}, {"_id": 0},
    ).sort("snapshot_date", 1).to_list(period_days + 1)
    reviews = await _candidate_collection(db, "kqs_candidate_reviews").find(
        {"user_id": user_id, "as_of": {"$gte": cutoff}}, {"_id": 0, "user_id": 0},
    ).to_list(5_000)
    reviews_by_asof: dict[str, dict[str, dict]] = {}
    for review in reviews:
        reviews_by_asof.setdefault(review.get("as_of", ""), {})[review.get("symbol_key", "")] = review

    status_counts = {"reviewed": 0, "deferred": 0, "pending": 0, "within_sla": 0, "after_sla": 0}
    daily = []
    for snapshot in snapshots:
        candidates = snapshot.get("candidates", [])
        review_by_key = reviews_by_asof.get(snapshot.get("as_of", ""), {})
        opened_at = snapshot.get("opened_at") or snapshot.get("captured_at")
        deadline_at = _utc(opened_at) + timedelta(hours=_bounded_env_int("KQS_REVIEW_SLA_HOURS", 4, 1, 72)) if opened_at else None
        reviewed = deferred = within_sla = after_sla = 0
        for candidate in candidates:
            review = review_by_key.get(candidate.get("symbol_key"))
            if not review:
                status_counts["pending"] += 1
                continue
            if review.get("status") == "reviewed":
                reviewed += 1
                status_counts["reviewed"] += 1
            else:
                deferred += 1
                status_counts["deferred"] += 1
            updated_at = review.get("updated_at")
            if deadline_at and updated_at:
                if _utc(updated_at) <= deadline_at:
                    within_sla += 1
                    status_counts["within_sla"] += 1
                else:
                    after_sla += 1
                    status_counts["after_sla"] += 1
        daily.append({
            "snapshot_date": snapshot.get("snapshot_date"), "as_of": snapshot.get("as_of"),
            "candidate_count": len(candidates), "reviewed_count": reviewed, "deferred_count": deferred,
            "pending_count": len(candidates) - reviewed - deferred,
            "within_sla_count": within_sla, "after_sla_count": after_sla,
        })
    recorded_count = status_counts["reviewed"] + status_counts["deferred"]
    candidate_count = recorded_count + status_counts["pending"]
    return {
        "report_version": "kqs-candidate-review-report-v1", "period_days": period_days, "cutoff_date": cutoff,
        "generated_at": datetime.now(timezone.utc).isoformat(), "daily": daily,
        "summary": {
            "candidate_observations": candidate_count, "reviewed_count": status_counts["reviewed"],
            "deferred_count": status_counts["deferred"], "pending_count": status_counts["pending"],
            "recorded_review_rate_pct": round(recorded_count / candidate_count * 100, 2) if candidate_count else None,
            "within_sla_count": status_counts["within_sla"], "after_sla_count": status_counts["after_sla"],
            "sla_compliance_rate_pct": round(status_counts["within_sla"] / (status_counts["within_sla"] + status_counts["after_sla"]) * 100, 2)
            if (status_counts["within_sla"] + status_counts["after_sla"]) else None,
        },
        "disclaimer": "후보 검토의 기록·응답 현황 리포트입니다. 후보 수익률이나 전략 성과를 추정하지 않으며 주문·승인·포트폴리오를 변경하지 않습니다.",
    }


async def build_candidate_selection_quality(user_id, days: int = 30) -> dict:
    """Measure selection continuity and review coverage, not returns or causal performance."""
    period_days = max(2, min(365, int(days)))
    cutoff = _korean_date_days_ago(period_days)
    snapshots = await _candidate_collection(get_db(), "kqs_candidate_queue_snapshots").find(
        {"snapshot_date": {"$gte": cutoff}}, {"_id": 0},
    ).sort("snapshot_date", 1).to_list(period_days + 1)
    continued = prior_total = new_count = 0
    transitions = []
    for prior, current in zip(snapshots, snapshots[1:]):
        prior_keys = {item.get("symbol_key") for item in prior.get("candidates", []) if item.get("symbol_key")}
        current_keys = {item.get("symbol_key") for item in current.get("candidates", []) if item.get("symbol_key")}
        overlap = prior_keys & current_keys
        prior_total += len(prior_keys)
        continued += len(overlap)
        new_count += len(current_keys - prior_keys)
        transitions.append({"from_date": prior.get("snapshot_date"), "to_date": current.get("snapshot_date"),
                            "prior_count": len(prior_keys), "current_count": len(current_keys),
                            "continued_count": len(overlap), "new_count": len(current_keys - prior_keys)})
    review_report = await build_candidate_review_report(user_id, period_days)
    return {
        "report_version": "kqs-candidate-selection-quality-v1", "period_days": period_days,
        "snapshot_count": len(snapshots), "transitions": transitions,
        "summary": {
            "candidate_continuity_rate_pct": round(continued / prior_total * 100, 2) if prior_total else None,
            "continued_candidate_count": continued, "new_candidate_count": new_count,
            "review_coverage_pct": review_report["summary"]["recorded_review_rate_pct"],
            "candidate_observations": review_report["summary"]["candidate_observations"],
        },
        "disclaimer": "선정 품질은 다음 일별 기준선까지 후보가 유지된 비율과 검토 기록 범위로만 표시합니다. 수익률·알파·전략 인과관계를 의미하지 않으며 투자 판단을 자동화하지 않습니다.",
    }


def _candidate_market_ticker(candidate: dict) -> str | None:
    """Map a safe KRX candidate code to the website market-data convention."""
    symbol = str(candidate.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    if "." in symbol:
        return symbol
    market = str(candidate.get("market") or "").strip().upper()
    if market == "KOSPI":
        return f"{symbol}.KS"
    if market == "KOSDAQ":
        return f"{symbol}.KQ"
    return None


def _return_pct(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start <= 0:
        return None
    return round((end / start - 1) * 100, 4)


def _max_drawdown_pct(bars: list[dict]) -> float | None:
    closes = [float(item["close"]) for item in bars if isinstance(item.get("close"), (int, float)) and item["close"] > 0]
    if not closes:
        return None
    peak, maximum = closes[0], 0.0
    for close in closes:
        peak = max(peak, close)
        maximum = max(maximum, (peak - close) / peak * 100 if peak else 0.0)
    return round(maximum, 4)


async def _candidate_outcome_document(snapshot: dict, candidate: dict, captured_at: datetime) -> dict:
    """Evaluate only already-observed closes; never fetches or predicts a price."""
    ticker = _candidate_market_ticker(candidate)
    as_of = str(snapshot.get("as_of") or "")[:10]
    base = {
        "as_of": as_of, "symbol_key": candidate.get("symbol_key"), "symbol": candidate.get("symbol"),
        "name": candidate.get("name", ""), "market": candidate.get("market", ""), "ticker": ticker,
        "signal_type": candidate.get("signal_type", ""), "execution_timing": candidate.get("execution_timing", ""),
        "source_snapshot_date": snapshot.get("snapshot_date"), "captured_at": captured_at,
    }
    if not ticker or not as_of:
        return {**base, "status": "unsupported_symbol_mapping", "horizons": {}}
    db = get_db()
    stock_bars = await db.market_ohlcv.find(
        {"ticker": ticker, "interval": "1d", "timestamp": {"$gte": as_of}}, {"_id": 0, "timestamp": 1, "close": 1},
    ).sort("timestamp", 1).to_list(25)
    if not stock_bars or stock_bars[0].get("timestamp") != as_of:
        return {**base, "status": "awaiting_baseline_close", "horizons": {}}
    benchmark_bars = await db.market_ohlcv.find(
        {"ticker": "^KS11", "interval": "1d", "timestamp": {"$gte": as_of}}, {"_id": 0, "timestamp": 1, "close": 1},
    ).sort("timestamp", 1).to_list(25)
    benchmark_by_date = {bar.get("timestamp"): bar.get("close") for bar in benchmark_bars}
    base_close = stock_bars[0].get("close")
    benchmark_base = benchmark_by_date.get(as_of)
    horizons: dict[str, dict] = {}
    for horizon in (1, 5, 20):
        if len(stock_bars) <= horizon:
            horizons[str(horizon)] = {"observed": False, "return_pct": None, "benchmark_return_pct": None, "excess_return_pct": None}
            continue
        target = stock_bars[horizon]
        stock_return = _return_pct(base_close, target.get("close"))
        benchmark_return = _return_pct(benchmark_base, benchmark_by_date.get(target.get("timestamp")))
        horizons[str(horizon)] = {
            "observed": stock_return is not None, "target_date": target.get("timestamp"), "return_pct": stock_return,
            "benchmark_return_pct": benchmark_return,
            "excess_return_pct": round(stock_return - benchmark_return, 4) if stock_return is not None and benchmark_return is not None else None,
            "max_drawdown_pct": _max_drawdown_pct(stock_bars[:horizon + 1]),
        }
    return {**base, "status": "observed", "horizons": horizons}


async def capture_candidate_outcome_snapshots() -> dict:
    """Persist observed post-selection outcomes after market-data refresh; no engine writes."""
    snapshots = await _candidate_collection(get_db(), "kqs_candidate_queue_snapshots").find(
        {"as_of": {"$ne": None}}, {"_id": 0},
    ).sort("snapshot_date", -1).to_list(366)
    now = datetime.now(timezone.utc)
    stored = 0
    for snapshot in snapshots:
        for candidate in snapshot.get("candidates", []):
            if not candidate.get("symbol_key"):
                continue
            document = await _candidate_outcome_document(snapshot, candidate, now)
            await _candidate_collection(get_db(), "kqs_candidate_outcomes").update_one(
                {"as_of": document["as_of"], "symbol_key": document["symbol_key"]}, {"$set": document}, upsert=True,
            )
            stored += 1
    return {"stored_count": stored, "captured_at": now.isoformat()}


async def build_candidate_outcome_report(days: int = 365) -> dict:
    """Return realized outcomes with explicit sample-size and availability limits."""
    period_days = max(1, min(365, int(days)))
    cutoff = _korean_date_days_ago(period_days)
    rows = await _candidate_collection(get_db(), "kqs_candidate_outcomes").find(
        {"as_of": {"$gte": cutoff}}, {"_id": 0},
    ).sort([("as_of", -1), ("symbol_key", 1)]).to_list(5_000)
    summaries = {}
    minimum_sample_size = _bounded_env_int("KQS_OUTCOME_MIN_SAMPLE_SIZE", 20, 5, 1_000)
    for horizon in (1, 5, 20):
        observed = [row.get("horizons", {}).get(str(horizon), {}) for row in rows]
        returns = [float(item["return_pct"]) for item in observed if item.get("return_pct") is not None]
        excess = [float(item["excess_return_pct"]) for item in observed if item.get("excess_return_pct") is not None]
        summaries[str(horizon)] = {
            "observed_count": len(returns), "positive_count": sum(value > 0 for value in returns),
            "average_return_pct": round(statistics.fmean(returns), 4) if returns else None,
            "median_return_pct": round(statistics.median(returns), 4) if returns else None,
            "average_excess_return_pct": round(statistics.fmean(excess), 4) if excess else None,
            "average_max_drawdown_pct": round(statistics.fmean([float(item["max_drawdown_pct"]) for item in observed if item.get("max_drawdown_pct") is not None]), 4)
            if any(item.get("max_drawdown_pct") is not None for item in observed) else None,
            "sample_gate": "ready" if len(returns) >= minimum_sample_size else "insufficient_sample",
        }
    safe_rows = [{
        "as_of": row.get("as_of"), "symbol": row.get("symbol"), "name": row.get("name"), "market": row.get("market"),
        "ticker": row.get("ticker"), "status": row.get("status"), "horizons": row.get("horizons", {}),
        "signal_type": row.get("signal_type", ""), "execution_timing": row.get("execution_timing", ""),
    } for row in rows]
    return {
        "report_version": "kqs-candidate-outcomes-v1", "period_days": period_days, "rows": safe_rows,
        "summary": {"candidate_count": len(rows), "horizons": summaries,
                    "status_counts": {status: sum(row.get("status") == status for row in rows) for status in sorted({row.get("status") for row in rows})}},
        "quality_gate": {"minimum_sample_size": minimum_sample_size,
                         "ready_horizons": [key for key, value in summaries.items() if value["sample_gate"] == "ready"]},
        "disclaimer": "선정 기준일 종가와 이후 검증된 일봉만으로 계산한 사후 관측치입니다. 표본 기준을 충족하지 않으면 성과 판정을 보류합니다. 배당·세금·체결비용·실제 주문을 반영하지 않으며 미래 수익이나 전략 유효성을 보장하지 않습니다.",
    }


async def build_candidate_performance_decomposition(days: int = 365) -> dict:
    """Decompose observed candidate outcomes by safe classification fields and sample gates."""
    report = await build_candidate_outcome_report(days)
    minimum = report["quality_gate"]["minimum_sample_size"]
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in report["rows"]:
        for dimension in ("market", "signal_type", "execution_timing"):
            value = str(row.get(dimension) or "미분류")
            groups.setdefault((dimension, value), []).append(row)
    items = []
    for (dimension, value), rows in sorted(groups.items()):
        metrics = {}
        for horizon in (1, 5, 20):
            observations = [row.get("horizons", {}).get(str(horizon), {}) for row in rows]
            returns = [float(item["return_pct"]) for item in observations if item.get("return_pct") is not None]
            excess = [float(item["excess_return_pct"]) for item in observations if item.get("excess_return_pct") is not None]
            metrics[str(horizon)] = {"observed_count": len(returns), "average_return_pct": round(statistics.fmean(returns), 4) if returns else None,
                                     "average_excess_return_pct": round(statistics.fmean(excess), 4) if excess else None,
                                     "sample_gate": "ready" if len(returns) >= minimum else "insufficient_sample"}
        items.append({"dimension": dimension, "value": value, "candidate_count": len(rows), "horizons": metrics})
    return {"report_version": "kqs-candidate-performance-decomposition-v1", "period_days": report["period_days"],
            "minimum_sample_size": minimum, "items": items,
            "disclaimer": "후보 분류별 사후 관측치를 분해한 리서치 자료입니다. 표본이 부족한 그룹은 비교·승격·중단 판단에 사용하면 안 되며, 주문·전략 상태를 변경하지 않습니다."}


async def build_kqs_operations_alerts(user_id) -> dict:
    """Build a read-only operational attention list from stored snapshots and data-quality alerts."""
    now = datetime.now(timezone.utc)
    today = now.astimezone(KST).date()
    latest = await _candidate_collection(get_db(), "kqs_candidate_queue_snapshots").find_one({}, {"_id": 0}, sort=[("snapshot_date", -1)])
    alerts = []
    if not latest:
        alerts.append({"fingerprint": "candidate_snapshot_missing", "severity": "warning", "type": "candidate_snapshot_missing", "title": "후보 큐 기준선 없음",
                       "message": "외부 엔진 후보 큐의 일별 기준선이 아직 없습니다.", "first_seen_at": now})
    else:
        try:
            age_days = max(0, (today - datetime.fromisoformat(latest["snapshot_date"]).date()).days)
        except (KeyError, ValueError):
            age_days = None
        if age_days is None or age_days > 1:
            alerts.append({"fingerprint": "candidate_snapshot_stale", "severity": "warning", "type": "candidate_snapshot_stale", "title": "후보 큐 기준선 최신성 확인",
                           "message": f"최근 후보 기준선은 {latest.get('snapshot_date', '-')}입니다.", "age_days": age_days,
                           "first_seen_at": latest.get("opened_at") or latest.get("captured_at") or now})
    market_alerts = await get_db().market_data_alerts.find(
        {"condition_active": True}, {"_id": 0, "fingerprint": 1, "type": 1, "severity": 1, "title": 1, "message": 1, "created_at": 1, "updated_at": 1},
    ).sort("updated_at", -1).to_list(20)
    for alert in market_alerts:
        alerts.append({"fingerprint": f"market:{alert.get('fingerprint') or alert.get('type', 'unknown')}", "severity": alert.get("severity", "warning"), "type": f"market_{alert.get('type', 'unknown')}",
                       "title": alert.get("title", "시세 데이터 점검"), "message": alert.get("message", "시세 데이터 상태를 확인하세요."),
                       "updated_at": alert.get("updated_at").isoformat() if alert.get("updated_at") else None,
                       "first_seen_at": alert.get("created_at") or alert.get("updated_at") or now})
    if latest and latest.get("as_of") and latest.get("candidates"):
        keys = [item.get("symbol_key") for item in latest["candidates"] if item.get("symbol_key")]
        reviews = await _candidate_collection(get_db(), "kqs_candidate_reviews").find(
            {"user_id": user_id, "as_of": latest["as_of"], "symbol_key": {"$in": keys}}, {"_id": 0, "user_id": 0},
        ).to_list(len(keys))
        workflow = _build_candidate_review_workflow(latest, reviews, now)
        if workflow["summary"]["overdue_count"]:
            alerts.append({"fingerprint": "candidate_review_overdue", "severity": "warning", "type": "candidate_review_overdue", "title": "후보 검토 기한 경과",
                           "message": f"미검토 후보 {workflow['summary']['overdue_count']}건이 검토 SLA를 경과했습니다.",
                           "first_seen_at": latest.get("opened_at") or latest.get("captured_at") or now})
    acknowledgements = await get_db().kqs_operations_alert_acknowledgements.find(
        {"user_id": user_id, "fingerprint": {"$in": [item["fingerprint"] for item in alerts]}}, {"_id": 0, "user_id": 0},
    ).to_list(len(alerts)) if alerts else []
    acknowledgement_by_fingerprint = {item["fingerprint"]: item for item in acknowledgements}
    ack_sla_hours = _bounded_env_int("KQS_OPERATIONS_ACK_SLA_HOURS", 8, 1, 168)
    for alert in alerts:
        first_seen = alert.pop("first_seen_at", now)
        first_seen = _utc(first_seen)
        acknowledgement = acknowledgement_by_fingerprint.get(alert["fingerprint"])
        alert["first_seen_at"] = first_seen.isoformat()
        alert["acknowledgement"] = ({"status": acknowledgement["status"], "note": acknowledgement.get("note", ""),
                                      "updated_at": acknowledgement.get("updated_at").isoformat() if acknowledgement.get("updated_at") else None}
                                     if acknowledgement else None)
        alert["acknowledgement_sla"] = "acknowledged" if acknowledgement else (
            "overdue" if now > first_seen + timedelta(hours=ack_sla_hours) else "open")
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda item: severity_order.get(item["severity"], 3))
    return {
        "generated_at": now.isoformat(), "alerts": alerts,
        "summary": {"active_alert_count": len(alerts), "critical_count": sum(item["severity"] == "critical" for item in alerts),
                    "warning_count": sum(item["severity"] == "warning" for item in alerts),
                    "acknowledgement_overdue_count": sum(item["acknowledgement_sla"] == "overdue" for item in alerts)},
        "policy": {"acknowledgement_sla_hours": ack_sla_hours},
        "disclaimer": "저장된 후보 기준선과 시세 데이터 경고를 합친 운영 점검입니다. 확인·보류 기록은 사용자별 메모만 남기며 원본 경고를 해소하거나 엔진 파이프라인, 주문, 복구 작업을 실행하지 않습니다.",
    }


@router.get("/status")
async def get_kqs_status(user: dict = Depends(current_user)) -> dict:
    """로그인 사용자에게만 엔진의 축약된 읽기 전용 운영 상태를 제공한다."""
    try:
        data = await asyncio.to_thread(_read_bridge_summary)
        return JSONResponse(content=data, headers={"Cache-Control": "no-store"})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/recommendations")
async def get_kqs_recommendations(user: dict = Depends(current_user)) -> dict:
    """엔진이 제공한 추천·근거를 읽기 전용으로 반환한다."""
    try:
        data = await asyncio.to_thread(_read_bridge_recommendations)
        return JSONResponse(content=data, headers={"Cache-Control": "no-store"})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/review-candidates")
async def get_kqs_review_candidates(user: dict = Depends(current_user)) -> dict:
    """로그인 사용자에게 최신 후보와 본인의 검토 상태를 제공한다."""
    try:
        data = await _candidate_queue_for_user(user["_id"])
        return JSONResponse(content=data, headers={"Cache-Control": "no-store"})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/candidate-queue/snapshot")
async def create_kqs_candidate_queue_snapshot(user: dict = Depends(current_user)) -> dict:
    """현재 후보 큐 연구 상태만 일별 스냅샷으로 저장한다."""
    try:
        return await capture_candidate_queue_snapshot()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/candidate-queue/history")
async def get_kqs_candidate_queue_history(days: int = Query(default=30, ge=1, le=365), user: dict = Depends(current_user)) -> dict:
    """후보 큐 변화와 로그인 사용자의 최신 미검토 후보 경고를 반환한다."""
    cutoff = (datetime.now(timezone.utc).astimezone(KST).date() - timedelta(days=days - 1)).isoformat()
    documents = await _candidate_collection(get_db(), "kqs_candidate_queue_snapshots").find(
        {"snapshot_date": {"$gte": cutoff}}, {"_id": 0},
    ).sort("snapshot_date", 1).to_list(days + 1)
    snapshots = [_serialize_candidate_queue_snapshot(document) for document in documents]
    latest = snapshots[-1] if snapshots else None
    review_summary = {"reviewed_count": 0, "deferred_count": 0, "pending": []}
    if latest and latest.get("as_of") and latest.get("candidates"):
        keys = [candidate["symbol_key"] for candidate in latest["candidates"]]
        review_docs = await _candidate_collection(get_db(), "kqs_candidate_reviews").find(
            {"user_id": user["_id"], "as_of": latest["as_of"], "symbol_key": {"$in": keys}}, {"_id": 0, "user_id": 0},
        ).to_list(len(keys))
        review_by_key = {item["symbol_key"]: item for item in review_docs}
        review_summary = {
            "reviewed_count": sum(item.get("status") == "reviewed" for item in review_docs),
            "deferred_count": sum(item.get("status") == "deferred" for item in review_docs),
            "pending": [candidate for candidate in latest["candidates"] if candidate["symbol_key"] not in review_by_key],
        }
    alerts = []
    if latest:
        changes = latest.get("changes", {})
        if changes.get("new"):
            alerts.append({"type": "new_candidate", "count": len(changes["new"]), "items": changes["new"]})
        if review_summary["pending"]:
            alerts.append({"type": "review_pending", "count": len(review_summary["pending"]), "items": review_summary["pending"]})
    return {
        "days": days, "snapshots": snapshots, "latest": latest, "review_summary": review_summary,
        "alerts": alerts,
        "disclaimer": "후보 큐의 연구용 일별 변화와 사용자별 검토 미완료 현황입니다. 자동 주문·후보 확정·포트폴리오 변경은 수행하지 않습니다.",
    }


@router.get("/candidate-queue/review-workflow")
async def get_kqs_candidate_review_workflow(user: dict = Depends(current_user)) -> dict:
    """Return the user's review-SLA queue for the current publication in native mode."""
    if _request_source() == "snapshot":
        try:
            queue = await asyncio.to_thread(_read_engine_review_candidates)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        snapshot = {**queue, "snapshot_date": _utc(datetime.fromisoformat(queue["generated_at"])).astimezone(KST).date().isoformat(),
                    "opened_at": datetime.fromisoformat(queue["generated_at"]),
                    "captured_at": datetime.fromisoformat(queue["generated_at"])}
    else:
        snapshot = await _candidate_collection(get_db(), "kqs_candidate_queue_snapshots").find_one({}, {"_id": 0}, sort=[("snapshot_date", -1)])
    if not snapshot:
        return {
            "available": False, "items": [],
            "summary": {"total_count": 0, "reviewed_count": 0, "pending_count": 0,
                        "high_priority_pending_count": 0, "due_soon_count": 0, "overdue_count": 0},
            "disclaimer": "후보 스냅샷이 생성되면 검토 우선순위와 기한이 표시됩니다. 이는 매매 지시·주문·승인이 아닙니다.",
        }
    keys = [candidate.get("symbol_key") for candidate in snapshot.get("candidates", []) if candidate.get("symbol_key")]
    reviews = []
    if snapshot.get("as_of") and keys:
        reviews = await _candidate_collection(get_db(), "kqs_candidate_reviews").find(
            {"user_id": user["_id"], "as_of": snapshot["as_of"], "symbol_key": {"$in": keys},
             **({"publication_snapshot_id": snapshot["snapshot_id"]} if snapshot.get("source") == "snapshot" else {})},
            {"_id": 0, "user_id": 0},
        ).to_list(len(keys))
    workflow = _build_candidate_review_workflow(snapshot, reviews)
    return {
        "available": True, "snapshot_date": snapshot.get("snapshot_date"), "as_of": snapshot.get("as_of"),
        "source": snapshot.get("source", "bridge"), "snapshot_id": snapshot.get("snapshot_id"),
        **workflow,
        "disclaimer": "후보 검토 누락을 줄이기 위한 업무 우선순위와 기한입니다. 자동 주문·후보 확정·매매 승인·포트폴리오 변경은 수행하지 않습니다.",
    }


@router.get("/candidate-queue/review-report")
async def get_kqs_candidate_review_report(days: int = Query(default=7, ge=1, le=365), user: dict = Depends(current_user)) -> dict:
    """Review-operations report; it only reads snapshots and personal review records."""
    return await build_candidate_review_report(user["_id"], days)


@router.get("/candidate-queue/review-report/export")
async def export_kqs_candidate_review_report(days: int = Query(default=7, ge=1, le=365), format: str = Query(default="csv"), user: dict = Depends(current_user)) -> Response:
    report = await build_candidate_review_report(user["_id"], days)
    stamp = datetime.now(timezone.utc).astimezone(KST).date().isoformat()
    if format == "json":
        return Response(content=json.dumps(report, ensure_ascii=False, default=str, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="kqs-candidate-review-{stamp}.json"'})
    if format != "csv":
        raise HTTPException(status_code=422, detail="format은 csv 또는 json 중 하나여야 합니다.")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["section", "date", "field", "value"])
    for key, value in report["summary"].items():
        writer.writerow(["summary", report["generated_at"], key, value])
    for item in report["daily"]:
        for key, value in item.items():
            writer.writerow(["daily", item.get("snapshot_date", ""), key, value])
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="kqs-candidate-review-{stamp}.csv"'})


@router.get("/candidate-queue/selection-quality")
async def get_kqs_candidate_selection_quality(days: int = Query(default=30, ge=2, le=365), user: dict = Depends(current_user)) -> dict:
    """Selection continuity and review coverage, intentionally not a return forecast."""
    return await build_candidate_selection_quality(user["_id"], days)


@router.get("/candidate-queue/outcomes")
async def get_kqs_candidate_outcomes(days: int = Query(default=365, ge=1, le=365), user: dict = Depends(current_user)) -> dict:
    """Observed 1/5/20-session candidate outcomes from locally verified daily bars."""
    return await build_candidate_outcome_report(days)


@router.get("/candidate-queue/performance-decomposition")
async def get_kqs_candidate_performance_decomposition(days: int = Query(default=365, ge=1, le=365), user: dict = Depends(current_user)) -> dict:
    """Candidate-group outcome decomposition with explicit insufficient-sample gates."""
    return await build_candidate_performance_decomposition(days)


@router.get("/operations-alerts")
async def get_kqs_operations_alerts(user: dict = Depends(current_user)) -> dict:
    """Read-only operational attention list for queue freshness, review SLA, and market data."""
    return await build_kqs_operations_alerts(user["_id"])


@router.post("/operations-alerts/{fingerprint}/acknowledgement")
async def save_kqs_operations_alert_acknowledgement(fingerprint: str, payload: OperationsAlertAcknowledgementInput, user: dict = Depends(current_user)) -> dict:
    """Save a personal acknowledgement without mutating the underlying global condition."""
    current = await build_kqs_operations_alerts(user["_id"])
    if fingerprint not in {item["fingerprint"] for item in current["alerts"]}:
        raise HTTPException(status_code=404, detail="현재 활성 운영 경고를 찾지 못했습니다.")
    now = datetime.now(timezone.utc)
    document = {"user_id": user["_id"], "fingerprint": fingerprint, "status": payload.status,
                "note": payload.note.strip(), "updated_at": now}
    await get_db().kqs_operations_alert_acknowledgements.update_one(
        {"user_id": user["_id"], "fingerprint": fingerprint}, {"$set": document, "$setOnInsert": {"created_at": now}}, upsert=True,
    )
    return {"acknowledgement": {key: value for key, value in document.items() if key != "user_id"} | {"updated_at": now.isoformat()},
            "disclaimer": "사용자별 확인·보류 기록만 저장했습니다. 원본 데이터 경고·엔진 파이프라인·주문 상태는 변경하지 않습니다."}


@router.get("/review-candidates/{symbol}/packet")
async def get_kqs_candidate_review_packet(symbol: str, user: dict = Depends(current_user)) -> dict:
    """최신 후보의 검토 패킷과 해당 사용자의 기록을 반환한다."""
    try:
        packet = await asyncio.to_thread(_read_engine_review_packet, symbol)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not packet.get("available"):
        raise HTTPException(status_code=503, detail="외부 엔진을 확인하지 못했습니다.")
    if not packet.get("found"):
        raise HTTPException(status_code=404, detail="최신 검토 큐에서 해당 후보를 찾지 못했습니다.")
    review = await _candidate_collection(get_db(), "kqs_candidate_reviews").find_one(
        {"user_id": user["_id"], "as_of": packet["as_of"], "symbol_key": packet["candidate"]["symbol_key"],
         **({"publication_snapshot_id": packet["snapshot_id"]} if packet.get("source") == "snapshot" else {})}, {"_id": 0, "user_id": 0},
    )
    packet["review"] = _serialize_candidate_review(review) if review else None
    return JSONResponse(content=packet, headers={"Cache-Control": "no-store"})


@router.post("/review-candidates/{symbol}/review")
async def save_kqs_candidate_review(symbol: str, payload: CandidateReviewInput, user: dict = Depends(current_user)) -> dict:
    """현재 검토 큐 후보에 대한 사용자 메모만 저장한다. 엔진에는 쓰지 않는다."""
    try:
        packet = await asyncio.to_thread(_read_engine_review_packet, symbol)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not packet.get("available"):
        raise HTTPException(status_code=503, detail="외부 엔진을 확인하지 못해 검토 기록을 저장하지 않았습니다.")
    if not packet.get("found"):
        raise HTTPException(status_code=404, detail="최신 검토 큐에서 해당 후보를 찾지 못했습니다.")
    if payload.as_of != packet.get("as_of"):
        raise HTTPException(status_code=409, detail="후보 기준일이 최신 엔진 기준일과 달라 새로 고침 후 다시 검토하세요.")
    candidate = packet["candidate"]
    symbol_key = candidate["symbol_key"]
    now = datetime.now(timezone.utc)
    document = {
        "user_id": user["_id"], "as_of": payload.as_of, "symbol_key": symbol_key,
        "symbol": candidate["symbol"], "name": candidate["name"], "status": payload.status,
        "note": payload.note.strip(), "updated_at": now,
        "source": packet.get("source", "bridge"), "publication_snapshot_id": packet.get("snapshot_id"),
        "signal_id": candidate.get("signal_id"),
    }
    await _candidate_collection(get_db(), "kqs_candidate_reviews").update_one(
        {"user_id": user["_id"], "as_of": payload.as_of, "symbol_key": symbol_key},
        {"$set": document, "$setOnInsert": {"created_at": now}}, upsert=True,
    )
    review = _serialize_candidate_review(document)
    # 매번 append-only로 남겨, 같은 후보의 상태·메모가 바뀌어도 당시 근거를 잃지 않는다.
    snapshot_packet = {**packet, "review": review}
    await _candidate_collection(get_db(), "kqs_candidate_review_snapshots").insert_one({
        "user_id": user["_id"], "as_of": payload.as_of, "symbol_key": symbol_key,
        "symbol": candidate["symbol"], "name": candidate["name"], "status": payload.status,
        "note": document["note"], "captured_at": now, "packet": snapshot_packet,
        "source": document["source"], "publication_snapshot_id": document["publication_snapshot_id"],
    })
    return {"review": review, "snapshot_captured_at": now.isoformat(), "disclaimer": "검토 기록과 당시 검토 패킷을 저장했습니다. 엔진 후보·주문·모의 체결·개인 포트폴리오는 변경하지 않습니다."}


KQS_DECISION_TYPES = ("buy", "hold", "exclude", "sell", "review_again")
_KQS_TRADE_DECISIONS = {"buy", "sell"}


class KqsDecisionInput(ManualRecordInput):
    """엔진 추천 하나에 대한 사용자의 개인 판단 기록. 자동 체결을 절대 만들지 않는다."""
    signal_id: str = Field(min_length=1, max_length=160)
    decision: Literal["buy", "hold", "exclude", "sell", "review_again"]
    quantity: float | None = Field(default=None, gt=0)
    fill_price: float | None = Field(default=None, gt=0)
    fee_krw: float = Field(default=0.0, ge=0)
    tax_krw: float = Field(default=0.0, ge=0)
    memo: str = Field(default="", max_length=1000)
    evidence_tags: list[str] = Field(default_factory=list, max_length=20)


def _clean_evidence_tags(values: list[str]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for tag in values[:20]:
        cleaned = tag.strip()[:40]
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            tags.append(cleaned)
    return tags


def _serialize_kqs_decision(document: dict) -> dict:
    return {
        "id": str(document["_id"]), "signal_id": document["signal_id"],
        "publication_snapshot_id": document.get("publication_snapshot_id"),
        "symbol": document["symbol"], "symbol_key": document["symbol_key"],
        "name": document.get("name", ""), "market": document.get("market", ""),
        "decision": document["decision"], "quantity": document.get("quantity"), "fill_price": document.get("fill_price"),
        "fee_krw": document.get("fee_krw", 0.0), "tax_krw": document.get("tax_krw", 0.0),
        "notional_krw": document.get("notional_krw"), "memo": document.get("memo", ""),
        "evidence_tags": document.get("evidence_tags", []),
        "data_as_of": document.get("data_as_of"),
        "record_origin": document.get("record_origin", "recommendation"),
        "trade_date": document.get("trade_date"),
        "trade_at": _utc(document["trade_at"]).isoformat() if document.get("trade_at") else None,
        "trade_time_source": document.get("trade_time_source", "legacy_unspecified"),
        "recorded_at": _utc(document.get("created_at") or document["decided_at"]).isoformat() if document.get("created_at") or document.get("decided_at") else None,
        "request_id": document.get("request_id"),
        "origin_buy_decision_ids": [str(item) for item in document.get("origin_buy_decision_ids", [])],
        "decided_at": _utc(document["decided_at"]).isoformat() if document.get("decided_at") else None,
    }


_QTY_EPSILON = 1e-8


def _is_position_exit(document: dict) -> bool:
    """EINT-008: 추천 판단이 아니라 보유 lot을 청산한 거래 기록인지 여부."""
    return document.get("record_origin") == "position_exit"


def _trade_day(document: dict) -> str | None:
    """거래일(KST). 사용자가 입력한 ``trade_date``가 있으면 우선하고, 없으면 기록 시각의 KST 날짜다."""
    if document.get("trade_date"):
        return str(document["trade_date"])[:10]
    decided = document.get("decided_at")
    if not decided:
        return None
    if decided.tzinfo is None:
        decided = decided.replace(tzinfo=timezone.utc)
    return decided.astimezone(KST).date().isoformat()


def _build_kqs_lots(trades: list[dict]) -> dict:
    """한 사용자의 매수·매도 기록을 종목별 선입선출(FIFO) lot으로 배분한다.

    매수 한 건이 lot 하나이며, 매도는 가장 오래된 열린 lot부터 수량 단위로 소진한다(부분 매도 포함).
    원장 기록은 수정하지 않고 매번 기록 순서대로 다시 계산한다. 보유 수량을 넘는 매도(검증 도입 전
    과거 기록)는 초과분을 ``oversold``로 드러내고 lot에 배분하지 않는다.
    """
    lots: dict[str, list[dict]] = {}
    oversold: list[dict] = []
    for row in trades:
        quantity, price = float(row.get("quantity") or 0), float(row.get("fill_price") or 0)
        if row.get("decision") not in _KQS_TRADE_DECISIONS or quantity <= 0 or price <= 0:
            continue
        queue = lots.setdefault(row["symbol_key"], [])
        if row["decision"] == "buy":
            queue.append({"buy": row, "quantity": quantity, "remaining": quantity, "allocations": []})
            continue
        left = quantity
        for lot in queue:
            if left <= _QTY_EPSILON:
                break
            if lot["remaining"] <= _QTY_EPSILON:
                continue
            take = min(lot["remaining"], left)
            lot["remaining"] = 0.0 if lot["remaining"] - take <= _QTY_EPSILON else lot["remaining"] - take
            left -= take
            lot["allocations"].append({"sell": row, "quantity": take})
        if left > _QTY_EPSILON:
            oversold.append({"sell_decision_id": row["_id"], "symbol_key": row["symbol_key"],
                             "unmatched_quantity": round(left, 8)})
    return {"lots": lots, "oversold": oversold}


async def _user_lot_book(user_id) -> dict:
    rows = await get_db().kqs_paper_decisions.find(
        {"user_id": user_id, "decision": {"$in": ["buy", "sell"]}},
    ).sort([("decided_at", 1), ("recorded_order_us", 1), ("_id", 1)]).to_list(10_001)
    if len(rows) > 10_000:
        raise HTTPException(409, detail="원장 조회 한도를 초과해 보유 수량을 확정할 수 없습니다.")
    return _build_kqs_lots(rows)


def _held_quantity(book: dict, symbol_key: str) -> float:
    return sum(lot["remaining"] for lot in book["lots"].get(symbol_key, []))


async def _reject_if_oversold(user_id, sell_id) -> None:
    """동시 기록 경합으로 보유 수량을 넘게 된 매도는 응답 전에 되돌린다(확정 전 쓰기 취소)."""
    book = await _user_lot_book(user_id)
    if any(item["sell_decision_id"] == sell_id for item in book["oversold"]):
        await get_db().kqs_paper_decisions.delete_one({"_id": sell_id, "user_id": user_id})
        raise HTTPException(status_code=409, detail="동시에 기록된 다른 매도와 합쳐 보유 수량을 넘었습니다. 보유 현황을 새로 불러와 다시 기록하세요.")



async def _validate_manual_trade_chronology(user_id, symbol_key, fields: dict, now: datetime) -> None:
    if await get_db().kqs_paper_decisions.count_documents({"user_id": user_id, "decision": {"$in": ["buy", "sell"]}}) >= 10_000:
        raise HTTPException(409, detail="개인 원장 처리 한도에 도달해 새 거래를 기록하지 않았습니다.")
    rows = await get_db().kqs_paper_decisions.find(
        {"user_id": user_id, "symbol_key": symbol_key, "decision": {"$in": ["buy", "sell"]}},
    ).sort([("decided_at", 1), ("recorded_order_us", 1), ("_id", 1)]).to_list(10_001)
    if len(rows) > 10_000:
        raise HTTPException(409, detail="원장 조회 한도를 초과해 새 거래를 기록하지 않았습니다.")
    if _build_kqs_lots(rows)["oversold"]:
        raise HTTPException(409, detail="기존 원장에 수량 불일치가 있어 새 거래를 기록하지 않았습니다.")
    for row in rows:
        day = _trade_day(row)
        if day and fields["trade_date"] < day:
            raise HTTPException(422, detail="이 종목의 마지막 거래일 이전으로 소급 기록할 수 없습니다.")
        if day == fields["trade_date"] and fields.get("trade_at") and row.get("trade_at"):
            if fields["trade_at"] < _utc(row["trade_at"]):
                raise HTTPException(422, detail="이 종목의 마지막 거래 시각 이전으로 소급 기록할 수 없습니다.")
        if row.get("decided_at") and _utc(row["decided_at"]) > now:
            raise HTTPException(409, detail="기록 시계가 기존 원장보다 이전입니다. 시각을 확인하세요.")


@router.post("/decisions")
async def record_kqs_decision(payload: KqsDecisionInput, user: dict = Depends(current_user)) -> dict:
    """엔진 추천 하나에 대한 사용자의 판단(모의매수/관망/제외/모의매도/재검토)을 원장에 기록한다.

    이 원장은 엔진의 자체 모의 체결(paper CSV)이나 사이트의 일반 주문·paper_fills
    파이프라인과 완전히 분리된 개인 의사결정 데이터다. 자동 체결·스케줄러 체결을
    절대 만들지 않으며, 매수·매도의 수량·체결가·수수료·세금은 사용자가 직접 입력한
    값을 그대로 기록한다(시세 조회나 위험 한도 계산을 하지 않는다). 결정 시점의 추천
    근거 전체를 함께 저장해, 나중에 "무엇을 왜 사거나 사지 않았는지"를 그대로 복원할
    수 있게 한다.
    """
    previous = await replay_record(get_db(), payload, user["_id"], "recommendation") if payload.request_id else None
    if previous:
        return _serialize_kqs_decision(previous)
    try:
        recommendations = await asyncio.to_thread(_read_bridge_recommendations)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    signal = next((item for item in recommendations.get("signals", []) if item.get("signal_id") == payload.signal_id), None)
    if not signal:
        raise HTTPException(status_code=404, detail="현재 유효한 엔진 추천에서 해당 signal_id를 찾지 못했습니다.")

    is_trade = payload.decision in _KQS_TRADE_DECISIONS
    if is_trade and (not payload.quantity or not payload.fill_price):
        raise HTTPException(status_code=422, detail="모의 매수·매도 기록에는 수량과 체결가가 모두 필요합니다.")

    async with manual_write_lock(get_db(), user["_id"]):
        previous = await replay_record(get_db(), payload, user["_id"], "recommendation") if payload.request_id else None
        if previous:
            return _serialize_kqs_decision(previous)
        if payload.decision == "sell":
            held = _held_quantity(await _user_lot_book(user["_id"]), _symbol_key(signal["symbol"]))
            if payload.quantity > held + _QTY_EPSILON:
                raise HTTPException(status_code=409, detail=f"보유 수량({held:g})을 넘는 모의 매도는 기록할 수 없습니다.")

        tags = _clean_evidence_tags(payload.evidence_tags)
        raw_now = datetime.now(timezone.utc)
        now = raw_now.replace(microsecond=raw_now.microsecond // 1000 * 1000)
        fields = trade_fields(payload, now) if is_trade else {}
        if not is_trade and (payload.trade_at is not None or payload.trade_date is not None):
            raise HTTPException(422, detail="거래 시각/거래일은 매수·매도 기록에만 입력하세요.")
        if is_trade:
            await _validate_manual_trade_chronology(user["_id"], _symbol_key(signal["symbol"]), fields, now)
            generated = recommendations.get("generated_at")
            if generated and (fields["trade_date"] < _utc(datetime.fromisoformat(generated.replace("Z", "+00:00"))).astimezone(KST).date().isoformat()
                              or (fields.get("trade_at") and fields["trade_at"] < _utc(datetime.fromisoformat(generated.replace("Z", "+00:00"))))):
                raise HTTPException(422, detail="추천 생성 이전의 거래는 이 추천 판단으로 기록할 수 없습니다.")
        document = {
            "user_id": user["_id"], "signal_id": payload.signal_id, "record_origin": "recommendation",
            "symbol": signal["symbol"], "symbol_key": _symbol_key(signal["symbol"]), "name": signal.get("name", ""),
            "market": signal.get("market", ""), "decision": payload.decision,
            "quantity": payload.quantity if is_trade else None,
            "fill_price": payload.fill_price if is_trade else None,
            "fee_krw": round(payload.fee_krw, 2) if is_trade else 0.0,
            "tax_krw": round(payload.tax_krw, 2) if is_trade else 0.0,
            "notional_krw": round(payload.quantity * payload.fill_price, 2) if is_trade else None,
            "memo": payload.memo.strip(), "evidence_tags": tags,
            # 76단계(학습용 데이터셋) 대비: 결정 시점의 추천 근거·엔진 메타데이터를 불변 스냅샷으로 남긴다.
            "recommendation_snapshot": signal, "engine_snapshot": recommendations.get("engine"),
            "publication_snapshot_id": recommendations.get("snapshot_id"),
            "recommendation_generated_at": recommendations.get("generated_at"),
            "recommendation_expires_at": recommendations.get("expires_at"),
            "data_as_of": (recommendations.get("engine") or {}).get("data_as_of"),
            **request_identity(payload, user["_id"], "recommendation"), **fields,
            "recorded_order_us": int(raw_now.timestamp() * 1_000_000),
            "decided_at": now, "created_at": now,
        }
        result = await get_db().kqs_paper_decisions.insert_one(document)
        document["_id"] = result.inserted_id
        await audit_without_rollback(append_execution_audit, user["_id"], "kqs_decision_recorded", "kqs_paper_decision", {
            "decision_id": str(document["_id"]), "signal_id": payload.signal_id, "decision": payload.decision,
            "live_trading_authorized": False,
        })
        return _serialize_kqs_decision(document)


@router.get("/decisions")
async def list_kqs_decisions(
    symbol_key: str | None = Query(default=None), limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(current_user),
) -> dict:
    """이 사용자의 개인 판단 원장을 최신순으로 반환한다. 자동 모의체결 결과가 아니다."""
    query: dict = {"user_id": user["_id"]}
    if symbol_key:
        query["symbol_key"] = symbol_key.strip().upper()
    rows = await get_db().kqs_paper_decisions.find(query).sort("decided_at", -1).to_list(limit)
    return {"items": [_serialize_kqs_decision(row) for row in rows],
            "disclaimer": "자동 모의체결이 아닌, 사용자가 직접 기록한 개인 판단 원장입니다."}


def _recommendation_expired(buy: dict, now: datetime) -> bool | None:
    raw = buy.get("recommendation_expires_at")
    if not raw:
        return None
    try:
        expires = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= now


@router.get("/decisions/positions")
async def list_kqs_decision_positions(user: dict = Depends(current_user)) -> dict:
    """매수·매도 기록을 FIFO lot으로 배분한 개인 원장 보유 현황이다. 시세 평가·주문은 포함하지 않는다.

    EINT-008: 추천이 만료된 뒤에도 보유 lot과 원래 추천(signal_id)·매수일·잔여 수량을 보여 주어,
    사용자가 추천 화면과 무관하게 매도·부분매도를 기록할 수 있게 한다.
    """
    book = await _user_lot_book(user["_id"])
    now = datetime.now(timezone.utc)
    items = []
    for symbol_key, lots in book["lots"].items():
        open_lots = [lot for lot in lots if lot["remaining"] > _QTY_EPSILON]
        if not open_lots:
            continue
        quantity = sum(lot["remaining"] for lot in open_lots)
        cost_basis = sum(lot["remaining"] * float(lot["buy"]["fill_price"]) for lot in open_lots)
        first = open_lots[0]["buy"]
        lot_rows = [{
            "buy_decision_id": str(lot["buy"]["_id"]), "signal_id": lot["buy"].get("signal_id"),
            "bought_on": _trade_day(lot["buy"]), "data_as_of": lot["buy"].get("data_as_of"),
            "fill_price": lot["buy"].get("fill_price"), "quantity": lot["quantity"],
            "remaining_quantity": round(lot["remaining"], 8), "sold_quantity": round(lot["quantity"] - lot["remaining"], 8),
            "recommendation_expires_at": lot["buy"].get("recommendation_expires_at"),
            "recommendation_expired": _recommendation_expired(lot["buy"], now),
        } for lot in open_lots]
        items.append({
            "symbol_key": symbol_key, "symbol": first["symbol"], "name": first.get("name", ""), "market": first.get("market", ""),
            "quantity": round(quantity, 8), "average_cost_krw": round(cost_basis / quantity, 4) if quantity else 0.0,
            "cost_basis_krw": round(cost_basis, 2), "lot_count": len(open_lots),
            "recommendation_expired": any(row["recommendation_expired"] for row in lot_rows), "lots": lot_rows,
        })
    issues = [{**item, "sell_decision_id": str(item["sell_decision_id"])} for item in book["oversold"]]
    return {"items": items, "data_issues": issues, "lot_policy": "FIFO, 매수 1건 = lot 1개, 부분 매도는 수량 비율로 비용 배분",
            "disclaimer": "매수·매도 기록만으로 계산한 FIFO lot 보유 현황이며 실시간 시세 평가나 주문은 포함하지 않습니다."}


@router.get("/strategy-board")
async def get_kqs_strategy_board(user: dict = Depends(current_user)) -> dict:
    """EINT-017 화면: 3개 아이템의 오늘 추천(아이템별 최대 5개)과 내 보유 종목의 매도 신호.

    추천은 검증된 게시본만 읽고, 매도 신호는 엔진이 게시한 청산 모니터와 이 사용자의 FIFO lot으로
    계산한다. 주문·자동 체결은 없으며 신호는 수동 판단을 돕는 정보다.
    """
    payload, detail = None, None
    try:
        payload = await asyncio.to_thread(read_local_recommendations)
    except RuntimeError as exc:
        detail = str(exc)
    book = await _user_lot_book(user["_id"])
    items = board_items(payload) if payload else board_items({"signals": []})
    # EINT-020: 오늘 추천에 이미 남긴 내 판단(카드에서 바로 기록한 매수·관망·제외 포함).
    signal_ids = [pick["signal_id"] for item in items for pick in item["picks"]]
    decided = await get_db().kqs_paper_decisions.find(
        {"user_id": user["_id"], "signal_id": {"$in": signal_ids}, "record_origin": "recommendation"},
        {"signal_id": 1, "decision": 1, "quantity": 1, "fill_price": 1, "decided_at": 1},
    ).sort("decided_at", 1).to_list(len(signal_ids) * 20 or 1)
    my_decisions: dict[str, list[dict]] = {}
    for row in decided:
        my_decisions.setdefault(row["signal_id"], []).append({
            "decision": row["decision"], "quantity": row.get("quantity"), "fill_price": row.get("fill_price"),
            "decided_at": row["decided_at"].isoformat() if row.get("decided_at") else None})
    response = {
        "available": payload is not None, "detail": detail,
        "items": items, "my_decisions": my_decisions,
        "health": await asyncio.to_thread(pipeline_health),
        "sell_signals": holding_signals(book, payload, _trade_day),
        "disclaimer": "아이템별 최대 5개 추천과 보유 종목 매도 신호는 판단을 돕는 정보입니다. 순서는 각 아이템의 기존 선정 규칙이며 수익률 예측이 아닙니다. 자동 주문·자동 모의체결은 없습니다.",
    }
    if payload:
        response.update(schema_version=payload["schema_version"], as_of=payload["engine"]["data_as_of"],
                        generated_at=payload["generated_at"], expires_at=payload["expires_at"],
                        snapshot_id=payload.get("snapshot_id"), package_version=payload["engine"].get("package_version"))
    return JSONResponse(content=response, headers={"Cache-Control": "no-store"})


@router.get("/pipeline-health")
async def get_kqs_pipeline_health(user: dict = Depends(current_user)) -> dict:
    """EINT-020: 엔진 일일 실행·게시 상태와 사용자에게 보여 줄 경고. 읽기 전용이며 실행·재시도하지 않는다."""
    return JSONResponse(content=await asyncio.to_thread(pipeline_health), headers={"Cache-Control": "no-store"})


@router.get("/recommendation-outcomes")
async def get_kqs_recommendation_outcomes(user: dict = Depends(current_user)) -> dict:
    """EINT-019: 게시된 3개 아이템 추천의 사후 성과(다음 거래일 시가 기준 1·5·10·20거래일). 읽기 전용."""
    return JSONResponse(content=await asyncio.to_thread(read_outcome_report), headers={"Cache-Control": "no-store"})


@router.get("/rebound-shadow")
async def get_kqs_rebound_shadow(user: dict = Depends(current_user)) -> dict:
    """EINT-032: 사전등록 B-1 '반등 확인 후 진입' 전향 관측(볼린저 RSI 신호, 원래 규칙과 짝 비교). 읽기 전용."""
    return JSONResponse(content=await asyncio.to_thread(read_rebound_shadow), headers={"Cache-Control": "no-store"})


class KqsPositionSellInput(ManualRecordInput):
    """보유 lot 청산(전량·부분) 기록. 현재 추천 여부와 무관하며 자동 체결을 만들지 않는다."""
    quantity: float = Field(gt=0)
    fill_price: float = Field(gt=0)
    fee_krw: float = Field(default=0.0, ge=0)
    tax_krw: float = Field(default=0.0, ge=0)
    trade_date: date | None = None
    memo: str = Field(default="", max_length=1000)
    evidence_tags: list[str] = Field(default_factory=list, max_length=20)


@router.post("/decisions/positions/{symbol_key}/sell")
async def record_kqs_position_sell(symbol_key: str, payload: KqsPositionSellInput, user: dict = Depends(current_user)) -> dict:
    """EINT-008: 추천이 만료된 보유 종목의 모의 매도·부분매도를 사용자가 직접 기록한다.

    수량·체결가·비용·거래일은 사용자가 입력한 값을 그대로 쓰고(시세 조회 없음), 보유 수량을 넘거나
    미래·첫 매수일 이전 거래일이면 거부한다. 이 기록은 추천에 대한 판단이 아니라 거래 기록이므로
    학습 데이터셋의 판단 표본에서는 빠지고, 해당 매수 lot의 실현 손익 레이블로만 반영된다.
    """
    key = _symbol_key(symbol_key)
    scope = f"position_exit:{key}"
    previous = await replay_record(get_db(), payload, user["_id"], scope) if payload.request_id else None
    if previous:
        return {"item": _serialize_kqs_decision(previous), "remaining_quantity": previous["remaining_quantity_after"],
                "disclaimer": "사용자가 직접 입력한 모의 매도 기록입니다. 주문·자동 체결은 만들지 않습니다."}
    async with manual_write_lock(get_db(), user["_id"]):
        previous = await replay_record(get_db(), payload, user["_id"], scope) if payload.request_id else None
        if previous:
            return {"item": _serialize_kqs_decision(previous), "remaining_quantity": previous["remaining_quantity_after"],
                    "disclaimer": "사용자가 직접 입력한 모의 매도 기록입니다. 주문·자동 체결은 만들지 않습니다."}
        book = await _user_lot_book(user["_id"])
        open_lots = [lot for lot in book["lots"].get(key, []) if lot["remaining"] > _QTY_EPSILON]
        if not open_lots:
            raise HTTPException(status_code=404, detail="이 종목의 보유 lot이 없습니다.")
        held = sum(lot["remaining"] for lot in open_lots)
        if payload.quantity > held + _QTY_EPSILON:
            raise HTTPException(status_code=409, detail=f"보유 수량({held:g})을 넘는 모의 매도는 기록할 수 없습니다.")
        raw_now = datetime.now(timezone.utc)
        now = raw_now.replace(microsecond=raw_now.microsecond // 1000 * 1000)
        fields = trade_fields(payload, now)
        await _validate_manual_trade_chronology(user["_id"], key, fields, now)
        today = now.astimezone(KST).date()
        trade_date = date.fromisoformat(fields["trade_date"])
        if trade_date > today:
            raise HTTPException(status_code=422, detail="미래 거래일은 기록할 수 없습니다.")
        first_buy_day = min(_trade_day(lot["buy"]) or "" for lot in open_lots)
        if first_buy_day and trade_date.isoformat() < first_buy_day:
            raise HTTPException(status_code=422, detail=f"거래일은 가장 오래된 보유 lot 매수일({first_buy_day}) 이후여야 합니다.")

        # 기록 시점의 FIFO 배분 대상(참고용). 실현 손익은 항상 원장 전체를 다시 배분해 계산한다.
        origin_ids, left = [], payload.quantity
        for lot in open_lots:
            if left <= _QTY_EPSILON:
                break
            origin_ids.append(lot["buy"]["_id"])
            left -= min(lot["remaining"], left)

        first = open_lots[0]["buy"]
        document = {
            "user_id": user["_id"], "signal_id": None, "record_origin": "position_exit",
            "symbol": first["symbol"], "symbol_key": key, "name": first.get("name", ""), "market": first.get("market", ""),
            "decision": "sell", "quantity": payload.quantity, "fill_price": payload.fill_price,
            "fee_krw": round(payload.fee_krw, 2), "tax_krw": round(payload.tax_krw, 2),
            "notional_krw": round(payload.quantity * payload.fill_price, 2),
            "memo": payload.memo.strip(), "evidence_tags": _clean_evidence_tags(payload.evidence_tags),
            "trade_date": trade_date.isoformat(), "origin_buy_decision_ids": origin_ids,
            "recommendation_snapshot": None, "engine_snapshot": None, "publication_snapshot_id": None, "data_as_of": None,
            **request_identity(payload, user["_id"], scope), **fields,
            "recorded_order_us": int(raw_now.timestamp() * 1_000_000),
            "remaining_quantity_after": round(held - payload.quantity, 8),
            "decided_at": now, "created_at": now,
        }
        result = await get_db().kqs_paper_decisions.insert_one(document)
        document["_id"] = result.inserted_id
        await audit_without_rollback(append_execution_audit, user["_id"], "kqs_position_exit_recorded", "kqs_paper_decision", {
            "decision_id": str(document["_id"]), "symbol_key": key, "quantity": payload.quantity,
            "live_trading_authorized": False,
        })
        return {"item": _serialize_kqs_decision(document), "remaining_quantity": round(held - payload.quantity, 8),
                "disclaimer": "사용자가 직접 입력한 모의 매도 기록입니다. 주문·자동 체결은 만들지 않습니다."}


async def _decision_outcome_document(decision: dict, captured_at: datetime) -> dict:
    """판단 시점(``data_as_of``) 종가를 기준으로 이미 확정된 종가만으로 사후 수익률을 계산한다.

    ``_candidate_outcome_document``와 같은 패턴이다: 미래를 예측하지 않고, 아직 관측되지
    않은 구간은 ``observed: False``로 남겨 시간이 지나면서 점진적으로 채워지게 한다.
    """
    ticker = _candidate_market_ticker(decision)
    as_of = str(decision.get("data_as_of") or "")[:10]
    base = {
        "decision_id": decision["_id"], "user_id": decision["user_id"], "signal_id": decision.get("signal_id"),
        "symbol_key": decision.get("symbol_key"), "symbol": decision.get("symbol"), "name": decision.get("name", ""),
        "market": decision.get("market", ""), "ticker": ticker, "decision": decision.get("decision"),
        "data_as_of": as_of, "decided_at": decision.get("decided_at"), "captured_at": captured_at,
    }
    if _is_position_exit(decision):
        return {**base, "status": "trade_record_only", "horizons": {}}
    if not ticker or not as_of:
        return {**base, "status": "unsupported_symbol_mapping", "horizons": {}}
    db = get_db()
    stock_bars = await db.market_ohlcv.find(
        {"ticker": ticker, "interval": "1d", "timestamp": {"$gte": as_of}}, {"_id": 0, "timestamp": 1, "close": 1},
    ).sort("timestamp", 1).to_list(25)
    if not stock_bars or stock_bars[0].get("timestamp") != as_of:
        return {**base, "status": "awaiting_baseline_close", "horizons": {}}
    benchmark_bars = await db.market_ohlcv.find(
        {"ticker": "^KS11", "interval": "1d", "timestamp": {"$gte": as_of}}, {"_id": 0, "timestamp": 1, "close": 1},
    ).sort("timestamp", 1).to_list(25)
    benchmark_by_date = {bar.get("timestamp"): bar.get("close") for bar in benchmark_bars}
    base_close = stock_bars[0].get("close")
    benchmark_base = benchmark_by_date.get(as_of)
    horizons: dict[str, dict] = {}
    for horizon in (1, 5, 20):
        if len(stock_bars) <= horizon:
            horizons[str(horizon)] = {"observed": False, "return_pct": None, "benchmark_return_pct": None,
                                       "excess_return_pct": None, "max_drawdown_pct": None}
            continue
        target = stock_bars[horizon]
        stock_return = _return_pct(base_close, target.get("close"))
        benchmark_return = _return_pct(benchmark_base, benchmark_by_date.get(target.get("timestamp")))
        horizons[str(horizon)] = {
            "observed": stock_return is not None, "target_date": target.get("timestamp"), "return_pct": stock_return,
            "benchmark_return_pct": benchmark_return,
            "excess_return_pct": round(stock_return - benchmark_return, 4) if stock_return is not None and benchmark_return is not None else None,
            "max_drawdown_pct": _max_drawdown_pct(stock_bars[:horizon + 1]),
        }
    return {**base, "status": "observed", "horizons": horizons}


def _trade_cost(document: dict) -> float:
    return float(document.get("fee_krw") or 0) + float(document.get("tax_krw") or 0)


async def _compute_lot_realized(lot: dict) -> dict | None:
    """매수 lot 하나의 실현 손익. 부분 매도는 매도분만 실현하고 비용은 수량 비율로 배분한다(확정 체결가 기준)."""
    if not lot["allocations"]:
        return None
    buy = lot["buy"]
    buy_price, buy_quantity = float(buy["fill_price"]), lot["quantity"]
    closed = sum(item["quantity"] for item in lot["allocations"])
    allocations, gross, cost = [], 0.0, _trade_cost(buy) * closed / buy_quantity
    for item in lot["allocations"]:
        sell, quantity = item["sell"], item["quantity"]
        sell_cost = _trade_cost(sell) * quantity / float(sell["quantity"])
        gross += quantity * (float(sell["fill_price"]) - buy_price)
        cost += sell_cost
        allocations.append({"sell_decision_id": sell["_id"], "quantity": round(quantity, 8),
                            "fill_price": sell.get("fill_price"), "trade_date": _trade_day(sell),
                            "sell_cost_krw": round(sell_cost, 2), "record_origin": sell.get("record_origin", "recommendation")})
    last_sell = lot["allocations"][-1]["sell"]
    buy_day, sell_day = _trade_day(buy), _trade_day(last_sell)
    holding_days = (date.fromisoformat(sell_day) - date.fromisoformat(buy_day)).days if buy_day and sell_day else None
    closed_notional = buy_price * closed
    gross_pnl_krw, total_cost_krw = round(gross, 2), round(cost, 2)
    net_pnl_krw = round(gross_pnl_krw - total_cost_krw, 2)

    ticker = _candidate_market_ticker(buy)
    mdd_during_holding_pct = None
    start_day, end_day = str(buy.get("data_as_of") or buy_day or ""), str(last_sell.get("data_as_of") or sell_day or "")
    if ticker and start_day and end_day:
        bars = await get_db().market_ohlcv.find(
            {"ticker": ticker, "interval": "1d", "timestamp": {"$gte": start_day, "$lte": end_day}}, {"_id": 0, "close": 1},
        ).sort("timestamp", 1).to_list(400)
        mdd_during_holding_pct = _max_drawdown_pct(bars)

    return {
        "status": "closed" if lot["remaining"] <= _QTY_EPSILON else "partial",
        "lot_quantity": buy_quantity, "closed_quantity": round(closed, 8), "remaining_quantity": round(lot["remaining"], 8),
        "matched_sell_decision_id": last_sell["_id"], "allocations": allocations, "holding_days": holding_days,
        "gross_pnl_krw": gross_pnl_krw, "total_cost_krw": total_cost_krw, "net_pnl_krw": net_pnl_krw,
        "net_return_pct": round(net_pnl_krw / closed_notional * 100, 4) if closed_notional else None,
        "mdd_during_holding_pct": mdd_during_holding_pct,
        "exit_reason": last_sell.get("memo", ""), "exit_evidence_tags": last_sell.get("evidence_tags", []),
    }


async def capture_kqs_decision_outcomes() -> dict:
    """개인 판단 원장의 사후 수익률과 매수 lot별 실현 손익을 저장한다. 시세를 예측하거나 주문을 만들지 않는다.

    76단계(학습용 데이터셋)의 정답값 절반이다 — 나머지 절반(결정 시점 피처)은 기록
    시점에 ``kqs_paper_decisions``에 이미 스냅샷으로 저장돼 있다. 모든 피처는 결정
    시점 기준으로 고정되고, 여기서 계산하는 수익률만 시간이 지나며 점진적으로 채워진다.
    EINT-008부터 실현 손익은 수량 단위 FIFO lot 배분(부분 매도 포함)으로 계산한다.
    """
    db = get_db()
    decisions = await db.kqs_paper_decisions.find({}).sort([("decided_at", 1), ("_id", 1)]).to_list(50_000)
    now = datetime.now(timezone.utc)

    stored = 0
    for decision in decisions:
        document = await _decision_outcome_document(decision, now)
        await db.kqs_decision_outcomes.update_one(
            {"decision_id": document["decision_id"]}, {"$set": document}, upsert=True,
        )
        stored += 1

    by_user: dict = {}
    for decision in decisions:
        by_user.setdefault(decision["user_id"], []).append(decision)
    matched = oversold = 0
    for trades in by_user.values():
        book = _build_kqs_lots(trades)
        oversold += len(book["oversold"])
        for lots in book["lots"].values():
            for lot in lots:
                realized = await _compute_lot_realized(lot)
                await db.kqs_decision_outcomes.update_one({"decision_id": lot["buy"]["_id"]}, {"$set": {"realized": realized}})
                matched += realized is not None

    return {"stored_count": stored, "matched_count": matched, "oversold_sell_count": oversold, "captured_at": now.isoformat()}


def _serialize_realized(realized: dict | None) -> dict | None:
    if not realized:
        return None
    return {**realized, "matched_sell_decision_id": str(realized["matched_sell_decision_id"]),
            "allocations": [{**item, "sell_decision_id": str(item["sell_decision_id"])} for item in realized.get("allocations", [])]}


def _serialize_kqs_decision_outcome(document: dict) -> dict:
    realized = document.get("realized")
    return {
        "decision_id": str(document["decision_id"]), "signal_id": document.get("signal_id"),
        "symbol": document.get("symbol"), "symbol_key": document.get("symbol_key"), "name": document.get("name", ""),
        "market": document.get("market", ""), "decision": document.get("decision"), "status": document["status"],
        "data_as_of": document.get("data_as_of"),
        "decided_at": document["decided_at"].isoformat() if document.get("decided_at") else None,
        "horizons": document.get("horizons", {}),
        "record_origin": document.get("record_origin", "recommendation"),
        "realized": _serialize_realized(realized),
    }


@router.get("/decisions/outcomes")
async def list_kqs_decision_outcomes(user: dict = Depends(current_user)) -> dict:
    """이 사용자의 판단별 사후 1·5·20일 수익률과 매수→매도 실현 손익을 반환한다.

    이미 확정된 종가로만 계산하며, 아직 관측되지 않은 구간은 ``observed: false``로 남는다.
    """
    rows = await get_db().kqs_decision_outcomes.find({"user_id": user["_id"]}).sort("decided_at", -1).to_list(1000)
    return {"items": [_serialize_kqs_decision_outcome(row) for row in rows],
            "disclaimer": "이미 확정된 종가로 계산한 사후 관측치입니다. 향후 수익률을 예측하거나 매매를 추천하지 않습니다."}


_HOLDING_BUCKETS = ((0, 5, "5일 미만"), (5, 20, "5~20일"), (20, None, "20일 이상"))


def _holding_bucket(days: int | None) -> str:
    if days is None:
        return "알 수 없음"
    for low, high, label in _HOLDING_BUCKETS:
        if days >= low and (high is None or days < high):
            return label
    return "알 수 없음"


def _avg(values: list) -> float | None:
    cleaned = [float(value) for value in values if value is not None]
    return round(sum(cleaned) / len(cleaned), 4) if cleaned else None


def _group_realized_trades(trades: list[dict], key_fn) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for trade in trades:
        key = key_fn(trade) or "미분류"
        groups.setdefault(key, []).append(trade)
    rows = []
    for key, items in groups.items():
        pnl_values = [item["net_pnl_krw"] for item in items if item["net_pnl_krw"] is not None]
        wins = sum(1 for value in pnl_values if value > 0)
        rows.append({
            "key": key, "trade_count": len(items), "net_pnl_krw": round(sum(pnl_values), 2) if pnl_values else 0.0,
            "avg_return_pct": _avg([item["net_return_pct"] for item in items]),
            "win_rate_pct": round(wins / len(items) * 100, 2) if items else None,
        })
    return sorted(rows, key=lambda row: row["key"])


async def build_kqs_decision_performance_report(user_id) -> dict:
    """개인 판단 원장의 실현·관측 데이터만으로 계산한 운용 성과 리포트.

    엔진의 자체 모의원장과 합산하지 않고, 매매를 추천하거나 자동화하지 않는다. 표본 기간이
    1년 미만이면 연환산 성과를 확정치로 보여주지 않고 관찰 단계로 표시한다.
    """
    db = get_db()
    decisions = await db.kqs_paper_decisions.find({"user_id": user_id}).sort("decided_at", 1).to_list(50_000)
    outcomes = await db.kqs_decision_outcomes.find({"user_id": user_id}).to_list(50_000)
    outcomes_by_id = {row["decision_id"]: row for row in outcomes}
    trades_and_judgments = decisions
    # EINT-008: 보유 lot 청산 기록은 추천에 대한 판단이 아니므로 판단 전환율·사후 비교에서 뺀다.
    decisions = [row for row in trades_and_judgments if not _is_position_exit(row)]

    total_decisions = len(decisions)
    counts = {kind: 0 for kind in KQS_DECISION_TYPES}
    for decision in decisions:
        if decision.get("decision") in counts:
            counts[decision["decision"]] += 1
    conversion = {
        "total_decisions": total_decisions, **{f"{kind}_count": count for kind, count in counts.items()},
        "position_exit_count": len(trades_and_judgments) - total_decisions,
        "buy_conversion_rate_pct": round(counts["buy"] / total_decisions * 100, 2) if total_decisions else None,
    }

    comparison: dict[str, dict] = {}
    for kind in KQS_DECISION_TYPES:
        rows = [outcomes_by_id[d["_id"]] for d in decisions if d.get("decision") == kind and d["_id"] in outcomes_by_id]
        horizon_avgs = {horizon: _avg([row.get("horizons", {}).get(horizon, {}).get("return_pct") for row in rows])
                        for horizon in ("1", "5", "20")}
        comparison[kind] = {
            "count": len(rows), "avg_return_pct": horizon_avgs,
            "avg_excess_return_pct_20d": _avg([row.get("horizons", {}).get("20", {}).get("excess_return_pct") for row in rows]),
        }

    realized_trades = []
    for decision in decisions:
        if decision.get("decision") != "buy":
            continue
        realized = (outcomes_by_id.get(decision["_id"]) or {}).get("realized")
        if not realized:
            continue
        realized_trades.append({
            "market": decision.get("market", ""),
            "signal_type": (decision.get("recommendation_snapshot") or {}).get("signal_type", ""),
            "holding_days": realized.get("holding_days"), "net_pnl_krw": realized.get("net_pnl_krw"),
            "net_return_pct": realized.get("net_return_pct"), "total_cost_krw": realized.get("total_cost_krw"),
            "decided_at": decision.get("decided_at"), "lot_status": realized.get("status", "closed"),
            # 부분 매도 lot은 매도된 수량의 원가만 실현 기준 원금으로 쓴다.
            "buy_notional_krw": float(decision.get("fill_price") or 0) * float(realized.get("closed_quantity") or decision.get("quantity") or 0),
        })

    by_market = _group_realized_trades(realized_trades, lambda t: t["market"])
    by_signal_type = _group_realized_trades(realized_trades, lambda t: t["signal_type"])
    by_holding_period = _group_realized_trades(realized_trades, lambda t: _holding_bucket(t["holding_days"]))

    # 실현 손익만으로 만든 단순 자본곡선이다(미청산 포지션의 평가손익은 포함하지 않는다).
    ordered_trades = sorted(realized_trades, key=lambda t: t["decided_at"] or datetime.min.replace(tzinfo=timezone.utc))
    cumulative, peak, mdd_krw = 0.0, 0.0, 0.0
    points = []
    for trade in ordered_trades:
        cumulative += trade["net_pnl_krw"] or 0.0
        points.append({"decided_at": trade["decided_at"].isoformat() if trade["decided_at"] else None,
                        "cumulative_net_pnl_krw": round(cumulative, 2)})
        peak = max(peak, cumulative)
        mdd_krw = max(mdd_krw, peak - cumulative)

    total_cost_krw = round(sum(t["total_cost_krw"] or 0 for t in realized_trades), 2)
    turnover_krw = round(sum(abs(float(d.get("notional_krw") or 0)) for d in trades_and_judgments
                             if d.get("decision") in {"buy", "sell"}), 2)

    decided_ats = [d.get("decided_at") for d in trades_and_judgments if d.get("decided_at")]
    data_span_days = (max(decided_ats) - min(decided_ats)).days if len(decided_ats) >= 2 else 0
    observation_stage = data_span_days < 365
    cagr_pct = None
    if not observation_stage and realized_trades:
        total_net_pnl = sum(t["net_pnl_krw"] or 0 for t in realized_trades)
        base_capital = _avg([t["buy_notional_krw"] for t in realized_trades]) or 0
        if base_capital > 0 and (1 + total_net_pnl / base_capital) > 0:
            cagr_pct = round(((1 + total_net_pnl / base_capital) ** (365 / data_span_days) - 1) * 100, 2)

    return {
        "conversion": conversion, "post_decision_comparison": comparison,
        "breakdown": {"by_market": by_market, "by_signal_type": by_signal_type, "by_holding_period": by_holding_period},
        "equity_curve": {"points": points, "mdd_krw": round(mdd_krw, 2), "total_cost_krw": total_cost_krw,
                         "turnover_krw": turnover_krw, "realized_trade_count": len(realized_trades),
                         "partial_lot_count": sum(1 for trade in realized_trades if trade["lot_status"] == "partial")},
        "engine_comparison": {
            "avg_excess_return_pct_20d_buy": comparison["buy"]["avg_excess_return_pct_20d"],
            "note": "엔진 자체 모의원장 성과와는 설계상 합산·직접 비교하지 않습니다. 여기서는 매수 판단의 KOSPI 대비 초과수익만 표시합니다.",
        },
        "data_span_days": data_span_days, "observation_stage": observation_stage, "cagr_pct": cagr_pct,
        "disclaimer": "개인 판단 원장의 실현·관측 데이터만으로 계산한 리포트입니다. 1년 미만 데이터는 연환산 성과를 확정치로 표시하지 않고 관찰 단계로 둡니다. 엔진의 자체 모의원장과는 합산하지 않으며, 매매를 추천하거나 자동화하지 않습니다.",
    }


@router.get("/decisions/performance-report")
async def get_kqs_decision_performance_report(user: dict = Depends(current_user)) -> dict:
    """개인 판단 원장 기준 운용 성과 리포트를 반환한다. 매매를 추천하거나 자동화하지 않는다."""
    return await build_kqs_decision_performance_report(user["_id"])


KQS_TRAINING_MIN_MATURE = 120
KQS_TRAINING_MIN_PER_SPLIT = 20
KQS_SUCCESSOR_MIN_FRESH_DATES = 2
KQS_DATASET_EXAMPLE_SCHEMA_V2 = "kqs-human-decisions/v2"
KQS_DATASET_EXAMPLE_SCHEMA = "kqs-human-decisions/v3"
KQS_FEATURE_SCHEMA_V3 = {
    "schema": "kqs-engine-features/v1",
    "fields": ["data_as_of", "market", "signal_type", "selection_outcome", "selection_rank",
               "execution_session", "config_fingerprint", "reasons", "engine_id",
               "engine_git_sha", "dataset_hash"],
    "source": "immutable recommendation_snapshot and engine_snapshot copied at human decision",
    "provenance_fields": ["publication_snapshot_id", "recommendation_generated_at", "source_payload_sha256"],
    "excluded": ["human_decision", "evidence_tags", "memo", "trade_at", "trade_date",
                 "quantity", "fill_price", "fee_krw", "tax_krw", "forward_returns", "realized_trade"],
}
KQS_DATASET_MANIFEST_SCHEMA = "kqs-dataset-manifest/v1"
KQS_LEARNING_CONSENT_POLICY_VERSION = "kqs-learning-consent/v1"
KQS_LEARNING_CONSENT_ACKNOWLEDGEMENT = "KQS_LEARNING_DATA_ONLY"
KQS_TRAINING_MIN_CLASS_COUNT = 10
KQS_TRAINING_MIN_DISTINCT_DATES = 10
KQS_TRAINING_MIN_SPAN_DAYS = 180
KQS_TRAINING_MAX_DATE_CONCENTRATION = 0.40
_KQS_SPLITS = ("train", "validation", "test")


def _kqs_training_partition(decisions: list[dict], outcomes_by_id: dict, after_date: str | None = None) -> dict:
    """20일 결과가 확정된 표본을 기준일 순서로 분할하고 경계 겹침을 제거한다.

    첫 분할(``after_date`` 없음)은 60/20/20이다. EINT-009: 이미 동결된 manifest가 있으면
    ``after_date``(이전 manifest들이 덮은 마지막 기준일) 이후의 새 기준일만 validation·test가 되고
    (앞 절반/뒤 절반), 그 이전 기준일은 모두 train이다. 한 번 validation·test로 쓰인 기간은 다시
    평가 구간이 되지 않는다(D-20/D-26: 소진한 구간은 복원되지 않는다).
    """
    eligible = [row for row in decisions if not _is_position_exit(row) and (outcomes_by_id.get(row["_id"], {})
                .get("horizons", {}).get("20", {}).get("observed")) and row.get("data_as_of")]
    dates = sorted({str(row["data_as_of"])[:10] for row in eligible})
    splits = {"train": set(), "validation": set(), "test": set()}
    excluded = 0
    date_split: dict[str, str] = {}
    if after_date is None and len(dates) >= 5:
        train_end = max(1, len(dates) * 3 // 5)
        validation_end = max(train_end + 1, len(dates) * 4 // 5)
        validation_end = min(validation_end, len(dates) - 1)
        date_split = {date: "train" for date in dates[:train_end]}
        date_split.update({date: "validation" for date in dates[train_end:validation_end]})
        date_split.update({date: "test" for date in dates[validation_end:]})
    fresh = [date for date in dates if after_date is not None and date > after_date]
    if after_date is not None and len(fresh) >= KQS_SUCCESSOR_MIN_FRESH_DATES:
        half = len(fresh) // 2
        date_split = {date: "train" for date in dates if date <= after_date}
        date_split.update({date: "validation" for date in fresh[:half]})
        date_split.update({date: "test" for date in fresh[half:]})
    if date_split:
        validation_start = min(date for date, kind in date_split.items() if kind == "validation")
        test_start = min(date for date, kind in date_split.items() if kind == "test")
        for row in eligible:
            kind = date_split[str(row["data_as_of"])[:10]]
            target_date = str((outcomes_by_id[row["_id"]].get("horizons", {}).get("20", {})
                               .get("target_date") or ""))[:10]
            if (not target_date or (kind == "train" and target_date >= validation_start)
                    or (kind == "validation" and target_date >= test_start)):
                excluded += 1
            else:
                splits[kind].add(row["_id"])
    counts = {kind: len(ids) for kind, ids in splits.items()}
    reasons = []
    if after_date is None and len(dates) < 5:
        reasons.append("20일 결과가 확정된 서로 다른 기준일이 5일 미만입니다.")
    if after_date is not None and len(fresh) < KQS_SUCCESSOR_MIN_FRESH_DATES:
        reasons.append(f"이전 manifest 이후({after_date})의 새 기준일이 {len(fresh)}/{KQS_SUCCESSOR_MIN_FRESH_DATES}일입니다.")
    if len(eligible) < KQS_TRAINING_MIN_MATURE:
        reasons.append(f"20일 결과 확정 표본이 {len(eligible)}/{KQS_TRAINING_MIN_MATURE}건입니다.")
    for kind, count in counts.items():
        if count < KQS_TRAINING_MIN_PER_SPLIT:
            reasons.append(f"{kind} 구간 표본이 {count}/{KQS_TRAINING_MIN_PER_SPLIT}건입니다.")
    boundaries = {kind: {"first": min((d for d, k in date_split.items() if k == kind), default=None),
                         "last": max((d for d, k in date_split.items() if k == kind), default=None)} for kind in _KQS_SPLITS}
    split_rule = ("chronological 60/20/20" if after_date is None
                  else f"successor: dates <= {after_date} train; later dates first half validation, second half test")
    return {"splits": splits, "counts": counts, "eligible_count": len(eligible), "date_count": len(dates),
            "excluded_overlap_count": excluded, "ready": not reasons, "reasons": reasons, "after_date": after_date,
            "boundaries": boundaries,
            "date_range": {"first": dates[0] if dates else None, "last": dates[-1] if dates else None},
            "policy": {"split": split_rule, "label_horizon": "20 trading sessions",
                       "minimum_mature_examples": KQS_TRAINING_MIN_MATURE, "minimum_per_split": KQS_TRAINING_MIN_PER_SPLIT,
                       "successor_minimum_fresh_dates": KQS_SUCCESSOR_MIN_FRESH_DATES}}


def _kqs_training_data_quality(decisions: list[dict], outcomes_by_id: dict, partition: dict) -> dict:
    """Assess only candidate data shape; never fit a model or inspect a sealed holdout."""
    selected_ids = set().union(*partition["splits"].values())
    rows = [row for row in decisions if row["_id"] in selected_ids]
    labels = Counter(str(row.get("decision") or "") for row in rows)
    labels.pop("", None)
    dates = [str(row.get("data_as_of") or "")[:10] for row in rows if row.get("data_as_of")]
    date_counts = Counter(dates)
    split_summary = {}
    reasons, warnings = [], []
    for split_name, ids in partition["splits"].items():
        split_rows = [row for row in rows if row["_id"] in ids]
        split_labels = Counter(str(row.get("decision") or "") for row in split_rows)
        split_labels.pop("", None)
        split_summary[split_name] = {
            "sample_count": len(split_rows),
            "label_counts": dict(sorted(split_labels.items())),
            "distinct_label_count": len(split_labels),
            "distinct_as_of_dates": len({str(row.get("data_as_of") or "")[:10] for row in split_rows}),
        }
        if len(split_labels) < 2:
            reasons.append(f"{split_name} 구간에 서로 다른 판단 레이블이 2개 미만입니다.")
    if len(labels) < 2:
        reasons.append("학습 후보 전체에 서로 다른 판단 레이블이 2개 미만입니다.")
    for label, count in sorted(labels.items()):
        if count < KQS_TRAINING_MIN_CLASS_COUNT:
            reasons.append(f"판단 레이블 {label} 표본이 {count}/{KQS_TRAINING_MIN_CLASS_COUNT}건입니다.")
    distinct_dates = sorted(set(dates))
    span_days = 0
    if len(distinct_dates) >= 2:
        try:
            span_days = (date.fromisoformat(distinct_dates[-1]) - date.fromisoformat(distinct_dates[0])).days
        except ValueError:
            reasons.append("기준일 형식이 올바르지 않은 표본이 있습니다.")
    if len(distinct_dates) < KQS_TRAINING_MIN_DISTINCT_DATES:
        reasons.append(f"서로 다른 기준일이 {len(distinct_dates)}/{KQS_TRAINING_MIN_DISTINCT_DATES}일입니다.")
    if len(distinct_dates) >= 2 and span_days < KQS_TRAINING_MIN_SPAN_DAYS:
        reasons.append(f"관측 기준일 범위가 {span_days}/{KQS_TRAINING_MIN_SPAN_DAYS}일입니다.")
    maximum_date_count = max(date_counts.values(), default=0)
    concentration = maximum_date_count / len(rows) if rows else 0.0
    if rows and concentration > KQS_TRAINING_MAX_DATE_CONCENTRATION:
        reasons.append(f"한 기준일에 표본 {concentration:.0%}가 집중돼 있습니다(상한 {KQS_TRAINING_MAX_DATE_CONCENTRATION:.0%}).")
    incomplete_categories = Counter()
    invalid_provenance = 0
    for row in rows:
        try:
            features, _ = _kqs_engine_features_v3(row)
        except ValueError:
            invalid_provenance += 1
            continue
        for key in ("market", "signal_type", "engine_id"):
            if not str(features.get(key) or "").strip():
                incomplete_categories[key] += 1
        if not features.get("reasons"):
            warnings.append("추천 근거가 비어 있는 표본이 있습니다.")
    observed_20d = sum(bool((outcomes_by_id.get(row["_id"], {}).get("horizons") or {}).get("20", {}).get("observed")) for row in rows)
    realized_trade_count = sum(bool((outcomes_by_id.get(row["_id"], {}) or {}).get("realized")) for row in rows)
    if invalid_provenance:
        reasons.append(f"추천 게시 시점 피처 출처가 불완전하거나 불일치하는 표본 {invalid_provenance}건이 있습니다.")
    if incomplete_categories:
        reasons.append("필수 범주 피처가 비어 있는 표본이 있습니다: " + ", ".join(sorted(incomplete_categories)))
    return {
        "report_version": "kqs-learning-data-quality/v1",
        "ready": not reasons,
        "blocking_reasons": reasons,
        "warnings": sorted(set(warnings)),
        "sample_count": len(rows),
        "label_counts": dict(sorted(labels.items())),
        "minimum_label_count": min(labels.values()) if labels else 0,
        "distinct_as_of_dates": len(distinct_dates),
        "as_of_range": {"first": distinct_dates[0] if distinct_dates else None, "last": distinct_dates[-1] if distinct_dates else None,
                        "span_days": span_days, "maximum_date_concentration": round(concentration, 6)},
        "split_summary": split_summary,
        "observed_20d_count": observed_20d,
        "realized_trade_count": realized_trade_count,
        "invalid_feature_provenance_count": invalid_provenance,
        "incomplete_category_counts": dict(sorted(incomplete_categories.items())),
        "policy": {
            "minimum_class_count": KQS_TRAINING_MIN_CLASS_COUNT,
            "minimum_distinct_dates": KQS_TRAINING_MIN_DISTINCT_DATES,
            "minimum_span_days": KQS_TRAINING_MIN_SPAN_DAYS,
            "maximum_date_concentration": KQS_TRAINING_MAX_DATE_CONCENTRATION,
            "minimum_distinct_labels_per_split": 2,
        },
        "disclaimer": "표본 수·분포·날짜 집중·출처를 읽기 전용으로 점검합니다. 통과는 예측력, 표본 독립성, 투자 적합성 또는 자동매매 승인을 뜻하지 않습니다.",
    }


def _canonical_sha256(payload) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _kqs_example_features(decision: dict, include_notes: bool = False) -> dict:
    """결정 시점에 알 수 있었던 값만 담은 피처(미래 성과 제외)."""
    signal = decision.get("recommendation_snapshot") or {}
    engine = decision.get("engine_snapshot") or {}
    features = {
        "data_as_of": decision.get("data_as_of"), "market": decision.get("market"),
        "signal_type": signal.get("signal_type"), "selection_outcome": signal.get("selection_outcome"),
        "selection_rank": signal.get("selection_rank"), "execution_session": signal.get("execution_session"),
        "config_fingerprint": signal.get("config_fingerprint"), "reasons": signal.get("reasons", []),
        "engine_id": engine.get("engine_id"), "engine_git_sha": engine.get("git_sha"),
        "dataset_hash": engine.get("dataset_hash"), "evidence_tags": decision.get("evidence_tags", []),
    }
    if include_notes:
        features["human_note"] = decision.get("memo", "")
    return features


def _kqs_engine_features_v3(decision: dict) -> tuple[dict, dict]:
    """Only publication-time snapshots may supply v3 model features; no human fields."""
    signal = decision.get("recommendation_snapshot")
    engine = decision.get("engine_snapshot")
    if not isinstance(signal, dict) or not isinstance(engine, dict):
        raise ValueError("추천/엔진 원본 스냅샷이 없어 v3 피처 출처를 검증할 수 없습니다.")
    required_signal = ("signal_id", "symbol", "market", "signal_type", "reasons")
    required_engine = ("engine_id", "data_as_of")
    if (any(not signal.get(key) and signal.get(key) != [] for key in required_signal)
            or any(not engine.get(key) for key in required_engine)
            or not isinstance(signal["reasons"], list)
            or not decision.get("publication_snapshot_id")
            or not decision.get("recommendation_generated_at")):
        raise ValueError("추천 게시 당시의 식별자·기준일·근거가 불완전합니다.")
    if (signal["signal_id"] != decision.get("signal_id")
            or signal["symbol"] != decision.get("symbol")
            or signal["market"] != decision.get("market")
            or engine["data_as_of"] != decision.get("data_as_of")):
        raise ValueError("추천 원본 스냅샷과 판단 기록의 식별자/기준일이 일치하지 않습니다.")
    features = {
        "data_as_of": engine["data_as_of"], "market": signal["market"],
        "signal_type": signal["signal_type"],
        "selection_outcome": signal.get("selection_outcome"),
        "selection_rank": signal.get("selection_rank"),
        "execution_session": signal.get("execution_session"),
        "config_fingerprint": signal.get("config_fingerprint"),
        "reasons": signal["reasons"], "engine_id": engine["engine_id"],
        "engine_git_sha": engine.get("git_sha"), "dataset_hash": engine.get("dataset_hash"),
    }
    provenance = {
        "publication_snapshot_id": decision["publication_snapshot_id"],
        "recommendation_generated_at": decision["recommendation_generated_at"],
        "source_payload_sha256": _canonical_sha256({"signal": signal, "engine": engine}),
    }
    if set(features) != set(KQS_FEATURE_SCHEMA_V3["fields"]):
        raise ValueError("v3 허용 피처 목록과 내보내기 계약이 일치하지 않습니다.")
    return features, provenance


def _kqs_example_line_v3(decision: dict, outcome: dict, split_name: str, include_notes: bool = False) -> dict:
    line = _kqs_example_line(decision, outcome, split_name, include_notes=False)
    line["features"], line["feature_provenance"] = _kqs_engine_features_v3(decision)
    line["human_context"] = {"evidence_tags": decision.get("evidence_tags", []),
                             "trade_at": decision.get("trade_at"),
                             "trade_date": decision.get("trade_date"),
                             "quantity": decision.get("quantity"),
                             "fill_price": decision.get("fill_price"),
                             "fee_krw": decision.get("fee_krw"),
                             "tax_krw": decision.get("tax_krw")}
    if include_notes:
        line["human_context"]["memo"] = decision.get("memo", "")
    return line


def _kqs_example_sha256_v3(decision: dict, outcome: dict) -> str:
    """v3 freezes source-verified engine features, decision label, and mature 20-day label."""
    features, provenance = _kqs_engine_features_v3(decision)
    horizon = (outcome.get("horizons") or {}).get("20", {})
    return _canonical_sha256({
        "id": str(decision["_id"]), "feature_schema": KQS_FEATURE_SCHEMA_V3,
        "features": features, "feature_provenance": provenance,
        "human_decision": decision.get("decision"),
        "forward_20": {key: horizon.get(key) for key in
                       ("observed", "target_date", "return_pct", "excess_return_pct")},
    })


def _kqs_manifest_example_sha256(manifest: dict, decision: dict, outcome: dict) -> str:
    schema = manifest.get("example_schema", KQS_DATASET_EXAMPLE_SCHEMA_V2)
    if schema == KQS_DATASET_EXAMPLE_SCHEMA_V2:
        return _kqs_example_sha256(decision, outcome)  # frozen historical v2 bytes/meaning
    if schema == KQS_DATASET_EXAMPLE_SCHEMA:
        if manifest.get("feature_schema_sha256") != _canonical_sha256(KQS_FEATURE_SCHEMA_V3):
            raise ValueError("v3 피처 계약 지문이 현재 검증 규칙과 다릅니다.")
        return _kqs_example_sha256_v3(decision, outcome)
    raise ValueError("알 수 없는 동결 데이터셋 예제 스키마입니다.")


def _kqs_forward_labels(outcome: dict) -> dict:
    horizons = outcome.get("horizons") or {}
    return {horizon: {"observed": bool(horizons.get(horizon, {}).get("observed")),
                      **{key: horizons.get(horizon, {}).get(key) for key in
                         ("return_pct", "excess_return_pct", "max_drawdown_pct", "target_date")}}
            for horizon in ("1", "5", "20")}


def _kqs_example_line(decision: dict, outcome: dict, split_name: str, include_notes: bool = False) -> dict:
    horizons = outcome.get("horizons") or {}
    return {"record_type": "example", "example_id": str(decision["_id"]),
            "signal_id": decision.get("signal_id"), "decided_at": decision.get("decided_at"),
            "symbol": decision.get("symbol"), "name": decision.get("name"), "dataset_split": split_name,
            "features": _kqs_example_features(decision, include_notes),
            "labels": {"human_decision": decision.get("decision"), "forward_returns": _kqs_forward_labels(outcome),
                       "realized_trade": _serialize_realized(outcome.get("realized"))},
            "label_quality": {"outcome_status": outcome.get("status", "pending_observation"),
                              "has_20d_forward_label": bool(horizons.get("20", {}).get("observed")),
                              "has_realized_trade_label": bool(outcome.get("realized"))}}


def _kqs_example_sha256(decision: dict, outcome: dict) -> str:
    """동결 대상 내용의 해시: 결정 시점 피처·사람 판단·확정 20일 레이블. 메모와 실현 손익(이후 매도로 변함)은 제외."""
    horizon = (outcome.get("horizons") or {}).get("20", {})
    return _canonical_sha256({
        "id": str(decision["_id"]), "features": _kqs_example_features(decision),
        "human_decision": decision.get("decision"),
        "forward_20": {key: horizon.get(key) for key in ("observed", "target_date", "return_pct", "excess_return_pct")},
    })


async def _kqs_user_dataset_rows(user_id) -> tuple[list[dict], dict]:
    db = get_db()
    rows = await db.kqs_paper_decisions.find({"user_id": user_id}).sort([("decided_at", 1), ("_id", 1)]).to_list(50_000)
    outcomes = await db.kqs_decision_outcomes.find({"user_id": user_id}).to_list(50_000)
    return [row for row in rows if not _is_position_exit(row)], {row["decision_id"]: row for row in outcomes}


async def _kqs_user_manifests(user_id) -> list[dict]:
    return await get_db().kqs_dataset_manifests.find({"user_id": user_id}).sort("sequence", 1).to_list(1_000)


def _serialize_learning_consent(document: dict | None) -> dict:
    if not document:
        return {
            "status": "not_recorded",
            "active": False,
            "policy_version": KQS_LEARNING_CONSENT_POLICY_VERSION,
            "purpose": "offline_learning_only",
        }
    return {
        "consent_id": document.get("consent_id"),
        "status": document.get("status"),
        "active": document.get("status") == "granted",
        "policy_version": document.get("policy_version"),
        "purpose": document.get("purpose"),
        "recorded_at": document.get("recorded_at"),
    }


async def _kqs_current_learning_consent(user_id) -> dict | None:
    return await get_db().kqs_learning_consents.find_one(
        {"user_id": user_id}, sort=[("recorded_at", -1), ("_id", -1)],
    )


async def _require_kqs_learning_consent(user_id) -> dict:
    consent = await _kqs_current_learning_consent(user_id)
    if not consent or consent.get("status") != "granted" or consent.get("policy_version") != KQS_LEARNING_CONSENT_POLICY_VERSION:
        raise HTTPException(
            status_code=409,
            detail="학습 사용 동의가 활성화되지 않았습니다. 이 동의는 오프라인 학습 데이터 사용만 허용하며 추천·주문·자동매매를 승인하지 않습니다.",
        )
    return consent


def _kqs_covered_until(manifests: list[dict]) -> str | None:
    """이전 manifest들이 평가 구간(validation·test)까지 덮은 마지막 기준일."""
    dates = [manifest["boundaries"][kind]["last"] for manifest in manifests for kind in _KQS_SPLITS
             if manifest["boundaries"].get(kind, {}).get("last")]
    return max(dates) if dates else None


def _kqs_manifest_summary(manifest: dict, opening: dict | None, superseded: bool = False) -> dict:
    return {"manifest_id": manifest["manifest_id"], "sequence": manifest["sequence"],
            "created_at": manifest["created_at"], "previous_manifest_id": manifest.get("previous_manifest_id"),
            "feature_schema": manifest.get("feature_schema"),
            "feature_schema_sha256": manifest.get("feature_schema_sha256"),
            "learning_consent": manifest.get("learning_consent"),
            "schema_version": manifest["schema_version"], "example_schema": manifest["example_schema"],
            "policy": manifest["policy"], "boundaries": manifest["boundaries"], "counts": manifest["counts"],
            "excluded_overlap_count": manifest["excluded_overlap_count"],
            "dataset_sha256": manifest["dataset_sha256"], "manifest_sha256": manifest["manifest_sha256"],
            "holdout": ({"status": "opened", "opened_at": opening["opened_at"],
                         "preregistration": opening["preregistration"],
                         "preregistration_sha256": opening["preregistration_sha256"]}
                        if opening else {"status": "retired_sealed" if superseded else "sealed"})}


async def _kqs_manifest_for_user(user_id, manifest_id: str) -> tuple[dict, dict | None]:
    db = get_db()
    manifest = await db.kqs_dataset_manifests.find_one({"user_id": user_id, "manifest_id": manifest_id})
    if not manifest:
        raise HTTPException(status_code=404, detail="데이터셋 manifest를 찾지 못했습니다.")
    opening = await db.kqs_dataset_holdout_openings.find_one({"manifest_id": manifest_id})
    return manifest, opening


def _kqs_verify_manifest_rows(manifest: dict, decisions: list[dict], outcomes_by_id: dict, split_names) -> dict:
    """동결 당시 해시와 현재 원장 내용을 대조한다. 한 건이라도 다르면 사용하지 않는다(fail closed)."""
    by_id = {str(row["_id"]): row for row in decisions}
    rows, drift = {}, []
    for split_name in split_names:
        rows[split_name] = []
        for entry in manifest["examples"][split_name]:
            decision = by_id.get(entry["id"])
            outcome = outcomes_by_id.get(decision["_id"], {}) if decision else {}
            try:
                matches = bool(decision) and _kqs_manifest_example_sha256(manifest, decision, outcome) == entry["sha256"]
            except ValueError:
                matches = False
            if not matches:
                drift.append(entry["id"])
                continue
            rows[split_name].append((decision, outcome))
    if drift:
        raise HTTPException(status_code=409, detail=(
            f"동결 이후 내용이 달라진 표본 {len(drift)}건이 있어 이 manifest를 사용할 수 없습니다. "
            "새 manifest를 동결하세요(이전 평가 구간은 재사용되지 않습니다)."))
    return rows


async def _kqs_build_training_readiness(user_id) -> dict:
    decisions, outcomes_by_id = await _kqs_user_dataset_rows(user_id)
    manifests = await _kqs_user_manifests(user_id)
    result = _kqs_training_partition(decisions, outcomes_by_id, after_date=_kqs_covered_until(manifests))
    quality = _kqs_training_data_quality(decisions, outcomes_by_id, result)
    consent = _serialize_learning_consent(await _kqs_current_learning_consent(user_id))
    invalid = quality["invalid_feature_provenance_count"]
    if invalid and not any("피처 출처" in reason for reason in result["reasons"]):
        result["reasons"].append(f"추천 게시 시점의 피처 출처가 불완전하거나 불일치하는 표본 {invalid}건이 있습니다.")
    if not quality["ready"]:
        result["reasons"].extend(reason for reason in quality["blocking_reasons"] if reason not in result["reasons"])
    if not consent["active"]:
        result["reasons"].append("학습 사용 동의가 활성화되지 않았습니다. 동의 전에는 신규 동결·v3 내보내기·holdout·실험을 할 수 없습니다.")
    result["ready"] = bool(result["ready"] and quality["ready"] and consent["active"])
    result.pop("splits")
    return {**result, "feature_schema": KQS_FEATURE_SCHEMA_V3["schema"],
            "feature_schema_sha256": _canonical_sha256(KQS_FEATURE_SCHEMA_V3),
            "invalid_feature_provenance_count": invalid, "data_quality": quality,
            "learning_consent": consent,
            "total_decision_count": len(decisions), "frozen_manifest_count": len(manifests),
            "disclaimer": "다음 동결 가능 여부만 읽기 전용으로 점검합니다. 동의는 오프라인 학습 데이터 사용만 허용하며, 이 API는 모델 학습·추천·주문을 실행하지 않습니다."}


@router.get("/decisions/training-readiness")
async def get_kqs_training_readiness(user: dict = Depends(current_user)) -> dict:
    """다음 데이터셋 manifest를 동결할 수 있는지 읽기 전용으로 점검한다(학습·추천·주문 없음)."""
    return await _kqs_build_training_readiness(user["_id"])


@router.get("/learning-consent")
async def get_kqs_learning_consent(user: dict = Depends(current_user)) -> dict:
    """Return only the current user's offline-learning consent state and immutable history."""
    db = get_db()
    rows = await db.kqs_learning_consents.find({"user_id": user["_id"]}).sort([("recorded_at", -1), ("_id", -1)]).to_list(20)
    return {
        "current": _serialize_learning_consent(rows[0] if rows else None),
        "history": [_serialize_learning_consent(row) for row in rows],
        "policy": {
            "version": KQS_LEARNING_CONSENT_POLICY_VERSION,
            "scope": "offline_learning_only",
            "automatic_trading_authorized": False,
            "automatic_deletion": False,
            "revocation_effect": "Blocks future v3 freeze/export/holdout/experiment; immutable historical manifests remain audit records.",
        },
        "disclaimer": "동의는 개인 KQS 기록을 향후 오프라인 학습 데이터로 사용할 수 있게 하는 선택입니다. 추천·주문·자동체결·실거래 권한은 만들지 않으며, 철회는 기존 불변 기록을 삭제하지 않습니다.",
    }


@router.put("/learning-consent")
async def set_kqs_learning_consent(payload: KqsLearningConsentInput, user: dict = Depends(current_user)) -> dict:
    """Append an immutable consent/revocation event; default remains no consent until this endpoint is used."""
    db = get_db()
    current = await _kqs_current_learning_consent(user["_id"])
    requested = "granted" if payload.consented else "revoked"
    if current and current.get("status") == requested and current.get("policy_version") == KQS_LEARNING_CONSENT_POLICY_VERSION:
        return {"current": _serialize_learning_consent(current), "changed": False,
                "disclaimer": "현재 학습 사용 동의 상태와 같습니다. 추천·주문·자동체결 권한은 변경하지 않습니다."}
    document = {
        "consent_id": f"kqs-consent-{uuid4().hex}",
        "user_id": user["_id"],
        "status": requested,
        "policy_version": KQS_LEARNING_CONSENT_POLICY_VERSION,
        "purpose": "offline_learning_only",
        "acknowledgement": payload.acknowledgement,
        "recorded_at": datetime.now(timezone.utc),
    }
    await db.kqs_learning_consents.create_index([("user_id", 1), ("recorded_at", -1)])
    await db.kqs_learning_consents.insert_one(document)
    return {"current": _serialize_learning_consent(document), "changed": True,
            "disclaimer": ("학습 사용 동의를 기록했습니다. 향후 v3 동결·내보내기·오프라인 실험에만 적용되며 추천·주문·자동체결은 계속 차단됩니다."
                            if requested == "granted" else
                            "학습 사용 동의를 철회했습니다. 이후 v3 동결·내보내기·holdout·실험은 차단되며 기존 불변 기록은 감사용으로 남습니다.")}


@router.post("/datasets", status_code=201)
async def freeze_kqs_dataset(user: dict = Depends(current_user)) -> dict:
    """EINT-009: 현재 확정 표본으로 불변 데이터셋 manifest를 동결한다.

    표본 id·기준일 경계·표본별 내용 해시·전체 해시를 남기고 이후 수정하지 않는다. 이전 manifest가 있으면
    그 평가 구간 이후의 새 기준일만 validation·test가 된다. test(holdout)는 봉인 상태로 시작한다.
    """
    readiness = await _kqs_build_training_readiness(user["_id"])
    if not readiness["ready"]:
        raise HTTPException(status_code=422, detail="동결 기준을 충족하지 못했습니다: " + " ".join(readiness["reasons"]))
    consent = await _require_kqs_learning_consent(user["_id"])
    decisions, outcomes_by_id = await _kqs_user_dataset_rows(user["_id"])
    manifests = await _kqs_user_manifests(user["_id"])
    previous = manifests[-1] if manifests else None
    partition = _kqs_training_partition(decisions, outcomes_by_id, after_date=_kqs_covered_until(manifests))
    by_id = {row["_id"]: row for row in decisions}
    for ids in partition["splits"].values():
        for _id in ids:
            try:
                _kqs_engine_features_v3(by_id[_id])
            except ValueError as exc:
                raise HTTPException(422, detail=f"v3 동결 전 추천 피처 출처 검증 실패: {exc}") from exc
    examples = {}
    for kind in _KQS_SPLITS:
        ordered = sorted(partition["splits"][kind], key=lambda _id: (str(by_id[_id]["data_as_of"]), str(_id)))
        examples[kind] = [{"id": str(_id), "as_of": str(by_id[_id]["data_as_of"])[:10],
                           "sha256": _kqs_example_sha256_v3(by_id[_id], outcomes_by_id.get(_id, {}))} for _id in ordered]
    body = {
        "schema_version": KQS_DATASET_MANIFEST_SCHEMA, "example_schema": KQS_DATASET_EXAMPLE_SCHEMA,
        "feature_schema": KQS_FEATURE_SCHEMA_V3["schema"],
        "feature_schema_sha256": _canonical_sha256(KQS_FEATURE_SCHEMA_V3),
        "sequence": (previous["sequence"] + 1) if previous else 1,
        "previous_manifest_id": previous["manifest_id"] if previous else None,
        "previous_manifest_sha256": previous["manifest_sha256"] if previous else None,
        "policy": partition["policy"], "boundaries": partition["boundaries"], "counts": partition["counts"],
        "excluded_overlap_count": partition["excluded_overlap_count"], "examples": examples,
        "dataset_sha256": _canonical_sha256(examples),
        "learning_consent": {"consent_id": consent["consent_id"], "status": "granted",
                             "policy_version": consent["policy_version"], "purpose": consent["purpose"],
                             "recorded_at": consent["recorded_at"]},
        "hash_scope": "v3 engine-only features + immutable publication provenance + human_decision + observed 20-day label + active learning-consent reference; human annotations, notes and realized P&L excluded",
    }
    now = datetime.now(timezone.utc)
    document = {"manifest_id": f"kqs-ds-{uuid4().hex}", "user_id": user["_id"], "created_at": now, **body,
                "manifest_sha256": _canonical_sha256(body)}
    collection = get_db().kqs_dataset_manifests
    await collection.create_index([("user_id", 1), ("sequence", 1)], unique=True)
    try:
        await collection.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="다른 동결 요청과 겹쳤습니다. 목록을 새로 불러오세요.") from exc
    return {"item": _kqs_manifest_summary(document, None),
            "disclaimer": "불변 manifest로 동결했습니다. test는 사전등록 후 1회만 개봉할 수 있습니다. 학습·주문은 실행하지 않습니다."}


@router.get("/datasets")
async def list_kqs_datasets(user: dict = Depends(current_user)) -> dict:
    manifests = await _kqs_user_manifests(user["_id"])
    ids = [manifest["manifest_id"] for manifest in manifests]
    openings = {row["manifest_id"]: row for row in await get_db().kqs_dataset_holdout_openings.find(
        {"manifest_id": {"$in": ids}}).to_list(1_000)}
    successors = {manifest.get("previous_manifest_id") for manifest in manifests}
    return {"items": [_kqs_manifest_summary(manifest, openings.get(manifest["manifest_id"]), manifest["manifest_id"] in successors)
                      for manifest in reversed(manifests)],
            "disclaimer": "동결된 학습 데이터셋 manifest 목록입니다. 수정·삭제 API는 없습니다."}


@router.get("/datasets/{manifest_id}/examples")
async def export_kqs_dataset_split(
    manifest_id: str, split: Literal["train", "validation", "test"] = Query(...),
    include_notes: bool = Query(default=False), user: dict = Depends(current_user),
) -> Response:
    """동결 manifest의 한 구간을 JSONL로 내보낸다. test는 holdout 개봉(사전등록) 뒤에만 가능하다."""
    manifest, opening = await _kqs_manifest_for_user(user["_id"], manifest_id)
    schema = manifest.get("example_schema", KQS_DATASET_EXAMPLE_SCHEMA_V2)
    current_consent = None
    if schema == KQS_DATASET_EXAMPLE_SCHEMA:
        current_consent = await _require_kqs_learning_consent(user["_id"])
    if split == "test" and not opening:
        raise HTTPException(status_code=409, detail="test(holdout)는 봉인 상태입니다. 최종 모델을 사전등록해 개봉한 뒤에만 내려받을 수 있습니다.")
    decisions, outcomes_by_id = await _kqs_user_dataset_rows(user["_id"])
    rows = _kqs_verify_manifest_rows(manifest, decisions, outcomes_by_id, [split])[split]
    if split == "test":
        await get_db().kqs_dataset_access_log.insert_one({"manifest_id": manifest_id, "user_id": user["_id"],
                                                         "split": split, "accessed_at": datetime.now(timezone.utc)})
    header = {"record_type": "manifest", "schema_version": schema, "manifest_id": manifest_id,
              "feature_schema": manifest.get("feature_schema"),
              "feature_schema_sha256": manifest.get("feature_schema_sha256"),
              "training_eligible": schema == KQS_DATASET_EXAMPLE_SCHEMA,
              "learning_consent": {"manifest": manifest.get("learning_consent"),
                                   "current": _serialize_learning_consent(current_consent)} if schema == KQS_DATASET_EXAMPLE_SCHEMA else None,
              "legacy_feature_leakage_warning": schema == KQS_DATASET_EXAMPLE_SCHEMA_V2,
              "manifest_sha256": manifest["manifest_sha256"], "dataset_sha256": manifest["dataset_sha256"],
              "split": split, "example_count": len(rows), "boundaries": manifest["boundaries"],
              "holdout_status": "opened" if opening else "sealed", "notes_included": include_notes,
              "features_exclude_future_outcomes": True, "hash_verified": True,
              "label_policy": "20-day labels are frozen by hash; realized_trade may grow after freezing and is not hashed",
              "disclaimer": "동결 데이터셋 내보내기입니다. 자동 학습·매매 판단·주문은 수행하지 않습니다."}
    line_builder = _kqs_example_line_v3 if schema == KQS_DATASET_EXAMPLE_SCHEMA else _kqs_example_line
    lines = [header] + [line_builder(decision, outcome, split, include_notes) for decision, outcome in rows]
    content = "\n".join(json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":")) for item in lines) + "\n"
    return Response(content=content, media_type="application/x-ndjson; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{manifest_id}-{split}.jsonl"',
                             "Cache-Control": "no-store"})


class KqsHoldoutPreregistrationInput(BaseModel):
    """holdout 개봉 전 최종 모델 사전등록. train·validation만 보고 고른 모델 하나를 고정한다."""
    model_family: Literal["majority_class_baseline", "logistic_regression", "tree_ensemble", "external_offline"]
    model_version: str = Field(min_length=1, max_length=80)
    training_config: dict[str, str | int | float | bool] = Field(default_factory=dict)
    validation: KqsExperimentSplitMetrics
    note: str = Field(default="", max_length=1_000)


@router.post("/datasets/{manifest_id}/holdout", status_code=201)
async def open_kqs_dataset_holdout(manifest_id: str, payload: KqsHoldoutPreregistrationInput,
                                   user: dict = Depends(current_user)) -> dict:
    """EINT-009: 사전등록과 함께 test(holdout)를 manifest당 1회만 개봉한다. 개봉 기록은 불변이다."""
    manifest, opening = await _kqs_manifest_for_user(user["_id"], manifest_id)
    if manifest.get("example_schema", KQS_DATASET_EXAMPLE_SCHEMA_V2) != KQS_DATASET_EXAMPLE_SCHEMA:
        raise HTTPException(409, detail="기존 v2 데이터셋은 사후 사용자 태그가 피처에 포함되어 새 holdout 개봉에 사용할 수 없습니다.")
    await _require_kqs_learning_consent(user["_id"])
    if opening:
        raise HTTPException(status_code=409, detail="이 manifest의 holdout은 이미 개봉되어 소진되었습니다. 새 manifest를 동결하세요.")
    if await get_db().kqs_dataset_manifests.find_one({"user_id": user["_id"], "previous_manifest_id": manifest_id}):
        raise HTTPException(status_code=409, detail="후속 manifest가 이미 동결되어 이 manifest의 평가 구간은 후속 train에 들어갔습니다. 봉인된 채 폐기되며 개봉할 수 없습니다.")
    if payload.validation.sample_count != manifest["counts"]["validation"]:
        raise HTTPException(status_code=422, detail="사전등록한 validation 표본 수가 manifest와 다릅니다.")
    decisions, outcomes_by_id = await _kqs_user_dataset_rows(user["_id"])
    _kqs_verify_manifest_rows(manifest, decisions, outcomes_by_id, _KQS_SPLITS)
    preregistration = {"model_family": payload.model_family, "model_version": payload.model_version.strip(),
                       "training_config": payload.training_config,
                       "training_config_sha256": _canonical_sha256(payload.training_config),
                       "validation": payload.validation.model_dump(), "note": payload.note.strip(),
                       "manifest_sha256": manifest["manifest_sha256"]}
    document = {"manifest_id": manifest_id, "user_id": user["_id"], "opened_at": datetime.now(timezone.utc),
                "preregistration": preregistration, "preregistration_sha256": _canonical_sha256(preregistration)}
    collection = get_db().kqs_dataset_holdout_openings
    await collection.create_index("manifest_id", unique=True)
    try:
        await collection.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="이 manifest의 holdout은 이미 개봉되었습니다.") from exc
    await append_execution_audit(user["_id"], "kqs_dataset_holdout_opened", "kqs_dataset_manifest", {
        "manifest_id": manifest_id, "preregistration_sha256": document["preregistration_sha256"],
        "live_trading_authorized": False,
    })
    return {"item": _kqs_manifest_summary(manifest, document),
            "disclaimer": "holdout을 개봉했습니다. 이 manifest의 test 성과는 사전등록한 모델로 1회만 기록할 수 있습니다."}


@router.get("/training-experiments")
async def get_kqs_training_experiments(user: dict = Depends(current_user)) -> dict:
    """사용자 본인의 오프라인 학습 실험 이력만 반환한다."""
    rows = await get_db().kqs_training_experiments.find(
        {"user_id": user["_id"]}, {"_id": 0, "user_id": 0, "test_report_key": 0},
    ).sort("created_at", -1).to_list(100)
    return {"items": rows, "disclaimer": "오프라인 실험의 재현 메타데이터·자기보고 성과 기록입니다. 추천 생성, 승인, 주문 또는 자동매매를 수행하지 않습니다."}


@router.post("/training-experiments", status_code=201)
async def record_kqs_training_experiment(
    payload: KqsTrainingExperimentInput, user: dict = Depends(current_user),
) -> dict:
    """동결 manifest에 연결된 오프라인 실험을 기록한다.

    EINT-009: 표본 수는 manifest와 같아야 한다. test 성과는 holdout을 개봉한 뒤, 사전등록한 모델
    (계열·버전·설정 해시 일치)로 manifest당 1회만 기록할 수 있다. 그 밖의 실험은 train·validation만 기록한다.
    """
    manifest, opening = await _kqs_manifest_for_user(user["_id"], payload.manifest_id)
    if manifest.get("example_schema", KQS_DATASET_EXAMPLE_SCHEMA_V2) != KQS_DATASET_EXAMPLE_SCHEMA:
        raise HTTPException(409, detail="기존 v2 데이터셋은 새 학습 실험 기록에 사용할 수 없습니다. 기존 기록은 보존됩니다.")
    await _require_kqs_learning_consent(user["_id"])
    reported = {key: getattr(payload, key) for key in _KQS_SPLITS}
    for key in ("train", "validation"):
        if reported[key].sample_count != manifest["counts"][key]:
            raise HTTPException(status_code=422, detail=f"{key} 표본 수가 동결 manifest와 일치하지 않습니다.")
    test_report_key = None
    if reported["test"] is not None:
        if not opening:
            raise HTTPException(status_code=409, detail="holdout이 봉인 상태라 test 성과를 기록할 수 없습니다.")
        prereg = opening["preregistration"]
        if (payload.model_family, payload.model_version.strip(), _canonical_sha256(payload.training_config)) != (
                prereg["model_family"], prereg["model_version"], prereg["training_config_sha256"]):
            raise HTTPException(status_code=409, detail="test 성과는 사전등록한 모델(계열·버전·설정)로만 기록할 수 있습니다.")
        if reported["test"].sample_count != manifest["counts"]["test"]:
            raise HTTPException(status_code=422, detail="test 표본 수가 동결 manifest와 일치하지 않습니다.")
        test_report_key = payload.manifest_id
    now = datetime.now(timezone.utc)
    document = {"experiment_id": f"kqs-exp-{uuid4().hex}", "user_id": user["_id"], "created_at": now,
                "dataset": {"schema_version": manifest["example_schema"], "manifest_id": payload.manifest_id,
                            "manifest_sha256": manifest["manifest_sha256"], "fingerprint": manifest["dataset_sha256"],
                            "boundaries": manifest["boundaries"], "counts": manifest["counts"]},
                "model": {"family": payload.model_family, "version": payload.model_version.strip(),
                          "training_config": payload.training_config,
                          "training_config_sha256": _canonical_sha256(payload.training_config)},
                "reported_metrics": {key: (value.model_dump() if value is not None else None) for key, value in reported.items()},
                "holdout_evaluation": test_report_key is not None,
                "preregistration_sha256": opening["preregistration_sha256"] if test_report_key else None,
                "note": payload.note.strip(), "status": "recorded_offline",
                "approval": {"status": "not_requested", "reason": "실험 기록만 지원하며 추천·주문 연결은 허용하지 않습니다."}}
    if test_report_key:
        document["test_report_key"] = test_report_key
    collection = get_db().kqs_training_experiments
    await collection.create_index("test_report_key", unique=True, sparse=True)
    try:
        await collection.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="이 manifest의 test 성과는 이미 기록되었습니다(holdout 1회 규칙).") from exc
    for key in ("_id", "user_id", "test_report_key"):
        document.pop(key, None)
    return {"item": document, "disclaimer": "실험이 연구 기록으로만 저장되었습니다. 이 기록은 추천·승인·주문·자동매매에 사용되지 않습니다."}


@router.get("/decisions/training-dataset")
async def export_kqs_decision_training_dataset(
    include_notes: bool = Query(default=False), split: Literal["all", "train", "validation", "test"] = Query(default="all"),
    schema: Literal["v3", "v2"] = Query(default="v3"), mature_only: bool = Query(default=False), user: dict = Depends(current_user),
) -> Response:
    """사용자 판단 이력 전체를 학습 전 검토용 JSONL로 내보낸다. 모델 학습이나 주문은 실행하지 않는다.

    EINT-009: 구간별(train/validation/test) 내보내기는 매번 다시 계산되는 분할이라 경계가 움직이고
    test를 반복 평가할 수 있었다. 이제 동결 manifest(`/api/kqs/datasets/...`)로만 제공한다.
    """
    if split != "all":
        raise HTTPException(status_code=410, detail="구간별 내보내기는 동결 manifest로 옮겼습니다: POST /api/kqs/datasets 후 /api/kqs/datasets/{manifest_id}/examples")
    current_consent = await _require_kqs_learning_consent(user["_id"]) if schema == "v3" else None
    db = get_db()
    all_rows = await db.kqs_paper_decisions.find({"user_id": user["_id"]}).sort("decided_at", 1).to_list(50_000)
    # EINT-008: 보유 lot 청산 기록은 판단 표본이 아니다. 매수 lot의 realized_trade 레이블로만 반영된다.
    decisions = [row for row in all_rows if not _is_position_exit(row)]
    outcomes = await db.kqs_decision_outcomes.find({"user_id": user["_id"]}).to_list(50_000)
    outcomes_by_id = {row["decision_id"]: row for row in outcomes}
    frozen: dict[str, str] = {}
    for manifest in await _kqs_user_manifests(user["_id"]):
        for kind in _KQS_SPLITS:
            for entry in manifest["examples"][kind]:
                frozen[entry["id"]] = f"{manifest['manifest_id']}:{kind}"
    selected = [row for row in decisions if not mature_only or
                outcomes_by_id.get(row["_id"], {}).get("horizons", {}).get("20", {}).get("observed")]
    if schema == "v3":
        for decision in selected:
            try:
                _kqs_engine_features_v3(decision)
            except ValueError as exc:
                raise HTTPException(422, detail=f"v3 검토 파일의 추천 피처 출처 검증 실패: {exc}") from exc
    export_schema = KQS_DATASET_EXAMPLE_SCHEMA if schema == "v3" else KQS_DATASET_EXAMPLE_SCHEMA_V2
    counts = {kind: 0 for kind in KQS_DECISION_TYPES}
    mature_20d = realized_count = 0
    for decision in selected:
        if decision.get("decision") in counts:
            counts[decision["decision"]] += 1
        outcome = outcomes_by_id.get(decision["_id"], {})
        mature_20d += bool(outcome.get("horizons", {}).get("20", {}).get("observed"))
        realized_count += bool(outcome.get("realized"))
    lines = [{"record_type": "manifest", "schema_version": export_schema,
              "feature_schema": KQS_FEATURE_SCHEMA_V3["schema"] if schema == "v3" else None,
              "feature_schema_sha256": _canonical_sha256(KQS_FEATURE_SCHEMA_V3) if schema == "v3" else None,
              "training_eligible": False,
              "learning_consent": _serialize_learning_consent(current_consent) if schema == "v3" else None,
              "legacy_feature_leakage_warning": schema == "v2",
              "position_exit_records_excluded": len(all_rows) - len(decisions),
              "realized_trade_policy": "FIFO lot per buy; partial sells realize only the sold quantity (status partial/closed)",
              "generated_at": datetime.now(timezone.utc).isoformat(), "example_count": len(selected),
              "decision_counts": counts, "observed_20d_count": mature_20d, "realized_trade_count": realized_count,
              "features_exclude_future_outcomes": True, "notes_included": include_notes,
              "requested_split": "all", "mature_only": mature_only,
              "split_policy": "review export only; dataset_split shows frozen manifest membership. Train/evaluate only from frozen manifests.",
              "label_policy": "human_decision is always observed; future returns and realized outcomes retain explicit observed/missing state",
              "disclaimer": "내보내기 파일은 학습 전 검토용입니다. 자동 학습·매매 판단·주문은 수행하지 않습니다."}]
    line_builder = _kqs_example_line_v3 if schema == "v3" else _kqs_example_line
    lines += [line_builder(decision, outcomes_by_id.get(decision["_id"], {}),
                           frozen.get(str(decision["_id"]), "unfrozen"), include_notes) for decision in selected]
    content = "\n".join(json.dumps(item, ensure_ascii=False, default=str, separators=(",", ":")) for item in lines) + "\n"
    stamp = datetime.now(timezone.utc).date().isoformat()
    return Response(content=content, media_type="application/x-ndjson; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="kqs-human-decisions-{stamp}.jsonl"',
                             "Cache-Control": "no-store"})


@router.get("/review-candidates/reviews")
async def get_kqs_candidate_review_history(days: int = Query(default=90, ge=1, le=365), user: dict = Depends(current_user)) -> dict:
    """사용자 본인의 후보 검토 감사 이력을 반환한다."""
    cutoff = (datetime.now(timezone.utc).astimezone(KST).date() - timedelta(days=days - 1)).isoformat()
    documents = await _candidate_collection(get_db(), "kqs_candidate_reviews").find(
        {"user_id": user["_id"], "as_of": {"$gte": cutoff}}, {"_id": 0, "user_id": 0},
    ).sort("updated_at", -1).to_list(500)
    return {
        "days": days,
        "items": [_serialize_candidate_review(document) for document in documents],
        "disclaimer": "사용자별 검토 메모 감사 이력입니다. 검토 완료·보류는 매매 승인이나 주문 실행을 뜻하지 않습니다.",
    }


@router.get("/review-candidates/review-snapshots")
async def get_kqs_candidate_review_snapshots(days: int = Query(default=90, ge=1, le=365), user: dict = Depends(current_user)) -> dict:
    """후보가 사라진 뒤에도 남는 사용자별 검토 시점 패킷 이력을 반환한다."""
    cutoff = (datetime.now(timezone.utc).astimezone(KST).date() - timedelta(days=days - 1)).isoformat()
    documents = await _candidate_collection(get_db(), "kqs_candidate_review_snapshots").find(
        {"user_id": user["_id"], "as_of": {"$gte": cutoff}}, {"_id": 0, "user_id": 0},
    ).sort("captured_at", -1).to_list(500)
    return {
        "days": days,
        "items": [_serialize_candidate_review_snapshot(document) for document in documents],
        "disclaimer": "검토 저장 시점의 정형 근거와 메모를 보존한 사용자별 감사 스냅샷입니다. 이력 조회는 주문·전략·포트폴리오를 변경하지 않습니다.",
    }


async def _portfolio_reconciliation(user_id, engine: dict | None = None) -> dict:
    """사용자 장부와 엔진 포지션을 대조하는 읽기 전용 계산 경로다."""
    if engine is None:
        engine = await asyncio.to_thread(_read_engine_positions)
    if engine.get("applicability") == "not_applicable":
        return engine
    document = await get_db().portfolios.find_one({"user_id": user_id}) or {}
    website_by_key: dict[str, dict] = {}
    for holding in document.get("holdings", []):
        ticker = str(holding.get("ticker", "")).strip().upper()
        key = _symbol_key(ticker)
        if not key:
            continue
        website_by_key[key] = {
            "ticker": ticker,
            "symbol_key": key,
            "name": str(holding.get("name", "")).strip(),
            "quantity": holding.get("quantity"),
            "average_price": holding.get("average_price"),
            "currency": holding.get("currency", "KRW"),
        }
    engine_by_key = {position["symbol_key"]: position for position in engine["positions"]}
    matching_keys = sorted(set(website_by_key) & set(engine_by_key))
    return {
        "available": engine["available"],
        "read_only": True,
        "as_of": engine["as_of"],
        "summary": {
            "website_holding_count": len(website_by_key),
            "engine_open_position_count": len(engine_by_key),
            "matching_symbol_count": len(matching_keys),
            "website_only_count": len(set(website_by_key) - set(engine_by_key)),
            "engine_only_count": len(set(engine_by_key) - set(website_by_key)),
        },
        "matched": [{"symbol_key": key, "website": website_by_key[key], "engine": engine_by_key[key]} for key in matching_keys],
        "website_only": [website_by_key[key] for key in sorted(set(website_by_key) - set(engine_by_key))],
        "engine_only": [engine_by_key[key] for key in sorted(set(engine_by_key) - set(website_by_key))],
        "disclaimer": "종목 코드만 대조한 읽기 전용 화면입니다. 엔진 모의 포지션에는 수량·투입금액이 없으므로 수량·평가금액의 일치 여부는 판단하지 않습니다. 이 화면은 주문·포트폴리오를 변경하지 않습니다.",
    }


def _snapshot_symbols(items: list[dict], key: str) -> list[str]:
    return sorted({str(item.get(key, "")).strip() for item in items if str(item.get(key, "")).strip()})


def _change_summary(current: dict, previous: dict | None) -> dict:
    """동일 사용자 직전 스냅샷과 비교한 종목 집합 변화만 계산한다."""
    current_sets = {
        "matched": _snapshot_symbols(current["matched"], "symbol_key"),
        "website_only": _snapshot_symbols(current["website_only"], "symbol_key"),
        "engine_only": _snapshot_symbols(current["engine_only"], "symbol_key"),
    }
    if not previous:
        return {"baseline": True, "changed": False, "new": {}, "resolved": {}}
    prior_sets = {name: set(previous.get(f"{name}_symbols", [])) for name in current_sets}
    added = {name: sorted(set(values) - prior_sets[name]) for name, values in current_sets.items()}
    resolved = {name: sorted(prior_sets[name] - set(values)) for name, values in current_sets.items()}
    return {
        "baseline": False,
        "changed": any(added[name] or resolved[name] for name in current_sets),
        "new": added,
        "resolved": resolved,
    }


async def capture_portfolio_reconciliation_snapshot(user_id, engine: dict | None = None) -> dict:
    """KST 하루 한 건의 대조 스냅샷을 저장하고 전일 대비 변화를 남긴다."""
    reconciliation = await _portfolio_reconciliation(user_id, engine)
    if not reconciliation["available"]:
        return {"stored": False, "reason": reconciliation.get("reason") or "엔진을 확인하지 못해 스냅샷을 저장하지 않았습니다.",
                "applicability": reconciliation.get("applicability")}
    now = datetime.now(timezone.utc)
    snapshot_date = now.astimezone(KST).date().isoformat()
    db = get_db()
    previous = await db.kqs_reconciliation_snapshots.find_one(
        {"user_id": user_id, "snapshot_date": {"$lt": snapshot_date}},
        sort=[("snapshot_date", -1)],
    )
    changes = _change_summary(reconciliation, previous)
    document = {
        "user_id": user_id,
        "snapshot_date": snapshot_date,
        "captured_at": now,
        "engine_as_of": reconciliation.get("as_of"),
        "summary": reconciliation["summary"],
        "matched_symbols": _snapshot_symbols(reconciliation["matched"], "symbol_key"),
        "website_only_symbols": _snapshot_symbols(reconciliation["website_only"], "symbol_key"),
        "engine_only_symbols": _snapshot_symbols(reconciliation["engine_only"], "symbol_key"),
        "changes": changes,
    }
    await db.kqs_reconciliation_snapshots.update_one(
        {"user_id": user_id, "snapshot_date": snapshot_date}, {"$set": document}, upsert=True,
    )
    return {"stored": True, **{key: value for key, value in document.items() if key != "user_id"}}


async def capture_all_portfolio_reconciliation_snapshots() -> None:
    """엔진을 한 번만 읽어 보유자산이 있는 모든 사용자 스냅샷을 갱신한다."""
    try:
        engine = await asyncio.to_thread(_read_engine_positions)
    except RuntimeError:
        return
    if not engine.get("available"):
        return
    user_ids = await get_db().portfolios.distinct("user_id")
    for user_id in user_ids:
        try:
            await capture_portfolio_reconciliation_snapshot(user_id, engine)
        except Exception:
            # 한 사용자의 저장 실패가 다른 사용자의 일별 기준선을 막지 않게 한다.
            continue


@router.get("/portfolio-reconciliation")
async def get_portfolio_reconciliation(user: dict = Depends(current_user)) -> dict:
    """개인 보유자산과 외부 엔진의 열린 모의 포지션을 종목 단위로 대조한다."""
    try:
        return await _portfolio_reconciliation(user["_id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/portfolio-reconciliation/snapshot")
async def create_portfolio_reconciliation_snapshot(user: dict = Depends(current_user)) -> dict:
    """현재 대조 결과를 일별 이력으로 저장한다. 엔진·개인 장부는 변경하지 않는다."""
    try:
        return await capture_portfolio_reconciliation_snapshot(user["_id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/portfolio-reconciliation/history")
async def get_portfolio_reconciliation_history(days: int = Query(default=30, ge=1, le=365), user: dict = Depends(current_user)) -> dict:
    """대조 이력은 legacy 엔진 모드에서만 사용한다."""
    if _request_source() == "snapshot":
        return {**native_positions_not_applicable(), "snapshots": [], "days": days}
    cutoff = (datetime.now(timezone.utc).astimezone(KST).date()).toordinal() - days + 1
    cutoff_date = datetime.fromordinal(cutoff).date().isoformat()
    rows = await get_db().kqs_reconciliation_snapshots.find(
        {"user_id": user["_id"], "snapshot_date": {"$gte": cutoff_date}}, {"_id": 0, "user_id": 0},
    ).sort("snapshot_date", 1).to_list(days + 1)
    for row in rows:
        if row.get("captured_at"):
            row["captured_at"] = row["captured_at"].isoformat()
    changed = [row for row in rows if row.get("changes", {}).get("changed")]
    return {
        "days": days,
        "snapshots": rows,
        "change_alert_count": len(changed),
        "disclaimer": "전일 스냅샷과 비교한 종목 집합 변화 알림입니다. 자동 주문·자동 동기화·포트폴리오 변경은 수행하지 않습니다.",
    }


@router.on_event("startup")
async def _ensure_kqs_indexes() -> None:
    try:
        db = get_db()
        await db.kqs_reconciliation_snapshots.create_index([("user_id", 1), ("snapshot_date", 1)], unique=True)
        await db.kqs_reconciliation_snapshots.create_index([("user_id", 1), ("snapshot_date", -1)])
        await _candidate_collection(db, "kqs_candidate_reviews").create_index([("user_id", 1), ("as_of", 1), ("symbol_key", 1)], unique=True)
        await _candidate_collection(db, "kqs_candidate_reviews").create_index([("user_id", 1), ("updated_at", -1)])
        await _candidate_collection(db, "kqs_candidate_review_snapshots").create_index([("user_id", 1), ("captured_at", -1)])
        await _candidate_collection(db, "kqs_candidate_review_snapshots").create_index([("user_id", 1), ("as_of", 1), ("symbol_key", 1)])
        await _candidate_collection(db, "kqs_candidate_queue_snapshots").create_index("snapshot_date", unique=True)
        await _candidate_collection(db, "kqs_candidate_outcomes").create_index([("as_of", 1), ("symbol_key", 1)], unique=True)
        await _candidate_collection(db, "kqs_candidate_outcomes").create_index([("as_of", -1), ("status", 1)])
        await db.kqs_operations_alert_acknowledgements.create_index([("user_id", 1), ("fingerprint", 1)], unique=True)
        await db.kqs_paper_decisions.create_index([("user_id", 1), ("decided_at", -1)])
        await db.kqs_paper_decisions.create_index([("user_id", 1), ("symbol_key", 1), ("decided_at", 1)])
        await db.kqs_decision_outcomes.create_index("decision_id", unique=True)
        await db.kqs_decision_outcomes.create_index([("user_id", 1), ("decided_at", -1)])
        await db.kqs_training_experiments.create_index([("user_id", 1), ("created_at", -1)])
        await db.kqs_training_experiments.create_index([("user_id", 1), ("experiment_id", 1)], unique=True)
        await db.kqs_training_experiments.create_index("test_report_key", unique=True, sparse=True)
        await db.kqs_dataset_manifests.create_index([("user_id", 1), ("sequence", 1)], unique=True)
        await db.kqs_dataset_manifests.create_index("manifest_id", unique=True)
        await db.kqs_dataset_holdout_openings.create_index("manifest_id", unique=True)
    except Exception:
        # MongoDB가 잠시 준비되지 않아도 기존 읽기 전용 대조는 요청 시 동작한다.
        return
