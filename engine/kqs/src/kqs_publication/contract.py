"""KQS 엔진 추천을 읽기 전용으로 검증하는 v1 계약.

이 모듈은 주문·체결·포트폴리오를 전혀 만들지 않는다. 엔진이 만든 추천과 그 시점의
근거만 검증·축약해 홈페이지가 안전하게 표시할 수 있게 한다.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from .calendar import CalendarError, validate_publication_calendar


RECOMMENDATION_SCHEMA_VERSION = "kqs-recommendations/v1"
MAX_SELECTED_ENTRIES = 3


class KqsContractError(ValueError):
    """엔진 응답이 추천 계약 또는 최신성 조건을 만족하지 않을 때 사용한다."""


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise KqsContractError(f"{field} 값이 없습니다.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KqsContractError(f"{field}는 ISO-8601 시각이어야 합니다.") from exc
    if parsed.tzinfo is None:
        raise KqsContractError(f"{field}에 시간대가 필요합니다.")
    return parsed.astimezone(timezone.utc)


def _required_text(value: object, field: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KqsContractError(f"{field} 값이 없습니다.")
    text = value.strip()
    if len(text) > maximum:
        raise KqsContractError(f"{field} 값이 너무 깁니다.")
    return text


def canonical_payload_digest(payload: Mapping[str, Any]) -> str:
    """integrity 필드를 제외한 계약 본문의 재현 가능한 SHA-256을 계산한다."""
    signed = {key: value for key, value in payload.items() if key != "integrity"}
    encoded = json.dumps(signed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_recommendation_payload(payload: object, *, now: datetime | None = None) -> dict:
    """엔진의 추천 payload를 검증하고 화면에 필요한 안전한 필드만 반환한다.

    무결성/만료/미래 생성/게시 정책 오류를 거부한다. legacy는 dirty를 거부하고
    통합 엔진은 실제 구성요소의 코드 해시로 검증한다. 추천은 정보 표시와
    사용자의 수동 판단을 위한 것이며, 이 함수의 반환값으로 주문을 만들 수 없다.
    """
    if not isinstance(payload, Mapping):
        raise KqsContractError("추천 응답은 JSON 객체여야 합니다.")
    if payload.get("schema_version") != RECOMMENDATION_SCHEMA_VERSION:
        raise KqsContractError("지원하지 않는 추천 계약 버전입니다.")

    integrity = payload.get("integrity")
    if not isinstance(integrity, Mapping):
        raise KqsContractError("추천 무결성 정보가 없습니다.")
    declared_digest = _required_text(integrity.get("payload_sha256"), "integrity.payload_sha256", 64)
    if declared_digest != canonical_payload_digest(payload):
        raise KqsContractError("추천 payload 무결성 해시가 일치하지 않습니다.")

    generated_at = _parse_datetime(payload.get("generated_at"), "generated_at")
    expires_at = _parse_datetime(payload.get("expires_at"), "expires_at")
    reference_now = now or datetime.now(timezone.utc)
    reference_now = reference_now.replace(tzinfo=timezone.utc) if reference_now.tzinfo is None else reference_now.astimezone(timezone.utc)
    if generated_at > reference_now + timedelta(minutes=5):
        raise KqsContractError("미래에 생성된 추천은 사용할 수 없습니다.")
    if expires_at <= generated_at:
        raise KqsContractError("expires_at은 generated_at 이후여야 합니다.")
    if expires_at <= reference_now:
        raise KqsContractError("만료된 엔진 추천은 사용할 수 없습니다.")

    engine = payload.get("engine")
    if not isinstance(engine, Mapping):
        raise KqsContractError("엔진 메타데이터가 없습니다.")
    native = engine.get("trust_mode") == "component_sha256"
    if not native and engine.get("dirty") is not False:
        raise KqsContractError("dirty 상태 엔진의 추천은 사용할 수 없습니다.")
    safe_engine = {
        "engine_id": _required_text(engine.get("engine_id"), "engine.engine_id", 100),
        "run_id": _required_text(engine.get("run_id"), "engine.run_id", 160),
        "git_sha": _required_text(engine.get("git_sha"), "engine.git_sha", 80),
        "data_as_of": _required_text(engine.get("data_as_of"), "engine.data_as_of", 32),
        "dataset_hash": _required_text(engine.get("dataset_hash"), "engine.dataset_hash", 128),
        "dirty": engine.get("dirty") if native else False,
    }

    try:
        data_date = date.fromisoformat(safe_engine["data_as_of"])
        if data_date.isoformat() != safe_engine["data_as_of"]:
            raise ValueError("noncanonical date")
    except ValueError as exc:
        raise KqsContractError("engine.data_as_of는 YYYY-MM-DD여야 합니다.") from exc
    publication = None
    if native:
        if safe_engine["engine_id"] != "iv-kqs-engine":
            raise KqsContractError("component 검증은 통합 엔진에만 허용됩니다.")
        for key in ("code_sha256", "config_sha256"):
            value = engine.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise KqsContractError(f"engine.{key} 지문이 올바르지 않습니다.")
            safe_engine[key] = value
        components = engine.get("component_sha256")
        if not isinstance(components, dict) or not 1 <= len(components) <= 64:
            raise KqsContractError("실행 구성요소 해시가 없습니다.")
        if any(not isinstance(key, str) or not isinstance(value, str) or
               not re.fullmatch(r"[0-9a-f]{64}", value) for key, value in components.items()):
            raise KqsContractError("실행 구성요소 해시가 올바르지 않습니다.")
        component_digest = hashlib.sha256(json.dumps(components, ensure_ascii=False, sort_keys=True,
                                                     separators=(",", ":")).encode()).hexdigest()
        if component_digest != safe_engine["code_sha256"]:
            raise KqsContractError("실행 구성요소 코드 지문이 일치하지 않습니다.")
        safe_engine.update(trust_mode="component_sha256", component_sha256=dict(components),
                           package_version=_required_text(engine.get("package_version"), "engine.package_version", 80),
                           source_commit=safe_engine["git_sha"])
        publication = payload.get("publication")
        if not isinstance(publication, Mapping) or publication.get("policy") != "explicit-session-expiry/v1":
            raise KqsContractError("고정 게시 정책이 없습니다.")
        if publication.get("expected_as_of") != safe_engine["data_as_of"]:
            raise KqsContractError("예상 자료 기준일이 일치하지 않습니다.")
        snapshot_id = _required_text(publication.get("snapshot_id"), "publication.snapshot_id", 160)
        if snapshot_id != "kqs-" + safe_engine["run_id"]:
            raise KqsContractError("게시 snapshot_id가 실행 ID와 일치하지 않습니다.")
        try:
            calendar = validate_publication_calendar(publication.get("calendar"), data_as_of=data_date,
                                                      generated_at=generated_at, expires_at=expires_at)
        except CalendarError as exc:
            raise KqsContractError(f"KRX 거래일/공표 정책 검증 실패: {exc}") from exc
        publication = {"snapshot_id": snapshot_id, "expected_as_of": safe_engine["data_as_of"],
                       "policy": "explicit-session-expiry/v1", "calendar": calendar}
    raw_signals = payload.get("signals")
    if not isinstance(raw_signals, list):
        raise KqsContractError("signals는 배열이어야 합니다.")
    if len(raw_signals) > 100:
        raise KqsContractError("signals가 계약상 최대 크기를 넘었습니다.")

    signals: list[dict] = []
    selected_entries: list[dict] = []
    signal_ids: set[str] = set()
    entry_symbols: set[str] = set()
    entry_ranks: set[int] = set()
    for item in raw_signals:
        if not isinstance(item, Mapping):
            raise KqsContractError("signals 항목은 객체여야 합니다.")
        signal_id = _required_text(item.get("signal_id"), "signal_id", 160)
        if signal_id in signal_ids:
            raise KqsContractError("signal_id가 중복되었습니다.")
        signal_ids.add(signal_id)
        action = item.get("action")
        side = item.get("side")
        if action not in {"entry", "exit"} or side not in {"buy", "sell"}:
            raise KqsContractError("signal action/side 조합이 올바르지 않습니다.")
        if (action, side) not in {("entry", "buy"), ("exit", "sell")}:
            raise KqsContractError("진입은 buy, 청산은 sell 신호만 허용합니다.")

        reasons = item.get("reasons")
        if not isinstance(reasons, list) or not reasons or len(reasons) > 10:
            raise KqsContractError("각 신호에는 1~10개의 설명 근거가 필요합니다.")
        safe_reasons = []
        for reason in reasons:
            if not isinstance(reason, Mapping):
                raise KqsContractError("설명 근거 형식이 올바르지 않습니다.")
            safe_reasons.append({
                "code": _required_text(reason.get("code"), "reason.code", 80),
                "label": _required_text(reason.get("label"), "reason.label", 160),
                "evidence": _required_text(reason.get("evidence"), "reason.evidence", 500),
            })
        safe = {
            "signal_id": signal_id,
            "action": action,
            "side": side,
            "symbol": _required_text(item.get("symbol"), "symbol", 40).upper(),
            "market": _required_text(item.get("market"), "market", 40).upper(),
            "name": _required_text(item.get("name"), "name", 160),
            "signal_type": _required_text(item.get("signal_type"), "signal_type", 80),
            "selection_outcome": _required_text(item.get("selection_outcome"), "selection_outcome", 80),
            "execution_session": _required_text(item.get("execution_session"), "execution_session", 80),
            "config_fingerprint": _required_text(item.get("config_fingerprint"), "config_fingerprint", 160),
            "audit_row_hash": _required_text(item.get("audit_row_hash"), "audit_row_hash", 128),
            "reasons": safe_reasons,
        }
        rank = item.get("selection_rank")
        if action == "entry" and safe["selection_outcome"] == "selected":
            if type(rank) is not int or not 1 <= rank <= MAX_SELECTED_ENTRIES:
                raise KqsContractError("선정된 진입 신호의 selection_rank는 1~3 정수여야 합니다.")
            if safe["symbol"] in entry_symbols:
                raise KqsContractError("선정 진입 신호에 같은 종목이 중복되었습니다.")
            if rank in entry_ranks:
                raise KqsContractError("선정 순위가 중복되었습니다.")
            entry_ranks.add(rank)
            entry_symbols.add(safe["symbol"])
            safe["selection_rank"] = rank
            selected_entries.append(safe)
        else:
            safe["selection_rank"] = rank if isinstance(rank, int) else None
        if native:
            if safe["config_fingerprint"] != safe_engine["config_sha256"]:
                raise KqsContractError("신호 설정 지문이 실행 설정과 일치하지 않습니다.")
            features = item.get("features")
            if not isinstance(features, dict) or not 1 <= len(features) <= 32:
                raise KqsContractError("추천 시점 특징 값이 없습니다.")
            if any(not isinstance(key, str) or len(key) > 80 or type(value) not in (int, float)
                   or not math.isfinite(value) for key, value in features.items()):
                raise KqsContractError("추천 특징은 유효한 지표 값이어야 합니다.")
            safe["features"] = dict(features)
            for key in ("exit_reference", "selection_note"):
                safe[key] = _required_text(item.get(key), key, 500)
        signals.append(safe)
    if len(selected_entries) > MAX_SELECTED_ENTRIES:
        raise KqsContractError("하루 선정 진입 신호는 최대 3개여야 합니다.")

    if entry_ranks != set(range(1, len(selected_entries) + 1)):
        raise KqsContractError("선정 순위는1부터 연속되어야 합니다.")
    result = {
        "schema_version": RECOMMENDATION_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "engine": safe_engine,
        "signals": signals,
        "selected_entry_count": len(selected_entries),
        "read_only": True,
        "disclaimer": "엔진 추천과 근거를 표시하기 위한 읽기 전용 데이터입니다. 홈페이지는 자동 모의체결·실거래 주문을 만들지 않습니다.",
    }

    if publication is not None:
        result.update(publication=publication, snapshot_id=publication["snapshot_id"],
                      source="snapshot", status="recommendations" if selected_entries else "no_recommendations",
                      source_payload_sha256=declared_digest)
    return result
