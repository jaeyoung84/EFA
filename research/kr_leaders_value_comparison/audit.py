"""Codex 수정: CODX-KRLC-001 (2026-10-16), 파일 전체.

Read-only reuse of saved stock returns; no downloads or strategy runners.
Run with pypdf available. See METHOD.md for assumptions and limitations.
"""
import csv
import hashlib
import json
import math
import re
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OLD = ROOT / "research/external_picks_kr/out"
DOWNLOADS = Path("/mnt/c/Users/gram/Downloads")


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def compound(returns):
    return 100 * (math.prod(1 + r / 100 for r in returns) - 1)


def drawdown(returns):
    equity = peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= 1 + r / 100
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return worst * 100


def require_coverage(expected, actual):
    if expected != actual:
        raise ValueError(f"Coverage mismatch: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")


def pdf_sets(path, expected_count):
    from pypdf import PdfReader
    months = {}
    for page in PdfReader(path).pages:
        text = page.extract_text()
        match = re.search(r"(202\d)년\s*(\d+)월", text)
        if match is None:
            raise ValueError("Missing PDF month")
        month = f"{match[1]}-{int(match[2]):02}"
        if month in months:
            raise ValueError("Duplicate PDF month")
        codes = set(re.findall(r"A(\d{6})(?!\d)", text))
        if len(codes) != expected_count:
            raise ValueError(f"{month}: {len(codes)} PDF codes")
        months[month] = codes
    if len(months) != 25:
        raise ValueError("Expected 25 PDF months")
    return months


def metrics(returns):
    return {
        "total_pct": compound(returns),
        "cagr_pct": 100 * ((1 + compound(returns) / 100) ** (12 / len(returns)) - 1),
        "annualized_monthly_vol_pct": statistics.stdev(returns) * math.sqrt(12),
        "month_end_mdd_pct": drawdown(returns),
        "positive_months": sum(r > 0 for r in returns),
        "worst_month_pct": min(returns),
        "best_month_pct": max(returns),
    }


def main():
    source_paths = [OLD / x for x in ("monthly_performance.csv", "stock_month_returns.csv", "compare_stock_months.csv", "compare_monthly.csv")]
    pdf_paths = [DOWNLOADS / "한국 대장주 가치 투자주.pdf", DOWNLOADS / "한국 저평가 가치 투자주 (1).pdf"]
    hashes_before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths + pdf_paths}
    leaders, value = pdf_sets(pdf_paths[0], 20), pdf_sets(pdf_paths[1], 15)
    months = sorted(m for m in leaders if m < "2026-10")
    require_coverage(set(months), {m for m in value if m < "2026-10"})
    if len(months) != 24:
        raise ValueError("Expected 24 completed months")
    old_monthly = {r["month"]: r for r in read_csv(source_paths[0])}
    old_value_stocks = read_csv(source_paths[1])
    saved_stocks = read_csv(source_paths[2])
    prior_comparison = {r["month"]: r for r in read_csv(source_paths[3])}
    # Only the one known missing symbol-month may be supplemented.
    supplemental_key = ("2026-02", "012510")
    db_path = ROOT / "research/kr_value_quality_v1/data/krx_daily.sqlite3"
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        prices = con.execute(
            "SELECT trade_date,symbol,name,open,close,volume,listed_shares FROM daily_prices "
            "WHERE symbol=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (supplemental_key[1], "2026-02-01", "2026-02-28"),
        ).fetchall()
        sessions = [r[0] for r in con.execute(
            "SELECT trade_date FROM daily_prices WHERE symbol='069500' "
            "AND trade_date BETWEEN '2026-02-01' AND '2026-02-28' ORDER BY trade_date"
        )]
    if not prices or [r[0] for r in prices] != sessions:
        raise ValueError("Supplement does not cover every February session")
    if len({r[6] for r in prices}) != 1 or any(r[3] <= 0 or r[4] <= 0 or r[5] <= 0 for r in prices):
        raise ValueError("Supplement has share-count change or non-trading observations")
    supplemental_return = (prices[-1][4] / prices[0][3] - 1) * 100
    output, stock_output, coverage = [], [], []
    for month in months:
        saved = [r for r in saved_stocks if r["month"] == month and r["in_L"] == "True"]
        saved_returns = {r["code"]: float(r["ret"]) for r in saved}
        if len(saved_returns) != len(saved):
            raise ValueError("Duplicate saved leader return")
        known_missing = {supplemental_key[1]} if month == supplemental_key[0] else set()
        require_coverage(leaders[month] - known_missing, set(saved_returns))
        old_leader = statistics.mean(saved_returns.values())
        if abs(old_leader - float(prior_comparison[month]["L"])) > 1e-9:
            raise ValueError("Saved leader monthly average mismatch")
        if known_missing:
            saved_returns[supplemental_key[1]] = supplemental_return
        require_coverage(leaders[month], set(saved_returns))
        vrows = [r for r in old_value_stocks if r["month"] == month]
        require_coverage(value[month], {r["code"] for r in vrows})
        if len(vrows) != 15 or abs(statistics.mean(float(r["ret"]) for r in vrows) - float(old_monthly[month]["picks"])) > 1e-9:
            raise ValueError("Existing value result mismatch")
        leader_return = statistics.mean(saved_returns.values())
        value_return = float(old_monthly[month]["picks"])
        output.append({"month": month, "leaders_n": 20, "value_n": 15,
                       "leaders_pct": leader_return, "value_existing_pct": value_return,
                       "leaders_before_supplement_pct": old_leader,
                       "leaders_minus_value_pp": leader_return - value_return,
                       **{k: float(old_monthly[month][k]) for k in ("KODEX200", "KODEX200_EW", "KOSDAQ150")}})
        coverage.append({"month": month, "leaders_expected": 20, "leaders_saved": len(saved),
                         "leaders_supplemented": len(known_missing), "leaders_final": len(saved_returns), "value_existing": len(vrows)})
        stock_output.extend({"month": month, "code": c, "return_pct": r,
                             "source": "KRX price-only supplement" if (month, c) == supplemental_key else "saved Yahoo adjusted return"}
                            for c, r in sorted(saved_returns.items()))
    columns = ("leaders_pct", "value_existing_pct", "leaders_before_supplement_pct", "KODEX200", "KODEX200_EW", "KOSDAQ150")
    summary = {c: metrics([r[c] for r in output]) for c in columns}
    diffs = [r["leaders_minus_value_pp"] for r in output]
    summary["paired_comparison"] = {"leaders_win_months": sum(x > 0 for x in diffs),
                                    "mean_monthly_difference_pp": statistics.mean(diffs),
                                    "monthly_difference_t_stat_descriptive_only": statistics.mean(diffs) / (statistics.stdev(diffs) / math.sqrt(24))}
    annual = [{"year": y, "months": sum(r["month"].startswith(y) for r in output),
               **{c: compound([r[c] for r in output if r["month"].startswith(y)]) for c in columns}}
              for y in ("2024", "2025", "2026")]
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes_before.items()):
        raise ValueError("Source file changed during audit")
    summary["supplement"] = {"symbol": "012510", "name": prices[0][2], "month": "2026-02",
                             "first_date": prices[0][0], "open": prices[0][3], "last_date": prices[-1][0],
                             "close": prices[-1][4], "return_pct": supplemental_return,
                             "dividend_adjustment_verified": False, "source_db": str(db_path.relative_to(ROOT))}
    summary["source_sha256"] = {str(Path(p).relative_to(ROOT)) if Path(p).is_relative_to(ROOT) else Path(p).name: h for p, h in hashes_before.items()}
    summary["supplement_rows_sha256"] = hashlib.sha256(json.dumps(prices, ensure_ascii=False).encode()).hexdigest()
    summary["code_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    config = {"period_start_month": months[0], "period_end_month": months[-1],
              "completed_months": 24, "leaders_count": 20, "value_count": 15,
              "weighting": "equal at each month first open", "exit": "same month last close",
              "month_boundary_overnight_return": "excluded", "supplement": list(supplemental_key)}
    summary["config"] = config
    summary["config_sha256"] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    summary["cost_assumption"] = {"commission_bps": 0, "tax_bps": 0, "slippage_bps": 0}
    summary["method_sha256"] = hashlib.sha256((HERE / "METHOD.md").read_bytes()).hexdigest()
    summary["audit_at_kst"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
    summary["python_version"] = sys.version
    summary["status"] = "retrospective_descriptive_comparison_not_independent_validation"
    write_csv(HERE / "monthly.csv", output)
    write_csv(HERE / "coverage.csv", coverage)
    write_csv(HERE / "leaders_stock_months.csv", stock_output)
    write_csv(HERE / "annual.csv", annual)
    (HERE / "RESULT.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("source_sha256",)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
