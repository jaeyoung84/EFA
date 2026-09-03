"""주문 제안·모의 체결이 실제 보유자산(portfolios)을 절대 건드리지 않는지 확인한다.

가상/LEAN 전략에는 실제 신호가 없으므로 주문 제안을 만들 수 없어야 한다는
안전 규칙도 함께 확인한다.
"""


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


async def test_approved_fill_does_not_mutate_real_portfolio(client, make_user):
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
