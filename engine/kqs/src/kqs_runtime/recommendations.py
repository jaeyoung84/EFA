"""Pure recommendation generation. No positions, fills, costs or ledger writes.

EINT-017 (user operating policy, not a validated improvement): three items are published side
by side -- swing (Bollinger-RSI), long_term (RSI-OBV) and pullback_v4 (Daily Trend Pullback v4).
Each item keeps its own filters and its own existing order (reversion: D-24 market-cap rank /
symbol; v4: the pre-registered score) and is capped at 5 names per session. A symbol may appear
in more than one item. The former global cap of 3 (D-9) and the swing-over-long_term duplicate
rule no longer apply to the published list, so these recommendations are not comparable with
the D-33/D-42/D-43 prospective observation samples.
"""

import hashlib
import json
from dataclasses import asdict

import numpy as np
import pandas as pd

from korea_quant.eligibility import DEFAULT_MARKET_BENCHMARKS
from korea_quant.reversion_scanner import (
    ReversionScannerConfig, live_selection_key, native_exit_observed, reversion_rule_parameters,
    scan_reversion_candidates,
)
from korea_quant.strategy import bollinger_rsi_signals, rsi_obv_signals
from kqs_runtime import PROTOCOL
from kqs_runtime import pullback_v4 as v4
from kqs_runtime.inputs import COLUMNS, normalize_symbol, validated_inputs
from kqs_runtime.provenance import code_identity, digest

# EINT-023 (user decision, 2026-10-02): the D-19 market regime gate (W=200, bearish) is turned OFF for the
# live reversion items. The user judges the market and records the decisions for learning; the market state
# stays visible as a reference reason (benchmark vs its 200-day average), not as an entry filter.
# The D-33 forward observation under the gate ends at the 2026-10-01 session; a gate-off stream starts here.
APPROVED_CONFIG = ReversionScannerConfig(market_filter_window=0, market_filter_inverted=True)
ITEMS = ("swing", "long_term", "pullback_v4")
MAX_PICKS_PER_ITEM = 5
EXIT_SERIES_SESSIONS = 40
ITEM_LABELS = {
    "swing": "Bollinger-RSI 과매도 평균회귀",
    "long_term": "RSI-OBV 회복 장기",
    "pullback_v4": "Trend Pullback v4 상승추세 눌림목",
}


def selection_audit(scan: pd.DataFrame, excluded_symbols: tuple[str, ...] = ()) -> list[dict]:
    """D-24 order inside each reversion item, capped per item (EINT-017), explicit caller context."""
    excluded = {normalize_symbol(symbol) for symbol in excluded_symbols}
    rows = scan.to_dict("records")
    eligible = []
    outcomes = {}
    for index, row in enumerate(rows):
        symbol = normalize_symbol(row["symbol"])
        row["symbol"] = symbol
        if row["state"] == "regime_gate_blocked":
            outcomes[index] = "regime_gate_blocked"
        elif row["state"] != "entry_due_next_session":
            outcomes[index] = "not_entry_signal"
        elif row["signal_type"] not in ("swing", "long_term"):
            outcomes[index] = "unsupported_signal_type"
        elif symbol in excluded:
            outcomes[index] = "already_tracked"
        else:
            key = live_selection_key(row["market_cap_rank"], symbol, row["signal_type"])
            eligible.append((key, index))
    selected: dict[str, set] = {item: set() for item in ("swing", "long_term")}
    for _key, index in sorted(eligible):
        item = rows[index]["signal_type"]
        symbol = rows[index]["symbol"]
        if symbol in selected[item]:
            outcomes[index] = "duplicate_symbol"
        elif len(selected[item]) >= MAX_PICKS_PER_ITEM:
            outcomes[index] = "daily_cap"
        else:
            selected[item].add(symbol)
            outcomes[index] = "selected"
    eligible_indices = {index for _, index in eligible}
    order = [index for _, index in sorted(eligible)]
    order.extend(index for index in range(len(rows)) if index not in eligible_indices)
    return [dict(rows[index], selection_outcome=outcomes[index]) for index in order]


def _records(frame: pd.DataFrame) -> list[dict]:
    # pandas converts nullable ranks to JSON null (no nonstandard NaN in output).
    return json.loads(frame.to_json(orient="records", date_format="iso", double_precision=15))


def input_fingerprint(data: pd.DataFrame, benchmarks: dict[str, pd.Series]) -> str:
    """Stream canonical rows, preserving full binary64 precision rather than rounded JSON."""
    hasher = hashlib.sha256()
    def add(row: list) -> None:
        hasher.update(json.dumps(row, ensure_ascii=False, separators=(",", ":"),
                                 allow_nan=False).encode("utf-8") + b"\n")
    add(["kqs-input-fingerprint/v1", COLUMNS])
    for row in data[COLUMNS].itertuples(index=False, name=None):
        add([row[0].date().isoformat(), *[str(value) for value in row[1:4]],
             *[float(value).hex() for value in row[4:]]])
    for market, series in sorted(benchmarks.items()):
        add(["benchmark", market])
        for observed_date, value in series.items():
            add([observed_date.date().isoformat(), float(value).hex()])
    return hasher.hexdigest()


def explain(row: dict, prices: pd.DataFrame, benchmark: pd.Series) -> dict:
    market_sma = float(benchmark.tail(200).mean())
    market_gap = (float(benchmark.iloc[-1]) / market_sma - 1) * 100
    features = {"close": float(prices["Close"].iloc[-1]),
                "benchmark_close": float(benchmark.iloc[-1]), "benchmark_sma200": market_sma,
                "benchmark_gap_pct": round(market_gap, 2),
                "market_cap_rank": int(row["market_cap_rank"]),
                "average_trading_value20": float(row["average_trading_value"])}
    common = [f"{row['market']} 시가총액 순위 {features['market_cap_rank']}위: 선정 범위 통과",
              f"20거래일 평균 거래대금 {features['average_trading_value20']:,.0f}원: 50억원 이상",
              f"참고: 시장 ETF 종가 {features['benchmark_close']:,.2f} · 200일 평균 {market_sma:,.2f} "
              f"({market_gap:+.1f}%, {'위' if market_gap >= 0 else '아래'}) — 매수 조건 아님, 시장 판단용"]
    if row["signal_type"] == "swing":
        signals = bollinger_rsi_signals(prices["Close"])
        latest = signals.iloc[-1]
        features.update(rsi14=float(latest["rsi"]), bb_lower20=float(latest["bb_lower"]),
                        bb_middle20=float(latest["bb_middle"]))
        rule = [f"종가 {features['close']:,.2f} ≤ 볼린저 하단 {features['bb_lower20']:,.2f}",
                f"RSI(14) {features['rsi14']:.2f} ≤ 30: 과매도"]
        exit_rule = "종가가 볼린저 중간선 이상 또는 RSI ≥ 50 (자동 매도하지 않음)"
    else:
        signals = rsi_obv_signals(prices)
        latest, previous = signals.iloc[-1], signals.iloc[-2]
        features.update(rsi14=float(latest["rsi"]), previous_rsi14=float(previous["rsi"]),
                        obv=float(latest["obv"]), obv_sma9=float(latest["obv_signal"]),
                        obv_delta=float(latest["obv"] - previous["obv"]))
        rule = [f"RSI(14) {features['previous_rsi14']:.2f} → {features['rsi14']:.2f}: 30 상향 돌파",
                f"OBV {features['obv']:,.0f} > 9일 평균 {features['obv_sma9']:,.0f}, 당일 OBV 증가"]
        exit_rule = "RSI ≥ 50; OBV 청산 게이트 없음 (자동 매도하지 않음)"
    return {"features": features, "reasons": rule + common, "exit_reference": exit_rule,
            "selection_note": "같은 아이템 안에서 시총 순위/종목코드 순 운영 선정(최대 5개)이며 수익률 예측 순위가 아닙니다."}


# ── Pullback v4 ────────────────────────────────────────────────────────────────
def _v4_inputs(history: pd.DataFrame, benchmarks: dict[str, pd.Series], as_of: str):
    """Rows the archived v4 loader would keep: 6-digit KOSPI/KOSDAQ symbols minus the two benchmark
    ETFs, halted (non-positive price) rows dropped, dates aligned to the 069500 calendar."""
    raw = history.copy()
    raw["Date"] = pd.to_datetime(raw["Date"])
    raw = raw.loc[raw["Date"] <= pd.Timestamp(as_of)]
    if "Halted" in raw.columns:
        raw = raw.loc[~raw["Halted"].astype(bool)]
    raw["Symbol"] = raw["Symbol"].map(normalize_symbol)
    excluded = set(v4.MARKET_BENCHMARKS.values())
    raw = raw.loc[raw["Market"].isin(("KOSPI", "KOSDAQ")) & raw["Symbol"].str.fullmatch(r"\d{6}")
                  & ~raw["Symbol"].isin(excluded)]
    bench = {market: pd.DataFrame({"Close": series.astype(float)}) for market, series in benchmarks.items()}
    frames = v4.frames_from_history(raw, bench["KOSPI"])
    latest_market = raw.sort_values("Date").groupby("Symbol")["Market"].last().to_dict()
    names = raw.sort_values("Date").groupby("Symbol")["Name"].last().to_dict()
    return frames, bench, {symbol: latest_market[symbol] for symbol in frames}, names


def pullback_v4_candidates(history: pd.DataFrame, benchmarks: dict[str, pd.Series], as_of: str) -> list[dict]:
    frames, bench, market_map, names = _v4_inputs(history, benchmarks, as_of)
    ranked = v4.scan_candidates(frames, bench, market_map, pd.Timestamp(as_of))
    rows = []
    for position, cand in enumerate(_records(ranked) if not ranked.empty else [], 1):
        market = cand["market"]
        gf = bench[market].copy()
        gf[v4.SMA_COL] = gf["Close"].rolling(v4.CONFIG["market_sma"]).mean()
        rows.append({
            "as_of": as_of, "execution_timing": "next_trading_session", "symbol": cand["ticker"],
            "name": names.get(cand["ticker"], cand["ticker"]), "market": market, "close": cand["close"],
            "signal_type": "pullback_v4", "state": "entry_due_next_session", "score_rank": position,
            "score": cand["score"], "rsi": cand["rsi"], "adx": cand["adx"], "dist_ema10": cand["dist_ema10"],
            "atr": cand["atr"], "ema10": cand["ema10"], "avg_turnover20": cand["avg_turnover20"],
            "benchmark_close": float(gf["Close"].iloc[-1]), "benchmark_sma50": float(gf[v4.SMA_COL].iloc[-1]),
            "selection_outcome": "selected" if position <= MAX_PICKS_PER_ITEM else "daily_cap",
        })
    return rows


def explain_pullback_v4(row: dict) -> dict:
    features = {key: float(row[key]) for key in ("close", "score", "rsi", "adx", "dist_ema10", "atr", "ema10",
                                                  "avg_turnover20", "benchmark_close", "benchmark_sma50")}
    stop_distance = v4.FIXED_CONFIG["atr_multiple"] * features["atr"]
    reasons = [
        f"주간 추세 상승 + EMA5 > EMA10, 종가 {features['close']:,.2f} > EMA10 {features['ema10']:,.2f}",
        f"최근 5거래일 안에 EMA10 아래로 눌림 후 회복 (EMA10 대비 {features['dist_ema10'] * 100:.2f}%)",
        f"RSI(14) {features['rsi']:.2f} (50~70), ADX {features['adx']:.2f} > 20, MACD 히스토그램 ≥ 0",
        f"20일 평균 거래대금 {features['avg_turnover20']:,.0f}원 ≥ 50억원, 당일 상승률 8% 미만",
        f"{row['market']} 시장 ETF 종가 {features['benchmark_close']:,.2f} > 50일 평균 {features['benchmark_sma50']:,.2f}",
    ]
    exit_rule = (f"손절: 매수가 - {stop_distance:,.2f}원(1.5×ATR) · 15거래일 보유 상한 · "
                 f"최고 종가 - 2×ATR 트레일링 · 시장 ETF가 50일선 이하이면 다음 날 시가 청산 (자동 매도하지 않음)")
    return {"features": features, "reasons": reasons, "exit_reference": exit_rule,
            "selection_note": f"사전등록 점수(EMA10 근접 40%·ADX 30%·RSI 낮음 30%) {row['score_rank']}위 · "
                              "최대 5개, 수익률 예측 순위가 아닙니다."}


# ── Exit monitor for previously recommended symbols (no user data enters the engine) ──
def exit_monitor(data: pd.DataFrame, history: pd.DataFrame, benchmarks: dict[str, pd.Series], as_of: str,
                 tracked: tuple) -> list[dict]:
    """Position-independent exit facts at ``as_of`` for symbols the engine recommended before.

    swing/long_term: the native exit condition on today's close. pullback_v4: the last sessions'
    close/ATR and today's market regime; the stop, trailing level and holding days depend on the
    user's own entry and are computed by the website from the user's ledger.
    """
    if not tracked:
        return []
    out = []
    v4_frames, v4_bench, v4_markets = None, None, None
    for symbol, item in sorted({(normalize_symbol(s), t) for s, t in tracked}):
        if item in ("swing", "long_term"):
            prices = data.loc[data["Symbol"].eq(symbol)].set_index("Date")
            if prices.empty or prices.index.max() != pd.Timestamp(as_of) or len(prices) < 60:
                out.append({"symbol": symbol, "signal_type": item, "state": "no_current_bar"})
                continue
            exit_now = native_exit_observed(prices[["Open", "High", "Low", "Close", "Volume"]]
                                            if item == "long_term" else prices["Close"], item)
            out.append({"symbol": symbol, "signal_type": item, "state": "exit_signal" if exit_now else "hold",
                        "close": float(prices["Close"].iloc[-1])})
        elif item == "pullback_v4":
            if v4_frames is None:
                v4_frames, v4_bench, v4_markets, _ = _v4_inputs(history, benchmarks, as_of)
            frame = v4_frames.get(symbol)
            if frame is None or frame.index[-1] != pd.Timestamp(as_of):
                out.append({"symbol": symbol, "signal_type": item, "state": "no_current_bar"})
                continue
            prepared = v4.compute_indicators(frame.tail(v4.CONFIG["warmup_days"] + 1)).tail(EXIT_SERIES_SESSIONS)
            gf = v4_bench[v4_markets[symbol]].copy()
            gf[v4.SMA_COL] = gf["Close"].rolling(v4.CONFIG["market_sma"]).mean()
            out.append({"symbol": symbol, "signal_type": item, "state": "series",
                        "market_regime_off": v4.regime_off(gf, pd.Timestamp(as_of)),
                        "series": [{"date": d.date().isoformat(), "close": float(r["Close"]), "atr": float(r["ATR"])}
                                   for d, r in prepared.iterrows() if np.isfinite(r["ATR"])]})
    return out


def generate_recommendations(history: pd.DataFrame, benchmarks: dict[str, pd.Series], *,
                             as_of: str, excluded_symbols: tuple[str, ...] = (), tracked: tuple = ()) -> dict:
    data, bounded_benchmarks = validated_inputs(history, benchmarks, as_of)
    context = sorted({normalize_symbol(symbol) for symbol in excluded_symbols})
    tracked_context = sorted({(normalize_symbol(symbol), item) for symbol, item in tracked
                              if item in ITEMS})
    config = {"items": list(ITEMS), "max_picks_per_item": MAX_PICKS_PER_ITEM,
              "scanner": asdict(APPROVED_CONFIG), "market_benchmarks": dict(DEFAULT_MARKET_BENCHMARKS),
              "rules": reversion_rule_parameters(),
              "pullback_v4": {"config": v4.FIXED_CONFIG, "rank_weights": v4.RANK_WEIGHTS,
                              "market_sma": v4.CONFIG["market_sma"], "benchmarks": v4.MARKET_BENCHMARKS,
                              "warmup_days": v4.CONFIG["warmup_days"]}}
    config_hash = digest(config)
    data_hash = input_fingerprint(data, bounded_benchmarks)
    halted = history.get("Halted")
    halted_hash = digest(sorted(f"{d}:{s}" for d, s in history.loc[halted.astype(bool), ["Date", "Symbol"]]
                                .astype(str).itertuples(index=False, name=None))) if halted is not None else None
    code = code_identity()
    identity = {"protocol": PROTOCOL, "as_of": as_of, "code_sha256": code["code_sha256"],
                "config_sha256": config_hash, "input_sha256": data_hash, "halted_rows_sha256": halted_hash,
                "excluded_symbols": context, "tracked": [list(row) for row in tracked_context],
                "dependencies": code["dependencies"], "python": code["python"]}
    run_id = digest(identity)
    scan = scan_reversion_candidates(data, APPROVED_CONFIG, bounded_benchmarks)
    audit = _records(pd.DataFrame(selection_audit(scan, tuple(context))))
    audit.extend(pullback_v4_candidates(history, bounded_benchmarks, as_of))
    recommendations = []
    for row in audit:
        row["candidate_id"] = digest({"run_id": run_id, "symbol": row["symbol"],
                                      "signal_type": row["signal_type"]})
        if row["selection_outcome"] != "selected":
            continue
        if row["signal_type"] == "pullback_v4":
            recommendations.append(dict(row, **explain_pullback_v4(row)))
            continue
        prices = data.loc[data["Symbol"].eq(row["symbol"])].set_index("Date")
        recommendations.append(dict(row, **explain(row, prices, bounded_benchmarks[row["market"]])))
    counts = {item: sum(1 for row in recommendations if row["signal_type"] == item) for item in ITEMS}
    return {"schema": PROTOCOL, "run_id": run_id, "as_of": as_of, "engine": code,
            "config": config, "config_sha256": config_hash, "input_sha256": data_hash, "halted_rows_sha256": halted_hash,
            "excluded_symbols": context, "tracked": [list(row) for row in tracked_context],
            "recommendations": recommendations, "audit": audit, "item_counts": counts,
            "exit_monitor": exit_monitor(data, history, bounded_benchmarks, as_of, tuple(tracked_context)),
            "status": "recommendations" if recommendations else "no_recommendations",
            "universe": "market_cap_rank_proxy_not_actual_index_membership",
            "usage": "manual_decision_only", "orders_enabled": False,
            "publication_ready": False, "cost_model": None,
            "limitations": ["No persistent publication snapshot/expiry; not the website API contract.",
                            "No collector/calendar/AWS scheduler; explicit dated input is required.",
                            "Not comparable with the legacy automatic-fill prospective ledger.",
                            "EINT-017 per-item caps are a user operating policy, not a validated rule."]}
