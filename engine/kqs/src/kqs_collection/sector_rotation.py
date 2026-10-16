"""Sector rotation (RRG) for the website's reference chart (EINT-021).

Domestic sector ETFs against KODEX 200 Equal Weight (EINT-047; KODEX 200 is kept as a reference view), on weekly
closes from the collected KRX daily bars. The cap-weighted KODEX 200 is dominated by two mega caps, so against it the
whole board tilted one way (2025-10..2026-10: 42% of sector-weeks "lagging" in the mega-cap rally, every sector
"leading" in the October 2026 mega-cap crash); the two benchmarks agreed on only 58% of sector-weeks.

- relative strength  RS  = 100 * sector / benchmark
- RS-Ratio    = 100 * EMA(RS, SHORT) / EMA(RS, LONG)   (above 100: the sector has been beating the market lately)
- RS-Momentum = 100 * RS-Ratio / EMA(RS-Ratio, MOMENTUM) (above 100: that lead is still growing)

Exponential smoothing keeps the weekly trails as smooth arcs (a plain rolling z-score turned out
too jumpy on real data).

Quadrants: 주도(leading) ratio>=100 & momentum>=100, 약화(weakening) ratio>=100 & momentum<100,
부진(lagging) both <100, 개선(improving) ratio<100 & momentum>=100. Sectors usually rotate clockwise
improving -> leading -> weakening -> lagging. This is a common public approximation of the RRG
idea, not the licensed JdK formula, and it describes the present only: it is not a signal and is
not used by any recommendation. Read-only on the market DB; written atomically for the website.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

SCHEMA = "kqs-sector-rotation/v1"
BENCHMARK = ("252650", "KODEX 200 동일가중")       # EINT-047: default comparison
REFERENCE_BENCHMARK = ("069500", "KODEX 200")     # reference view only
CONCENTRATION_GAP_PCT = 5.0                       # |4w return KODEX 200 - equal weight| at or above this -> notice
SHORT, LONG, MOMENTUM = 4, 13, 4   # EMA spans in weeks
WARMUP = LONG * 2                   # weeks before the first published point (EMA settles)
TAIL_WEEKS = 12                     # trail points published per sector
HISTORY_WEEKS = WARMUP + TAIL_WEEKS + 4
SECTORS = (           # (key, display name, ETF symbol)
    ("semis", "반도체", "091160"),
    ("it_hw", "IT하드웨어", "266370"),
    ("battery", "2차전지", "305720"),
    ("it_sw", "소프트웨어", "157490"),
    ("internet", "인터넷", "365000"),
    ("auto", "자동차", "091180"),
    ("shipbuilding", "조선", "494670"),
    ("defense", "방산", "463250"),
    ("machinery", "기계장비", "102960"),
    ("construction", "건설", "117700"),
    ("steel", "철강", "117680"),
    ("energy_chem", "에너지화학", "117460"),
    ("banks", "은행", "091170"),
    ("brokers", "증권", "102970"),
    ("insurance", "보험", "140700"),
    ("healthcare", "헬스케어", "266420"),
    ("staples", "필수소비재", "266410"),
    ("discretionary", "경기소비재", "266390"),
    ("cosmetics", "화장품", "228790"),
    ("media", "미디어·엔터", "266360"),
    ("transport", "운송", "140710"),
)
QUADRANTS = {"leading": "주도", "weakening": "약화", "lagging": "부진", "improving": "개선"}


class SectorRotationError(ValueError):
    """The sector rotation could not be built."""


def weekly_closes(rows: list[tuple[str, float]]) -> dict[str, float]:
    """Last valid close of each ISO week, keyed by that day (YYYY-MM-DD). Halted (<=0) days are skipped."""
    weeks: dict[tuple[int, int], tuple[str, float]] = {}
    for day, close in rows:
        if close is None or close <= 0:
            continue
        key = date.fromisoformat(day).isocalendar()[:2]
        if key not in weeks or day > weeks[key][0]:
            weeks[key] = (day, float(close))
    return {day: close for day, close in sorted(weeks.values())}


def _ema(values: list[float], span: int) -> list[float]:
    alpha = 2.0 / (span + 1)
    out, current = [], None
    for value in values:
        current = value if current is None else alpha * value + (1 - alpha) * current
        out.append(current)
    return out


def rrg_series(sector: list[float], benchmark: list[float]) -> list[tuple[float, float] | None]:
    """(RS-Ratio, RS-Momentum) per week for aligned weekly closes; None during the EMA warm-up."""
    rs = [100.0 * s / b for s, b in zip(sector, benchmark)]
    ratio = [100.0 * short / long for short, long in zip(_ema(rs, SHORT), _ema(rs, LONG))]
    momentum = [100.0 * r / smooth for r, smooth in zip(ratio, _ema(ratio, MOMENTUM))]
    return [None if index < WARMUP else (round(r, 3), round(m, 3))
            for index, (r, m) in enumerate(zip(ratio, momentum))]


def quadrant(ratio: float, momentum: float) -> str:
    if ratio >= 100:
        return "leading" if momentum >= 100 else "weakening"
    return "improving" if momentum >= 100 else "lagging"


def _pct(series: list[float], weeks: int) -> float | None:
    if len(series) <= weeks or series[-1 - weeks] <= 0:
        return None
    return round((series[-1] / series[-1 - weeks] - 1) * 100, 2)


def build_sector_rotation(market_db: Path, as_of: str) -> dict:
    if date.fromisoformat(as_of).isoformat() != as_of:
        raise SectorRotationError("as_of must be YYYY-MM-DD.")
    try:
        resolved = Path(market_db).resolve(strict=True)
    except OSError as exc:
        raise SectorRotationError("Market DB does not exist.") from exc
    start = date.fromordinal(date.fromisoformat(as_of).toordinal() - HISTORY_WEEKS * 7 - 7).isoformat()
    symbols = [BENCHMARK[0], REFERENCE_BENCHMARK[0]] + [symbol for _, _, symbol in SECTORS]
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        placeholders = ",".join("?" * len(symbols))
        rows = connection.execute(
            f"SELECT symbol, trade_date, close, name FROM daily_prices WHERE symbol IN ({placeholders}) "
            "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date", (*symbols, start, as_of)).fetchall()
    except sqlite3.Error as exc:
        raise SectorRotationError(f"Sector query failed: {exc}") from exc
    finally:
        connection.close()
    by_symbol: dict[str, list[tuple[str, float]]] = {}
    names: dict[str, str] = {}
    for symbol, day, close, name in rows:
        by_symbol.setdefault(symbol, []).append((day, close))
        names[symbol] = name
    bench = weekly_closes(by_symbol.get(BENCHMARK[0], []))
    if len(bench) < WARMUP + 2 or max(bench) != as_of:
        raise SectorRotationError("Benchmark history is too short or has no bar on the session.")
    weeks = sorted(bench)
    reference = weekly_closes(by_symbol.get(REFERENCE_BENCHMARK[0], []))

    sectors, skipped = [], []
    for key, label, symbol in SECTORS:
        closes = weekly_closes(by_symbol.get(symbol, []))
        common = [day for day in weeks if day in closes]
        if not closes or max(closes) != weeks[-1]:
            skipped.append({"key": key, "name": label, "symbol": symbol, "reason": "기준일 시세 없음"})
            continue
        if len(common) < WARMUP + 2:
            skipped.append({"key": key, "name": label, "symbol": symbol, "reason": "주간 시세 이력 부족"})
            continue
        sector_series = [closes[day] for day in common]
        bench_series = [bench[day] for day in common]
        points = rrg_series(sector_series, bench_series)
        trail = [{"week": day, "rs_ratio": point[0], "rs_momentum": point[1]}
                 for day, point in zip(common, points) if point is not None][-TAIL_WEEKS:]
        if len(trail) < 2:
            skipped.append({"key": key, "name": label, "symbol": symbol, "reason": "지표 계산 구간 부족"})
            continue
        now, before = trail[-1], trail[-5] if len(trail) >= 5 else trail[0]
        relative = [100.0 * s / b for s, b in zip(sector_series, bench_series)]
        ref_common = [day for day in common if day in reference]
        ref_view = None
        if len(ref_common) >= WARMUP + 2 and ref_common[-1] == common[-1]:
            ref_point = rrg_series([closes[d] for d in ref_common], [reference[d] for d in ref_common])[-1]
            ref_rel = [100.0 * closes[d] / reference[d] for d in ref_common]
            ref_view = {"quadrant": quadrant(*ref_point), "rs_ratio": ref_point[0], "rs_momentum": ref_point[1],
                        "relative_4w_pct": _pct(ref_rel, 4)}
        sectors.append({
            "key": key, "name": label, "symbol": symbol, "etf_name": names.get(symbol, ""),
            "quadrant": quadrant(now["rs_ratio"], now["rs_momentum"]),
            "quadrant_4w_ago": quadrant(before["rs_ratio"], before["rs_momentum"]),
            "rs_ratio": now["rs_ratio"], "rs_momentum": now["rs_momentum"],
            "return_1w_pct": _pct(sector_series, 1),
            "return_4w_pct": _pct(sector_series, 4), "return_12w_pct": _pct(sector_series, 12),
            "relative_4w_pct": _pct(relative, 4), "relative_12w_pct": _pct(relative, 12),
            "trail": trail, "reference": ref_view,
        })
    if not sectors:
        raise SectorRotationError("No sector ETF has enough history.")
    digest = hashlib.sha256(json.dumps(sectors, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    bench_series_all = [bench[d] for d in weeks]
    ref_weeks = [d for d in weeks if d in reference]
    ref_4w = _pct([reference[d] for d in ref_weeks], 4) if ref_weeks and ref_weeks[-1] == weeks[-1] else None
    ew_4w = _pct(bench_series_all, 4)
    gap = round(ref_4w - ew_4w, 2) if ref_4w is not None and ew_4w is not None else None
    concentration = {"cap_weighted_4w_pct": ref_4w, "equal_weight_4w_pct": ew_4w, "gap_pctp": gap,
                     "threshold_pctp": CONCENTRATION_GAP_PCT, "notice": gap is not None and abs(gap) >= CONCENTRATION_GAP_PCT,
                     "direction": None if gap is None else ("mega_caps_weaker" if gap < 0 else "mega_caps_stronger")}
    return {"schema": SCHEMA, "as_of": as_of, "latest_week": weeks[-1], "benchmark": {"symbol": BENCHMARK[0], "name": BENCHMARK[1]},
            "reference_benchmark": {"symbol": REFERENCE_BENCHMARK[0], "name": REFERENCE_BENCHMARK[1]},
            "benchmark_return_1w_pct": _pct(bench_series_all, 1), "benchmark_return_4w_pct": ew_4w,
            "concentration": concentration,
            "smoothing_weeks": {"short": SHORT, "long": LONG, "momentum": MOMENTUM}, "tail_weeks": TAIL_WEEKS, "quadrant_labels": QUADRANTS,
            "sectors": sectors, "skipped": skipped, "items_sha256": digest}


def write_sector_rotation(path: Path, report: dict) -> Path:
    """Replace the file atomically so the website never reads a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".sector-rotation-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, separators=(",", ":"))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path
