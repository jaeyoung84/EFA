"""One-shot, read-only exploratory comparison for CODX-MA-001.

Codex 수정: CODX-MA-001 (2026-10-16), 파일 전체.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREREG = HERE / "PREREGISTRATION.md"
RESULT = HERE / "RESULT.json"
DEFAULT_DB = ROOT / "research" / "kr_value_quality_v1" / "data" / "krx_daily.sqlite3"
TIERS = {"KOSPI": 200, "KOSDAQ": 150}
BUY_COST = 0.00015 + 0.0005
SELL_COST = 0.00015 + 0.0018 + 0.0005
TRAINING_SESSIONS = 252


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect_immutable(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = "file:" + str(path) + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def top_rank_rows(connection: sqlite3.Connection) -> pd.DataFrame:
    rows: list[tuple[str, str, str, int]] = []
    cursor = connection.execute(
        """
        SELECT trade_date, symbol, market, market_cap
        FROM daily_prices
        WHERE market IN ('KOSPI','KOSDAQ')
        ORDER BY trade_date, market, market_cap DESC, symbol
        """
    )
    current = None
    rank = 0
    for day, symbol, market, _ in cursor:
        key = (day, market)
        if key != current:
            current, rank = key, 1
        else:
            rank += 1
        if rank <= TIERS[market]:
            rows.append((day, str(symbol), market, rank))
    return pd.DataFrame(rows, columns=["Date", "Symbol", "Market", "MarketCapRank"])


def candidate_history(connection: sqlite3.Connection, symbols: set[str]) -> pd.DataFrame:
    ordered = sorted(symbols)
    placeholders = ",".join("?" for _ in ordered)
    # Keep the full scan and filtering inside SQLite's C loop. Iterating all
    # 7.5 million rows through Python is needlessly slow over a WSL UNC path.
    return pd.read_sql_query(
        f"""
        SELECT trade_date, symbol, name, market, open, high, low, close,
               volume, trading_value, market_cap
        FROM daily_prices
        WHERE market IN ('KOSPI','KOSDAQ') AND symbol IN ({placeholders})
        ORDER BY symbol, trade_date
        """,
        connection,
        params=ordered,
    ).rename(
        columns={
            "trade_date": "Date", "symbol": "Symbol", "name": "Name", "market": "Market",
            "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume",
            "trading_value": "TradingValue", "market_cap": "MarketCap",
        }
    )


def fill_halts(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values(["Symbol", "Date"]).reset_index(drop=True)
    price_columns = ["Open", "High", "Low", "Close"]
    halted = frame[price_columns].le(0).any(axis=1)
    frame.loc[halted, price_columns] = np.nan
    prior = frame.groupby("Symbol", sort=False)["Close"].ffill()
    frame.loc[halted, price_columns] = np.repeat(prior.loc[halted].to_numpy()[:, None], 4, axis=1)
    return frame.dropna(subset=price_columns).reset_index(drop=True)


def eligibility(history: pd.DataFrame, ranks: pd.DataFrame) -> pd.DataFrame:
    data = history.merge(ranks, on=["Date", "Symbol", "Market"], how="left", validate="many_to_one")
    data = data.sort_values(["Market", "Symbol", "Date"]).reset_index(drop=True)
    groups = data.groupby(["Market", "Symbol"], sort=False)
    data["AverageTradingValue"] = groups["TradingValue"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    outlier = groups["Close"].pct_change(fill_method=None).abs().ge(0.50)
    data["OutlierCooldown"] = outlier.groupby(
        [data["Market"], data["Symbol"]], sort=False
    ).transform(lambda values: values.rolling(60, min_periods=1).max().astype(bool))
    enough = groups.cumcount().add(1).ge(260)
    observed = (
        data["MarketCapRank"].notna()
        & data["AverageTradingValue"].ge(5_000_000_000)
        & enough
        & ~data["OutlierCooldown"]
    )
    data["EntryAllowed"] = observed.groupby(
        [data["Market"], data["Symbol"]], sort=False
    ).shift(1, fill_value=False)
    data["PriorMarketCapRank"] = data.groupby(
        ["Market", "Symbol"], sort=False
    )["MarketCapRank"].shift(1)
    return data


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    value = 100 - 100 / (1 + avg_gain / avg_loss)
    value = value.mask((avg_gain == 0) & (avg_loss == 0))
    return value.mask((avg_loss == 0) & (avg_gain > 0), 100.0)


def long_only_events(entry_observed: pd.Series, exit_observed: pd.Series) -> tuple[pd.Series, pd.Series]:
    holding = False
    entries, exits = [], []
    for enter, exit_ in zip(entry_observed.fillna(False), exit_observed.fillna(False), strict=True):
        entry = bool(enter) and not holding
        exit_signal = bool(exit_) and holding
        if entry:
            holding = True
        elif exit_signal:
            holding = False
        entries.append(entry)
        exits.append(exit_signal)
    return pd.Series(entries, index=entry_observed.index), pd.Series(exits, index=entry_observed.index)


def signals(group: pd.DataFrame, sma60_filter: bool) -> tuple[pd.Series, pd.Series, pd.Series]:
    close = group["Close"].astype(float)
    volume = group["Volume"].astype(float)
    level = rsi(close)
    direction = close.diff().map(lambda value: 1 if value > 0 else (-1 if value < 0 else 0))
    obv = (volume.fillna(0) * direction).cumsum()
    obv_signal = obv.rolling(9, min_periods=9).mean()
    entry_observed = (
        (level > 30) & (level.shift(1) <= 30) & (obv > obv_signal) & (obv.diff() > 0)
    )
    sma60 = close.rolling(60, min_periods=60).mean()
    if sma60_filter:
        entry_observed &= close > sma60
    entry, exit_ = long_only_events(entry_observed, level >= 50)
    return entry.shift(1, fill_value=False), exit_.shift(1, fill_value=False), sma60


def trade_blocks(group: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    previous = group["Close"].shift(1)
    halted = group["Volume"].eq(0) | group["Volume"].isna()
    limit_up = previous * 1.295
    limit_down = previous * 0.705
    cant_buy = halted | (group["Open"].ge(limit_up) & group["Low"].ge(limit_up))
    cant_sell = halted | (group["Open"].le(limit_down) & group["High"].le(limit_down))
    return cant_buy, cant_sell


def delayed_exits(exits: pd.Series, cant_sell: pd.Series) -> pd.Series:
    values = exits.to_numpy(dtype=bool, copy=True)
    blocked = cant_sell.to_numpy(dtype=bool)
    for index in range(len(values) - 1):
        if values[index] and blocked[index]:
            values[index] = False
            values[index + 1] = True
    if len(values) and values[-1] and blocked[-1]:
        values[-1] = False
    return pd.Series(values, index=exits.index)


def costed_return(entry_price: float, exit_price: float) -> float:
    paid = entry_price * (1 + 0.0005) * (1 + 0.00015)
    received = exit_price * (1 - 0.0005) * (1 - 0.00015 - 0.0018)
    return received / paid - 1


def simulate(group: pd.DataFrame, variant: str, dates: pd.DatetimeIndex) -> tuple[list[dict], dict]:
    filtered = variant == "sma60"
    entries, exits, sma60 = signals(group, filtered)
    cant_buy, cant_sell = trade_blocks(group)
    entries = entries.astype(bool) & group["EntryAllowed"].astype(bool) & ~cant_buy
    exits = delayed_exits(exits.astype(bool), cant_sell)
    rows = []
    holding = None
    for position, (_, bar) in enumerate(group.iterrows()):
        if holding is None and bool(entries.iloc[position]):
            holding = {
                "entry_pos": position,
                "Entry Timestamp": pd.Timestamp(bar["Date"]),
                "EntryPrice": float(bar["Open"]),
                "PriorMarketCapRank": float(bar["PriorMarketCapRank"]),
                "SignalClose": float(group.iloc[position - 1]["Close"]) if position else math.nan,
                "SignalSMA60": float(sma60.iloc[position - 1]) if position and pd.notna(sma60.iloc[position - 1]) else math.nan,
            }
        elif holding is not None and bool(exits.iloc[position]):
            ret = costed_return(holding["EntryPrice"], float(bar["Open"]))
            entry_loc = dates.get_indexer([holding["Entry Timestamp"]], method="pad")[0]
            rows.append({
                **holding, "Exit Timestamp": pd.Timestamp(bar["Date"]), "ExitPrice": float(bar["Open"]),
                "NetReturnPct": ret, "Status": "closed", "HoldingSessions": position - holding["entry_pos"],
                "Window": ((entry_loc - TRAINING_SESSIONS) // 63 + 1) if entry_loc >= TRAINING_SESSIONS else None,
            })
            holding = None
    if holding is not None:
        last = group.iloc[-1]
        rows.append({
            **holding, "Exit Timestamp": pd.Timestamp(last["Date"]), "ExitPrice": float(last["Close"]),
            "NetReturnPct": costed_return(holding["EntryPrice"], float(last["Close"])),
            "Status": "open", "HoldingSessions": len(group) - 1 - holding["entry_pos"], "Window": None,
        })
    diagnostics = {
        "entry_events_after_eligibility": int(entries.sum()),
        "signal_close_above_sma60_count": int(
            sum(1 for row in rows if pd.notna(row["SignalSMA60"]) and row["SignalClose"] > row["SignalSMA60"])
        ),
    }
    return rows, diagnostics


def metrics(frame: pd.DataFrame) -> dict:
    closed = frame.loc[frame["Status"].eq("closed")].copy()
    values = closed["NetReturnPct"].astype(float)
    wins, losses = values[values > 0], values[values <= 0]
    gross_profit, gross_loss = float(wins.sum()), float(-losses.sum())
    per_window = closed.dropna(subset=["Window"]).groupby("Window")["NetReturnPct"].sum()
    return {
        "closed_trades": int(len(closed)), "open_trades": int(frame["Status"].eq("open").sum()),
        "win_rate": float((values > 0).mean()) if len(values) else None,
        "mean_net_return": float(values.mean()) if len(values) else None,
        "median_net_return": float(values.median()) if len(values) else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "payoff_ratio": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else None,
        "q05_net_return": float(values.quantile(0.05)) if len(values) else None,
        "q95_net_return": float(values.quantile(0.95)) if len(values) else None,
        "average_holding_sessions": float(closed["HoldingSessions"].mean()) if len(closed) else None,
        "profitable_window_share": float((per_window > 0).mean()) if len(per_window) else None,
        "window_count": int(len(per_window)),
    }


def deploy(frame: pd.DataFrame, history_by_symbol: dict[str, pd.DataFrame], first_day: pd.Timestamp,
           last_day: pd.Timestamp) -> dict:
    data = frame.copy()
    for idx, row in data.iterrows():
        cap = pd.Timestamp(row["Entry Timestamp"]) + pd.Timedelta(days=365)
        if row["Status"] == "closed" and pd.Timestamp(row["Exit Timestamp"]) <= cap:
            continue
        group = history_by_symbol[str(row["Symbol"])]
        eligible = group.loc[group["Date"].ge(cap)].copy()
        if not eligible.empty:
            _, cant_sell = trade_blocks(group)
            eligible = eligible.loc[~cant_sell.loc[eligible.index]]
        if not eligible.empty:
            selected = eligible.iloc[0]
            data.at[idx, "Exit Timestamp"] = pd.Timestamp(selected["Date"])
            data.at[idx, "NetReturnPct"] = costed_return(float(row["EntryPrice"]), float(selected["Open"]))
        else:
            selected = group.iloc[-1]
            data.at[idx, "Exit Timestamp"] = pd.Timestamp(selected["Date"])
            data.at[idx, "NetReturnPct"] = costed_return(float(row["EntryPrice"]), float(selected["Close"]))
        data.at[idx, "Status"] = "closed"

    data = data.sort_values(["Entry Timestamp", "PriorMarketCapRank", "Symbol"], kind="stable")
    slots_free = [pd.Timestamp.min] * 15
    held_until: dict[str, pd.Timestamp] = {}
    events = []
    skipped_cap = skipped_slot = skipped_duplicate = 0
    for day, candidates in data.groupby("Entry Timestamp", sort=True):
        entered = 0
        for _, row in candidates.iterrows():
            symbol, exit_day = str(row["Symbol"]), pd.Timestamp(row["Exit Timestamp"])
            if held_until.get(symbol, pd.Timestamp.min) >= day:
                skipped_duplicate += 1
                continue
            if entered >= 3:
                skipped_cap += 1
                continue
            free = next((index for index, available in enumerate(slots_free) if available <= day), None)
            if free is None:
                skipped_slot += 1
                continue
            slots_free[free] = exit_day
            held_until[symbol] = exit_day
            events.append((pd.Timestamp(day), exit_day, float(row["NetReturnPct"])))
            entered += 1

    entries = defaultdict(list)
    exits = defaultdict(list)
    for position, (entry_day, exit_day, net_return) in enumerate(events):
        entries[entry_day].append(position)
        exits[exit_day].append((position, net_return))
    cash, principals, equity, peak, mdd = 1.0, {}, 1.0, 1.0, 0.0
    for day in sorted(set(entries) | set(exits)):
        for position, net_return in exits.get(day, []):
            principal = principals.pop(position, None)
            if principal is not None:
                cash += principal * (1 + net_return)
        for position in entries.get(day, []):
            principal = min((cash + sum(principals.values())) / 15, cash)
            if principal > 0:
                cash -= principal
                principals[position] = principal
        equity = cash + sum(principals.values())
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)
    years = max((last_day - first_day).days / 365.2425, 1e-9)
    cagr = equity ** (1 / years) - 1 if equity > 0 else -1.0
    calendar = pd.date_range(first_day, last_day, freq="D")
    exposed = sum(any(entry <= day < exit_ for entry, exit_, _ in events) for day in calendar)
    return {
        "cagr": float(cagr), "realized_event_mdd": float(mdd), "ending_equity_multiple": float(equity),
        "deployed_trades": len(events), "skipped_daily_cap": skipped_cap, "skipped_no_slot": skipped_slot,
        "skipped_duplicate": skipped_duplicate, "calendar_exposure_share": exposed / len(calendar),
        "note": "MDD is measured at entry/exit cash events and understates intrahold drawdown.",
    }


def main() -> int:
    if RESULT.exists():
        raise SystemExit("RESULT.json already exists; CODX-MA-001 is one-shot.")
    db = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_DB.resolve()
    print("[1/7] hashing inputs", flush=True)
    hashes = {"database_sha256": sha256(db), "preregistration_sha256": sha256(PREREG), "runner_sha256": sha256(Path(__file__))}
    with connect_immutable(db) as connection:
        bounds = connection.execute(
            "SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT symbol) FROM daily_prices"
        ).fetchone()
        print("[2/7] calculating point-in-time market-cap ranks", flush=True)
        ranks = top_rank_rows(connection)
        symbols = set(ranks["Symbol"])
        print(f"[3/7] loading history for {len(symbols)} candidate symbols", flush=True)
        history = candidate_history(connection, symbols)
    history["Date"] = pd.to_datetime(history["Date"])
    ranks["Date"] = pd.to_datetime(ranks["Date"])
    print("[4/7] applying common eligibility", flush=True)
    history = eligibility(fill_halts(history), ranks)
    dates = pd.DatetimeIndex(history["Date"].drop_duplicates().sort_values())
    measured_start = dates[TRAINING_SESSIONS]
    history_by_symbol = {str(symbol): group.copy() for symbol, group in history.groupby("Symbol", sort=True)}
    all_rows, diagnostic = {}, {}
    for variant in ("baseline", "sma60"):
        print(f"[5/7] simulating {variant}", flush=True)
        rows, details = [], {"entry_events_after_eligibility": 0, "signal_close_above_sma60_count": 0}
        for symbol, group in history_by_symbol.items():
            result, diag = simulate(group.reset_index(drop=True), variant, dates)
            for row in result:
                row.update({"Symbol": symbol, "Market": str(group["Market"].iloc[-1]), "Variant": variant})
            rows.extend(row for row in result if row["Entry Timestamp"] >= measured_start)
            for key in details:
                details[key] += diag[key]
        all_rows[variant] = pd.DataFrame(rows)
        diagnostic[variant] = details
    print("[6/7] calculating trade and 15-slot portfolio metrics", flush=True)
    report = {}
    for variant, frame in all_rows.items():
        report[variant] = {
            "all": metrics(frame),
            "KOSPI": metrics(frame.loc[frame["Market"].eq("KOSPI")]),
            "KOSDAQ": metrics(frame.loc[frame["Market"].eq("KOSDAQ")]),
            "deployment": deploy(frame, history_by_symbol, measured_start, dates[-1]),
            "diagnostics": diagnostic[variant],
        }
    baseline_keys = set(zip(all_rows["baseline"]["Symbol"], all_rows["baseline"]["Entry Timestamp"]))
    variant_keys = set(zip(all_rows["sma60"]["Symbol"], all_rows["sma60"]["Entry Timestamp"]))
    report["entry_overlap"] = {
        "same_entries": len(baseline_keys & variant_keys),
        "baseline_only": len(baseline_keys - variant_keys),
        "sma60_only_later_state_entries": len(variant_keys - baseline_keys),
    }
    payload = {
        "change_id": "CODX-MA-001", "status": "exploratory_only_consumed_historical_window",
        "rule": "RSI-OBV baseline versus baseline AND signal-close>SMA60; market SMA200 gate OFF",
        "database": {"path": str(db), "min_date": bounds[0], "max_date": bounds[1], "rows": bounds[2], "symbols": bounds[3], **hashes},
        "measured_start": str(measured_start.date()), "measured_end": str(dates[-1].date()),
        "costs": {"buy_total": BUY_COST, "sell_total": SELL_COST},
        "eligibility": {"KOSPI_rank": 200, "KOSDAQ_rank": 150, "average_trading_value_20_min": 5_000_000_000,
                        "minimum_history_sessions": 260, "outlier_threshold": 0.50, "cooldown_sessions": 60},
        "results": report,
        "limitations": [
            "The historical window was already consumed by prior mean-reversion research; this is not an independent validation.",
            "Universe membership is reconstructed by point-in-time market-cap rank, not historical KOSPI200/KOSDAQ150 constituent files.",
            "Portfolio MDD is based on realized entry/exit cash events and understates intrahold drawdown.",
            "No recommendation, order, live rule, or existing forward counter was changed.",
        ],
    }
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[7/7] RESULT.json written", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

