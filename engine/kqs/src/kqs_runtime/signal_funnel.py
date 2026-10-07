"""Daily signal funnel and watch candidates for the two reversion items (EINT-033). Observation only.

After a publication, this explains *why* Bollinger-RSI and RSI-OBV fired or did not: how many
symbols passed each eligibility stage (read from the engine's own stored scan audit) and, among the
eligible ones, how many met each entry condition separately and together. It also lists the
eligible symbols closest to each entry rule as "watch candidates".

It recomputes indicators with the scanner's own functions on the same reader output (halted days
filled as the scanner does). "Conditions met" can exceed "new entry": the strategies are stateful and
signal only the first day of a run of qualifying closes (the simulated position is still open on the
following days), exactly as the scanner's next-session projection does. It
never changes a rule, ranking, cap or publication; watch candidates are not recommendations, are not
validated signals and are never added to any prospective sample. A rolling history of daily counts
is kept for the later step-4 review (which condition, if any, is the bottleneck).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from korea_quant.reversion_scanner import next_session_projection
from korea_quant.strategy import bollinger_rsi_signals, rsi_obv_signals
from kqs_runtime.sqlite_reader import drop_unfillable_halted_rows, fill_halted_trading_days

SCHEMA = "kqs-signal-funnel/v1"
HISTORY_DAYS = 730
HISTORY_KEEP_SESSIONS = 250
WATCH_LIMIT = 10
# Watch-candidate bands (display only; not entry rules): close at most 3% above the lower band with
# RSI <= 35 for Bollinger-RSI; RSI between 27 and 30 with rising OBV, or an RSI cross without the
# OBV confirmation, for RSI-OBV.
SWING_WATCH_BAND_PCT = 3.0
SWING_WATCH_RSI = 35.0
LONG_WATCH_RSI_LOW = 27.0
ELIGIBLE_STATES = {"no_signal", "entry_due_next_session", "regime_gate_blocked"}
STAGE_ORDER = ("stale_security", "insufficient_history", "low_liquidity", "outside_market_cap_tier",
               "outlier_cooldown")


class SignalFunnelError(ValueError):
    """The funnel could not be built."""


def _latest_runtime(snapshot_db: Path, as_of: str) -> dict:
    connection = sqlite3.connect(Path(snapshot_db).resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        row = connection.execute("SELECT runtime_json FROM kqs_recommendation_snapshots WHERE data_as_of = ? "
                                 "ORDER BY generated_at DESC LIMIT 1", (as_of,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise SignalFunnelError(f"No published run for {as_of}.")
    return json.loads(row[0])


def _prices(market_db: Path, symbols: list[str], as_of: str) -> dict[str, pd.DataFrame]:
    if not symbols:
        return {}
    start = (date.fromisoformat(as_of) - timedelta(days=HISTORY_DAYS)).isoformat()
    connection = sqlite3.connect(Path(market_db).resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        marks = ",".join("?" * len(symbols))
        history = pd.read_sql_query(
            "SELECT trade_date AS Date, symbol AS Symbol, name AS Name, market AS Market, open AS Open, high AS High, "
            "low AS Low, close AS Close, volume AS Volume, trading_value AS TradingValue, market_cap AS MarketCap "
            f"FROM daily_prices WHERE trade_date >= ? AND trade_date <= ? AND symbol IN ({marks}) "
            "ORDER BY trade_date, symbol", connection, params=(start, as_of, *symbols))
    finally:
        connection.close()
    history["Date"] = pd.to_datetime(history["Date"])
    history = drop_unfillable_halted_rows(fill_halted_trading_days(history))
    return {symbol: group.set_index("Date").sort_index() for symbol, group in history.groupby("Symbol")}


def _swing_row(prices: pd.DataFrame) -> dict:
    latest = bollinger_rsi_signals(prices["Close"].astype(float)).iloc[-1]
    close, lower, rsi = float(prices["Close"].iloc[-1]), float(latest["bb_lower"]), float(latest["rsi"])
    new_entry = bool(next_session_projection(prices["Close"].astype(float), bollinger_rsi_signals)["entries"])
    return {"close": close, "bb_lower": round(lower, 4), "rsi": round(rsi, 2),
            "band_gap_pct": round((close / lower - 1) * 100, 2), "below_band": close <= lower, "rsi_ok": rsi <= 30,
            "new_entry": new_entry}


def _long_row(prices: pd.DataFrame) -> dict:
    signals = rsi_obv_signals(prices[["Close", "Volume"]].astype(float))
    latest, previous = signals.iloc[-1], signals.iloc[-2]
    new_entry = bool(next_session_projection(prices[["Close", "Volume"]].astype(float), rsi_obv_signals)["entries"])
    return {"rsi": round(float(latest["rsi"]), 2), "previous_rsi": round(float(previous["rsi"]), 2),
            "rsi_cross": bool(latest["rsi_recovery"]), "obv_rising": bool(latest["obv_rising"]), "new_entry": new_entry}


def build_signal_funnel(snapshot_db: Path, market_db: Path, as_of: str, previous: dict | None = None) -> dict:
    if date.fromisoformat(as_of).isoformat() != as_of:
        raise SignalFunnelError("as_of must be YYYY-MM-DD.")
    runtime = _latest_runtime(snapshot_db, as_of)
    audit = runtime.get("audit") or []
    scan = [row for row in audit if "state" in row and row.get("signal_type") != "pullback_v4"]
    by_symbol: dict[str, dict] = {}
    for row in scan:
        by_symbol.setdefault(row["symbol"], row)
    stages = {state: sum(1 for row in by_symbol.values() if row["state"] == state) for state in STAGE_ORDER}
    eligible = sorted(symbol for symbol, row in by_symbol.items() if row["state"] in ELIGIBLE_STATES)
    selected = {item: sum(1 for row in audit if row.get("signal_type") == item and row.get("selection_outcome") == "selected")
                for item in ("swing", "long_term")}
    prices = _prices(Path(market_db), eligible, as_of)
    swing_rows, long_rows = [], []
    for symbol in eligible:
        frame = prices.get(symbol)
        if frame is None or frame.index[-1] != pd.Timestamp(as_of) or len(frame) < 30:
            continue
        meta = {"symbol": symbol, "name": str(frame["Name"].iloc[-1]), "market": str(frame["Market"].iloc[-1]),
                "market_cap_rank": by_symbol[symbol].get("market_cap_rank")}
        swing_rows.append({**meta, **_swing_row(frame)})
        long_rows.append({**meta, **_long_row(frame)})
    funnel = {
        "swing": {"eligible": len(swing_rows), "below_band": sum(r["below_band"] for r in swing_rows),
                  "rsi_le_30": sum(r["rsi_ok"] for r in swing_rows),
                  "both": sum(r["below_band"] and r["rsi_ok"] for r in swing_rows),
                  "new_entry": sum(r["new_entry"] for r in swing_rows),
                  "rsi_le_35": sum(r["rsi"] <= 35 for r in swing_rows), "selected": selected["swing"]},
        "long_term": {"eligible": len(long_rows), "rsi_cross_30": sum(r["rsi_cross"] for r in long_rows),
                      "obv_rising": sum(r["obv_rising"] for r in long_rows),
                      "both": sum(r["rsi_cross"] and r["obv_rising"] for r in long_rows),
                      "new_entry": sum(r["new_entry"] for r in long_rows), "selected": selected["long_term"]},
    }
    swing_watch = sorted(
        ({**r, "missing": [label for ok, label in ((r["below_band"], "볼린저 하단 이하"), (r["rsi_ok"], "RSI ≤ 30")) if not ok]}
         for r in swing_rows if not r["new_entry"]
         and r["band_gap_pct"] <= SWING_WATCH_BAND_PCT and r["rsi"] <= SWING_WATCH_RSI),
        key=lambda r: (max(r["band_gap_pct"], 0) + max(r["rsi"] - 30, 0), r["symbol"]))[:WATCH_LIMIT]
    long_watch = sorted(
        ({**r, "missing": [label for ok, label in ((r["rsi_cross"], "RSI 30 상향 돌파"), (r["obv_rising"], "OBV 9일 평균 위·증가"))
                           if not ok]}
         for r in long_rows if not r["new_entry"]
         and ((r["rsi_cross"] != r["obv_rising"] and (r["rsi_cross"] or LONG_WATCH_RSI_LOW <= r["rsi"] <= 30))
              or (r["rsi_cross"] and r["obv_rising"]))),
        key=lambda r: (abs(r["rsi"] - 30), r["symbol"]))[:WATCH_LIMIT]
    day = {"as_of": as_of, "universe": len(by_symbol), "stages": stages, "eligible": len(eligible), **funnel}
    history = [row for row in (previous or {}).get("history", []) if row.get("as_of") != as_of]
    history = sorted([*history, {"as_of": as_of, "eligible": len(eligible), "swing": funnel["swing"],
                                 "long_term": funnel["long_term"]}], key=lambda row: row["as_of"])[-HISTORY_KEEP_SESSIONS:]
    report = {"schema": SCHEMA, "as_of": as_of, "run_id": runtime.get("run_id"), "today": day,
              "watch": {"swing": swing_watch, "long_term": long_watch}, "history": history,
              "watch_rules": {"swing": f"종가가 볼린저 하단 +{SWING_WATCH_BAND_PCT:g}% 이내이고 RSI ≤ {SWING_WATCH_RSI:g}",
                              "long_term": f"RSI 30 상향 돌파(OBV 미확인) 또는 OBV 상승 중 RSI {LONG_WATCH_RSI_LOW:g}~30"},
              "orders_enabled": False, "recommendation": False}
    report["sha256"] = hashlib.sha256(json.dumps({k: v for k, v in report.items() if k != "history"}, ensure_ascii=False,
                                                 sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return report


def read_previous(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def write_signal_funnel(path: Path, report: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".signal-funnel-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, separators=(",", ":"), default=str)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path
