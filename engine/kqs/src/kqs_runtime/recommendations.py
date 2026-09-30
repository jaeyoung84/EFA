"""Pure recommendation generation. No positions, fills, costs or ledger writes."""

import hashlib
import json
from dataclasses import asdict

import pandas as pd

from korea_quant.eligibility import DEFAULT_MARKET_BENCHMARKS
from korea_quant.reversion_scanner import (
    ReversionScannerConfig, live_selection_key, reversion_rule_parameters,
    scan_reversion_candidates,
)
from korea_quant.strategy import bollinger_rsi_signals, rsi_obv_signals
from kqs_runtime import PROTOCOL
from kqs_runtime.inputs import COLUMNS, normalize_symbol, validated_inputs
from kqs_runtime.provenance import code_identity, digest

# D-19: the research dataclass intentionally defaults to filter OFF; runtime must be explicit.
APPROVED_CONFIG = ReversionScannerConfig(market_filter_window=200, market_filter_inverted=True)
MAX_PICKS = 3


def selection_audit(scan: pd.DataFrame, excluded_symbols: tuple[str, ...] = ()) -> list[dict]:
    """D-24 ordering/global cap, with an explicit caller context (never a loaded auto ledger)."""
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
    selected = set()
    for _key, index in sorted(eligible):
        symbol = rows[index]["symbol"]
        if symbol in selected:
            outcomes[index] = "duplicate_symbol"
        elif len(selected) >= MAX_PICKS:
            outcomes[index] = "daily_cap"
        else:
            selected.add(symbol)
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
    features = {"close": float(prices["Close"].iloc[-1]),
                "benchmark_close": float(benchmark.iloc[-1]), "benchmark_sma200": market_sma,
                "market_cap_rank": int(row["market_cap_rank"]),
                "average_trading_value20": float(row["average_trading_value"])}
    common = [f"{row['market']} 시가총액 순위 {features['market_cap_rank']}위: 선정 범위 통과",
              f"20거래일 평균 거래대금 {features['average_trading_value20']:,.0f}원: 50억원 이상",
              f"시장 ETF 종가 {features['benchmark_close']:,.2f} < 200일 평균 {market_sma:,.2f}"]
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
            "selection_note": "시총 순위/종목코드/신호 순서의 운영 선정이며 수익률 예측 순위가 아닙니다."}


def generate_recommendations(history: pd.DataFrame, benchmarks: dict[str, pd.Series], *,
                             as_of: str, excluded_symbols: tuple[str, ...] = ()) -> dict:
    data, bounded_benchmarks = validated_inputs(history, benchmarks, as_of)
    context = sorted({normalize_symbol(symbol) for symbol in excluded_symbols})
    config = {"max_picks": MAX_PICKS, "scanner": asdict(APPROVED_CONFIG),
              "market_benchmarks": dict(DEFAULT_MARKET_BENCHMARKS),
              "rules": reversion_rule_parameters()}
    config_hash = digest(config)
    data_hash = input_fingerprint(data, bounded_benchmarks)
    code = code_identity()
    identity = {"protocol": PROTOCOL, "as_of": as_of, "code_sha256": code["code_sha256"],
                "config_sha256": config_hash, "input_sha256": data_hash,
                "excluded_symbols": context, "dependencies": code["dependencies"], "python": code["python"]}
    run_id = digest(identity)
    scan = scan_reversion_candidates(data, APPROVED_CONFIG, bounded_benchmarks)
    audit = selection_audit(scan, tuple(context))
    # Convert pandas nullable scalars before producing strict JSON.
    audit = _records(pd.DataFrame(audit))
    recommendations = []
    for row in audit:
        row["candidate_id"] = digest({"run_id": run_id, "symbol": row["symbol"],
                                      "signal_type": row["signal_type"]})
        if row["selection_outcome"] != "selected":
            continue
        prices = data.loc[data["Symbol"].eq(row["symbol"])].set_index("Date")
        recommendations.append(dict(row, **explain(row, prices, bounded_benchmarks[row["market"]])))
    return {"schema": PROTOCOL, "run_id": run_id, "as_of": as_of, "engine": code,
            "config": config, "config_sha256": config_hash, "input_sha256": data_hash,
            "excluded_symbols": context, "recommendations": recommendations, "audit": audit,
            "status": "recommendations" if recommendations else "no_recommendations",
            "universe": "market_cap_rank_proxy_not_actual_index_membership",
            "usage": "manual_decision_only", "orders_enabled": False,
            "publication_ready": False, "cost_model": None,
            "limitations": ["No persistent publication snapshot/expiry; not the website API contract.",
                            "No collector/calendar/AWS scheduler; explicit dated input is required.",
                            "Not comparable with the legacy automatic-fill prospective ledger."]}
