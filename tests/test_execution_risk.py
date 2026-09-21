import pandas as pd
import pytest

from app.backend.execution_risk import limit_buy_quantity
from app.backend.risk_engine import DEFAULT_RISK_POLICY
from app.backend.routers import strategy


def test_buy_quantity_is_reduced_to_position_limit():
    result = limit_buy_quantity(
        requested_quantity=5, unit_notional_krw=100, cash_krw=1_000, positions=[], ticker="005930.KS",
        currency="KRW", sector="반도체", policy={**DEFAULT_RISK_POLICY, "max_position_pct": 20},
    )
    assert result["action"] == "reduced"
    assert result["approved_quantity"] == pytest.approx(2)


def test_buy_is_blocked_when_existing_paper_book_already_breaks_policy():
    result = limit_buy_quantity(
        requested_quantity=1, unit_notional_krw=100, cash_krw=100,
        positions=[{"ticker": "005930.KS", "currency": "KRW", "sector": "반도체", "value_krw": 900}],
        ticker="005380.KS", currency="KRW", sector="자동차", policy={**DEFAULT_RISK_POLICY, "max_position_pct": 50},
    )
    assert result["action"] == "blocked"
    assert result["approved_quantity"] == 0


def _mock_market(monkeypatch):
    close = pd.Series(range(1, 121), index=pd.date_range("2025-01-01", periods=120, freq="B"), dtype=float)
    monkeypatch.setattr(strategy, "_download_close_series", lambda ticker, period: close)
    monkeypatch.setattr(strategy, "fetch_quote_cached", lambda ticker: {
        "status": "ok", "price": 100.0, "change_pct": 0.0, "quoted_at": "2026-01-01T00:00:00Z",
    })


@pytest.mark.asyncio
async def test_approval_auto_reduces_by_user_risk_policy(client, make_user, monkeypatch):
    _mock_market(monkeypatch)
    headers = await make_user("execution-risk@example.com")
    await client.put("/api/portfolio/risk-policy", json={
        "max_position_pct": 20, "max_sector_pct": 100, "max_foreign_currency_pct": 100,
        "max_var_95_pct": 100, "max_mdd_pct": 100, "target_annual_volatility_pct": 15,
    }, headers=headers)
    account = await client.put("/api/strategy/paper-account", json={"initial_capital_krw": 1_000}, headers=headers)
    assert account.status_code == 200
    saved = await client.post("/api/strategy", json={
        "type": "real_backtest", "name": "위험 통제", "ticker": "005930.KS",
        "params": {"fast_ma": 20, "slow_ma": 60, "period": "1y", "transaction_cost_bps": 10, "slippage_bps": 5, "benchmark_ticker": "^KS11"},
        "snapshot": {"metrics": {}},
    }, headers=headers)
    proposal = await client.post(f"/api/strategy/{saved.json()['id']}/proposals", headers=headers)
    approved = await client.post(f"/api/strategy/proposals/{proposal.json()['id']}/approve", json={"quantity": 5}, headers=headers)
    assert approved.status_code == 200
    # 위험 한도는 모의 체결가(시장가 슬리피지 포함)로 평가한다. 따라서 기준가
    # 100원에서 5bp가 더해진 100.05원으로 20% 집중도 한도를 계산한다.
    assert approved.json()["quantity"] == pytest.approx(200 / 100.05)
    assert approved.json()["requested_quantity"] == 5
    assert approved.json()["risk_control"]["action"] == "reduced"
