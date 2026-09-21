from datetime import date, timedelta

import pytest

from app.backend.db import get_db


def _ohlcv(ticker: str, start_close: float) -> list[dict]:
    start = date(2026, 1, 1)
    return [{"ticker": ticker, "interval": "1d", "timestamp": (start + timedelta(days=index)).isoformat(),
             "close": start_close + index, "volume": 1_000 + index, "fingerprint": f"{ticker}-{index}",
             "source": "fixture", "dataset_version": "fixture-v1"} for index in range(130)]


@pytest.mark.asyncio
async def test_factor_exposure_uses_user_holdings_and_common_as_of(client, make_user):
    headers = await make_user("factor-exposure@example.com")
    await get_db().market_ohlcv.insert_many(_ohlcv("AAA", 100) + _ohlcv("BBB", 200))
    saved = await client.put("/api/portfolio", headers=headers, json={"cash_krw": 0, "holdings": [
        {"ticker": "AAA", "name": "Alpha", "quantity": 10, "average_price": 100, "currency": "KRW", "sector": "테스트"},
        {"ticker": "BBB", "name": "Beta", "quantity": 5, "average_price": 200, "currency": "KRW", "sector": "테스트"},
    ]})
    assert saved.status_code == 200, saved.text
    response = await client.get("/api/portfolio/factor-exposure", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["available"] is True
    assert payload["as_of"] == "2026-05-10"
    assert payload["position_count"] == 2
    assert payload["coverage_pct"] == 100.0
    assert payload["weighted_factors"]["return_20d"] is not None
