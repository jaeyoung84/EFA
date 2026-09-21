import pytest

from app.backend.risk_engine import DEFAULT_RISK_POLICY, evaluate_policy, volatility_target


def test_policy_detects_position_sector_currency_and_risk_breaches():
    composition = {
        "max_position": {"ticker": "AAPL", "weight_pct": 55.0},
        "sector_exposure": {"IT": 70.0}, "currency_exposure": {"USD": 70.0, "KRW": 30.0},
    }
    alerts = evaluate_policy(composition, {"var_95_pct": 4.0, "mdd_pct": 40.0}, DEFAULT_RISK_POLICY)
    assert {alert["code"] for alert in alerts} == {"max_position", "max_sector", "foreign_currency", "var_95", "mdd"}
    target = volatility_target(30.0, DEFAULT_RISK_POLICY)
    assert target["risky_asset_scale"] == 0.5
    assert target["cash_buffer_pct"] == 50.0


@pytest.mark.asyncio
async def test_risk_policy_is_user_specific(client, make_user):
    headers = await make_user("risk-policy@example.com")
    response = await client.put("/api/portfolio/risk-policy", json={"max_position_pct": 20, "max_sector_pct": 30,
        "max_foreign_currency_pct": 40, "max_var_95_pct": 2, "max_mdd_pct": 15, "target_annual_volatility_pct": 10}, headers=headers)
    assert response.status_code == 200
    assert response.json()["max_position_pct"] == 20
    fetched = await client.get("/api/portfolio/risk-policy", headers=headers)
    assert fetched.json()["target_annual_volatility_pct"] == 10
