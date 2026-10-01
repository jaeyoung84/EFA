"""Daily Trend Pullback v4 ("market-regime exit"), ported for the recommendation engine (EINT-017).

Source of truth: the archived research code
``docs/engine/snapshots/2026-09-29-0c6b2e8308cc/repo/research/daily_trend_pullback_v4/``
(``strategy_validation_v4.py`` sha256 prefix 8e4dc15f3f135f5b, ``data_loader.py`` ff1f239c95de9c24).
Rules, parameters, ranking weights and the day loop are copied without change; only the
research-only parts (bootstrap, fold reports, power labels, printing) are left out.
Equivalence is checked against that code (synthetic golden fixture) and against the archived
observation #5 fingerprint on the real DB (see docs/engine/verification/EINT-017.json).

Status carried over unchanged (D-43): "fail hint", not PASS; offline forward observation pending.
Nothing here places orders or fills; the backtest is an in-memory simulation.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

# ── 1. CONFIG (archived CONFIG/FIXED_CONFIG; research-only keys omitted) ─────────────
CONFIG = {
    "fee_bps": 1.5,
    "tax_bps": 18.0,
    "slip_bps": 5.0,
    "max_positions": 5,
    "max_weight": 0.20,
    "risk_per_trade": 0.005,
    "atr_multiple": 1.5,
    "holding_limit": 15,
    "trailing_atr_multiple": 2.0,
    "regime_exit": True,
    "min_turnover": 5_000_000_000,
    "max_daily_return": 0.08,
    "market_sma": 50,
    "warmup_days": 300,
    "market_min_coverage": 0.95,
}
FIXED_CONFIG = {key: CONFIG[key] for key in (
    "max_positions", "max_weight", "risk_per_trade", "atr_multiple", "holding_limit",
    "trailing_atr_multiple", "regime_exit", "min_turnover", "max_daily_return")}
RANK_WEIGHTS = {"dist_ema10": 0.40, "adx": 0.30, "rsi": 0.30}
MARKET_BENCHMARKS = {"KOSPI": "069500", "KOSDAQ": "229200"}

ENTRY_COST = (CONFIG["fee_bps"] + CONFIG["slip_bps"]) / 10_000
EXIT_COST = (CONFIG["fee_bps"] + CONFIG["tax_bps"] + CONFIG["slip_bps"]) / 10_000
SMA_COL = f"SMA{CONFIG['market_sma']}"
GATE_ALL = "__ALL__"

TRADE_COLUMNS = [
    "ticker", "sector", "entry_date", "exit_date", "entry_price", "exit_price", "stop_price",
    "shares", "entry_cost", "exit_cost", "pnl_net", "r_multiple", "exit_reason", "holding_days", "score",
]
VALID_EXIT_REASONS = frozenset(["stop", "holding_limit", "trailing", "forced_close", "market_regime"])


# ── 2. Input cleaning (archived data_loader.clean_ohlcv / clean_ohlcv_with_market_calendar) ──
def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """drop_non_trading=False path: dedupe, ffill NaN prices, drop non-positive prices, fix High<Low.

    Unlike the reversion runtime this drops halted (zero-price) rows instead of carrying the
    close forward -- that is what the archived v4 loader did, so the port keeps it.
    """
    df = df.copy().sort_index()
    df = df[~df.index.duplicated(keep="last")]
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = df[col].ffill()
    df = df[(df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)]
    invalid = df["High"] < df["Low"]
    if invalid.any():
        df.loc[invalid, ["High", "Low"]] = df.loc[invalid, ["Low", "High"]].values
    if "Turnover" not in df.columns:
        df = df.copy()
        df["Turnover"] = df["Close"] * df["Volume"]
    return df


def align_to_market_calendar(df: pd.DataFrame, market_df: pd.DataFrame) -> pd.DataFrame:
    trading_days = set(market_df.index)
    df = df.copy().sort_index()
    df = df[~df.index.duplicated(keep="last")]
    if any(d not in trading_days for d in df.index):
        df = df[df.index.isin(trading_days)]
    return df


def frames_from_history(history: pd.DataFrame, market_df: pd.DataFrame | None) -> dict[str, pd.DataFrame]:
    """Long table (Date, Symbol, Open, High, Low, Close, Volume, TradingValue) -> cleaned per-symbol frames."""
    out: dict[str, pd.DataFrame] = {}
    columns = {"Open": "Open", "High": "High", "Low": "Low", "Close": "Close", "Volume": "Volume",
               "TradingValue": "Turnover"}
    for symbol, group in history.groupby("Symbol", sort=True):
        frame = group.set_index(pd.to_datetime(group["Date"]))[list(columns)].rename(columns=columns).sort_index()
        frame.index.name = "date"
        if frame.empty:
            continue
        frame = clean_ohlcv(frame)
        if market_df is not None:
            frame = align_to_market_calendar(frame, market_df)
        if not frame.empty:
            out[str(symbol)] = frame
    return out


# ── 5. Indicators ───────────────────────────────────────────────────────────────
def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_index()
    c, h, l = df["Close"], df["High"], df["Low"]
    df["EMA5"] = c.ewm(span=5, adjust=False).mean()
    df["EMA10"] = c.ewm(span=10, adjust=False).mean()
    df["SMA20"] = c.rolling(20).mean()
    df["SMA50"] = c.rolling(50).mean()
    delta = c.diff()
    df["RSI"] = 100 - 100 / (
        1 + _wilder_smooth(delta.clip(lower=0), 14)
        / _wilder_smooth((-delta).clip(lower=0), 14).replace(0, np.nan)
    )
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["ATR"] = _wilder_smooth(tr, 14)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    df["MACD_HIST"] = macd - macd.ewm(span=9, adjust=False).mean()
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    pdi = 100 * _wilder_smooth(pdm, 14) / df["ATR"]
    mdi = 100 * _wilder_smooth(mdm, 14) / df["ATR"]
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    df["ADX"] = _wilder_smooth(dx, 14)
    if "Turnover" not in df.columns:
        df["Turnover"] = c * df["Volume"]
    df["AVG_TURNOVER20"] = df["Turnover"].rolling(20).mean()
    wc = c.resample("W-FRI").last()
    ws = wc.rolling(20).mean()
    df["WEEKLY_TREND"] = (wc > ws).reindex(df.index, method="ffill").shift(1)
    df["RETURN20"] = c.pct_change(20)
    df["DIST_SMA50"] = c / df["SMA50"] - 1
    df["VOL_RATIO"] = df["Volume"] / df["Volume"].rolling(20).mean()
    df["DAILY_RET"] = c.pct_change()
    df["DIST_EMA10"] = c / df["EMA10"] - 1
    df["EMA10_TOUCH5"] = (c < df["EMA10"]).rolling(5, min_periods=1).max().astype(bool)
    return df


# ── 6. Entry / exit / ranking ────────────────────────────────────────────────────
def entry_signals(prepared: dict, config: dict) -> dict:
    """{ticker: {"base": Series[bool], "full": Series[bool]}} -- full = base AND touched EMA10 in 5 sessions."""
    min_turnover = config.get("min_turnover", CONFIG["min_turnover"])
    max_daily_return = config.get("max_daily_return", CONFIG["max_daily_return"])
    out = {}
    for ticker, df in prepared.items():
        wt = df["WEEKLY_TREND"].fillna(False).astype(bool)
        base = (
            wt & (df["EMA5"] > df["EMA10"]) & (df["Close"] > df["EMA10"]) & df["RSI"].between(50, 70)
            & (df["ADX"] > 20) & (df["MACD_HIST"] >= 0) & (df["AVG_TURNOVER20"] >= min_turnover)
            & (df["DAILY_RET"] < max_daily_return) & df["ATR"].notna() & (df["ATR"] > 0)
        )
        full = base & df["EMA10_TOUCH5"].fillna(False).astype(bool)
        out[ticker] = {"base": base, "full": full}
    return out


def exit_signal(row: pd.Series, pos: dict, holding_days: int, next_open: float, config: dict,
                regime_off: bool = False) -> tuple[bool, str]:
    """Priority stop > holding_limit > trailing > market_regime; all execute at the next open."""
    stop_hit = next_open <= pos["stop_price"]
    limit_hit = holding_days >= config["holding_limit"]
    trailing_hit = next_open <= pos["highest_close"] - config["trailing_atr_multiple"] * row["ATR"]
    regime_hit = bool(regime_off) and bool(config.get("regime_exit", CONFIG["regime_exit"]))
    if stop_hit:
        return True, "stop"
    if limit_hit:
        return True, "holding_limit"
    if trailing_hit:
        return True, "trailing"
    if regime_hit:
        return True, "market_regime"
    return False, ""


def rank_candidates(candidates: list) -> pd.DataFrame:
    """Pre-registered score: near the EMA10 anchor 40% + ADX 30% + lower RSI 30% (pct ranks)."""
    if not candidates:
        return pd.DataFrame()
    df = pd.DataFrame(candidates)
    for col in ["dist_ema10", "adx", "rsi"]:
        df[f"{col}_rank"] = df[col].rank(pct=True)
    df["score"] = (
        RANK_WEIGHTS["dist_ema10"] * (1.0 - df["dist_ema10_rank"])
        + RANK_WEIGHTS["adx"] * df["adx_rank"]
        + RANK_WEIGHTS["rsi"] * (1.0 - df["rsi_rank"])
    )
    return df.sort_values("score", ascending=False).reset_index(drop=True)


# ── Market gate helpers ───────────────────────────────────────────────────────────
def market_of(market_map: dict, ticker: str, date) -> str:
    entry = market_map.get(ticker)
    if entry is None:
        raise RuntimeError(f"market_map has no {ticker}")
    if isinstance(entry, str):
        return entry
    market = entry[0][1]
    for seg_start, seg_market in entry:
        if pd.Timestamp(seg_start) <= date:
            market = seg_market
        else:
            break
    return market


def validate_market_map(tickers, market_map: dict, benchmarks: dict) -> None:
    if market_map is None:
        raise RuntimeError("market_map is required with benchmarks")
    known = set(benchmarks.keys())
    for t in tickers:
        entry = market_map.get(t)
        if entry is None:
            raise RuntimeError(f"market_map has no {t}")
        segs = [(None, entry)] if isinstance(entry, str) else list(entry)
        if not segs:
            raise RuntimeError(f"{t}: empty market segments")
        seen, prev = [], None
        for seg_start, m in segs:
            if m not in known:
                raise RuntimeError(f"{t}: no benchmark for market {m!r}")
            if seg_start is not None:
                ts = pd.Timestamp(seg_start)
                if prev is not None and ts <= prev:
                    raise RuntimeError(f"{t}: segment starts are not ascending ({seg_start})")
                prev = ts
            if m in seen:
                raise RuntimeError(f"{t}: market flip-flop/duplicate {[x[1] for x in segs]}")
            seen.append(m)


def attach_warmup(df: pd.DataFrame, start, end) -> pd.DataFrame:
    before = df.loc[df.index < start].tail(CONFIG["warmup_days"])
    period = df.loc[(df.index >= start) & (df.index <= end)]
    merged = pd.concat([before, period])
    return merged.loc[~merged.index.duplicated(keep="last")]


def gate_frame(bench_df: pd.DataFrame, period_start, period_end, all_dates, label: str) -> pd.DataFrame:
    gf = attach_warmup(bench_df, period_start, period_end).copy()
    gf[SMA_COL] = gf["Close"].rolling(CONFIG["market_sma"]).mean()
    in_index = sum(1 for d in all_dates if d in gf.index)
    if in_index == 0 or in_index / len(all_dates) < CONFIG["market_min_coverage"]:
        raise RuntimeError(f"[{label}] benchmark date coverage {in_index}/{len(all_dates)} is too low")
    return gf


def gate_ok(gf: pd.DataFrame, date) -> bool:
    """Entries allowed: benchmark row exists, SMA50 valid and Close > SMA50."""
    if date not in gf.index:
        return False
    row = gf.loc[date]
    sma = row.get(SMA_COL, np.nan)
    if np.isnan(sma):
        return False
    return bool(row["Close"] > sma)


def regime_off(gf: pd.DataFrame, date) -> bool:
    """Holding exit flag: row exists, SMA50 valid and Close <= SMA50 (missing data keeps the position)."""
    if date not in gf.index:
        return False
    row = gf.loc[date]
    sma = row.get(SMA_COL, np.nan)
    if np.isnan(sma):
        return False
    return bool(row["Close"] <= sma)


def attach_costs(trades: pd.DataFrame) -> pd.DataFrame:
    t = trades.copy()
    t["entry_cost"] = t["entry_price"] * ENTRY_COST * t["shares"]
    t["exit_cost"] = t["exit_price"] * EXIT_COST * t["shares"]
    t["pnl_net"] = t["exit_price"] * t["shares"] - t["entry_price"] * t["shares"] - t["entry_cost"] - t["exit_cost"]
    risk = ((t["entry_price"] - t["stop_price"]) * t["shares"]).where(lambda x: x > 0, np.nan)
    t["r_multiple"] = t["pnl_net"] / risk
    return t


# ── 8. Backtest day loop (archived run_period, pullback-effectiveness diagnostics omitted) ──
def run_period(ohlcv: dict, market_df: pd.DataFrame, period_start: pd.Timestamp, period_end: pd.Timestamp,
               config: dict, initial_capital: float = 100_000_000, sector_map: dict | None = None,
               benchmarks: dict | None = None, market_map: dict | None = None) -> tuple:
    if sector_map is None:
        sector_map = {}
    prepared = {t: compute_indicators(attach_warmup(df, period_start, period_end)) for t, df in ohlcv.items()}
    mkt = attach_warmup(market_df, period_start, period_end).copy()
    mkt[SMA_COL] = mkt["Close"].rolling(CONFIG["market_sma"]).mean()
    all_dates = sorted(set(d for df in prepared.values() for d in df.index if period_start <= d <= period_end))
    if len(all_dates) < 2:
        eq_empty = pd.DataFrame(columns=["equity", "cash", "positions"])
        eq_empty.index = pd.DatetimeIndex([])
        return eq_empty, pd.DataFrame(columns=TRADE_COLUMNS)
    in_index = sum(1 for d in all_dates if d in mkt.index)
    if in_index == 0 or in_index / len(all_dates) < CONFIG["market_min_coverage"]:
        raise RuntimeError(f"market date coverage {in_index}/{len(all_dates)} is too low")
    if benchmarks is None:
        gate_frames = {GATE_ALL: mkt}
    else:
        validate_market_map(prepared.keys(), market_map, benchmarks)
        gate_frames = {m: gate_frame(bdf, period_start, period_end, all_dates, m) for m, bdf in benchmarks.items()}

    entry_cache = entry_signals(prepared, config)
    date_to_signal_tickers: dict = {}
    for ticker, sig in entry_cache.items():
        for d in sig["full"].index[sig["full"]]:
            date_to_signal_tickers.setdefault(d, []).append(ticker)
    ticker_last_date = {
        t: df.loc[(df.index >= period_start) & (df.index <= period_end)].index.max()
        for t, df in prepared.items() if not df.empty
    }

    cash = float(initial_capital)
    positions, pending_entries, pending_exits, last_close_cache = {}, {}, {}, {}
    equity_log, trade_log = [], []

    for i, date in enumerate(all_dates[:-1]):
        next_date = all_dates[i + 1]
        for ticker, pex in list(pending_exits.items()):
            cash += pex["proceeds"]
            trade_log.append(pex["record"])
            del positions[ticker]
        pending_exits.clear()
        for ticker, pend in list(pending_entries.items()):
            cash -= pend["cost"]
            positions[ticker] = pend["pos"]
        pending_entries.clear()
        for t in list(positions.keys()):
            if date in prepared[t].index:
                last_close_cache[t] = prepared[t].loc[date, "Close"]

        mkt_ok_by = {m: gate_ok(gf, date) for m, gf in gate_frames.items()}
        mkt_ok = any(mkt_ok_by.values())
        regime_off_by = {m: regime_off(gf, date) for m, gf in gate_frames.items()}

        for ticker in list(positions.keys()):
            pos = positions[ticker]
            df = prepared.get(ticker)
            if df is None or date not in df.index:
                continue
            row = df.loc[date]
            pos["highest_close"] = max(pos["highest_close"], row["Close"])
            holding_days = i - pos["entry_idx"]
            if not ticker_last_date.get(ticker, period_start) > date:
                exit_price = row["Close"]
                cash += pos["shares"] * exit_price * (1 - EXIT_COST)
                trade_log.append(_trade_record(ticker, sector_map, pos, date, exit_price, "forced_close",
                                               i - pos["entry_idx"] - 1))
                del positions[ticker]
                continue
            if next_date not in df.index:
                continue
            next_open = df.loc[next_date, "Open"]
            off = regime_off_by[market_of(market_map, ticker, date) if benchmarks is not None else GATE_ALL]
            should_exit, exit_reason = exit_signal(row, pos, holding_days, next_open, config, regime_off=off)
            if not should_exit:
                continue
            pending_exits[ticker] = {
                "proceeds": pos["shares"] * next_open * (1 - EXIT_COST),
                "record": _trade_record(ticker, sector_map, pos, next_date, next_open, exit_reason, holding_days),
            }

        reserved_cash = 0.0
        for pend in pending_entries.values():
            reserved_cash += pend["cost"]
        slots = config["max_positions"] - (len(positions) - len(pending_exits)) - len(pending_entries)
        if mkt_ok and slots > 0:
            candidates = []
            for ticker in date_to_signal_tickers.get(date, []):
                if ticker in positions or ticker in pending_entries or ticker in pending_exits:
                    continue
                if benchmarks is not None and not mkt_ok_by[market_of(market_map, ticker, date)]:
                    continue
                df = prepared.get(ticker)
                if df is None or date not in df.index or next_date not in df.index:
                    continue
                if next_date == all_dates[-1]:
                    continue
                if ticker_last_date.get(ticker, period_start) <= next_date:
                    continue
                row = df.loc[date]
                candidates.append(_candidate(ticker, row, df.loc[next_date, "Open"]))
            ranked = rank_candidates(candidates)
            for _, cand in ranked.iterrows():
                if slots <= 0:
                    break
                ticker = cand["ticker"]
                entry_price = cand["next_open"]
                stop_price = entry_price - config["atr_multiple"] * cand["atr"]
                if stop_price <= 0:
                    continue
                rps = entry_price - stop_price
                if rps <= 0:
                    continue
                port_val = (cash - reserved_cash) + sum(
                    p["shares"] * last_close_cache.get(t, p["entry_price"]) for t, p in positions.items())
                shares = min(int(port_val * config["risk_per_trade"] / rps),
                             int(port_val * config["max_weight"] / (entry_price * (1 + ENTRY_COST))))
                if shares <= 0:
                    continue
                cost = shares * entry_price * (1 + ENTRY_COST)
                if cost > cash - reserved_cash:
                    continue
                reserved_cash += cost
                pending_entries[ticker] = {"cost": cost, "pos": {
                    "shares": shares, "entry_price": entry_price, "stop_price": stop_price,
                    "entry_date": next_date, "highest_close": entry_price, "entry_idx": i,
                    "score": cand.get("score", 0.5)}}
                slots -= 1

        mkt_val = cash + sum(p["shares"] * last_close_cache.get(t, p["entry_price"]) for t, p in positions.items())
        equity_log.append({"date": date, "equity": mkt_val, "cash": cash, "positions": len(positions)})

    for ticker, pex in list(pending_exits.items()):
        cash += pex["proceeds"]
        trade_log.append(pex["record"])
        del positions[ticker]
    pending_exits.clear()
    if pending_entries:
        raise RuntimeError(f"pending entries left after the loop: {list(pending_entries)}")
    last_date, last_idx = all_dates[-1], len(all_dates) - 1
    for ticker, pos in list(positions.items()):
        df = prepared.get(ticker)
        if df is None or last_date not in df.index:
            raise RuntimeError(f"{ticker}: held at period end without a bar (invariant broken)")
        exit_price = df.loc[last_date, "Close"]
        cash += pos["shares"] * exit_price * (1 - EXIT_COST)
        trade_log.append(_trade_record(ticker, sector_map, pos, last_date, exit_price, "forced_close",
                                       last_idx - pos["entry_idx"] - 1))
    positions.clear()
    equity_log.append({"date": last_date, "equity": cash, "cash": cash, "positions": 0})

    equity = pd.DataFrame(equity_log).set_index("date")
    equity.index = pd.to_datetime(equity.index)
    trades = pd.DataFrame(trade_log, columns=TRADE_COLUMNS).reset_index(drop=True)
    for col in ["entry_date", "exit_date"]:
        trades[col] = pd.to_datetime(trades[col])
    if not trades.empty:
        trades = attach_costs(trades)
    return equity, trades


def _candidate(ticker: str, row: pd.Series, next_open: float) -> dict:
    return {
        "ticker": ticker, "return20": row.get("RETURN20", 0), "dist_sma50": row.get("DIST_SMA50", 0),
        "vol_ratio": row.get("VOL_RATIO", 0), "rsi": row.get("RSI", 50), "dist_ema10": row.get("DIST_EMA10", 0),
        "adx": row.get("ADX", 0), "atr": row["ATR"], "next_open": next_open,
    }


def _trade_record(ticker, sector_map, pos, exit_date, exit_price, reason, holding_days) -> dict:
    return {
        "ticker": ticker, "sector": sector_map.get(ticker, "Unknown"), "entry_date": pos["entry_date"],
        "exit_date": exit_date, "entry_price": pos["entry_price"], "exit_price": exit_price,
        "stop_price": pos["stop_price"], "shares": pos["shares"], "entry_cost": np.nan, "exit_cost": np.nan,
        "pnl_net": np.nan, "r_multiple": np.nan, "exit_reason": reason, "holding_days": holding_days,
        "score": pos["score"],
    }


# ── Live use: next-session candidates and a holding's exit check ──────────────────────
def scan_candidates(ohlcv: dict, benchmarks: dict, market_map: dict, as_of: pd.Timestamp,
                    config: dict | None = None) -> pd.DataFrame:
    """Entries observed at ``as_of`` close for the next session, ranked by the pre-registered score.

    Same filters as the backtest day loop: full signal on ``as_of``, the symbol's own market gate
    open (benchmark Close > SMA50) and a bar on ``as_of``. The next open is unknown live, so the
    stop is reported as ``next_open - 1.5 * ATR`` (ATR value given). No position/slot state is used:
    the recommendation cap is applied by the caller.
    """
    config = config or FIXED_CONFIG
    as_of = pd.Timestamp(as_of)
    validate_market_map(ohlcv.keys(), market_map, benchmarks)
    gates = {}
    for market, bench in benchmarks.items():
        gf = bench.loc[bench.index <= as_of].copy()
        gf[SMA_COL] = gf["Close"].rolling(CONFIG["market_sma"]).mean()
        gates[market] = gf
    gate_open = {market: gate_ok(gf, as_of) for market, gf in gates.items()}
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for ticker, frame in ohlcv.items():
            frame = frame.loc[frame.index <= as_of]
            if frame.empty or frame.index[-1] != as_of:
                continue
            prepared = compute_indicators(frame.tail(CONFIG["warmup_days"] + 1))
            signal = entry_signals({ticker: prepared}, config)[ticker]["full"]
            if not bool(signal.iloc[-1]):
                continue
            market = market_of(market_map, ticker, as_of)
            if not gate_open.get(market, False):
                continue
            row = prepared.iloc[-1]
            candidate = _candidate(ticker, row, np.nan)
            candidate.update(market=market, close=float(row["Close"]), ema10=float(row["EMA10"]),
                             avg_turnover20=float(row["AVG_TURNOVER20"]))
            rows.append(candidate)
    ranked = rank_candidates(rows)
    return ranked.drop(columns=["next_open"], errors="ignore") if not ranked.empty else ranked


def holding_exit(frame: pd.DataFrame, benchmark: pd.DataFrame, entry_date, entry_price: float,
                 as_of, config: dict | None = None) -> dict:
    """Exit check for a manually recorded v4 holding, observed at the ``as_of`` close.

    holding_limit and market_regime are exactly the backtest rules (they only need today's data).
    stop/trailing compare the *next open* in the backtest; that open is unknown before the session,
    so live they are reported on today's close as a warning that the next open is likely to trigger.
    The stop uses the ATR on the session before ``entry_date`` (the signal day), as in the backtest.
    """
    config = config or FIXED_CONFIG
    as_of, entry_date = pd.Timestamp(as_of), pd.Timestamp(entry_date)
    frame = frame.loc[frame.index <= as_of]
    if frame.empty or frame.index[-1] != as_of:
        return {"state": "no_bar", "reason": None}
    # Same window as the published exit monitor series (and a one-day backtest run on ``as_of``).
    prepared = compute_indicators(frame.tail(CONFIG["warmup_days"] + 1))
    before = prepared.loc[prepared.index < entry_date]
    if before.empty:
        return {"state": "insufficient_history", "reason": None}
    signal_atr = float(before["ATR"].iloc[-1])
    stop_price = entry_price - config["atr_multiple"] * signal_atr
    since = prepared.loc[prepared.index >= entry_date, "Close"]
    highest_close = max(float(entry_price), float(since.max()) if not since.empty else float(entry_price))
    row = prepared.iloc[-1]
    # Sessions counted like entry_idx: the signal day is the session before the entry date.
    holding_days = int((prepared.index >= entry_date).sum())
    gf = benchmark.loc[benchmark.index <= as_of].copy()
    gf[SMA_COL] = gf["Close"].rolling(CONFIG["market_sma"]).mean()
    off = regime_off(gf, as_of)
    close = float(row["Close"])
    trailing_level = highest_close - config["trailing_atr_multiple"] * float(row["ATR"])
    checks = [
        ("stop", close <= stop_price),
        ("holding_limit", holding_days >= config["holding_limit"]),
        ("trailing", close <= trailing_level),
        ("market_regime", bool(off) and bool(config.get("regime_exit", True))),
    ]
    reason = next((name for name, hit in checks if hit), None)
    return {"state": "exit_signal" if reason else "hold", "reason": reason,
            "stop_price": round(stop_price, 4), "trailing_level": round(trailing_level, 4),
            "holding_days": holding_days, "holding_limit": config["holding_limit"],
            "highest_close": highest_close, "close": close, "market_regime_off": bool(off)}
