"""Build a fixed v2 publication (EINT-017: three items, up to 5 each) from a recommendation-only run."""

import hashlib
import json
import re
from datetime import date, datetime

from .calendar import calendar_for_session
from .contract import (
    MAX_SELECTED_PER_ITEM, RECOMMENDATION_ITEMS, RECOMMENDATION_SCHEMA_V2, KqsContractError,
    canonical_payload_digest, validate_recommendation_payload,
)

REASON_CODES = {
    "swing": [("bollinger_lower", "볼린저 하단 조건"), ("rsi_oversold", "RSI 과매도"),
              ("market_cap_proxy", "시총 proxy 범위"), ("liquidity20", "20일 거래대금"), ("bearish_regime200", "시장 레짐")],
    "long_term": [("rsi_recovery", "RSI 회복"), ("obv_confirmation", "OBV 상승 확인"),
                  ("market_cap_proxy", "시총 proxy 범위"), ("liquidity20", "20일 거래대금"), ("bearish_regime200", "시장 레짐")],
    "pullback_v4": [("weekly_trend_ema", "상승 추세"), ("ema10_pullback", "EMA10 눌림 회복"),
                    ("momentum_band", "RSI·ADX·MACD 조건"), ("liquidity20", "20일 거래대금"),
                    ("bullish_regime50", "시장 50일선 위")],
}


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(value: object) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def validate_run(runtime: dict) -> None:
    if runtime.get("schema") != "kqs-manual-recommendations/v2" or runtime.get("orders_enabled") is not False:
        raise KqsContractError("추천 전용 실행 결과만 게시할 수 있습니다.")
    if runtime.get("usage") != "manual_decision_only" or runtime.get("excluded_symbols") != []:
        raise KqsContractError("전역 게시에는 사용자별 제외 문맥을 사용할 수 없습니다.")
    engine = runtime["engine"]
    identity = {"protocol": runtime["schema"], "as_of": runtime["as_of"],
                "code_sha256": engine["code_sha256"], "config_sha256": runtime["config_sha256"],
                "input_sha256": runtime["input_sha256"], "halted_rows_sha256": runtime.get("halted_rows_sha256"),
                "excluded_symbols": [], "tracked": runtime.get("tracked", []),
                "dependencies": engine["dependencies"], "python": engine["python"]}
    for key in ("run_id", "input_sha256", "config_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", runtime[key]):
            raise KqsContractError("실행 지문 형식이 올바르지 않습니다.")
    if sha256(identity) != runtime["run_id"] or sha256(runtime["config"]) != runtime["config_sha256"]:
        raise KqsContractError("실행/설정 지문이 일치하지 않습니다.")
    if runtime["config"].get("max_picks_per_item") != MAX_SELECTED_PER_ITEM:
        raise KqsContractError("아이템별 최대 5개 추천 규칙이 아닙니다.")
    for item in RECOMMENDATION_ITEMS:
        if sum(1 for row in runtime["recommendations"] if row["signal_type"] == item) > MAX_SELECTED_PER_ITEM:
            raise KqsContractError("아이템별 최대 5개 추천 규칙을 위반했습니다.")
    selected = {row["candidate_id"]: row for row in runtime["audit"] if row["selection_outcome"] == "selected"}
    if len(selected) != len(runtime["recommendations"]):
        raise KqsContractError("선정 감사와 추천 개수가 일치하지 않습니다.")
    for row in runtime["recommendations"]:
        if row["candidate_id"] != sha256({"run_id": runtime["run_id"], "symbol": row["symbol"],
                                         "signal_type": row["signal_type"]}):
            raise KqsContractError("추천 식별자가 일치하지 않습니다.")
        audit = selected.get(row["candidate_id"])
        if audit is None or any(row.get(key) != value for key, value in audit.items()):
            raise KqsContractError("추천과 원래 선정 감사 값이 다릅니다.")


def build_payload(runtime: dict, *, generated_at: datetime, expires_at: datetime,
                  expected_as_of: str) -> dict:
    validate_run(runtime)
    if runtime["as_of"] != expected_as_of:
        raise KqsContractError("예상 자료 기준일과 실제 엔진 기준일이 다릅니다.")
    data_date = date.fromisoformat(expected_as_of)
    calendar = calendar_for_session(data_date)
    source = runtime["engine"]
    signals = []
    audits = {row["candidate_id"]: row for row in runtime["audit"]}
    ranks = {item: 0 for item in RECOMMENDATION_ITEMS}
    for row in runtime["recommendations"]:
        codes = REASON_CODES.get(row["signal_type"])
        if codes is None:
            raise KqsContractError("승인되지 않은 추천 전략입니다.")
        if len(row["reasons"]) != len(codes):
            raise KqsContractError("계산된 추천 근거가 완전하지 않습니다.")
        ranks[row["signal_type"]] += 1
        signals.append({"signal_id": row["candidate_id"], "action": "entry", "side": "buy",
                        "symbol": row["symbol"], "market": row["market"], "name": row["name"],
                        "signal_type": row["signal_type"], "selection_rank": ranks[row["signal_type"]],
                        "selection_outcome": "selected", "execution_session": "next_trading_session",
                        "config_fingerprint": runtime["config_sha256"],
                        "audit_row_hash": sha256(audits[row["candidate_id"]]),
                        "features": row["features"], "exit_reference": row["exit_reference"],
                        "selection_note": row["selection_note"],
                        "reasons": [{"code": code, "label": label, "evidence": evidence}
                                    for (code, label), evidence in zip(codes, row["reasons"])]})
    payload = {
        "schema_version": RECOMMENDATION_SCHEMA_V2, "generated_at": generated_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "publication": {"snapshot_id": "kqs-" + runtime["run_id"], "expected_as_of": expected_as_of,
                        "policy": "explicit-session-expiry/v1", "calendar": calendar.public_metadata()},
        "engine": {"engine_id": "iv-kqs-engine", "run_id": runtime["run_id"],
                   "git_sha": source["source_commit"], "dirty": None, "trust_mode": "component_sha256",
                   "data_as_of": runtime["as_of"], "dataset_hash": runtime["input_sha256"],
                   "code_sha256": source["code_sha256"], "component_sha256": source["component_sha256"],
                   "config_sha256": runtime["config_sha256"], "package_version": source["package_version"]},
        "signals": signals, "exit_monitor": runtime.get("exit_monitor", []), "integrity": {},
    }
    payload["integrity"]["payload_sha256"] = canonical_payload_digest(payload)
    validate_recommendation_payload(payload, now=generated_at)
    return payload
