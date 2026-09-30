"""Scan standalone reversion strategies, kept separate by holding horizon.

The confluence engine (``confluence.py``) combines strategies by replacing
every component's own exit with a single shared EMA30 close-cross exit. That
exit is designed for trend/breakout entries: it assumes the entry happens
while price is still above its 30-session average. A mean-reversion entry
(bollinger-rsi, rsi-obv) typically fires while price is already *below* its
30-session average, so the shared EMA30 exit triggers almost immediately --
backtesting confirmed a ~1-session average holding period and a ~49% win
rate, no better than noise.

Running each reversion strategy standalone with its own native exit (the
Bollinger middle band / RSI recovery for bollinger-rsi, RSI-overbought with
weakening OBV for rsi-obv) and restricting entries to the largest, most
liquid names removed that defect. Backtested over 2021-08-30..2026-08-27
against the KRX daily database, restricted at the time to the top 100 names
by market cap within each market; the scanner now tracks the KOSPI200 and
KOSDAQ150 benchmark universes instead (see ``eligibility``), so the figures
below predate that widening:

- bollinger-rsi ("swing"): ~14-15-session average hold; 71% win rate on
  KOSPI (n=440), 67% on KOSDAQ (n=340) -- KOSDAQ trades a bit less often but
  for a larger median gain (+5.2% vs +3.2%). Positive in every calendar year
  2022-2026 on both markets, including the 2022 bear market.
- rsi-obv ("long_term"): ~180-210-session average hold; 75% win rate on
  KOSPI (n=72), 64% on KOSDAQ (n=74) -- both closer to a multi-month
  position than a swing trade, so it is reported as its own separate
  category rather than mixed into the swing list.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

import pandas as pd

from korea_quant.eligibility import (
    DEFAULT_MARKET_CAP_TIERS,
    SUPPORTED_MARKETS,
    market_regime_entry_allowed,
    validate_market_cap_tiers,
)
from korea_quant.strategy import bollinger_rsi_signals, rsi_obv_signals

SWING_STRATEGY = "bollinger-rsi"
LONG_TERM_STRATEGY = "rsi-obv"

SIGNAL_DESCRIPTIONS = {
    "swing": "볼린저 밴드 하단 이탈 + RSI 과매도 반등 (평균 보유 약 3주)",
    "long_term": "RSI 회복 + OBV 동반 상승 (평균 보유 약 9개월, 장기 포지션)",
}

# The scanner presents swing before long_term when one name triggers both.
# It is not a return forecast: daily_picks_cli drops the later duplicate, so a
# fixed order is required only to make that operational choice reproducible.
_SIGNAL_SELECTION_ORDER = {"swing": 0, "long_term": 1}


def reversion_rule_parameters() -> dict[str, dict[str, Any]]:
    """Report the entry/exit parameters the scanner actually runs with.

    The scanner calls ``bollinger_rsi_signals``/``rsi_obv_signals`` without
    arguments, so the live rule parameters are the functions' own defaults and
    nothing in ``ReversionScannerConfig`` records them. D-33 needs a retune of
    those defaults to invalidate the accumulating prospective sample, so they
    are read back here and folded into the candidate-audit fingerprint.

    A change to the signal *logic* that leaves every default untouched is still
    invisible here; D-33 requires such a change to be logged as a manual reset.
    """

    parameters: dict[str, dict[str, Any]] = {}
    for strategy, signal_fn in (
        (SWING_STRATEGY, bollinger_rsi_signals),
        (LONG_TERM_STRATEGY, rsi_obv_signals),
    ):
        parameters[strategy] = {
            name: param.default
            for name, param in inspect.signature(signal_fn).parameters.items()
            if param.default is not inspect.Parameter.empty
        }
    return parameters


def live_selection_key(market_cap_rank: float, symbol: object, signal_type: str) -> str:
    """Encode the scanner's deterministic candidate order for one live session."""

    signal_order = _SIGNAL_SELECTION_ORDER.get(signal_type)
    if signal_order is None:
        raise ValueError(f"Unknown signal_type for live selection: {signal_type}")
    if pd.isna(market_cap_rank) or int(market_cap_rank) < 1:
        raise ValueError("market_cap_rank must be a positive value for live selection.")
    # Zero-padding preserves numerical rank order under the string sort used by
    # capital_metrics, then mirrors the scanner's symbol/signal tie-breaks.
    return f"{int(market_cap_rank):06d}:{symbol!s}:{signal_order:02d}"


@dataclass(frozen=True)
class ReversionScannerConfig:
    """Filters for the swing/long-term reversion scanner."""

    liquidity_window: int = 20
    minimum_average_trading_value: float = 5_000_000_000
    market_cap_tiers: tuple[tuple[str, int], ...] = DEFAULT_MARKET_CAP_TIERS
    minimum_history_rows: int = 260
    markets: tuple[str, ...] = SUPPORTED_MARKETS
    outlier_gap_threshold: float = 0.50
    outlier_cooldown_sessions: int = 60
    # Zero disables the benchmark regime gate. D-8 validated window=200,
    # inverted=True (only buy while the benchmark sits below its own SMA) for
    # both reversion strategies on every market, but the live default stays
    # off so enabling it against real positions is a deliberate CLI choice
    # rather than a silent behavior change.
    market_filter_window: int = 0
    market_filter_inverted: bool = True


def scan_reversion_candidates(
    history: pd.DataFrame,
    config: ReversionScannerConfig | None = None,
    benchmark_close: dict[str, pd.Series] | None = None,
) -> pd.DataFrame:
    """Return swing and long-term reversion candidates observed for the next session."""
    config = config or ReversionScannerConfig()
    _validate_config(config)
    required = {
        "Date", "Symbol", "Name", "Market", "Open", "High", "Low", "Close",
        "Volume", "TradingValue", "MarketCap",
    }
    missing = required - set(history.columns)
    if missing:
        raise ValueError(f"Reversion scanner history is missing: {', '.join(sorted(missing))}")
    if history.empty:
        raise ValueError("Reversion scanner history cannot be empty.")

    universe = history.copy()
    universe["Date"] = pd.to_datetime(universe["Date"])
    universe = universe[universe["Market"].isin(config.markets)].sort_values(["Symbol", "Date"])
    if universe.empty:
        raise ValueError(f"No history for configured markets: {config.markets}")
    as_of = universe["Date"].max()
    market_cap_rank = _market_cap_rank(universe, as_of)
    tiers = validate_market_cap_tiers(config.market_cap_tiers)
    market_gate = _next_session_market_gate(benchmark_close, config)

    rows: list[dict[str, Any]] = []
    for symbol, group in universe.groupby("Symbol", sort=True):
        group = group.sort_values("Date")
        latest = group.iloc[-1]
        trading_value = pd.to_numeric(group["TradingValue"], errors="coerce")
        average_value = trading_value.tail(config.liquidity_window).mean()
        rank = market_cap_rank.get(symbol)
        tier = tiers.get(str(latest["Market"]))
        base_row: dict[str, Any] = {
            "as_of": as_of.date().isoformat(),
            "execution_timing": "next_trading_session",
            "symbol": symbol,
            "name": latest["Name"],
            "market": latest["Market"],
            "close": float(latest["Close"]),
            "market_cap_rank": rank,
            "market_cap_tier": tier,
            "average_trading_value": float(average_value),
            "state": "no_signal",
            "signal_type": None,
        }
        if pd.Timestamp(latest["Date"]) != as_of:
            base_row["state"] = "stale_security"
        elif len(group) < config.minimum_history_rows:
            base_row["state"] = "insufficient_history"
        elif average_value < config.minimum_average_trading_value:
            base_row["state"] = "low_liquidity"
        elif rank is None or tier is None or rank > tier:
            base_row["state"] = "outside_market_cap_tier"
        elif _has_recent_outlier(group["Close"], config):
            base_row["state"] = "outlier_cooldown"
        else:
            prices = group.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
            swing = next_session_projection(prices["Close"], bollinger_rsi_signals)
            long_term = next_session_projection(prices, rsi_obv_signals)
            triggered = [
                signal_type
                for signal_type, projection in (("swing", swing), ("long_term", long_term))
                if bool(projection["entries"])
            ]
            # Only entries are gated -- native_exit_observed stays free to cut
            # risk regardless of the regime, matching reversion_validation.py.
            blocked_by_regime = bool(triggered) and bool(market_gate) and not market_gate.get(
                str(latest["Market"]), False
            )
            if blocked_by_regime:
                # A real signal fired but the regime gate suppressed the entry.
                # This must stay distinguishable from a genuine no-signal day --
                # otherwise the candidate audit log (D-27) cannot tell "the
                # strategy had nothing to say" apart from "we said no on
                # purpose", which is exactly the distinction the prospective
                # audit (D-29/D-30) needs while the KOSPI regime gate is
                # closed for an extended stretch.
                blocked_row = dict(base_row)
                blocked_row["state"] = "regime_gate_blocked"
                blocked_row["signal_type"] = "+".join(triggered)
                rows.append(blocked_row)
                continue
            if not triggered:
                rows.append(base_row)
                continue
            for signal_type in triggered:
                candidate = dict(base_row)
                candidate["state"] = "entry_due_next_session"
                candidate["signal_type"] = signal_type
                rows.append(candidate)
            continue
        rows.append(base_row)

    result = pd.DataFrame(rows)
    priority = {
        "entry_due_next_session": 0,
        "regime_gate_blocked": 1,
        "no_signal": 2,
        "outside_market_cap_tier": 3,
        "outlier_cooldown": 4,
        "low_liquidity": 5,
        "insufficient_history": 6,
        "stale_security": 7,
    }
    result["_priority"] = result["state"].map(priority)
    # market_cap_rank is intentionally per market (the eligibility universe is
    # KOSPI200 plus KOSDAQ150), so rank 1 can occur twice. Make those global
    # ties explicit rather than relying on pandas' incidental input order. The
    # validation path builds the identical key for the capital replay.
    result["_symbol_tie_break"] = result["symbol"].astype(str)
    result["_signal_tie_break"] = result["signal_type"].map(_SIGNAL_SELECTION_ORDER).fillna(99)
    return (
        result.sort_values(
            ["_priority", "market_cap_rank", "_symbol_tie_break", "_signal_tie_break"],
            na_position="last", kind="stable",
        )
        .drop(columns=["_priority", "_symbol_tie_break", "_signal_tie_break"])
        .reset_index(drop=True)
    )


def next_session_projection(data: pd.Series | pd.DataFrame, signal_fn):
    """Extend ``data`` with a synthetic next-session row and read that row's signal.

    Every component signal function delays its own entries/exits by one
    session after the close-based observation (see ``strategy.py``). The
    duplicated final row only ever contributes through that shift, so the
    projected value at the new index depends solely on real historical data
    -- the same technique ``confluence.py`` uses for its own next-session
    lookahead.
    """
    placeholder = data.iloc[[-1]].copy()
    placeholder.index = pd.DatetimeIndex([data.index[-1] + pd.Timedelta(days=1)])
    extended = pd.concat([data, placeholder])
    return signal_fn(extended).iloc[-1]


def native_exit_observed(data: pd.Series | pd.DataFrame, signal_type: str) -> bool:
    """Evaluate a live position's native exit from the latest real close.

    Strategy exit columns are stateful: they only emit an exit when the same
    calculation window also contains the corresponding simulated entry. Paper
    positions can predate the scanner's history window or fill later after a
    blocked session, so live monitoring evaluates the native indicator
    condition directly instead of relying on that simulated holding state.
    """
    if signal_type == "swing":
        close = data if isinstance(data, pd.Series) else data["Close"]
        signals = bollinger_rsi_signals(close)
        exit_rsi = signals.attrs["rsi_levels"][1]
        latest = signals.iloc[-1]
        return bool(close.iloc[-1] >= latest["bb_middle"] or latest["rsi"] >= exit_rsi)
    if signal_type == "long_term":
        if isinstance(data, pd.Series):
            raise ValueError("long_term exit evaluation requires OHLCV prices.")
        signals = rsi_obv_signals(data)
        exit_rsi = signals.attrs["rsi_levels"][1]
        latest = signals.iloc[-1]
        # Mirrors rsi_obv_signals exactly. This check is a second copy of the
        # exit rule, so it has to move whenever that one does or live positions
        # will be held past the point the backtest says they were sold.
        return bool(latest["rsi"] >= exit_rsi)
    raise ValueError(f"Unknown signal_type: {signal_type}")


def _next_session_market_gate(
    benchmark_close: dict[str, pd.Series] | None, config: ReversionScannerConfig
) -> dict[str, bool]:
    """Resolve whether entries are allowed next session, per configured market.

    Reuses next_session_projection's placeholder-row trick on the benchmark
    close instead of a symbol's own prices, so the regime gate and the
    swing/long-term signals agree on what "as of next session" means -- both
    read the shifted comparison off a synthetic day appended one calendar day
    past the last real bar.
    """
    if config.market_filter_window <= 0:
        return {}
    if not benchmark_close:
        raise ValueError("market_filter_window requires a benchmark close per market.")
    missing = set(config.markets) - set(benchmark_close)
    if missing:
        raise ValueError(f"Benchmark close is missing markets: {', '.join(sorted(missing))}")
    gate: dict[str, bool] = {}
    for market in config.markets:
        series = pd.to_numeric(benchmark_close[market], errors="coerce").dropna()
        series.index = pd.to_datetime(series.index)
        series = series.sort_index()
        placeholder = series.iloc[[-1]].copy()
        placeholder.index = pd.DatetimeIndex([series.index[-1] + pd.Timedelta(days=1)])
        extended = pd.concat([series, placeholder])
        allowed = market_regime_entry_allowed(
            extended, config.market_filter_window, inverted=config.market_filter_inverted
        )
        gate[market] = bool(allowed.iloc[-1])
    return gate


def _has_recent_outlier(close: pd.Series, config: ReversionScannerConfig) -> bool:
    values = pd.to_numeric(close, errors="coerce")
    outlier = values.pct_change(fill_method=None).abs().ge(config.outlier_gap_threshold)
    return bool(outlier.tail(config.outlier_cooldown_sessions).any())


def _market_cap_rank(universe: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, int]:
    """Rank each symbol by market cap within its own market.

    Ranking across markets would let KOSPI's much larger caps crowd out
    every KOSDAQ name from a shared top-N tier, so this must group by
    ``Market`` even when a single market is configured.
    """
    latest = universe[universe["Date"] == as_of].copy()
    latest["MarketCap"] = pd.to_numeric(latest["MarketCap"], errors="coerce")
    latest = latest[latest["MarketCap"].notna() & latest["MarketCap"].gt(0)]
    latest["rank"] = latest.groupby("Market")["MarketCap"].rank(ascending=False, method="first")
    return dict(zip(latest["Symbol"], latest["rank"].astype(int)))


def _validate_config(config: ReversionScannerConfig) -> None:
    if config.liquidity_window < 2:
        raise ValueError("liquidity_window must be greater than one.")
    if config.minimum_average_trading_value <= 0:
        raise ValueError("minimum_average_trading_value must be positive.")
    validate_market_cap_tiers(config.market_cap_tiers)
    if config.minimum_history_rows < 2:
        raise ValueError("minimum_history_rows must be greater than one.")
    if not config.markets:
        raise ValueError("markets must not be empty.")
    invalid_markets = set(config.markets) - set(SUPPORTED_MARKETS)
    if invalid_markets:
        raise ValueError(f"Unsupported markets: {', '.join(sorted(invalid_markets))}")
    if config.outlier_gap_threshold <= 0:
        raise ValueError("outlier_gap_threshold must be positive.")
    if config.outlier_cooldown_sessions < 1:
        raise ValueError("outlier_cooldown_sessions must be positive.")
    if config.market_filter_window < 0 or config.market_filter_window == 1:
        raise ValueError("market_filter_window must be zero (disabled) or at least two.")
