"""InvestingPro 한국 '저평가 가치'(V) vs '대장주 가치'(L) 25개월 비교 — 로컬 전용 탐색 분석(소급 입력, 사전등록 아님).

각 명단은 그달 1일 리밸런싱: 첫 거래일 시가 → 마지막 거래일 종가, 동일 비중(Yahoo 일봉, 분할·배당 조정), 비용 미반영.
비교: KODEX 200(시총가중), KODEX 200 동일가중. 겹침(V∩L)·V만·L만, 연속 편입(전달에도 있었는지)별 성과와 특징.

usage: python compare.py  → out/compare_*.csv
"""
from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import leaders  # noqa: E402
import study  # noqa: E402
from analyze import BENCH, download, month_return, resolve  # noqa: E402

OUT = HERE / "out"


def tstat(x: pd.Series) -> float:
    x = x.dropna()
    return x.mean() / (x.std(ddof=1) / math.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else math.nan


def chain(x: pd.Series) -> float:
    return ((1 + x.dropna() / 100).prod() - 1) * 100


def main() -> None:
    v = study.parse((HERE / "propicks_kr_value_2024_2026.txt").read_text(encoding="utf-8")).assign(list="V")
    l = leaders.parse((HERE / "kr_leaders_value.txt").read_text(encoding="utf-8")).assign(list="L")
    months = sorted(set(v["month"]) & set(l["month"]))
    print(f"공통 {len(months)}개월 {months[0]}~{months[-1]} | 저평가 {len(v)}행 고유 {v['code'].nunique()} | 대장주 {len(l)}행 고유 {l['code'].nunique()}")
    start = pd.Timestamp(months[0] + "-01").date() - pd.Timedelta(days=10)
    prices = resolve(set(v["code"]) | set(l["code"]), start)
    bench = download(list(BENCH.values()), start)
    cal = bench[BENCH["KODEX200"]].index
    last_month = cal[-1].strftime("%Y-%m")

    sets = {name: {m: set(df[df["month"] == m]["code"]) for m in months} for name, df in (("V", v), ("L", l))}
    rows, stock_rows = [], []
    for i, m in enumerate(months):
        days = cal[cal.strftime("%Y-%m") == m]
        if not len(days):
            continue
        first, last = days[0], days[-1]
        ret = {c: month_return(prices[c], first, last) for c in sets["V"][m] | sets["L"][m] if c in prices}
        ret = {c: r for c, r in ret.items() if r is not None}
        prev = (lambda name: sets[name][months[i - 1]] if i else set())
        groups = {"V": sets["V"][m], "L": sets["L"][m], "both": sets["V"][m] & sets["L"][m],
                  "V_only": sets["V"][m] - sets["L"][m], "L_only": sets["L"][m] - sets["V"][m],
                  "union": sets["V"][m] | sets["L"][m],
                  "V_hold": sets["V"][m] & prev("V"), "L_hold": sets["L"][m] & prev("L"),
                  "hold_either": (sets["V"][m] & prev("V")) | (sets["L"][m] & prev("L")),
                  "both_or_hold2": (sets["V"][m] & sets["L"][m]) | ((sets["V"][m] & prev("V")) & (sets["L"][m] | prev("L")))}
        row = {"month": m, "partial": m == last_month, **{f"n_{g}": len(c) for g, c in groups.items()}}
        for g, codes in groups.items():
            vals = [ret[c] for c in codes if c in ret]
            row[g] = statistics.mean(vals) if vals else math.nan
        for k, t in BENCH.items():
            row[k] = month_return(bench[t], first, last)
        rows.append(row)
        for c, r in ret.items():
            stock_rows.append({"month": m, "code": c, "ret": r, "in_V": c in sets["V"][m], "in_L": c in sets["L"][m],
                               "V_hold": c in groups["V_hold"], "L_hold": c in groups["L_hold"], "ew_bench": row["KODEX200_EW"]})
    t = pd.DataFrame(rows)
    done = t[~t["partial"]]
    OUT.mkdir(exist_ok=True)
    t.to_csv(OUT / "compare_monthly.csv", index=False)
    pd.DataFrame(stock_rows).to_csv(OUT / "compare_stock_months.csv", index=False)
    pd.set_option("display.width", 250)

    print(f"\n[완결 {len(done)}개월, 월평균·누적(%), 200동일가중 대비 월 초과·t·이긴 달]")
    out = []
    for g in ("V", "L", "both", "V_only", "L_only", "union", "V_hold", "L_hold", "hold_either", "both_or_hold2", "KODEX200", "KODEX200_EW"):
        x = done[g]
        ex = x - done["KODEX200_EW"]
        n = done[f"n_{g}"].mean() if f"n_{g}" in done else None
        out.append({"group": g, "avg_n": round(n, 1) if n is not None else None, "mean_pct": round(x.mean(), 2), "total_pct": round(chain(x), 1),
                    "vol_ann": round(x.std(ddof=1) * math.sqrt(12), 1), "ex_vs_EW": round(ex.mean(), 2), "t": round(tstat(ex), 2),
                    "win": f"{int((ex > 0).sum())}/{int(ex.notna().sum())}",
                    "down_months": round(x[done["KODEX200"] <= 0].mean(), 2)})
    perf = pd.DataFrame(out)
    print(perf.to_string(index=False))
    perf.to_csv(OUT / "compare_groups.csv", index=False)

    print("\n[겹침] 월별 V∩L 종목 수:", t.set_index("month")["n_both"].to_dict())
    lf = l.dropna(subset=["per"])
    vf = v.dropna(subset=["per"])
    print("\n[특징 중앙값] 시총(조원)·PER·P/S·ROE·영업이익률·52주고점%·FCF수익률")
    for name, f in (("저평가 V", vf), ("대장주 L", lf)):
        print(f"  {name}: 시총 {f['mcap_krw'].median() / 1e12:.2f} · PER {f['per'].median():.1f} · P/S {f['ps'].median():.2f} · ROE {f['roe'].median():.1f} · "
              f"OPM {f['opm'].median():.1f} · 52주 {f['pct_of_52w_high'].median():.1f} · FCF {f['fcf_yield'].median():.1f} | 금융 비중 "
              f"{(f['sector'].fillna('').str.contains('재정|금융')).mean() * 100:.0f}%")
    print("  대장주 업종:", lf["sector"].value_counts().head(8).to_dict())
    turn = [len(sets["L"][b] - sets["L"][a]) for a, b in zip(months, months[1:])]
    print(f"  대장주 월 교체 평균 {statistics.mean(turn):.1f}/20, 가장 오래 편입:",
          l.groupby("name").size().sort_values(ascending=False).head(10).to_dict())
    both_codes = sorted({c for m in months for c in sets["V"][m] & sets["L"][m]})
    print(f"  25개월 동안 두 명단에 함께 오른 적 있는 종목 {len(both_codes)}개:",
          sorted({n for n, c in zip(l["name"], l["code"]) if c in both_codes})[:30])


if __name__ == "__main__":
    main()
