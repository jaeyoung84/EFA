"""InvestingPro '한국 저평가 가치 투자주' 25개월(2024-10~2026-10) 명단 분석 — 로컬 전용, 사이트·배포와 무관.

입력: PDF에서 뽑은 텍스트(페이지마다 'YYYY년 M월' 제목, 종목 행). 각 달 명단은 그달 1일 리밸런싱(첫 거래일 시가 → 마지막 거래일 종가).
출력: 월별 성과·벤치마크, 선정 특징 요약, 명단 안 지표와 그달 수익의 관계. 소급 입력 자료이며 비용·세금 미반영.

usage: python research/external_picks_kr/study.py <picks.txt> <out_dir>
"""
from __future__ import annotations

import math
import re
import statistics
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import BENCH, download, month_return, resolve  # noqa: E402

UNIT = {"K": 1e3, "M": 1e6, "B": 1e9}
ROW = re.compile(r"^A(\d{6})\s*(.*)$")
NUM = re.compile(r"(-?[\d,]+(?:\.\d+)?)\s?([KMB])\b")
RATIO = re.compile(r"(-?[\d,]+(?:\.\d+)?)(%|x)")


def parse(text: str) -> pd.DataFrame:
    rows = []
    for page in text.split("=====PAGE")[1:]:
        m = re.search(r"(\d{4})년\s*(\d{1,2})월", page)
        if not m:
            continue
        month = f"{m.group(1)}-{int(m.group(2)):02d}"
        for line in page.splitlines():
            r = ROW.match(line.strip())
            if not r:
                continue
            code, rest = r.groups()
            exch = "KOSDAQ" if "KOSDAQ" in rest else "KOSPI"
            name = re.split(r"KOSE|KOSDAQ|\d", rest)[0].strip()
            row = {"month": month, "code": code, "name": name, "exchange": exch}
            sector = re.search(r"(?:KOSE|KOSDAQ)\s*(.+?)\s+프런티어", rest)
            row["sector"] = sector.group(1).strip() if sector else None
            nums = [float(v.replace(",", "")) * UNIT[u] for v, u in NUM.findall(rest)]
            ratios = [(float(v.replace(",", "")), u) for v, u in RATIO.findall(rest)]
            if sector and len(nums) >= 4 and len(ratios) >= 5:     # 스크리너 형식(10월 화면 형식은 종목만)
                row.update(adv_usd=nums[0], mcap_krw=nums[1], revenue_usd=nums[2], net_income_usd=nums[3],
                           opm=ratios[0][0], ps=ratios[1][0], per=ratios[2][0], fcf_yield=ratios[3][0], roe=ratios[4][0])
                if len(ratios) >= 7:
                    row.update(cash_ratio=ratios[5][0], pct_of_52w_high=ratios[6][0])
            rows.append(row)
    return pd.DataFrame(rows).drop_duplicates(["month", "code"])


def spearman(a: pd.Series, b: pd.Series) -> float:
    ok = a.notna() & b.notna()
    return a[ok].rank().corr(b[ok].rank()) if ok.sum() > 5 else math.nan


def main(src: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    picks = parse(Path(src).read_text(encoding="utf-8"))
    picks.to_csv(out / "picks_features.csv", index=False)
    months = sorted(picks["month"].unique())
    print(f"명단 {len(months)}개월 {months[0]}~{months[-1]}, 행 {len(picks)}, 고유 종목 {picks['code'].nunique()}")
    print("월별 종목 수:", picks.groupby("month").size().to_dict())

    start = pd.Timestamp(months[0] + "-01").date() - pd.Timedelta(days=10)
    prices = resolve(set(picks["code"]), start)
    bench = download(list(BENCH.values()), start)
    cal = bench[BENCH["KODEX200"]].index
    rets, table = [], []
    for month in months:
        days = cal[cal.strftime("%Y-%m") == month]
        if not len(days):
            continue
        first, last = days[0], days[-1]
        b = {k: month_return(bench[t], first, last) for k, t in BENCH.items()}
        sub = picks[picks["month"] == month]
        r = {c: month_return(prices[c], first, last) if c in prices else None for c in sub["code"]}
        for c, v in r.items():
            rets.append({"month": month, "code": c, "ret": v})
        vals = [v for v in r.values() if v is not None]
        ew = statistics.mean(vals)
        table.append({"month": month, "n": len(vals), "picks": ew, **b, "ex_200": ew - b["KODEX200"], "ex_ew": ew - b["KODEX200_EW"],
                      "partial": month == cal[-1].strftime("%Y-%m")})
    perf = pd.DataFrame(table)
    perf.to_csv(out / "monthly_performance.csv", index=False)
    pd.set_option("display.width", 220)
    print("\n[월별 성과 %]\n" + perf.round(2).to_string(index=False))
    done = perf[~perf["partial"]]
    chain = lambda col: ((1 + done[col] / 100).prod() - 1) * 100  # noqa: E731
    years = len(done) / 12
    ann = lambda col: ((1 + chain(col) / 100) ** (1 / years) - 1) * 100  # noqa: E731
    print(f"\n[완료 {len(done)}개월 누적] 명단 {chain('picks'):+.1f}% (연 {ann('picks'):+.1f}%) | KODEX200 {chain('KODEX200'):+.1f}% (연 {ann('KODEX200'):+.1f}%) | "
          f"200동일가중 {chain('KODEX200_EW'):+.1f}% (연 {ann('KODEX200_EW'):+.1f}%) | 코스닥150 {chain('KOSDAQ150'):+.1f}%")
    for col, label in (("ex_200", "KODEX200 대비"), ("ex_ew", "200동일가중 대비")):
        ex = done[col]
        t = ex.mean() / (ex.std(ddof=1) / math.sqrt(len(ex)))
        print(f"{label}: 월평균 {ex.mean():+.2f}%p, 표준편차 {ex.std(ddof=1):.2f}, t={t:.2f}, 이긴 달 {(ex > 0).sum()}/{len(ex)}, 최악 {ex.min():+.2f}, 최고 {ex.max():+.2f}")
    vol = lambda col: done[col].std(ddof=1) * math.sqrt(12)  # noqa: E731
    dd = lambda col: ((1 + done[col] / 100).cumprod() / (1 + done[col] / 100).cumprod().cummax() - 1).min() * 100  # noqa: E731
    print(f"연변동성 명단 {vol('picks'):.1f}% / KODEX200 {vol('KODEX200'):.1f}% / 동일가중 {vol('KODEX200_EW'):.1f}%, "
          f"월말 기준 최대낙폭 명단 {dd('picks'):.1f}% / KODEX200 {dd('KODEX200'):.1f}%")
    up = done[done["KODEX200"] > 0]
    down = done[done["KODEX200"] <= 0]
    print(f"시장 상승 달 {len(up)}개: 명단 평균 {up['picks'].mean():+.2f} vs 200 {up['KODEX200'].mean():+.2f} | "
          f"하락 달 {len(down)}개: 명단 {down['picks'].mean():+.2f} vs 200 {down['KODEX200'].mean():+.2f}")

    # 선정 특징
    f = picks.dropna(subset=["mcap_krw"])
    print("\n[선정 종목 특징: 최소 / 중앙값 / 최대]")
    for col, scale, unit in (("mcap_krw", 1e12, "조원"), ("adv_usd", 1e3, "천달러"), ("per", 1, "배"), ("ps", 1, "배"), ("fcf_yield", 1, "%"),
                             ("roe", 1, "%"), ("opm", 1, "%"), ("pct_of_52w_high", 1, "%")):
        s = f[col].dropna() / scale
        if len(s):
            print(f"  {col:16s} {s.min():8.2f} / {s.median():8.2f} / {s.max():8.2f} {unit}  (n={len(s)})")
    print("  거래소:", f["exchange"].value_counts().to_dict())
    print("  업종:", f["sector"].value_counts().head(10).to_dict())
    print(f"  PER 25배 이하 {100 * (f['per'] <= 25).mean():.0f}%, P/S 2배 이하 {100 * (f['ps'] <= 2).mean():.0f}%, FCF수익률>0 {100 * (f['fcf_yield'] > 0).mean():.0f}%, ROE>0 {100 * (f['roe'] > 0).mean():.0f}%")
    sets = {m: set(picks[picks["month"] == m]["code"]) for m in months}
    turnover = [len(sets[b] - sets[a]) for a, b in zip(months, months[1:])]
    print(f"  월 교체 종목 수 평균 {statistics.mean(turnover):.1f} (최소 {min(turnover)}, 최대 {max(turnover)}) / 15")
    tenure = picks.groupby("code").size().sort_values(ascending=False)
    print("  가장 오래 편입:", {picks[picks['code'] == c]['name'].iloc[0] or c: int(n) for c, n in tenure.head(8).items()})

    # 명단 안 지표와 그달 수익
    r = pd.DataFrame(rets).dropna()
    merged = r.merge(picks, on=["month", "code"])
    merged = merged.merge(perf[["month", "KODEX200_EW"]], on="month")
    merged["ex"] = merged["ret"] - merged["KODEX200_EW"]
    prev = {m: sets[p] for p, m in zip(months, months[1:])}
    merged["new_entry"] = [c not in prev.get(m, set()) for m, c in zip(merged["month"], merged["code"])]
    merged.to_csv(out / "stock_month_returns.csv", index=False)
    print("\n[명단 안에서 지표와 그달 초과수익의 순위상관(스피어만), 월별 IC 평균]")
    for col in ("per", "ps", "fcf_yield", "roe", "opm", "mcap_krw", "pct_of_52w_high"):
        ics = [spearman(g[col], g["ex"]) for _, g in merged.groupby("month")]
        ics = [x for x in ics if not math.isnan(x)]
        if len(ics) >= 5:
            t = statistics.mean(ics) / (statistics.stdev(ics) / math.sqrt(len(ics)))
            print(f"  {col:16s} IC 평균 {statistics.mean(ics):+.3f}, t={t:+.2f}, 양수 달 {sum(x > 0 for x in ics)}/{len(ics)}")
    print(f"  신규 편입 평균 초과 {merged[merged['new_entry']]['ex'].mean():+.2f}%p (n={merged['new_entry'].sum()}) / 유지 종목 {merged[~merged['new_entry']]['ex'].mean():+.2f}%p")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
