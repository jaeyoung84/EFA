import pytest


def _payload(name: str, sharpe: float, cagr: float, mdd: float, previous_version_id: str = "") -> dict:
    return {
        "type": "real_backtest", "name": name, "ticker": "005930.KS",
        "params": {
            "fast_ma": 10, "slow_ma": 60, "period": "2y",
            "transaction_cost_bps": 10, "slippage_bps": 5, "benchmark_ticker": "^KS11",
        },
        "snapshot": {
            "metrics": {"total_return": cagr * 2, "cagr": cagr, "sharpe": sharpe, "mdd": mdd},
            "benchmark_validation": {"beta": 0.8},
            "strategy_return_series": [{"date": f"2026-01-{day:02d}", "return": (day % 4 - 1.5) / 100} for day in range(1, 25)],
        },
        "previous_version_id": previous_version_id,
    }


@pytest.mark.asyncio
async def test_operator_can_approve_and_revoke_candidate_without_orders(client, make_user, monkeypatch):
    monkeypatch.setenv("EXECUTION_OPERATOR_EMAILS", "operator@example.com")
    headers = await make_user("operator@example.com")
    parent = await client.post("/api/strategy", json=_payload("기준 버전", 1.0, 0.10, -0.20), headers=headers)
    assert parent.status_code == 200
    child = await client.post("/api/strategy", json=_payload("개선 버전", 1.2, 0.15, -0.15, parent.json()["id"]), headers=headers)
    assert child.status_code == 200
    strategy_id = child.json()["id"]

    before = await client.get(f"/api/strategy/strategy-version-promotion/{strategy_id}", headers=headers)
    assert before.status_code == 200
    assert before.json()["eligible_for_approval"] is True
    assert before.json()["can_manage"] is True
    assert before.json()["approved_strategy_id"] is None

    approved = await client.post(
        f"/api/strategy/strategy-version-promotion/{strategy_id}/approve", json={"note": "비용 포함 성과 검토 완료"}, headers=headers,
    )
    assert approved.status_code == 200
    result = approved.json()
    assert result["is_current_approved"] is True
    assert result["approved_strategy_id"] == strategy_id
    assert result["approval_fingerprint_matches"] is True
    assert len(result["history"]) == 1
    assert result["history"][0]["action"] == "approved"
    assert len(result["history"][0]["strategy_fingerprint"]) == 64
    assert result["creates_order_authority"] is False
    assert result["changes_strategy_allocation"] is False
    assert (await client.get("/api/strategy/fills", headers=headers)).json()["items"] == []

    revoked = await client.post(
        f"/api/strategy/strategy-version-promotion/{strategy_id}/revoke", json={"note": "재검토"}, headers=headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["approved_strategy_id"] is None
    assert [event["action"] for event in revoked.json()["history"]] == ["revoked", "approved"]
    audit = await client.get("/api/strategy/execution-audit", headers=headers)
    assert audit.json()["verification"]["valid"] is True
    assert {item["action"] for item in audit.json()["items"]} >= {"strategy_version_approved", "strategy_version_revoked"}


@pytest.mark.asyncio
async def test_promotion_rejects_non_operator_and_non_candidate(client, make_user, monkeypatch):
    monkeypatch.setenv("EXECUTION_OPERATOR_EMAILS", "operator@example.com")
    headers = await make_user("not-operator@example.com")
    parent = await client.post("/api/strategy", json=_payload("기준", 1.2, 0.15, -0.15), headers=headers)
    child = await client.post("/api/strategy", json=_payload("악화", 0.8, 0.08, -0.25, parent.json()["id"]), headers=headers)
    response = await client.post(
        f"/api/strategy/strategy-version-promotion/{child.json()['id']}/approve", json={}, headers=headers,
    )
    assert response.status_code == 403

    operator = await make_user("operator@example.com")
    operator_parent = await client.post("/api/strategy", json=_payload("운영 기준", 1.2, 0.15, -0.15), headers=operator)
    operator_child = await client.post("/api/strategy", json=_payload("운영 악화", 0.8, 0.08, -0.25, operator_parent.json()["id"]), headers=operator)
    rejected = await client.post(
        f"/api/strategy/strategy-version-promotion/{operator_child.json()['id']}/approve", json={}, headers=operator,
    )
    assert rejected.status_code == 422
