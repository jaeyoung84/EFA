"""탐색 분석(사전등록 아님): 외국인 코스피200 선물 수급에 따른 코스피 현물의 이후 반응과 급락 뒤 반등 확률.

자료: 한투 시장별 투자자매매동향(일별) — 선물 K2I/F001, 현물 KSP/0001 (2000-06 ~ 2026-10-15, 서버 수집 원자료).
수급은 금액(백만 원)을 직전 250거래일 평균·표준편차로 표준화(z, 당일 미포함 → 미래 정보 없음).
코스피 수익률은 종가 기준(bstp_nmix_prpr), 배당 미반영.

usage: python rebound_study.py data/ff_hist.json
"""
from __future__ import annotations

import json
import math
import sys

import numpy as np
import pandas as pd

HORIZONS = (1, 5, 20, 60)
EPISODE_GAP = 20          # 사건 사이가 20거래일 미만이면 같은 급락 구간으로 묶는다


def frame(rows: dict) -> pd.DataFrame:
    df = pd.DataFrame(rows.values())
    df["date"] = pd.to_datetime(df["stck_bsop_date"])
    for c in df.columns:
        if c not in ("date", "stck_bsop_date", "prdy_vrss_sign"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("date").sort_index()


def zscore(s: pd.Series, n: int = 250) -> pd.Series:
    mu, sd = s.shift(1).rolling(n, min_periods=120).mean(), s.shift(1).rolling(n, min_periods=120).std()
    return (s - mu) / sd


def build(path: str) -> pd.DataFrame:
    raw = json.load(open(path))
    f, s = frame(raw["futures"]), frame(raw["spot"])
    d = pd.DataFrame({"kospi": f["bstp_nmix_prpr"], "fut_frgn": f["frgn_ntby_tr_pbmn"], "fut_frgn_qty": f["frgn_ntby_qty"],
                      "spot_frgn": s["frgn_ntby_tr_pbmn"], "spot_prsn": s["prsn_ntby_tr_pbmn"]}).dropna(subset=["kospi"])
    d = d[d.index >= "2000-06-21"]
    d["r1"] = d["kospi"].pct_change() * 100
    d["r5_past"] = (d["kospi"] / d["kospi"].shift(5) - 1) * 100
    d["dd"] = (d["kospi"] / d["kospi"].rolling(250, min_periods=60).max() - 1) * 100
    d["fut_z"] = zscore(d["fut_frgn"])
    d["fut5_z"] = zscore(d["fut_frgn"].rolling(5).sum())
    d["spot_z"] = zscore(d["spot_frgn"])
    d["spot5_z"] = zscore(d["spot_frgn"].rolling(5).sum())
    for h in HORIZONS:
        d[f"f{h}"] = (d["kospi"].shift(-h) / d["kospi"] - 1) * 100
    # 반등: 20거래일 안에 종가가 +5% 이상 오른 적이 있음
    fwd_max = pd.concat([d["kospi"].shift(-k) for k in range(1, 21)], axis=1).max(axis=1)
    d["bounce5_20"] = np.where(d["f20"].notna(), (fwd_max / d["kospi"] - 1) * 100 >= 5, np.nan)
    return d


def episodes(idx: pd.DatetimeIndex, d: pd.DataFrame) -> list[pd.Timestamp]:
    """연속 사건을 묶어 각 급락 구간의 첫날만 남긴다."""
    pos = d.index.get_indexer(idx)
    keep, last = [], -10**9
    for p, t in zip(pos, idx):
        if p - last >= EPISODE_GAP:
            keep.append(t)
        last = p
    return keep


def describe(sub: pd.DataFrame, label: str) -> dict:
    out = {"label": label, "n_days": len(sub)}
    for h in HORIZONS:
        x = sub[f"f{h}"].dropna()
        out[f"up{h}"] = round((x > 0).mean() * 100, 1) if len(x) else None
        out[f"mean{h}"] = round(x.mean(), 2) if len(x) else None
        out[f"med{h}"] = round(x.median(), 2) if len(x) else None
    b = sub["bounce5_20"].dropna()
    out["bounce5_20"] = round(b.mean() * 100, 1) if len(b) else None
    return out


def main(path: str) -> None:
    d = build(path)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    today = d.iloc[-1]
    print(f"자료 {d.index[0].date()} ~ {d.index[-1].date()}, {len(d)}거래일")
    print(f"오늘 {d.index[-1].date()}: 코스피 {today.kospi:.2f} ({today.r1:+.2f}%), 5일 {today.r5_past:+.2f}%, 52주 고점 대비 {today.dd:+.1f}%")
    print(f"  외국인 선물 {today.fut_frgn_qty:+.0f}계약 / {today.fut_frgn / 100:+,.0f}억 (z {today.fut_z:+.2f}, 5일 z {today.fut5_z:+.2f}), "
          f"외국인 현물 {today.spot_frgn / 100:+,.0f}억 (z {today.spot_z:+.2f}, 5일 z {today.spot5_z:+.2f}), 개인 현물 {today.spot_prsn / 100:+,.0f}억")
    print("\n최근 8거래일:")
    print(d[["kospi", "r1", "fut_frgn_qty", "fut_z", "spot_frgn", "spot_z"]].tail(8).round(2).to_string())

    rows = [describe(d, "전체(무조건)")]
    # 1) 전체 기간에서 외국인 선물 수급과 다음 날·5일 수익률의 관계(순위상관)
    ic = {h: d["fut_z"].corr(d[f"f{h}"], method="spearman") for h in HORIZONS}
    ic5 = {h: d["fut5_z"].corr(d[f"f{h}"], method="spearman") for h in HORIZONS}
    ics = {h: d["spot_z"].corr(d[f"f{h}"], method="spearman") for h in HORIZONS}
    print("\n[전 기간 순위상관] 외국인 선물 z(당일) → 이후 수익:", {h: round(v, 3) for h, v in ic.items()},
          "\n                  외국인 선물 z(5일) → 이후 수익:", {h: round(v, 3) for h, v in ic5.items()},
          "\n                  외국인 현물 z(당일) → 이후 수익:", {h: round(v, 3) for h, v in ics.items()})
    for q, lab in ((d["fut_z"] >= 1.5, "외국인 선물 강한 순매수(z≥1.5)"), (d["fut_z"] <= -1.5, "외국인 선물 강한 순매도(z≤−1.5)")):
        rows.append(describe(d[q], lab))

    # 2) 급락 조건: 하루 −4% 이하 또는 5일 −8% 이하
    crash = (d["r1"] <= -4) | (d["r5_past"] <= -8)
    rows.append(describe(d[crash], "급락일 전체"))
    for q, lab in ((crash & (d["fut_z"] > 0), "급락 + 외국인 선물 순매수(당일)"), (crash & (d["fut_z"] <= 0), "급락 + 외국인 선물 순매도(당일)"),
                   (crash & (d["fut5_z"] > 0), "급락 + 외국인 선물 5일 순매수"), (crash & (d["fut5_z"] <= 0), "급락 + 외국인 선물 5일 순매도"),
                   (crash & (d["fut_z"] > 0) & (d["spot_z"] < 0), "급락 + 선물 매수·현물 매도(지금과 같은 조합)"),
                   (crash & (d["fut_z"] > 0) & (d["spot_z"] >= 0), "급락 + 선물·현물 모두 매수"),
                   (crash & (d["fut_z"] <= 0) & (d["spot_z"] < 0), "급락 + 선물·현물 모두 매도")):
        rows.append(describe(d[q], lab))
    # 3) 지금과 가장 비슷한 강도: 5일 −12% 이하
    deep = d["r5_past"] <= -12
    rows.append(describe(d[deep], "5일 −12% 이하 폭락"))
    rows.append(describe(d[deep & (d["fut5_z"] > 0)], "5일 −12% 이하 + 외국인 선물 5일 순매수"))
    rows.append(describe(d[deep & (d["fut5_z"] <= 0)], "5일 −12% 이하 + 외국인 선물 5일 순매도"))
    t = pd.DataFrame(rows).set_index("label")
    print("\n[조건별 이후 코스피: 상승 확률 %(upN) · 평균 %(meanN) · 중앙값 %(medN), bounce5_20 = 20일 안에 +5% 반등 확률 %]")
    print(t[["n_days", "up1", "up5", "up20", "up60", "med5", "med20", "med60", "mean20", "bounce5_20"]].to_string())

    # 4) 급락 구간(에피소드) 단위 — 같은 위기 안의 연속 사건을 하나로
    print("\n[급락 구간 단위: 5일 −12% 이하 첫날, 20거래일 간격으로 묶음]")
    ev = episodes(d.index[deep], d)
    e = d.loc[ev, ["kospi", "r1", "r5_past", "dd", "fut_z", "fut5_z", "spot5_z", "f5", "f20", "f60", "bounce5_20"]].round(2)
    print(e.to_string())
    for lab, q in (("선물 5일 순매수", e["fut5_z"] > 0), ("선물 5일 순매도", e["fut5_z"] <= 0)):
        sub = e[q]
        print(f"  {lab}: {len(sub)}구간, 20일 상승 {int((sub.f20 > 0).sum())}/{int(sub.f20.notna().sum())}, 60일 상승 {int((sub.f60 > 0).sum())}/{int(sub.f60.notna().sum())}, "
              f"20일 안 +5% 반등 {int(sub.bounce5_20.sum())}/{int(sub.bounce5_20.notna().sum())}, 20일 중앙값 {sub.f20.median():+.2f}%")
    t.to_csv("conditional_table.csv")
    e.to_csv("deep_episodes.csv")


if __name__ == "__main__":
    main(sys.argv[1])
