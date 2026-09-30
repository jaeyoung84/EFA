from datetime import datetime, timedelta, timezone

import pytest

from app.backend.kqs_contract import (
    KqsContractError,
    RECOMMENDATION_SCHEMA_VERSION,
    canonical_payload_digest,
    validate_recommendation_payload,
)


def _payload(*, entries=1):
    generated_at = datetime(2026, 9, 28, 8, tzinfo=timezone.utc)
    signals = []
    for rank in range(1, entries + 1):
        signals.append({
            "signal_id": f"run-1-entry-{rank}", "action": "entry", "side": "buy",
            "symbol": f"00000{rank}", "market": "KOSPI", "name": f"테스트{rank}",
            "signal_type": "swing", "selection_rank": rank, "selection_outcome": "selected",
            "execution_session": "next_trading_session", "config_fingerprint": "config-v1",
            "audit_row_hash": f"audit-{rank}",
            "reasons": [{"code": "trend", "label": "추세 조건 충족", "evidence": "종가가 전략의 진입 조건을 충족"}],
        })
    payload = {
        "schema_version": RECOMMENDATION_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "expires_at": (generated_at + timedelta(hours=24)).isoformat(),
        "engine": {"engine_id": "korea-quant-system", "run_id": "run-1", "git_sha": "abc1234", "dirty": False,
                   "data_as_of": "2026-09-28", "dataset_hash": "dataset-v1"},
        "signals": signals,
        "integrity": {"payload_sha256": ""},
    }
    payload["integrity"]["payload_sha256"] = canonical_payload_digest(payload)
    return payload


def test_contract_accepts_zero_to_three_selected_entries_with_engine_reasons():
    payload = _payload(entries=3)
    result = validate_recommendation_payload(payload, now=datetime(2026, 9, 28, 9, tzinfo=timezone.utc))

    assert result["read_only"] is True
    assert result["selected_entry_count"] == 3
    assert result["signals"][0]["reasons"][0]["code"] == "trend"


def test_contract_rejects_more_than_three_selected_entries():
    payload = _payload(entries=4)
    payload["integrity"]["payload_sha256"] = canonical_payload_digest(payload)

    with pytest.raises(KqsContractError, match="selection_rank"):
        validate_recommendation_payload(payload, now=datetime(2026, 9, 28, 9, tzinfo=timezone.utc))


def test_contract_rejects_dirty_engine_expired_or_tampered_payload():
    payload = _payload()
    payload["engine"]["dirty"] = True
    payload["integrity"]["payload_sha256"] = canonical_payload_digest(payload)
    with pytest.raises(KqsContractError, match="dirty"):
        validate_recommendation_payload(payload, now=datetime(2026, 9, 28, 9, tzinfo=timezone.utc))

    payload = _payload()
    payload["signals"][0]["name"] = "위변조"
    with pytest.raises(KqsContractError, match="무결성"):
        validate_recommendation_payload(payload, now=datetime(2026, 9, 28, 9, tzinfo=timezone.utc))
