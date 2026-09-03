"""사용자 간 포트폴리오·전략 데이터가 서로 보이거나 조작되지 않는지 확인한다."""


async def test_portfolio_isolated_between_users(client, make_user):
    headers_a = await make_user("iso-portfolio-a@example.com")
    headers_b = await make_user("iso-portfolio-b@example.com")

    put_resp = await client.put(
        "/api/portfolio",
        json={"cash_krw": 1_000_000, "holdings": []},
        headers=headers_a,
    )
    assert put_resp.status_code == 200

    resp_b = await client.get("/api/portfolio", headers=headers_b)
    assert resp_b.status_code == 200
    assert resp_b.json()["cash_krw"] == 0

    resp_a = await client.get("/api/portfolio", headers=headers_a)
    assert resp_a.json()["cash_krw"] == 1_000_000


async def test_strategy_not_visible_to_other_user(client, make_user):
    headers_a = await make_user("iso-strategy-a@example.com")
    headers_b = await make_user("iso-strategy-b@example.com")

    save_resp = await client.post(
        "/api/strategy",
        json={"type": "lean", "name": "A의 전략", "symbol": "samsung"},
        headers=headers_a,
    )
    assert save_resp.status_code == 200
    strategy_id = save_resp.json()["id"]

    list_b = await client.get("/api/strategy", headers=headers_b)
    assert all(item["id"] != strategy_id for item in list_b.json()["items"])

    detail_b = await client.get(f"/api/strategy/{strategy_id}", headers=headers_b)
    assert detail_b.status_code == 404

    delete_b = await client.delete(f"/api/strategy/{strategy_id}", headers=headers_b)
    assert delete_b.status_code == 404

    detail_a = await client.get(f"/api/strategy/{strategy_id}", headers=headers_a)
    assert detail_a.status_code == 200
