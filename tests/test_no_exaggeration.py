"""시세 조회 실패 시 평가·리스크 수치를 꾸며내지 않고 명시적으로 제외하는지 확인한다.

실제 Yahoo Finance 조회를 사용하므로(모킹하지 않음) 네트워크가 필요하고 느릴 수 있다.
"""

BOGUS_TICKER = "ZZZZINVALIDTICKER"


async def test_valuation_excludes_unpriceable_ticker_instead_of_faking_it(client, make_user):
    headers = await make_user("no-exaggerate-valuation@example.com")
    await client.put(
        "/api/portfolio",
        json={
            "cash_krw": 100_000,
            "holdings": [
                {"ticker": BOGUS_TICKER, "name": "존재하지 않는 종목", "quantity": 1, "average_price": 1000, "currency": "KRW"},
            ],
        },
        headers=headers,
    )

    resp = await client.get("/api/portfolio/valuation", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["value_krw"] is None
    assert data["profit_krw"] is None
    assert len(data["excluded"]) == 1
    assert data["excluded"][0]["ticker"] == BOGUS_TICKER


async def test_risk_reports_unavailable_when_only_holding_has_no_price_history(client, make_user):
    headers = await make_user("no-exaggerate-risk@example.com")
    await client.put(
        "/api/portfolio",
        json={
            "cash_krw": 0,
            "holdings": [
                {"ticker": BOGUS_TICKER, "name": "존재하지 않는 종목", "quantity": 1, "average_price": 1000, "currency": "KRW"},
            ],
        },
        headers=headers,
    )

    resp = await client.get("/api/portfolio/risk?period=1y", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert "var_95_pct" not in data
