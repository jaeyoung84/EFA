"""Shared point-in-time entry-eligibility filtering for KRX validation harnesses."""

from __future__ import annotations

import pandas as pd

SUPPORTED_MARKETS = ("KOSPI", "KOSDAQ")

# KOSPI200 and KOSDAQ150 are the two benchmark universes this system trades
# against, but real index membership history is not collected, so market cap
# rank within each market stands in as a point-in-time proxy. The two markets
# need different depths -- one shared tier would either cut KOSPI short of its
# index or stretch KOSDAQ past its own.
DEFAULT_MARKET_CAP_TIERS: tuple[tuple[str, int], ...] = (("KOSPI", 200), ("KOSDAQ", 150))

# Index ETFs stand in for the index level itself: the KRX collector already
# stores ETF bars in the same daily_prices table, so the market regime is
# readable from the same database as the universe, with no second data source
# and no vendor index series to keep in sync. Each ETF tracks exactly the index
# its market cap tier proxies.
DEFAULT_MARKET_BENCHMARKS: tuple[tuple[str, str], ...] = (
    ("KOSPI", "069500"),  # KODEX 200
    ("KOSDAQ", "229200"),  # KODEX 코스닥150
)


def validate_market_cap_tiers(tiers: tuple[tuple[str, int], ...]) -> dict[str, int]:
    """Return the per-market rank cutoffs, rejecting unknown or non-positive entries."""
    mapping = dict(tiers)
    if not mapping:
        raise ValueError("market_cap_tiers must not be empty.")
    unsupported = set(mapping) - set(SUPPORTED_MARKETS)
    if unsupported:
        raise ValueError(f"Unsupported markets in market_cap_tiers: {', '.join(sorted(unsupported))}")
    if any(tier < 1 for tier in mapping.values()):
        raise ValueError("market_cap_tiers values must be positive.")
    return mapping


def build_market_cap_tiers(kospi_tier: int, kosdaq_tier: int) -> tuple[tuple[str, int], ...]:
    """Build a tier mapping from two CLI flags, failing at parse time rather than mid-run."""
    tiers = (("KOSPI", kospi_tier), ("KOSDAQ", kosdaq_tier))
    validate_market_cap_tiers(tiers)
    return tiers


def build_market_benchmarks(kospi_symbol: str, kosdaq_symbol: str) -> dict[str, str]:
    """Build a per-market benchmark symbol mapping from two CLI flags."""
    mapping = {"KOSPI": str(kospi_symbol), "KOSDAQ": str(kosdaq_symbol)}
    if any(not symbol.strip() for symbol in mapping.values()):
        raise ValueError("Benchmark symbols must not be blank.")
    return mapping


def market_regime_entry_allowed(
    benchmark_close: pd.Series, window: int, *, inverted: bool = False
) -> pd.Series:
    """Flag sessions whose prior benchmark close sits on the tradable side of its SMA.

    Trend-following wants the benchmark above its own moving average; the
    mean-reversion family wants the opposite, so ``inverted`` flips the
    comparison instead of duplicating the shift bookkeeping. The one-session
    shift matches entry_eligibility: a close observed on session t can only gate
    an order that fills at the next session's open. Warm-up sessions have no
    average yet and stay blocked in both orientations, which costs no
    out-of-sample evidence as long as the warm-up falls inside training.
    """

    if window < 2:
        raise ValueError("Market filter window must be at least two.")
    values = pd.to_numeric(benchmark_close, errors="coerce")
    values.index = pd.to_datetime(values.index)
    values = values.sort_index().dropna()
    if values.empty:
        raise ValueError("Benchmark close must contain at least one observation.")
    if values.index.has_duplicates:
        raise ValueError("Benchmark close must not contain duplicate dates.")
    if (values <= 0).any():
        raise ValueError("Benchmark close must be positive.")
    average = values.rolling(window, min_periods=window).mean()
    observed = values.lt(average) if inverted else values.gt(average)
    return observed.shift(1, fill_value=False).astype(bool)


def market_regime_allowances(
    benchmark_close: dict[str, pd.Series] | None,
    markets: object,
    window: int,
    *,
    inverted: bool = False,
) -> dict[str, pd.Series]:
    """Build the per-market entry gate, or nothing at all when the filter is off."""

    if window <= 0:
        return {}
    if not benchmark_close:
        raise ValueError("A market filter window requires a benchmark close per market.")
    missing = {str(market) for market in markets} - set(benchmark_close)
    if missing:
        raise ValueError(f"Benchmark close is missing markets: {', '.join(sorted(missing))}")
    return {
        market: market_regime_entry_allowed(series, window, inverted=inverted)
        for market, series in benchmark_close.items()
    }


def benchmark_fingerprint(benchmark_close: dict[str, pd.Series] | None) -> dict[str, list]:
    """Summarise the benchmark series so a checkpoint signature notices it changing.

    The filter window and orientation already live in the config, but the price
    series arrives as a runtime argument, so without this a rerun against
    refreshed or re-pointed benchmark data would silently reuse stale chunks.
    """

    if not benchmark_close:
        return {}
    summary: dict[str, list] = {}
    for market, series in sorted(benchmark_close.items()):
        values = pd.to_numeric(series, errors="coerce").dropna()
        dates = pd.to_datetime(values.index)
        summary[str(market)] = [
            str(dates.min()) if len(dates) else "",
            str(dates.max()) if len(dates) else "",
            len(values),
            round(float(values.sum()), 4),
        ]
    return summary


def validate_market_history(history: pd.DataFrame) -> pd.DataFrame:
    required = {
        "Date", "Symbol", "Name", "Market", "Open", "High", "Low", "Close",
        "Volume", "TradingValue", "MarketCap",
    }
    missing = required - set(history.columns)
    if missing:
        raise ValueError(f"Validation history is missing: {', '.join(sorted(missing))}")
    if history.empty:
        raise ValueError("Validation history cannot be empty.")
    data = history.copy()
    data["Date"] = pd.to_datetime(data["Date"])
    data = data[data["Market"].isin(SUPPORTED_MARKETS)]
    if data.empty:
        raise ValueError("Validation requires KOSPI or KOSDAQ history.")
    return data


def entry_eligibility(
    history: pd.DataFrame,
    *,
    market_cap_tiers: tuple[tuple[str, int], ...],
    liquidity_window: int,
    minimum_average_trading_value: float,
    minimum_history_sessions: int,
    outlier_gap_threshold: float,
    outlier_cooldown_sessions: int,
) -> pd.DataFrame:
    """Flag each bar as tradable using only information known at the prior close."""

    tiers = validate_market_cap_tiers(market_cap_tiers)
    data = history.copy().sort_values(["Market", "Symbol", "Date"])
    for column in ("MarketCap", "TradingValue", "Close"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["MarketCapRank"] = data.groupby(["Date", "Market"])["MarketCap"].rank(
        ascending=False, method="first"
    )
    data["AverageTradingValue"] = data.groupby(["Market", "Symbol"])["TradingValue"].transform(
        lambda values: values.rolling(liquidity_window, min_periods=liquidity_window).mean()
    )
    outlier = data.groupby(["Market", "Symbol"])["Close"].pct_change(fill_method=None).abs().ge(
        outlier_gap_threshold
    )
    data["OutlierCooldown"] = outlier.groupby([data["Market"], data["Symbol"]]).transform(
        lambda values: values.rolling(outlier_cooldown_sessions, min_periods=1).max().astype(bool)
    )
    enough_history = data.groupby(["Market", "Symbol"]).cumcount().add(1).ge(
        minimum_history_sessions
    )
    # A market absent from the mapping maps to NaN, and every comparison against
    # NaN is False, so it is excluded rather than silently admitted.
    tier_limit = data["Market"].map(tiers)
    observed = (
        data["MarketCapRank"].le(tier_limit)
        & data["AverageTradingValue"].ge(minimum_average_trading_value)
        & enough_history & ~data["OutlierCooldown"]
    )
    data["EntryAllowed"] = observed.groupby([data["Market"], data["Symbol"]]).shift(
        1, fill_value=False
    )
    return data
