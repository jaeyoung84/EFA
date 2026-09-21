"""주문 제안·모의 체결이 실제 보유자산(portfolios)을 절대 건드리지 않는지 확인한다.

가상/LEAN 전략에는 실제 신호가 없으므로 주문 제안을 만들 수 없어야 한다는
안전 규칙도 함께 확인한다.
"""
import asyncio

import pandas as pd

from app.backend.routers import strategy


def _price_series(*, rising: bool) -> pd.Series:
    values = list(range(1, 121))
    if not rising:
        values.reverse()
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=len(values), freq="B"), dtype=float)


def _mock_market(monkeypatch, *, rising: bool = True) -> None:
    monkeypatch.setattr(strategy, "_download_close_series", lambda ticker, period: _price_series(rising=rising))
    monkeypatch.setattr(
        strategy,
        "fetch_quote_cached",
        lambda ticker: {"status": "ok", "price": 100.0, "change_pct": 0.0, "quoted_at": "2026-01-01T00:00:00Z"},
    )


async def test_order_proposal_blocked_for_lean_strategy(client, make_user):
    headers = await make_user("lean-proposal-block@example.com")
    save_resp = await client.post(
        "/api/strategy",
        json={"type": "lean", "name": "LEAN 참고용", "symbol": "samsung"},
        headers=headers,
    )
    strategy_id = save_resp.json()["id"]

    resp = await client.post(f"/api/strategy/{strategy_id}/proposals", headers=headers)
    assert resp.status_code == 422


async def test_order_proposal_blocked_for_virtual_backtest_strategy(client, make_user):
    headers = await make_user("virtual-proposal-block@example.com")
    save_resp = await client.post(
        "/api/strategy",
        json={
            "type": "virtual_backtest",
            "name": "가상 시뮬레이션",
            "params": {"fast_ma": 20, "slow_ma": 60, "n_days": 1260, "volatility": 0.2},
            "snapshot": {"metrics": {"cagr": 0.1, "sharpe": 1.0, "mdd": -0.1, "win_rate": 0.5,
                                      "profit_factor": 1.0, "n_trades": 10, "total_return": 0.2, "bh_return": 0.1}},
        },
        headers=headers,
    )
    strategy_id = save_resp.json()["id"]

    resp = await client.post(f"/api/strategy/{strategy_id}/proposals", headers=headers)
    assert resp.status_code == 422


async def test_approved_fill_does_not_mutate_real_portfolio(client, make_user, monkeypatch):
    _mock_market(monkeypatch)
    headers = await make_user("paper-fill-safety@example.com")

    save_resp = await client.post(
        "/api/strategy",
        json={
            "type": "real_backtest",
            "name": "안전장치 테스트",
            "ticker": "AAPL",
            "params": {
                "fast_ma": 20, "slow_ma": 60, "period": "1y",
                "transaction_cost_bps": 10, "slippage_bps": 5, "benchmark_ticker": "AAPL",
            },
            "snapshot": {"metrics": {"cagr": 0.1, "sharpe": 1.0, "sortino": 1.0, "mdd": -0.1,
                                      "total_return": 0.2, "win_rate": 0.5}},
        },
        headers=headers,
    )
    assert save_resp.status_code == 200
    strategy_id = save_resp.json()["id"]

    proposal_resp = await client.post(f"/api/strategy/{strategy_id}/proposals", headers=headers)
    assert proposal_resp.status_code == 200
    proposal_id = proposal_resp.json()["id"]

    approve_resp = await client.post(
        f"/api/strategy/proposals/{proposal_id}/approve",
        json={"quantity": 1},
        headers=headers,
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["fill_price"] > 0

    # 모의 체결이 실제 보유자산 장부에는 절대 반영되지 않아야 한다.
    portfolio_resp = await client.get("/api/portfolio", headers=headers)
    assert portfolio_resp.status_code == 200
    portfolio = portfolio_resp.json()
    assert portfolio["holdings"] == []
    assert portfolio["cash_krw"] == 0

    fills_resp = await client.get("/api/strategy/fills", headers=headers)
    assert len(fills_resp.json()["items"]) == 1

    # 이미 처리된 제안을 다시 승인하면 안 된다.
    re_approve_resp = await client.post(
        f"/api/strategy/proposals/{proposal_id}/approve",
        json={"quantity": 1},
        headers=headers,
    )
    assert re_approve_resp.status_code == 409


async def test_same_buy_signal_cannot_create_repeated_position(client, make_user, monkeypatch):
    _mock_market(monkeypatch)
    headers = await make_user("paper-repeat-buy@example.com")
    save_resp = await client.post(
        "/api/strategy",
        json={
            "type": "real_backtest", "name": "반복 매수 방지", "ticker": "AAPL",
            "params": {"fast_ma": 20, "slow_ma": 60, "period": "1y", "transaction_cost_bps": 10, "slippage_bps": 5, "benchmark_ticker": "AAPL"},
            "snapshot": {"metrics": {}},
        },
        headers=headers,
    )
    strategy_id = save_resp.json()["id"]
    proposal = await client.post(f"/api/strategy/{strategy_id}/proposals", headers=headers)
    await client.post(
        f"/api/strategy/proposals/{proposal.json()['id']}/approve",
        json={"quantity": 2}, headers=headers,
    )

    repeated = await client.post(f"/api/strategy/{strategy_id}/proposals", headers=headers)
    assert repeated.status_code == 409
    assert "추가 매수" in repeated.json()["detail"]


async def test_sell_signal_requires_paper_position(client, make_user, monkeypatch):
    _mock_market(monkeypatch, rising=False)
    headers = await make_user("paper-naked-sell@example.com")
    save_resp = await client.post(
        "/api/strategy",
        json={
            "type": "real_backtest", "name": "무보유 매도 방지", "ticker": "AAPL",
            "params": {"fast_ma": 20, "slow_ma": 60, "period": "1y", "transaction_cost_bps": 10, "slippage_bps": 5, "benchmark_ticker": "AAPL"},
            "snapshot": {"metrics": {}},
        }, headers=headers,
    )
    response = await client.post(f"/api/strategy/{save_resp.json()['id']}/proposals", headers=headers)
    assert response.status_code == 409
    assert "보유수량" in response.json()["detail"]


async def test_concurrent_approval_creates_only_one_fill(client, make_user, monkeypatch):
    _mock_market(monkeypatch)
    headers = await make_user("paper-concurrent@example.com")
    save_resp = await client.post(
        "/api/strategy",
        json={
            "type": "real_backtest", "name": "중복 승인 방지", "ticker": "AAPL",
            "params": {"fast_ma": 20, "slow_ma": 60, "period": "1y", "transaction_cost_bps": 10, "slippage_bps": 5, "benchmark_ticker": "AAPL"},
            "snapshot": {"metrics": {}},
        }, headers=headers,
    )
    proposal = await client.post(f"/api/strategy/{save_resp.json()['id']}/proposals", headers=headers)
    path = f"/api/strategy/proposals/{proposal.json()['id']}/approve"
    first, second = await asyncio.gather(
        client.post(path, json={"quantity": 1}, headers=headers),
        client.post(path, json={"quantity": 1}, headers=headers),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    fills = await client.get("/api/strategy/fills", headers=headers)
    assert len(fills.json()["items"]) == 1
