from datetime import date, timedelta

import pytest

from app.backend import factor_engine
from app.backend.db import get_db


def _bars(days: int = 130) -> list[dict]:
    start = date(2026, 1, 1)
    return [{"timestamp": (start + timedelta(days=index)).isoformat(), "close": 100 + index,
             "volume": 1_000 + index, "fingerprint": f"bar-{index}"} for index in range(days)]


def test_factor_engine_uses_only_observed_bars_and_records_fingerprint():
    result = factor_engine.calculate_daily_factors(_bars())
    assert result["available"] is True
    assert result["as_of"] == "2026-05-10"
    assert result["factors"]["return_20d"] is not None
    assert result["factors"]["sma_120"] is not None
    assert len(result["input_fingerprint"]) == 64


def test_factor_universe_ranks_observed_metrics_without_signal():
    rows = [
        {"ticker": "MOMENTUM", "factors": {"return_20d": 0.2, "realized_volatility_20d": 0.3, "max_drawdown_60d": -0.1}},
        {"ticker": "DEFENSIVE", "factors": {"return_20d": 0.1, "realized_volatility_20d": 0.1, "max_drawdown_60d": -0.05}},
    ]
    result = factor_engine.rank_factor_universe(rows)
    assert result[0]["ticker"] == "DEFENSIVE"
    assert result[0]["factor_composite"] is not None
    assert set(result[0]["factor_ranks"]) == {"momentum_20d", "low_volatility_20d", "drawdown_resilience_60d"}


@pytest.mark.asyncio
async def test_market_factor_endpoint_preserves_data_provenance(client):
    rows = [{**bar, "ticker": "TEST", "interval": "1d", "source": "fixture", "dataset_version": "fixture-v1"} for bar in _bars()]
    await get_db().market_ohlcv.insert_many(rows)
    response = await client.get("/api/market/factors?ticker=TEST")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["available"] is True
    assert payload["provenance"]["source"] == "fixture"
    assert payload["provenance"]["dataset_version"] == "fixture-v1"
    saved = await get_db().market_factor_snapshots.find_one({"ticker": "TEST", "as_of": payload["as_of"]})
    assert saved["input_fingerprint"] == payload["input_fingerprint"]


@pytest.mark.asyncio
async def test_factor_universe_requires_a_common_as_of_date(client):
    first = [{**bar, "ticker": "AAA", "interval": "1d", "source": "fixture", "dataset_version": "v1"} for bar in _bars()]
    second = [{**bar, "ticker": "BBB", "interval": "1d", "source": "fixture", "dataset_version": "v1"} for bar in _bars()]
    await get_db().market_ohlcv.insert_many(first + second)
    response = await client.get("/api/market/factor-universe?tickers=AAA,BBB")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["as_of"] == "2026-05-10"
    assert payload["available_count"] == 2
    assert all(item["factor_composite"] is not None for item in payload["items"])
