"""Deterministic synthetic market for the Pullback v4 equivalence test (no real data)."""

import numpy as np
import pandas as pd

START, END = pd.Timestamp("2021-01-01"), pd.Timestamp("2024-06-28")
PERIOD_START, PERIOD_END = pd.Timestamp("2022-01-03"), END
# Golden values produced once by the archived strategy_validation_v4.py (sha256 8e4dc15f3f135f5b)
# on this fixture; see docs/engine/verification/EINT-017.json.
GOLDEN = {"trades": 66, "trades_sha16": "aec93b65a7c0aa5c", "equity_sha16": "e85de783ab712f7f",
          "exit_reasons": {"forced_close": 1, "holding_limit": 16, "market_regime": 22, "stop": 12, "trailing": 15}}


def _walk(rng, n, start, drift, vol, regime_len=120):
    shocks = rng.normal(0, vol, n)
    trend = np.repeat(rng.choice([drift, -drift, drift * 2], size=n // regime_len + 1), regime_len)[:n]
    return start * np.exp(np.cumsum(trend + shocks))


def build():
    rng = np.random.default_rng(20261001)
    dates = pd.bdate_range(START, END)
    benchmarks = {}
    for market, seed_start in (("KOSPI", 30000.0), ("KOSDAQ", 9000.0)):
        close = _walk(rng, len(dates), seed_start, 0.0006, 0.009, regime_len=90)
        benchmarks[market] = pd.DataFrame({
            "Open": close * (1 + rng.normal(0, 0.002, len(dates))), "High": close * 1.006,
            "Low": close * 0.994, "Close": close, "Volume": 1e6, "Turnover": close * 1e6,
        }, index=pd.DatetimeIndex(dates, name="date"))
    ohlcv, market_map = {}, {}
    for k in range(40):
        ticker = f"{100000 + k:06d}"
        close = _walk(rng, len(dates), rng.uniform(5000, 80000), rng.uniform(0.0004, 0.0012), rng.uniform(0.012, 0.025))
        open_ = close * (1 + rng.normal(0, 0.006, len(dates)))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, len(dates))))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, len(dates))))
        volume = rng.uniform(2e5, 2e6, len(dates))
        frame = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume,
                              "Turnover": close * volume * 2.0}, index=pd.DatetimeIndex(dates, name="date"))
        if k == 7:  # a listing that starts late
            frame = frame.loc[frame.index >= pd.Timestamp("2022-06-01")]
        if k == 11:  # a delisting inside the period
            frame = frame.loc[frame.index <= pd.Timestamp("2023-09-29")]
        ohlcv[ticker] = frame
        market_map[ticker] = "KOSPI" if k % 2 == 0 else "KOSDAQ"
    market_map[f"{100000 + 3:06d}"] = [("2021-01-01", "KOSDAQ"), ("2023-03-02", "KOSPI")]  # a transfer
    return ohlcv, benchmarks, market_map
