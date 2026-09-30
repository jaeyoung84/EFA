"""Website reads publications only: no engine import, market DB, regeneration or writes."""

import os
import sqlite3
from pathlib import Path

try:
    from kqs_publication.contract import KqsContractError, validate_recommendation_payload
    from kqs_publication.store import read_latest
except ModuleNotFoundError as exc:
    if exc.name != "kqs_publication":
        raise
    from engine.kqs.src.kqs_publication.contract import KqsContractError, validate_recommendation_payload
    from engine.kqs.src.kqs_publication.store import read_latest


def snapshot_path() -> Path:
    configured = os.getenv("KQS_SNAPSHOT_DB", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parents[2] / "state/kqs/recommendations.sqlite3"


def read_local_recommendations() -> dict:
    try:
        payload = read_latest(snapshot_path())
        return validate_recommendation_payload(payload)
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        raise RuntimeError(f"게시된 KQS 추천을 사용할 수 없습니다: {exc}") from exc

def recommendation_source() -> str:
    """One explicit source policy for every current KQS website view."""
    source = os.getenv("KQS_RECOMMENDATION_SOURCE", "snapshot").strip().lower()
    if source not in {"snapshot", "bridge"}:
        raise RuntimeError("KQS_RECOMMENDATION_SOURCE는 snapshot 또는 bridge여야 합니다.")
    return source


def publication_trace(payload: dict) -> dict:
    return {
        "source": "snapshot", "snapshot_id": payload["snapshot_id"],
        "as_of": payload["engine"]["data_as_of"],
        "generated_at": payload["generated_at"], "expires_at": payload["expires_at"],
        "engine": dict(payload["engine"]), "publication": dict(payload["publication"]),
    }


def publication_status(payload: dict) -> dict:
    """Publication availability is NOT proof of worker/timer/process health."""
    return {
        **publication_trace(payload), "available": True, "read_only": True,
        "status": "recommendations" if payload["selected_entry_count"] else "no_recommendations",
        "selected_entry_count": payload["selected_entry_count"],
        "market_data": {"latest_trade_date": payload["engine"]["data_as_of"]},
        "operations": {"verified": False, "state": "not_observed"},
        "auto_positions": {"applicability": "not_applicable"},
        "disclaimer": "검증된 통합 엔진 추천 게시 상태입니다. 추천 0건도 정상이며, 이 조회는 수집 작업·타이머·프로세스의 현재 건강을 증명하지 않습니다. 주문·자동 모의체결은 없습니다.",
    }


def _publication_candidate(signal: dict) -> dict:
    features = signal.get("features", {})
    return {
        "signal_id": signal["signal_id"], "symbol": signal["symbol"],
        "symbol_key": signal["symbol"].split(".", 1)[0].upper(),
        "name": signal.get("name", ""), "market": signal["market"],
        "signal_type": signal["signal_type"], "execution_timing": signal["execution_session"],
        "selection_rank": signal["selection_rank"],
        "market_cap_rank": features.get("market_cap_rank"),
        "average_trading_value": features.get("average_trading_value20"),
        "reasons": [dict(reason) for reason in signal["reasons"]],
    }


def publication_candidates(payload: dict) -> dict:
    selected = [signal for signal in payload["signals"]
                if signal["action"] == "entry" and signal["selection_outcome"] == "selected"]
    return {
        **publication_trace(payload), "available": True, "read_only": True,
        "latest_selected_count": len(selected), "entry_due_count": len(selected),
        # The public contract contains selected signals only, not scan-wide rejection counts.
        "blocked_signal_count": None,
        "candidates": [_publication_candidate(signal) for signal in selected],
        "disclaimer": "추천 근거 화면과 같은 유효한 게시 스냅샷의 진입 후보입니다. 선정 순서는 수익 예측 순위가 아닙니다. 매수 여부는 사용자가 판단하며 자동 체결은 없습니다. 전체 스캔의 레짐 차단 건수는 이 게시 계약에 없습니다.",
    }


def publication_packet(payload: dict, symbol: str) -> dict:
    key = str(symbol).strip().upper().split(".", 1)[0]
    signal = next((item for item in payload["signals"]
                   if item["action"] == "entry" and item["selection_outcome"] == "selected"
                   and item["symbol"].upper().split(".", 1)[0] == key), None)
    if signal is None:
        return {**publication_trace(payload), "available": True, "read_only": True, "found": False}
    features = dict(signal.get("features", {}))
    return {
        **publication_trace(payload), "available": True, "found": True, "read_only": True,
        "packet_version": "kqs-candidate-review-v2", "candidate": _publication_candidate(signal),
        "selection_evidence": {
            "outcome": signal["selection_outcome"], "market_cap_rank": features.get("market_cap_rank"),
            "average_trading_value": features.get("average_trading_value20"),
            "reasons": [dict(reason) for reason in signal["reasons"]], "features": features,
            "exit_reference": signal.get("exit_reference"), "selection_note": signal.get("selection_note"),
        },
        "market_context": {"db_latest_trade_date": payload["engine"]["data_as_of"]},
        "engine_context": dict(payload["engine"]),
        "disclaimer": "게시 당시 실제 지표·추천 이유와 버전·기준일·만료를 그대로 표시합니다. 시세·목표가를 새로 추정하지 않으며, 주문·자동 체결·엔진 원장 변경은 없습니다.",
    }


def native_positions_not_applicable() -> dict:
    return {
        "source": "snapshot", "available": False, "read_only": True,
        "applicability": "not_applicable", "positions": [],
        "reason": "통합 엔진은 추천만 게시하므로 자동 모의 포지션 대조는 미사용입니다. 보유 및 매수·매도 기록은 개인 수동 판단 원장에서 확인하세요. 예전 엔진 기록은 별도로 보존됩니다.",
    }
