"""EINT-045: market-wide mean-reversion signal-density shadow (CODX-DIR-001/002 direction, user-approved 2026-10-16).

Prospective only. Arm A is every published ``swing`` and ``long_term`` entry pick, entered at the next
session's open and exited with the item's live native rule (swing: close >= middle band or RSI >= 50;
long_term: RSI >= 50; sell at the next tradable open). Arm B takes the same picks only on days when the
raw signal density -- the number of ``entry_due_next_session`` rows for the two items in that day's
engine scan audit, before the 5-per-item publication cap -- is at least 9. Both arms run through the
same 15-slot, 3-new-per-day portfolio marked to market at every close.

It never changes recommendations, rankings, exits, ledgers or orders. D0 is the first session this
module runs on (written once and carried forward); the verdict is computed once, on the data through
the first KRX month-end after both D0 + 24 months and 100 closed B trades, or REJECT_TOO_SPARSE at the
first month-end after D0 + 36 months, and is then frozen. See
docs/engine/PREREGISTRATION_SIGNAL_DENSITY_v1.md.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from korea_quant.strategy import bollinger_rsi_signals, rsi_obv_signals
from kqs_publication.contract import RECOMMENDATION_SCHEMA_V2
from kqs_publication.store import published_payloads

SCHEMA = "kqs-signal-density-shadow/v1"
PREREGISTRATION = "docs/engine/PREREGISTRATION_SIGNAL_DENSITY_v1.md"
PREREGISTRATION_SHA256 = "072bfa508844d1017f9cf05341729276803f4bf2523e39375a579fe28276a01d"
MIN_DENSITY = 9
ITEMS = ("swing", "long_term")
SLOTS, DAILY_CAP = 15, 3
MIN_B_CLOSED = 100
MIN_RETENTION = 0.20
MIN_MONTHS, MAX_MONTHS = 24, 36
BLOCK_MONTHS, BOOTSTRAP_DRAWS, BOOTSTRAP_SEED = 6, 10_000, 20261016
HISTORY_DAYS = 730
BUY_COST = 0.00015 + 0.0005
SELL_COST = 0.00015 + 0.0018 + 0.0005
PRICE_COLUMNS = ("open", "high", "low", "close")


class SignalDensityShadowError(ValueError):
    """The prospective observation could not be built."""


def add_months(day: str, months: int) -> str:
    d = date.fromisoformat(day)
    year, month = divmod(d.month - 1 + months, 12)
    y, m = d.year + year, month + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last)).isoformat()


def raw_density(runtime: dict) -> int:
    """Raw entry signals for the two reversion items in one scan audit (one row per symbol and item)."""
    return sum(1 for row in runtime.get("audit") or []
               if row.get("state") == "entry_due_next_session" and row.get("signal_type") in ITEMS)


def _runtime(connection: sqlite3.Connection, as_of: str) -> dict | None:
    row = connection.execute("SELECT runtime_json FROM kqs_recommendation_snapshots WHERE data_as_of = ? "
                             "ORDER BY generated_at ASC LIMIT 1", (as_of,)).fetchone()
    return json.loads(row[0]) if row else None


def _halted(bar: dict) -> bool:
    return any(float(bar[column]) <= 0 for column in PRICE_COLUMNS)


def _history(connection: sqlite3.Connection, symbol: str, start: str, through: str) -> pd.DataFrame:
    rows = connection.execute(
        "SELECT trade_date, open, high, low, close, volume FROM daily_prices WHERE symbol = ? "
        "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date", (symbol, start, through)).fetchall()
    return pd.DataFrame(rows, columns=["date", *PRICE_COLUMNS, "volume"]).set_index("date")


def _exit_flags(item: str, bars: pd.DataFrame) -> pd.Series:
    close = bars["close"].astype(float)
    if item == "swing":
        s = bollinger_rsi_signals(close)
        return (close >= s["bb_middle"]) | (s["rsi"] >= 50)
    s = rsi_obv_signals(pd.DataFrame({"Close": close, "Volume": bars["volume"].astype(float)}, index=bars.index))
    return s["rsi"] >= 50


def trade_path(bars: pd.DataFrame, exit_flags: pd.Series, sessions: list[str], entry_day: str) -> dict:
    """Next-open entry, native exit at the open after the first qualifying close; daily closes for marking."""
    if entry_day not in bars.index:
        return {"status": "missing_bar", "entry_date": entry_day}
    first = bars.loc[entry_day].to_dict()
    if _halted(first):
        return {"status": "entry_halted", "entry_date": entry_day}
    out = {"entry_date": entry_day, "entry_price": float(first["open"]), "exit_signal_date": None, "exit_date": None,
           "exit_price": None, "net_return_pct": None, "closes": {}}
    pending = False
    for day in sessions[sessions.index(entry_day):]:
        if day not in bars.index:
            return {**out, "status": "missing_bar"}
        bar = bars.loc[day].to_dict()
        if pending:
            if _halted(bar):
                continue                                  # sell at the next tradable open; mark stays at the last close
            price = float(bar["open"])
            out.update(exit_date=day, exit_price=price,
                       net_return_pct=round((price * (1 - SELL_COST) / (out["entry_price"] * (1 + BUY_COST)) - 1) * 100, 4))
            return {**out, "status": "closed"}
        if not _halted(bar):
            out["closes"][day] = float(bar["close"])
            if bool(exit_flags.get(day, False)):
                pending = True
                out["exit_signal_date"] = day
    return {**out, "status": "open"}


def simulate(trades: list[dict], sessions: list[str]) -> pd.Series:
    """Same portfolio for both arms: 15 equal slots, at most 3 new entries per day in publication order,
    each entry sized at 1/15 of equity (or the cash left), marked to market at every close."""
    by_entry: dict[str, list[dict]] = {}
    for t in trades:
        by_entry.setdefault(t["entry_date"], []).append(t)
    cash, held, equity = 1.0, [], {}
    for day in sessions:
        for pos in [p for p in held if p["t"]["exit_date"] == day]:
            cash += pos["shares"] * pos["t"]["exit_price"] * (1 - SELL_COST)
            held.remove(pos)
        value_now = cash + sum(p["shares"] * p["mark"] for p in held)
        for t in sorted(by_entry.get(day, []), key=lambda t: t["order"])[:DAILY_CAP]:
            if len(held) >= SLOTS or cash <= 0 or any(p["t"]["symbol"] == t["symbol"] for p in held):
                continue
            budget = min(cash, value_now / SLOTS)
            shares = budget / (t["entry_price"] * (1 + BUY_COST))
            cash -= budget
            held.append({"t": t, "shares": shares, "mark": t["entry_price"]})
        for p in held:
            p["mark"] = p["t"]["closes"].get(day, p["mark"])
        equity[day] = cash + sum(p["shares"] * p["mark"] for p in held)
    return pd.Series(equity, dtype=float)


def _cagr(series: pd.Series) -> float | None:
    if len(series) < 2:
        return None
    days = (date.fromisoformat(series.index[-1]) - date.fromisoformat(series.index[0])).days
    return round((series.iloc[-1] / series.iloc[0]) ** (365.25 / days) - 1, 6) if days > 0 else None


def _mdd(series: pd.Series) -> float | None:
    return round(float((series / series.cummax() - 1).min()), 6) if len(series) else None


def _monthly(series: pd.Series) -> pd.Series:
    month_end = series.groupby(series.index.str[:7]).last()
    return month_end.pct_change().dropna()


def block_bootstrap_cagr_diff(a: pd.Series, b: pd.Series) -> list[float] | None:
    """95% interval of annualized(B) - annualized(A) from monthly returns, 6-month moving blocks, fixed seed."""
    diff_index = a.index.intersection(b.index)
    ra, rb = a.loc[diff_index].to_list(), b.loc[diff_index].to_list()
    n = len(ra)
    if n < BLOCK_MONTHS:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    stats = []
    for _ in range(BOOTSTRAP_DRAWS):
        picks = []
        while len(picks) < n:
            start = rng.randrange(0, n - BLOCK_MONTHS + 1)
            picks.extend(range(start, start + BLOCK_MONTHS))
        picks = picks[:n]
        ga = math.prod(1 + ra[i] for i in picks) ** (12 / n) - 1
        gb = math.prod(1 + rb[i] for i in picks) ** (12 / n) - 1
        stats.append(gb - ga)
    stats.sort()
    return [round(stats[int(0.025 * BOOTSTRAP_DRAWS)], 6), round(stats[int(0.975 * BOOTSTRAP_DRAWS) - 1], 6)]


def evaluate(rows: list[dict], sessions: list[str], cutoff: str) -> dict:
    """Portfolio and trade metrics for both arms using only sessions <= cutoff."""
    days = [d for d in sessions if d <= cutoff]
    trades_a, trades_b = [], []
    for r in rows:
        arm = r["arm"]
        if arm.get("status") not in ("closed", "open") or arm["entry_date"] > cutoff:
            continue
        t = {**arm, "symbol": r["symbol"], "order": (r["as_of"], ITEMS.index(r["item"]), r["rank"] or 0)}
        if t["exit_date"] and t["exit_date"] > cutoff:        # not yet sold as of the cutoff
            t.update(exit_date=None, exit_price=None, net_return_pct=None)
        trades_a.append(t)
        if r["b_enters"]:
            trades_b.append(t)
    eq_a, eq_b = simulate(trades_a, days), simulate(trades_b, days)
    closed_a = [t for t in trades_a if t["exit_date"]]
    closed_b = [t for t in trades_b if t["exit_date"]]
    return {"cutoff": cutoff, "a_trades": len(trades_a), "b_trades": len(trades_b), "a_closed": len(closed_a),
            "b_closed": len(closed_b), "retention": round(len(closed_b) / len(closed_a), 4) if closed_a else None,
            "a_mean_net_pct": round(sum(t["net_return_pct"] for t in closed_a) / len(closed_a), 4) if closed_a else None,
            "b_mean_net_pct": round(sum(t["net_return_pct"] for t in closed_b) / len(closed_b), 4) if closed_b else None,
            "a_cagr": _cagr(eq_a), "b_cagr": _cagr(eq_b), "a_daily_mdd": _mdd(eq_a), "b_daily_mdd": _mdd(eq_b),
            "cagr_diff_ci95": block_bootstrap_cagr_diff(_monthly(eq_a), _monthly(eq_b))}


def judgment_cutoff(d0: str, sessions: list[str], b_closed_by: dict[str, int]) -> tuple[str | None, str]:
    """First month-end session (last session of a month that is already over) satisfying the timing rule."""
    month_ends = [d for d, nxt in zip(sessions, sessions[1:]) if d[:7] != nxt[:7]]
    for end in month_ends:
        if end >= add_months(d0, MIN_MONTHS) and b_closed_by.get(end, 0) >= MIN_B_CLOSED:
            return end, "due"
        if end >= add_months(d0, MAX_MONTHS):
            return end, "deadline"
    return None, "not_due"


def verdict(metrics: dict, kind: str) -> str:
    if kind == "deadline" and (metrics["b_closed"] < MIN_B_CLOSED):
        return "REJECT_TOO_SPARSE"
    if (metrics["retention"] or 0) < MIN_RETENTION:
        return "FAIL"
    ci = metrics["cagr_diff_ci95"]
    better = (metrics["b_cagr"] or -1) > (metrics["a_cagr"] or -1)
    mdd_ok = (metrics["b_daily_mdd"] or -1) >= (metrics["a_daily_mdd"] or -1)
    if better and mdd_ok and ci and ci[0] > 0:
        return "PASS_CANDIDATE"
    return "FAIL" if (not better or not mdd_ok or (ci and ci[1] < 0)) else "INCONCLUSIVE"


def build_signal_density_shadow(snapshot_db: Path, market_db: Path, through: str, previous: dict | None = None) -> dict:
    if date.fromisoformat(through).isoformat() != through:
        raise SignalDensityShadowError("through must be YYYY-MM-DD.")
    d0 = (previous or {}).get("d0") or through
    known_density = {r["as_of"]: r["density"] for r in (previous or {}).get("days", [])}
    payloads = [p for p in published_payloads(Path(snapshot_db), schema=RECOMMENDATION_SCHEMA_V2, through=through)
                if p["engine"]["data_as_of"] >= d0]
    snap = sqlite3.connect(Path(snapshot_db).resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        days = []
        for p in payloads:
            as_of = p["engine"]["data_as_of"]
            if as_of in known_density:                     # first recorded value is kept
                days.append({"as_of": as_of, "density": known_density[as_of]})
                continue
            runtime = _runtime(snap, as_of)
            if runtime is not None:
                days.append({"as_of": as_of, "density": raw_density(runtime)})
    finally:
        snap.close()
    density = {d["as_of"]: d["density"] for d in days}
    market = sqlite3.connect(Path(market_db).resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        market.execute("PRAGMA query_only = ON")
        sessions = [r[0] for r in market.execute(
            "SELECT DISTINCT trade_date FROM daily_prices WHERE market IN ('KOSPI','KOSDAQ') AND trade_date > ? "
            "AND trade_date <= ? ORDER BY trade_date", (d0, through))]
        rows = []
        for p in payloads:
            as_of = p["engine"]["data_as_of"]
            if as_of not in density:
                continue
            for s in p["signals"]:
                if s.get("action") != "entry" or s.get("selection_outcome") != "selected" or s.get("signal_type") not in ITEMS:
                    continue
                row = {"as_of": as_of, "item": s["signal_type"], "symbol": s["symbol"], "name": s.get("name"),
                       "rank": s.get("selection_rank"), "density": density[as_of], "b_enters": density[as_of] >= MIN_DENSITY}
                after = [d for d in sessions if d > as_of]
                if not after:
                    rows.append({**row, "arm": {"status": "awaiting_entry"}})
                    continue
                start = (date.fromisoformat(as_of) - timedelta(days=HISTORY_DAYS)).isoformat()
                bars = _history(market, s["symbol"], start, through)
                rows.append({**row, "arm": trade_path(bars, _exit_flags(s["signal_type"], bars), after, after[0])
                             if len(bars) >= 30 else {"status": "missing_bar"}})
    except sqlite3.Error as exc:
        raise SignalDensityShadowError(f"signal density shadow query failed: {exc}") from exc
    finally:
        market.close()
    rows.sort(key=lambda r: (r["as_of"], ITEMS.index(r["item"]), r["rank"] or 0, r["symbol"]))
    closed_b_dates = sorted(r["arm"]["exit_date"] for r in rows if r["b_enters"] and r["arm"].get("status") == "closed")
    b_closed_by = {d: sum(1 for x in closed_b_dates if x <= d) for d in sessions}
    judged = (previous or {}).get("judgment")
    if not judged:
        cutoff, kind = judgment_cutoff(d0, sessions, b_closed_by)
        if cutoff:
            m = evaluate(rows, sessions, cutoff)
            judged = {"kind": kind, "cutoff": cutoff, "verdict": verdict(m, kind), "metrics": m}
    for r in rows:                                        # daily closes are needed only for the evaluation above
        r["arm"] = {k: v for k, v in r["arm"].items() if k != "closes"}
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema": SCHEMA, "through": through, "d0": d0, "preregistration": PREREGISTRATION,
            "preregistration_sha256": PREREGISTRATION_SHA256,
            "min_density": MIN_DENSITY, "earliest_judgment_from": add_months(d0, MIN_MONTHS),
            "deadline_from": add_months(d0, MAX_MONTHS), "recommendations_changed": False, "orders_enabled": False,
            "days": days, "judgment": judged, "status": judged["verdict"] if judged else "not_due",
            "counts": {"signals": len(rows), "b_signals": sum(r["b_enters"] for r in rows),
                       "a_closed": sum(r["arm"].get("status") == "closed" for r in rows), "b_closed": len(closed_b_dates),
                       "dense_days": sum(d["density"] >= MIN_DENSITY for d in days), "days": len(days)},
            "rows_sha256": digest, "rows": rows,
            "note": "Before the judgment only counts are shown; interim returns are not computed or displayed."}


def read_previous(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_signal_density_shadow(path: Path, report: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".signal-density-shadow-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, separators=(",", ":"))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path
