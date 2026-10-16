"""EINT-043: RSI-OBV(A) vs A + SMA60 상승 기울기(B) 과거 참고 백테스트 (PREREGISTRATION.md).

A는 CODX-MA-001 실행기(research/rsi_obv_sma60_exploratory/run.py)의 baseline을 코드 수정 없이 불러 만든다.
B는 A의 각 거래 중 신호일 SMA60 > 신호 전일 SMA60인 것만 같은 가격·시점으로 진입, 나머지는 현금 0%.

usage: python run.py [krx_daily.sqlite3]   → RESULT.json (한 번만)
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULT = HERE / "RESULT.json"
PREREG = HERE / "PREREGISTRATION.md"
BASE_RUNNER = ROOT / "research" / "rsi_obv_sma60_exploratory" / "run.py"
SEED, DRAWS = 20261016, 10_000

spec = importlib.util.spec_from_file_location("codx_ma_001", BASE_RUNNER)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def slope_up_at_signal(group: pd.DataFrame, entry_ts: pd.Timestamp) -> bool | None:
    """진입일 바로 전 행 = 신호 확인일. SMA60[신호일] > SMA60[신호 전일]. 둘 중 결측이면 None(B 미진입으로 셈)."""
    pos = group.index[group["Date"] == entry_ts]
    if not len(pos) or pos[0] < 2:
        return None
    sma = group["Close"].astype(float).rolling(60, min_periods=60).mean()
    today, yday = sma.iloc[pos[0] - 1], sma.iloc[pos[0] - 2]
    if pd.isna(today) or pd.isna(yday):
        return None
    return bool(today > yday)


def bootstrap_mean(values: np.ndarray) -> tuple[float, float]:
    rng = random.Random(SEED)
    n = len(values)
    means = sorted(float(np.mean([values[rng.randrange(n)] for _ in range(n)])) for _ in range(DRAWS))
    return means[int(0.025 * DRAWS)], means[int(0.975 * DRAWS) - 1]


def main() -> int:
    if RESULT.exists():
        raise SystemExit("RESULT.json already exists; EINT-043 is one-shot.")
    db = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else base.DEFAULT_DB.resolve()
    hashes = {"database_sha256": sha256(db), "preregistration_sha256": sha256(PREREG),
              "runner_sha256": sha256(Path(__file__)), "base_runner_sha256": sha256(BASE_RUNNER)}
    with base.connect_immutable(db) as connection:
        ranks = base.top_rank_rows(connection)
        history = base.candidate_history(connection, set(ranks["Symbol"]))
    history["Date"] = pd.to_datetime(history["Date"])
    ranks["Date"] = pd.to_datetime(ranks["Date"])
    history = base.eligibility(base.fill_halts(history), ranks)
    dates = pd.DatetimeIndex(history["Date"].drop_duplicates().sort_values())
    start = dates[base.TRAINING_SESSIONS]
    by_symbol = {str(s): g.reset_index(drop=True) for s, g in history.groupby("Symbol", sort=True)}
    rows = []
    for symbol, group in by_symbol.items():
        result, _ = base.simulate(group, "baseline", dates)
        for row in result:
            if row["Entry Timestamp"] >= start:
                row.update(Symbol=symbol, Market=str(group["Market"].iloc[-1]),
                           SlopeUp=slope_up_at_signal(group, row["Entry Timestamp"]))
                rows.append(row)
    a = pd.DataFrame(rows)
    b = a[a["SlopeUp"].eq(True)].copy()
    closed = a[a["Status"].eq("closed")]
    diff = np.where(closed["SlopeUp"].eq(True), 0.0, -closed["NetReturnPct"].to_numpy())   # B−A: 진입하면 0, 미진입이면 0 − A
    lo, hi = bootstrap_mean(diff)
    ma, mb = base.metrics(a), base.metrics(b)
    retention = mb["closed_trades"] / ma["closed_trades"] if ma["closed_trades"] else None
    cond = {"B_closed_ge_100": mb["closed_trades"] >= 100, "retention_ge_20pct": (retention or 0) >= 0.20,
            "diff_ci_low_gt_0": lo > 0, "B_pf_gt_A_pf": (mb["profit_factor"] or 0) > (ma["profit_factor"] or 0),
            "B_q05_ge_A_q05": mb["q05_net_return"] >= ma["q05_net_return"]}
    if not (cond["B_closed_ge_100"] and cond["retention_ge_20pct"]):
        label = "REJECT_TOO_SPARSE"
    elif cond["diff_ci_low_gt_0"] and cond["B_pf_gt_A_pf"] and cond["B_q05_ge_A_q05"]:
        label = "PASS"
    elif hi < 0 or not cond["B_pf_gt_A_pf"] or not cond["B_q05_ge_A_q05"]:
        label = "FAIL"
    else:
        label = "INCONCLUSIVE"
    yearly = {str(y): {"A_mean": round(float(g["NetReturnPct"].mean()), 4), "A_n": int(len(g)),
                       "B_mean": round(float(g.loc[g["SlopeUp"].eq(True), "NetReturnPct"].mean()), 4) if g["SlopeUp"].eq(True).any() else None,
                       "B_n": int(g["SlopeUp"].eq(True).sum())}
              for y, g in closed.groupby(closed["Entry Timestamp"].dt.year)}
    report = {
        "A": {"all": ma, "KOSPI": base.metrics(a[a["Market"].eq("KOSPI")]), "KOSDAQ": base.metrics(a[a["Market"].eq("KOSDAQ")]),
              "deployment": base.deploy(a, by_symbol, start, dates[-1])},
        "B": {"all": mb, "KOSPI": base.metrics(b[b["Market"].eq("KOSPI")]), "KOSDAQ": base.metrics(b[b["Market"].eq("KOSDAQ")]),
              "deployment": base.deploy(b, by_symbol, start, dates[-1])},
        "retention_closed": retention,
        "slope_unknown_signals": int(a["SlopeUp"].isna().sum()),
        "paired_diff": {"signals": int(len(diff)), "mean": float(diff.mean()), "bootstrap95": [lo, hi], "seed": SEED, "draws": DRAWS},
        "yearly": yearly,
        "codx_ma_002_rule_applied_to_history": {"label": label, "conditions": cond,
                                                "note": "참고 표시. CODX-MA-002 판정은 2026-10-16 이후 실제 게시 신호로만 한다."},
    }
    payload = {"change_id": "EINT-043", "status": "exploratory_reference_consumed_historical_window",
               "rule": "A = CODX-MA-001 baseline; B = A where SMA60[signal] > SMA60[signal-1], else cash",
               "database": str(db), "measured_start": str(start.date()), "measured_end": str(dates[-1].date()),
               "hashes": hashes, "results": report, "executed_at": datetime.now(timezone.utc).isoformat()}
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
