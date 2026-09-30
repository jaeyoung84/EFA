"""Transparent baseline strategies."""

from __future__ import annotations

import pandas as pd

STRATEGIES = (
    "sma", "bollinger-rsi", "donchian-atr", "macd-trend", "candle-volume", "rsi-obv",
    "ema30-resistance", "volatility-squeeze", "trend-pullback", "trend-multiplier", "confluence",
    "darvas-box", "turtle",
)


def moving_average_signals(
    close: pd.Series, fast_window: int = 20, slow_window: int = 60
) -> pd.DataFrame:
    """Create next-session entry and exit signals for a long-only SMA strategy."""

    if fast_window <= 0 or slow_window <= 0:
        raise ValueError("Moving-average windows must be positive.")
    if fast_window >= slow_window:
        raise ValueError("fast_window must be smaller than slow_window.")

    values = pd.to_numeric(close, errors="coerce")
    fast = values.rolling(fast_window, min_periods=fast_window).mean()
    slow = values.rolling(slow_window, min_periods=slow_window).mean()
    regime = fast > slow

    # A signal observed at today's close can trade no earlier than the next session.
    entries = (regime & ~regime.shift(1, fill_value=False)).shift(1, fill_value=False)
    exits = (~regime & regime.shift(1, fill_value=False)).shift(1, fill_value=False)
    return pd.DataFrame({"fast": fast, "slow": slow, "entries": entries, "exits": exits})


def bollinger_rsi_signals(
    close: pd.Series,
    window: int = 20,
    standard_deviations: float = 2.0,
    rsi_window: int = 14,
    oversold: float = 30.0,
    exit_rsi: float = 50.0,
) -> pd.DataFrame:
    """Create next-session signals for a long-only Bollinger/RSI mean reversion strategy.

    Entry is observed when price is at or below the lower band while RSI is
    oversold. The position exits after price reaches the middle band or RSI
    recovers to ``exit_rsi``. Orders are shifted one session after observation.
    """

    if window <= 1 or rsi_window <= 1:
        raise ValueError("Bollinger and RSI windows must be greater than one.")
    if standard_deviations <= 0:
        raise ValueError("standard_deviations must be positive.")
    if not 0 <= oversold < exit_rsi <= 100:
        raise ValueError("RSI levels must satisfy 0 <= oversold < exit_rsi <= 100.")

    values = pd.to_numeric(close, errors="coerce")
    middle = values.rolling(window, min_periods=window).mean()
    deviation = values.rolling(window, min_periods=window).std(ddof=0)
    upper = middle + standard_deviations * deviation
    lower = middle - standard_deviations * deviation
    rsi = _rsi(values, rsi_window)

    entry_observed = (values <= lower) & (rsi <= oversold)
    exit_observed = (values >= middle) | (rsi >= exit_rsi)
    entries, exits = _long_only_events(entry_observed, exit_observed)
    result = pd.DataFrame(
        {
            "bb_upper": upper,
            "bb_middle": middle,
            "bb_lower": lower,
            "rsi": rsi,
            "entries": entries.shift(1, fill_value=False),
            "exits": exits.shift(1, fill_value=False),
        }
    )
    result.attrs["rsi_levels"] = (oversold, exit_rsi)
    return result


def donchian_atr_signals(
    prices: pd.DataFrame,
    entry_window: int = 20,
    exit_window: int = 10,
    atr_window: int = 14,
    atr_multiplier: float = 3.0,
) -> pd.DataFrame:
    """Create next-session Donchian breakout and ATR trailing-stop signals.

    Channels exclude today's bar, so a close can only break levels that were
    already known before that session. ATR and the trailing stop are evaluated
    after the close; generated orders are shifted to the next session.
    """

    required = {"High", "Low", "Close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Donchian/ATR prices are missing: {', '.join(sorted(missing))}")
    if entry_window <= 1 or exit_window <= 1 or atr_window <= 1:
        raise ValueError("Donchian and ATR windows must be greater than one.")
    if exit_window > entry_window:
        raise ValueError("exit_window must not exceed entry_window.")
    if atr_multiplier <= 0:
        raise ValueError("atr_multiplier must be positive.")

    high = pd.to_numeric(prices["High"], errors="coerce")
    low = pd.to_numeric(prices["Low"], errors="coerce")
    close = pd.to_numeric(prices["Close"], errors="coerce")
    upper = high.shift(1).rolling(entry_window, min_periods=entry_window).max()
    lower = low.shift(1).rolling(exit_window, min_periods=exit_window).min()
    atr = _atr(high, low, close, atr_window)

    holding = False
    highest_close = float("nan")
    entries: list[bool] = []
    exits: list[bool] = []
    stops: list[float] = []
    for current_close, channel_high, channel_low, current_atr in zip(
        close, upper, lower, atr, strict=True
    ):
        entry = False
        exit_ = False
        stop = float("nan")
        ready = pd.notna(current_close) and pd.notna(current_atr)
        if not holding and ready and pd.notna(channel_high) and current_close > channel_high:
            entry = True
            holding = True
            highest_close = float(current_close)
            stop = highest_close - atr_multiplier * float(current_atr)
        elif holding and ready:
            highest_close = max(highest_close, float(current_close))
            stop = highest_close - atr_multiplier * float(current_atr)
            exit_ = (
                (pd.notna(channel_low) and current_close < channel_low)
                or current_close <= stop
            )
            if exit_:
                holding = False
                highest_close = float("nan")
        entries.append(entry)
        exits.append(exit_)
        stops.append(stop)

    observed_entries = pd.Series(entries, index=prices.index, dtype=bool)
    observed_exits = pd.Series(exits, index=prices.index, dtype=bool)
    result = pd.DataFrame(
        {
            "donchian_upper": upper,
            "donchian_lower": lower,
            "atr": atr,
            "atr_stop": pd.Series(stops, index=prices.index),
            "entries": observed_entries.shift(1, fill_value=False),
            "exits": observed_exits.shift(1, fill_value=False),
        }
    )
    return result


def macd_trend_signals(
    close: pd.Series,
    fast_span: int = 12,
    slow_span: int = 26,
    signal_span: int = 9,
    trend_window: int = 200,
) -> pd.DataFrame:
    """Create next-session MACD crossover signals with a long-term SMA filter."""

    if min(fast_span, slow_span, signal_span, trend_window) <= 1:
        raise ValueError("MACD spans and trend_window must be greater than one.")
    if fast_span >= slow_span:
        raise ValueError("fast_span must be smaller than slow_span.")

    values = pd.to_numeric(close, errors="coerce")
    fast_ema = values.ewm(span=fast_span, adjust=False, min_periods=fast_span).mean()
    slow_ema = values.ewm(span=slow_span, adjust=False, min_periods=slow_span).mean()
    macd = fast_ema - slow_ema
    signal = macd.ewm(span=signal_span, adjust=False, min_periods=signal_span).mean()
    histogram = macd - signal
    trend = values.rolling(trend_window, min_periods=trend_window).mean()

    above_signal = macd > signal
    bullish_cross = above_signal & ~above_signal.shift(1, fill_value=False)
    bearish_cross = ~above_signal & above_signal.shift(1, fill_value=False)
    entry_observed = bullish_cross & (values > trend)
    exit_observed = bearish_cross | (values < trend)
    entries, exits = _long_only_events(entry_observed, exit_observed)
    return pd.DataFrame(
        {
            "trend_sma": trend,
            "macd": macd,
            "macd_signal": signal,
            "macd_histogram": histogram,
            "entries": entries.shift(1, fill_value=False),
            "exits": exits.shift(1, fill_value=False),
        }
    )


def candle_volume_signals(
    prices: pd.DataFrame,
    volume_window: int = 20,
    volume_multiplier: float = 1.5,
    hammer_shadow_ratio: float = 2.0,
) -> pd.DataFrame:
    """Create next-session candlestick signals confirmed by unusual volume.

    Long entries use a bullish engulfing candle or bullish hammer. Exits use a
    bearish engulfing candle. Every pattern requires current volume to exceed a
    multiple of the *prior* rolling mean, excluding today's volume.
    """

    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Candle/volume prices are missing: {', '.join(sorted(missing))}")
    if volume_window <= 1:
        raise ValueError("volume_window must be greater than one.")
    if volume_multiplier <= 0 or hammer_shadow_ratio <= 0:
        raise ValueError("Volume multiplier and hammer ratio must be positive.")

    open_ = pd.to_numeric(prices["Open"], errors="coerce")
    high = pd.to_numeric(prices["High"], errors="coerce")
    low = pd.to_numeric(prices["Low"], errors="coerce")
    close = pd.to_numeric(prices["Close"], errors="coerce")
    volume = pd.to_numeric(prices["Volume"], errors="coerce")
    previous_open = open_.shift(1)
    previous_close = close.shift(1)

    bullish_engulfing = (
        (previous_close < previous_open)
        & (close > open_)
        & (open_ <= previous_close)
        & (close >= previous_open)
    )
    bearish_engulfing = (
        (previous_close > previous_open)
        & (close < open_)
        & (open_ >= previous_close)
        & (close <= previous_open)
    )
    body = (close - open_).abs()
    lower_shadow = open_.where(open_ < close, close) - low
    upper_shadow = high - close.where(close > open_, open_)
    bullish_hammer = (
        (close > open_)
        & (lower_shadow >= hammer_shadow_ratio * body)
        & (upper_shadow <= body)
        & (body > 0)
    )
    volume_average = volume.shift(1).rolling(
        volume_window, min_periods=volume_window
    ).mean()
    volume_confirmed = volume >= volume_average * volume_multiplier

    entry_observed = (bullish_engulfing | bullish_hammer) & volume_confirmed
    exit_observed = bearish_engulfing & volume_confirmed
    entries, exits = _long_only_events(entry_observed, exit_observed)
    return pd.DataFrame(
        {
            "volume_average": volume_average,
            "volume_confirmed": volume_confirmed,
            "bullish_engulfing": bullish_engulfing,
            "bullish_hammer": bullish_hammer,
            "bearish_engulfing": bearish_engulfing,
            "entries": entries.shift(1, fill_value=False),
            "exits": exits.shift(1, fill_value=False),
        }
    )


def rsi_obv_signals(
    prices: pd.DataFrame,
    rsi_window: int = 14,
    oversold: float = 30.0,
    exit_rsi: float = 50.0,
    obv_signal_window: int = 9,
) -> pd.DataFrame:
    """Confirm RSI recovery with OBV direction and execute next session.

    Entry requires RSI to cross upward out of oversold while OBV is rising
    above its signal line. Exit is the completion of that recovery: RSI back at
    the neutral level, the same target bollinger_rsi_signals uses. OBV is a
    signed-volume proxy, not a direct measurement of fund flows.

    The exit used to be `(rsi >= 70) & obv_weakening`, and both halves were
    wrong. obv_weakening is an OR of two lenient conditions, true on nearly any
    down or flat session, so it never bound; the rule was RSI 70 alone. That
    asked a position entered on a recovery through RSI 30 to run to the opposite
    extreme before it could close, and when it did not, nothing closed it --
    over ten years 9.4% of positions never exited at all, holding for an average
    of 233 sessions and consuming 5.8% of all capital-time for no realised
    return. Targeting neutral instead cuts the average hold to 18 sessions,
    takes the 5th-percentile trade from -61% to -22%, and roughly quadruples
    return per session held. A price stop was tested as the alternative and
    rejected: every width from 5% to 20% drove this strategy to negative
    expectancy, because selling the dip is what the entry is built to buy.
    """

    required = {"Close", "Volume"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"RSI/OBV prices are missing: {', '.join(sorted(missing))}")
    if rsi_window <= 1 or obv_signal_window <= 1:
        raise ValueError("RSI and OBV signal windows must be greater than one.")
    if not 0 <= oversold < exit_rsi <= 100:
        raise ValueError("RSI levels must satisfy 0 <= oversold < exit_rsi <= 100.")

    close = pd.to_numeric(prices["Close"], errors="coerce")
    volume = pd.to_numeric(prices["Volume"], errors="coerce")
    rsi = _rsi(close, rsi_window)
    direction = close.diff().apply(lambda value: 1 if value > 0 else (-1 if value < 0 else 0))
    obv = (volume.fillna(0) * direction).cumsum()
    obv_signal = obv.rolling(
        obv_signal_window, min_periods=obv_signal_window
    ).mean()
    rsi_recovery = (rsi > oversold) & (rsi.shift(1) <= oversold)
    obv_rising = (obv > obv_signal) & (obv.diff() > 0)
    obv_weakening = (obv < obv_signal) | (obv.diff() <= 0)
    entry_observed = rsi_recovery & obv_rising
    # obv_weakening is still reported because the scanner and the charts show
    # it, but it no longer gates the exit: as a condition it is almost always
    # true, so all it ever did was let RSI 70 be the real rule.
    exit_observed = rsi >= exit_rsi
    entries, exits = _long_only_events(entry_observed, exit_observed)
    result = pd.DataFrame(
        {
            "rsi": rsi,
            "obv": obv,
            "obv_signal": obv_signal,
            "rsi_recovery": rsi_recovery,
            "obv_rising": obv_rising,
            "obv_weakening": obv_weakening,
            "entries": entries.shift(1, fill_value=False),
            "exits": exits.shift(1, fill_value=False),
        }
    )
    result.attrs["rsi_levels"] = (oversold, exit_rsi)
    return result


def volatility_squeeze_signals(
    prices: pd.DataFrame,
    ema_window: int = 30,
    band_window: int = 20,
    atr_window: int = 14,
    compression_lookback: int = 120,
    compression_percentile: float = 0.2,
    breakout_window: int = 20,
    volume_window: int = 20,
    volume_multiplier: float = 1.5,
) -> pd.DataFrame:
    """Trade a causal volatility contraction followed by volume-backed breakout.

    A contraction must be known at the prior close. The breakout candle and
    volume confirmation are observed at today's close, then the order is sent
    at the next session open. A close below the EMA exits an open position.
    """
    required = {"High", "Low", "Close", "Volume"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Volatility squeeze prices are missing: {', '.join(sorted(missing))}")
    if min(ema_window, band_window, atr_window, compression_lookback, breakout_window, volume_window) <= 1:
        raise ValueError("Volatility squeeze windows must be greater than one.")
    if not 0 < compression_percentile <= 1 or volume_multiplier <= 0:
        raise ValueError("Compression percentile and volume multiplier must be positive.")

    high = pd.to_numeric(prices["High"], errors="coerce")
    low = pd.to_numeric(prices["Low"], errors="coerce")
    close = pd.to_numeric(prices["Close"], errors="coerce")
    volume = pd.to_numeric(prices["Volume"], errors="coerce")
    ema = close.ewm(span=ema_window, adjust=False, min_periods=ema_window).mean()
    middle = close.rolling(band_window, min_periods=band_window).mean()
    deviation = close.rolling(band_window, min_periods=band_window).std(ddof=0)
    bandwidth = ((middle + 2 * deviation) - (middle - 2 * deviation)) / middle
    atr_percent = _atr(high, low, close, atr_window) / close
    bandwidth_rank = bandwidth.rolling(
        compression_lookback, min_periods=compression_lookback
    ).rank(pct=True)
    atr_rank = atr_percent.rolling(
        compression_lookback, min_periods=compression_lookback
    ).rank(pct=True)
    squeeze = (bandwidth_rank <= compression_percentile) & (atr_rank <= compression_percentile)
    prior_breakout_level = high.shift(1).rolling(
        breakout_window, min_periods=breakout_window
    ).max()
    prior_volume_average = volume.shift(1).rolling(
        volume_window, min_periods=volume_window
    ).mean()
    volume_confirmed = volume >= prior_volume_average * volume_multiplier
    breakout = (close > prior_breakout_level) & (close > ema)
    entry_observed = squeeze.shift(1, fill_value=False) & breakout & volume_confirmed
    exit_observed = close < ema
    entries, exits = _long_only_events(entry_observed, exit_observed)
    return pd.DataFrame(
        {
            "ema30": ema,
            "squeeze_bandwidth": bandwidth,
            "squeeze_atr_percent": atr_percent,
            "squeeze": squeeze,
            "breakout_level": prior_breakout_level,
            "volume_average": prior_volume_average,
            "volume_confirmed": volume_confirmed,
            "breakout": breakout,
            "entries": entries.shift(1, fill_value=False),
            "exits": exits.shift(1, fill_value=False),
        }
    )



def trend_multiplier_signals(prices: pd.DataFrame, breakout_window: int = 126, fast_ema_window: int = 50, slow_ema_window: int = 200, volume_window: int = 20, volume_multiplier: float = 1.5, atr_window: int = 14, atr_multiplier: float = 4.0) -> pd.DataFrame:
    """Hold qualified trend breakouts with a wide ATR trailing stop."""
    required = {"High", "Low", "Close", "Volume"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Trend multiplier prices are missing: {', '.join(sorted(missing))}")
    if min(breakout_window, fast_ema_window, slow_ema_window, volume_window, atr_window) <= 1:
        raise ValueError("Trend multiplier windows must be greater than one.")
    if fast_ema_window >= slow_ema_window or volume_multiplier <= 0 or atr_multiplier <= 0:
        raise ValueError("Trend multiplier parameters are invalid.")
    high = pd.to_numeric(prices["High"], errors="coerce")
    low = pd.to_numeric(prices["Low"], errors="coerce")
    close = pd.to_numeric(prices["Close"], errors="coerce")
    volume = pd.to_numeric(prices["Volume"], errors="coerce")
    fast = close.ewm(span=fast_ema_window, adjust=False, min_periods=fast_ema_window).mean()
    slow = close.ewm(span=slow_ema_window, adjust=False, min_periods=slow_ema_window).mean()
    level = high.shift(1).rolling(breakout_window, min_periods=breakout_window).max()
    average_volume = volume.shift(1).rolling(volume_window, min_periods=volume_window).mean()
    atr = _atr(high, low, close, atr_window)
    observed = (close > level) & (close > fast) & (fast > slow) & (slow > slow.shift(20)) & (volume >= average_volume * volume_multiplier)
    holding = False
    peak = float("nan")
    entries, exits, stops = [], [], []
    for value, current_atr, entry_ready in zip(close, atr, observed, strict=True):
        entry = exit_ = False
        stop = float("nan")
        if not holding and bool(entry_ready) and pd.notna(current_atr):
            entry, holding, peak = True, True, float(value)
            stop = peak - atr_multiplier * float(current_atr)
        elif holding and pd.notna(value) and pd.notna(current_atr):
            peak = max(peak, float(value))
            stop = peak - atr_multiplier * float(current_atr)
            exit_ = value <= stop
            if exit_:
                holding, peak = False, float("nan")
        entries.append(entry); exits.append(exit_); stops.append(stop)
    return pd.DataFrame({"trend_fast_ema": fast, "trend_slow_ema": slow, "trend_breakout_level": level, "trend_volume_average": average_volume, "trend_atr": atr, "trend_atr_stop": pd.Series(stops, index=prices.index), "entries": pd.Series(entries, index=prices.index).shift(1, fill_value=False), "exits": pd.Series(exits, index=prices.index).shift(1, fill_value=False)})

def trend_pullback_signals(
    prices: pd.DataFrame,
    pullback_ema_window: int = 20,
    trend_ema_window: int = 50,
    pullback_lookback: int = 5,
    breakout_window: int = 3,
    atr_window: int = 14,
    atr_multiplier: float = 3.0,
) -> pd.DataFrame:
    """Enter a causal pullback recovery only within a rising primary trend.

    The setup is observed at the close: price must be above a rising 50 EMA,
    the low must have touched the 20 EMA during the preceding five sessions,
    and the close must break the prior three-session high.  It executes only
    at the next session's open.  A close below the trend EMA exits next open.
    """
    required = {"High", "Low", "Close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Trend pullback prices are missing: {', '.join(sorted(missing))}")
    if min(pullback_ema_window, trend_ema_window, pullback_lookback, breakout_window, atr_window) <= 1:
        raise ValueError("Trend pullback windows must be greater than one.")
    if pullback_ema_window >= trend_ema_window:
        raise ValueError("Pullback EMA window must be shorter than trend EMA window.")
    if atr_multiplier <= 0:
        raise ValueError("ATR multiplier must be positive.")

    high = pd.to_numeric(prices["High"], errors="coerce")
    low = pd.to_numeric(prices["Low"], errors="coerce")
    close = pd.to_numeric(prices["Close"], errors="coerce")
    pullback_ema = close.ewm(
        span=pullback_ema_window, adjust=False, min_periods=pullback_ema_window
    ).mean()
    trend_ema = close.ewm(
        span=trend_ema_window, adjust=False, min_periods=trend_ema_window
    ).mean()
    touched_pullback = low <= pullback_ema
    recent_pullback = touched_pullback.shift(1).rolling(
        pullback_lookback, min_periods=pullback_lookback
    ).max().fillna(False).astype(bool)
    prior_high = high.shift(1).rolling(breakout_window, min_periods=breakout_window).max()
    atr = _atr(high, low, close, atr_window)
    rising_trend = (close > trend_ema) & (trend_ema > trend_ema.shift(1))
    breakout = close > prior_high
    entry_observed = rising_trend & recent_pullback & breakout
    holding = False
    highest_close = float("nan")
    active_stop = float("nan")
    entries: list[bool] = []
    exits: list[bool] = []
    stops: list[float] = []
    for enter, current_close, current_atr, current_trend in zip(
        entry_observed.fillna(False), close, atr, trend_ema, strict=True
    ):
        entry = False
        exit_ = False
        if not holding and bool(enter) and pd.notna(current_atr):
            holding = True
            entry = True
            highest_close = float(current_close)
            active_stop = highest_close - float(current_atr) * atr_multiplier
        elif holding:
            highest_close = max(highest_close, float(current_close))
            active_stop = max(active_stop, highest_close - float(current_atr) * atr_multiplier)
            exit_ = bool(current_close < active_stop or current_close < current_trend)
        stops.append(active_stop if holding else float("nan"))
        if exit_:
            holding = False
            highest_close = float("nan")
            active_stop = float("nan")
        entries.append(entry)
        exits.append(exit_)
    return pd.DataFrame(
        {
            "pullback_ema": pullback_ema,
            "trend_ema": trend_ema,
            "recent_pullback": recent_pullback,
            "breakout_level": prior_high,
            "rising_trend": rising_trend,
            "breakout": breakout,
            "atr": atr,
            "atr_trailing_stop": pd.Series(stops, index=prices.index),
            "entries": pd.Series(entries, index=prices.index).shift(1, fill_value=False),
            "exits": pd.Series(exits, index=prices.index).shift(1, fill_value=False),
        }
    )


def ema_resistance_signals(
    prices: pd.DataFrame,
    ema_window: int = 30,
    resistance_threshold: float = 0.005,
    minimum_touches: int = 2,
    strong_body_lookback: int = 20,
    strong_body_multiplier: float = 1.5,
    minimum_close_position: float = 0.75,
) -> pd.DataFrame:
    """Causal adaptation of the user's EMA30 structural-resistance breakout.

    A one-bar swing high is confirmed only after the candle to its right has
    closed. Resistance at each date uses only swing highs confirmed by then.
    Entry also requires a strong bullish candle. Its stop is fixed at the most
    recent confirmed swing low. Breakout and exit observations execute on the
    following session.
    """

    required = {"Open", "High", "Low", "Close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"EMA resistance prices are missing: {', '.join(sorted(missing))}")
    if ema_window <= 1 or strong_body_lookback <= 1 or minimum_touches < 2:
        raise ValueError("EMA/body windows must exceed one and touches must be at least two.")
    if resistance_threshold <= 0 or strong_body_multiplier <= 0:
        raise ValueError("Resistance tolerance and body multiplier must be positive.")
    if not 0 < minimum_close_position <= 1:
        raise ValueError("minimum_close_position must be in (0, 1].")

    open_ = pd.to_numeric(prices["Open"], errors="coerce")
    high = pd.to_numeric(prices["High"], errors="coerce")
    low = pd.to_numeric(prices["Low"], errors="coerce")
    close = pd.to_numeric(prices["Close"], errors="coerce")
    ema = close.ewm(span=ema_window, adjust=False, min_periods=ema_window).mean()
    body = (close - open_).abs()
    prior_average_body = body.shift(1).rolling(
        strong_body_lookback, min_periods=strong_body_lookback
    ).mean()
    candle_range = (high - low).where(high > low)
    close_position = (close - low) / candle_range
    strong_bullish = (
        (close > open_)
        & (body >= prior_average_body * strong_body_multiplier)
        & (close_position >= minimum_close_position)
    )
    confirmed_high = pd.Series(float("nan"), index=prices.index)
    confirmed_low = pd.Series(float("nan"), index=prices.index)
    resistance = pd.Series(float("nan"), index=prices.index)
    latest_support = pd.Series(float("nan"), index=prices.index)
    historical_highs: list[float] = []
    most_recent_low = float("nan")

    for position in range(2, len(prices)):
        left, pivot, right = high.iloc[position - 2 : position + 1]
        if pd.notna(pivot) and pivot > left and pivot > right:
            historical_highs.append(float(pivot))
            confirmed_high.iloc[position] = float(pivot)
        left_low, pivot_low, right_low = low.iloc[position - 2 : position + 1]
        if pd.notna(pivot_low) and pivot_low < left_low and pivot_low < right_low:
            most_recent_low = float(pivot_low)
            confirmed_low.iloc[position] = most_recent_low
        latest_support.iloc[position] = most_recent_low
        lines = _structural_lines(
            historical_highs, resistance_threshold, minimum_touches
        )
        current_close = close.iloc[position]
        if lines and pd.notna(current_close):
            resistance.iloc[position] = min(lines, key=lambda line: abs(line - current_close))

    price_breakout = (
        (close > ema)
        & (close > resistance)
        & (close.shift(1) <= resistance)
    )
    breakout = (
        price_breakout & strong_bullish & latest_support.notna() & (latest_support < close)
    )
    holding = False
    active_stop = float("nan")
    entries: list[bool] = []
    exits: list[bool] = []
    stops: list[float] = []
    for enter, current_close, current_ema, support in zip(
        breakout.fillna(False), close, ema, latest_support, strict=True
    ):
        entry = False
        exit_ = False
        if not holding and bool(enter):
            entry = True
            holding = True
            active_stop = float(support)
        elif holding and (
            (pd.notna(current_close) and current_close < active_stop)
            or (pd.notna(current_ema) and current_close < current_ema)
        ):
            exit_ = True
        stops.append(active_stop if holding else float("nan"))
        if exit_:
            holding = False
            active_stop = float("nan")
        entries.append(entry)
        exits.append(exit_)
    observed_entries = pd.Series(entries, index=prices.index, dtype=bool)
    observed_exits = pd.Series(exits, index=prices.index, dtype=bool)
    return pd.DataFrame(
        {
            "ema30": ema,
            "resistance": resistance,
            "latest_support": latest_support,
            "structure_stop": pd.Series(stops, index=prices.index),
            "confirmed_swing_high": confirmed_high,
            "confirmed_swing_low": confirmed_low,
            "strong_bullish": strong_bullish,
            "breakout": breakout,
            "entries": observed_entries.shift(1, fill_value=False),
            "exits": observed_exits.shift(1, fill_value=False),
        }
    )


def darvas_box_signals(
    prices: pd.DataFrame,
    box_window: int = 20,
) -> pd.DataFrame:
    """Trade a causal Darvas-box breakout with a floor that only ratchets up.

    The box ceiling and floor use only the prior ``box_window`` sessions, so a
    breakout can only be confirmed against structure already known before that
    session. Once in a position the stop is the highest box floor observed
    since entry -- it can rise as a new, higher box forms but never falls.
    There is no fixed profit target; the position exits only when the close
    breaks back below that floor. Orders are shifted to the next session.
    """

    required = {"High", "Low", "Close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Darvas box prices are missing: {', '.join(sorted(missing))}")
    if box_window <= 1:
        raise ValueError("box_window must be greater than one.")

    high = pd.to_numeric(prices["High"], errors="coerce")
    low = pd.to_numeric(prices["Low"], errors="coerce")
    close = pd.to_numeric(prices["Close"], errors="coerce")
    ceiling = high.shift(1).rolling(box_window, min_periods=box_window).max()
    floor = low.shift(1).rolling(box_window, min_periods=box_window).min()

    holding = False
    active_floor = float("nan")
    entries: list[bool] = []
    exits: list[bool] = []
    stops: list[float] = []
    for current_close, current_ceiling, current_floor in zip(close, ceiling, floor, strict=True):
        entry = False
        exit_ = False
        if not holding and pd.notna(current_close) and pd.notna(current_ceiling) and current_close > current_ceiling:
            entry = True
            holding = True
            active_floor = float(current_floor) if pd.notna(current_floor) else float("-inf")
        elif holding:
            if pd.notna(current_floor):
                active_floor = max(active_floor, float(current_floor))
            if pd.notna(current_close) and current_close < active_floor:
                exit_ = True
        stops.append(active_floor if holding else float("nan"))
        if exit_:
            holding = False
            active_floor = float("nan")
        entries.append(entry)
        exits.append(exit_)

    return pd.DataFrame(
        {
            "box_ceiling": ceiling,
            "box_floor": floor,
            "box_stop": pd.Series(stops, index=prices.index),
            "entries": pd.Series(entries, index=prices.index, dtype=bool).shift(1, fill_value=False),
            "exits": pd.Series(exits, index=prices.index, dtype=bool).shift(1, fill_value=False),
        }
    )


def turtle_signals(
    prices: pd.DataFrame,
    entry_window: int = 20,
    exit_window: int = 10,
    atr_window: int = 20,
    atr_stop_multiplier: float = 2.0,
    pyramid_atr_fraction: float = 0.5,
    max_units: int = 4,
) -> pd.DataFrame:
    """Trade a causal Turtle-system breakout with ATR pyramiding and a trailing stop.

    Entry uses a prior ``entry_window``-session high (excluding today); exit
    uses a prior ``exit_window``-session low. While a position is open,
    additional units may be added ("pyramided") each time the close advances
    another ``pyramid_atr_fraction`` * ATR ("N") beyond the last add, up to
    ``max_units`` total. The trailing stop is ``atr_stop_multiplier`` * N below
    the highest close reached since entry and only ever rises. Every signal is
    shifted one session so it executes at the next open.
    """

    required = {"High", "Low", "Close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Turtle prices are missing: {', '.join(sorted(missing))}")
    if entry_window <= 1 or exit_window <= 1 or atr_window <= 1:
        raise ValueError("Turtle windows must be greater than one.")
    if atr_stop_multiplier <= 0 or pyramid_atr_fraction <= 0:
        raise ValueError("Turtle ATR multipliers must be positive.")
    if max_units < 1:
        raise ValueError("max_units must be at least one.")

    high = pd.to_numeric(prices["High"], errors="coerce")
    low = pd.to_numeric(prices["Low"], errors="coerce")
    close = pd.to_numeric(prices["Close"], errors="coerce")
    entry_channel = high.shift(1).rolling(entry_window, min_periods=entry_window).max()
    exit_channel = low.shift(1).rolling(exit_window, min_periods=exit_window).min()
    atr = _atr(high, low, close, atr_window)

    holding = False
    units = 0
    highest_close = float("nan")
    last_add_price = float("nan")
    stop = float("nan")
    entries: list[bool] = []
    exits: list[bool] = []
    stops: list[float] = []
    unit_counts: list[int] = []
    for current_close, channel_high, channel_low, current_atr in zip(
        close, entry_channel, exit_channel, atr, strict=True
    ):
        entry = False
        exit_ = False
        ready = pd.notna(current_close) and pd.notna(current_atr)
        if not holding and ready and pd.notna(channel_high) and current_close > channel_high:
            entry = True
            holding = True
            units = 1
            highest_close = float(current_close)
            last_add_price = float(current_close)
            stop = highest_close - atr_stop_multiplier * float(current_atr)
        elif holding and ready:
            highest_close = max(highest_close, float(current_close))
            stop = max(stop, highest_close - atr_stop_multiplier * float(current_atr))
            exit_ = (
                (pd.notna(channel_low) and current_close < channel_low)
                or current_close <= stop
            )
            if (
                not exit_
                and units < max_units
                and current_close >= last_add_price + pyramid_atr_fraction * float(current_atr)
            ):
                entry = True
                units += 1
                last_add_price = float(current_close)
            if exit_:
                holding = False
                units = 0
                highest_close = float("nan")
                last_add_price = float("nan")
        entries.append(entry)
        exits.append(exit_)
        stops.append(stop if holding else float("nan"))
        unit_counts.append(units)

    return pd.DataFrame(
        {
            "entry_channel": entry_channel,
            "exit_channel": exit_channel,
            "atr": atr,
            "turtle_stop": pd.Series(stops, index=prices.index),
            "units": pd.Series(unit_counts, index=prices.index),
            "entries": pd.Series(entries, index=prices.index, dtype=bool).shift(1, fill_value=False),
            "exits": pd.Series(exits, index=prices.index, dtype=bool).shift(1, fill_value=False),
        }
    )


def _structural_lines(
    price_points: list[float], threshold: float, minimum_touches: int
) -> list[float]:
    """Cluster confirmed price points without reading any future observations."""
    lines: list[float] = []
    unused = list(price_points)
    while unused:
        anchor = unused.pop(0)
        cluster = [anchor]
        remaining = []
        for point in unused:
            if abs(point - anchor) / anchor <= threshold:
                cluster.append(point)
            else:
                remaining.append(point)
        if len(cluster) >= minimum_touches:
            lines.append(sum(cluster) / len(cluster))
        unused = remaining
    return lines


def _rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    average_loss = losses.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    relative_strength = average_gain / average_loss
    rsi = 100 - 100 / (1 + relative_strength)
    rsi = rsi.mask((average_gain == 0) & (average_loss == 0))
    return rsi.mask((average_loss == 0) & (average_gain > 0), 100.0)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def _long_only_events(
    entry_observed: pd.Series, exit_observed: pd.Series
) -> tuple[pd.Series, pd.Series]:
    """Turn conditions into non-overlapping long-only entry and exit events."""
    holding = False
    entries = []
    exits = []
    for enter, exit_ in zip(entry_observed.fillna(False), exit_observed.fillna(False), strict=True):
        entry = bool(enter) and not holding
        exit_signal = bool(exit_) and holding
        if entry:
            holding = True
        elif exit_signal:
            holding = False
        entries.append(entry)
        exits.append(exit_signal)
    return (
        pd.Series(entries, index=entry_observed.index, dtype=bool),
        pd.Series(exits, index=entry_observed.index, dtype=bool),
    )
