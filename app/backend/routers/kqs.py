"""Ubuntu-26.04 korea-quant-system 읽기 전용 브리지 프록시."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import statistics
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

try:
    from ..db import get_db
    from .auth import current_user
except ImportError:
    from db import get_db  # type: ignore
    from routers.auth import current_user  # type: ignore


router = APIRouter(prefix="/api/kqs", tags=["퀀트 엔진 브리지"])
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


def _read_bridge_summary() -> dict:
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
    """최신 기준일에 다음 거래일 진입 대상으로 남은 후보만 안전하게 추출한다."""
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
    """최신 후보 하나의 검토에 필요한 정형 근거만 추출한다."""
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


def _serialize_candidate_review(document: dict) -> dict:
    return {
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
    documents = await get_db().kqs_candidate_reviews.find(
        {"user_id": user_id, "as_of": as_of, "symbol_key": {"$in": keys}}, {"_id": 0, "user_id": 0},
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
    previous = await db.kqs_candidate_queue_snapshots.find_one(
        {"snapshot_date": {"$lt": snapshot_date}}, sort=[("snapshot_date", -1)],
    )
    candidates = _snapshot_queue_candidates(queue.get("candidates", []))
    existing = await db.kqs_candidate_queue_snapshots.find_one(
        {"snapshot_date": snapshot_date}, {"opened_at": 1, "captured_at": 1},
    )
    # An operator refresh must not silently reset the review-SLA clock.
    opened_at = (existing or {}).get("opened_at") or (existing or {}).get("captured_at") or now
    document = {
        "snapshot_date": snapshot_date, "captured_at": now, "opened_at": opened_at, "as_of": queue.get("as_of"),
        "summary": {key: queue.get(key, 0) for key in ("latest_selected_count", "entry_due_count", "blocked_signal_count")},
        "candidates": candidates, "changes": _candidate_queue_changes(candidates, previous),
    }
    await db.kqs_candidate_queue_snapshots.update_one(
        {"snapshot_date": snapshot_date}, {"$set": document}, upsert=True,
    )
    return {"stored": True, **document}


def _serialize_candidate_queue_snapshot(document: dict) -> dict:
    return {
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
    snapshots = await db.kqs_candidate_queue_snapshots.find(
        {"snapshot_date": {"$gte": cutoff}}, {"_id": 0},
    ).sort("snapshot_date", 1).to_list(period_days + 1)
    reviews = await db.kqs_candidate_reviews.find(
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
    snapshots = await get_db().kqs_candidate_queue_snapshots.find(
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
    snapshots = await get_db().kqs_candidate_queue_snapshots.find(
        {"as_of": {"$ne": None}}, {"_id": 0},
    ).sort("snapshot_date", -1).to_list(366)
    now = datetime.now(timezone.utc)
    stored = 0
    for snapshot in snapshots:
        for candidate in snapshot.get("candidates", []):
            if not candidate.get("symbol_key"):
                continue
            document = await _candidate_outcome_document(snapshot, candidate, now)
            await get_db().kqs_candidate_outcomes.update_one(
                {"as_of": document["as_of"], "symbol_key": document["symbol_key"]}, {"$set": document}, upsert=True,
            )
            stored += 1
    return {"stored_count": stored, "captured_at": now.isoformat()}


async def build_candidate_outcome_report(days: int = 365) -> dict:
    """Return realized outcomes with explicit sample-size and availability limits."""
    period_days = max(1, min(365, int(days)))
    cutoff = _korean_date_days_ago(period_days)
    rows = await get_db().kqs_candidate_outcomes.find(
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
    latest = await get_db().kqs_candidate_queue_snapshots.find_one({}, {"_id": 0}, sort=[("snapshot_date", -1)])
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
        reviews = await get_db().kqs_candidate_reviews.find(
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
        return await asyncio.to_thread(_read_bridge_summary)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/review-candidates")
async def get_kqs_review_candidates(user: dict = Depends(current_user)) -> dict:
    """로그인 사용자에게 최신 후보와 본인의 검토 상태를 제공한다."""
    try:
        return await _candidate_queue_for_user(user["_id"])
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
    documents = await get_db().kqs_candidate_queue_snapshots.find(
        {"snapshot_date": {"$gte": cutoff}}, {"_id": 0},
    ).sort("snapshot_date", 1).to_list(days + 1)
    snapshots = [_serialize_candidate_queue_snapshot(document) for document in documents]
    latest = snapshots[-1] if snapshots else None
    review_summary = {"reviewed_count": 0, "deferred_count": 0, "pending": []}
    if latest and latest.get("as_of") and latest.get("candidates"):
        keys = [candidate["symbol_key"] for candidate in latest["candidates"]]
        review_docs = await get_db().kqs_candidate_reviews.find(
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
    """Return the user's review-SLA queue for the most recent global candidate snapshot."""
    snapshot = await get_db().kqs_candidate_queue_snapshots.find_one({}, {"_id": 0}, sort=[("snapshot_date", -1)])
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
        reviews = await get_db().kqs_candidate_reviews.find(
            {"user_id": user["_id"], "as_of": snapshot["as_of"], "symbol_key": {"$in": keys}},
            {"_id": 0, "user_id": 0},
        ).to_list(len(keys))
    workflow = _build_candidate_review_workflow(snapshot, reviews)
    return {
        "available": True, "snapshot_date": snapshot.get("snapshot_date"), "as_of": snapshot.get("as_of"),
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
    review = await get_db().kqs_candidate_reviews.find_one(
        {"user_id": user["_id"], "as_of": packet["as_of"], "symbol_key": packet["candidate"]["symbol_key"]}, {"_id": 0, "user_id": 0},
    )
    packet["review"] = _serialize_candidate_review(review) if review else None
    return packet


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
    }
    await get_db().kqs_candidate_reviews.update_one(
        {"user_id": user["_id"], "as_of": payload.as_of, "symbol_key": symbol_key},
        {"$set": document, "$setOnInsert": {"created_at": now}}, upsert=True,
    )
    review = _serialize_candidate_review(document)
    # 매번 append-only로 남겨, 같은 후보의 상태·메모가 바뀌어도 당시 근거를 잃지 않는다.
    snapshot_packet = {**packet, "review": review}
    await get_db().kqs_candidate_review_snapshots.insert_one({
        "user_id": user["_id"], "as_of": payload.as_of, "symbol_key": symbol_key,
        "symbol": candidate["symbol"], "name": candidate["name"], "status": payload.status,
        "note": document["note"], "captured_at": now, "packet": snapshot_packet,
    })
    return {"review": review, "snapshot_captured_at": now.isoformat(), "disclaimer": "검토 기록과 당시 검토 패킷을 저장했습니다. 엔진 후보·주문·모의 체결·개인 포트폴리오는 변경하지 않습니다."}


@router.get("/review-candidates/reviews")
async def get_kqs_candidate_review_history(days: int = Query(default=90, ge=1, le=365), user: dict = Depends(current_user)) -> dict:
    """사용자 본인의 후보 검토 감사 이력을 반환한다."""
    cutoff = (datetime.now(timezone.utc).astimezone(KST).date() - timedelta(days=days - 1)).isoformat()
    documents = await get_db().kqs_candidate_reviews.find(
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
    documents = await get_db().kqs_candidate_review_snapshots.find(
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
        return {"stored": False, "reason": "외부 엔진을 확인하지 못해 스냅샷을 저장하지 않았습니다."}
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
    """대조 이력과 각 일자의 전일 대비 변동 경고를 반환한다."""
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
        await db.kqs_candidate_reviews.create_index([("user_id", 1), ("as_of", 1), ("symbol_key", 1)], unique=True)
        await db.kqs_candidate_reviews.create_index([("user_id", 1), ("updated_at", -1)])
        await db.kqs_candidate_review_snapshots.create_index([("user_id", 1), ("captured_at", -1)])
        await db.kqs_candidate_review_snapshots.create_index([("user_id", 1), ("as_of", 1), ("symbol_key", 1)])
        await db.kqs_candidate_queue_snapshots.create_index("snapshot_date", unique=True)
        await db.kqs_candidate_outcomes.create_index([("as_of", 1), ("symbol_key", 1)], unique=True)
        await db.kqs_candidate_outcomes.create_index([("as_of", -1), ("status", 1)])
        await db.kqs_operations_alert_acknowledgements.create_index([("user_id", 1), ("fingerprint", 1)], unique=True)
    except Exception:
        # MongoDB가 잠시 준비되지 않아도 기존 읽기 전용 대조는 요청 시 동작한다.
        return
