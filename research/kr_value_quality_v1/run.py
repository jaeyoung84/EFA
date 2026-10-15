"""KR-VQM-v1 실행기(PREREGISTRATION_KR_VQM_v1.md). 로컬 연구 전용 — 사이트·운영 DB와 무관, 주문 없음.

  python run.py --check   데이터 품질만(유니버스 크기, 재무 연결 비율, 분할 조정 건수). 수익률은 계산·출력하지 않는다.
  python run.py           잠금 확인 후 1회 실행(RESULT.json이 있으면 거부).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PREREG = HERE / "PREREGISTRATION_KR_VQM_v1.md"
LOCK = HERE / "LOCK.json"
RESULT = HERE / "RESULT.json"

FORMATION = ("2017-09", "2026-08")
MIN_MCAP, MIN_TV20, MIN_DAYS = 5e11, 2e9, 200
TOP_N, MOM_CUT = 20, 0.30
BUY_COST, SELL_COST = 6.5 / 1e4, 24.5 / 1e4
LAG = {1: 45, 2: 45, 3: 45, 4: 90}            # 분기 말 이후 사용 가능까지 일수
BOOT_DRAWS, BOOT_SEED, BOOT_BLOCK = 10_000, 20261014, 12
FLOW = ("매출액", "영업이익", "당기순이익")
FUND_DB = "kr_fund2.sqlite3"                   # 2차 수집(전 계정, 빈 묶음 재조회)
# DART 계정명 변형 → 표준명(2차 수집 전 계정 목록에서 확인한 것만). 매출액 변형은 없었다(은행·금융지주는 매출액 자체가 없음).
SYNONYMS = {"영업이익(손실)": "영업이익", "당기순이익(손실)": "당기순이익"}
FILL_DB = "kr_fill.sqlite3"                    # 주요계정 API의 빈 손익 항목을 전체 재무제표 API로 채운 값(fill_gaps.py) — 빈 칸에만 사용
# 금융업 양식(은행·보험·금융지주 등): 주요계정 API가 2023년 3분기부터만 제공 → 전 기간 제외(사전등록 §2)
FIN_ACCOUNTS = {"순이자손익", "순수수료손익", "보험계약부채", "예수부채"}


class RunRefused(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── 가격 ────────────────────────────────────────────────────────────
def load_prices() -> pd.DataFrame:
    con = sqlite3.connect(f"file:{DATA / 'krx_daily.sqlite3'}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT trade_date, symbol, name, market, section, open, close, trading_value, market_cap, listed_shares "
                           "FROM daily_prices WHERE market IN ('KOSPI','KOSDAQ') OR symbol = '069500'", con)
    con.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.sort_values(["symbol", "trade_date"]).reset_index(drop=True)


def adjust_splits(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """상장주식수 비율 r과 같은 날 종가 비율이 1/r ±25%면 분할·병합으로 보고 이전 가격을 r로 나눈다."""
    g = df.groupby("symbol", sort=False)
    r = df["listed_shares"] / g["listed_shares"].shift(1)
    px = df["close"] / g["close"].shift(1)
    event = ((r >= 1.5) | (r <= 1 / 1.5)) & ((px * r - 1).abs() <= 0.25)
    step = np.where(event, 1 / r, 1.0)
    df = df.assign(_step=step)
    # 날짜 t의 계수 = t 이후(초과) 사건들의 1/r 곱
    df["_f"] = df.iloc[::-1].groupby("symbol", sort=False)["_step"].cumprod().iloc[::-1].values / df["_step"].values
    df["adj_open"] = df["open"] * df["_f"]
    df["adj_close"] = df["close"] * df["_f"]
    return df.drop(columns=["_step", "_f"]), int(event.sum())


# ── 재무(시점 규칙) ─────────────────────────────────────────────────
def load_quarters() -> pd.DataFrame:
    con = sqlite3.connect(f"file:{DATA / FUND_DB}?mode=ro", uri=True)
    f = pd.read_sql_query("SELECT * FROM fundamentals", con)
    f["account"] = f["account"].replace(SYNONYMS)
    first_year = f.groupby("corp_code")["bsns_year"].min()
    fin = {c for c, a in f.groupby("corp_code")["account"] if set(a) & FIN_ACCOUNTS and first_year[c] >= "2023"}
    fcon = sqlite3.connect(f"file:{DATA / FILL_DB}?mode=ro", uri=True)
    fill = pd.read_sql_query("SELECT corp_code, bsns_year, report, fs_div, account, amount, account_nm FROM fill", fcon)
    fcon.close()
    nm = fill["account_nm"].fillna("").str.replace(" ", "")
    fill = fill[~(nm.str.endswith("손실") & ~nm.str.contains("이익"))].drop(columns="account_nm")  # '영업손실'처럼 부호가 모호한 줄은 쓰지 않음
    f = pd.concat([f, fill], ignore_index=True)  # 원래 값이 먼저 → pivot의 first가 원래 값을 쓴다(채움은 빈 칸에만)
    cmap = pd.read_sql_query("SELECT symbol, corp_code FROM corp_map", con)
    con.close()
    has_cfs = f[f["fs_div"] == "CFS"][["corp_code", "bsns_year", "report"]].drop_duplicates().assign(cfs=True)
    f = f.merge(has_cfs, on=["corp_code", "bsns_year", "report"], how="left")
    f = f[(f["fs_div"] == "CFS") | f["cfs"].isna()]
    f["end"] = pd.to_datetime(f["period_end"].str.extract(r"~\s*(\d{4}\.\d{2}\.\d{2})")[0], format="%Y.%m.%d", errors="coerce")
    wide = f.pivot_table(index=["corp_code", "bsns_year", "report"], columns="account", values="amount", aggfunc="first").reset_index()
    ends = f.groupby(["corp_code", "bsns_year", "report"])["end"].max().reset_index()
    wide = wide.merge(ends, on=["corp_code", "bsns_year", "report"])
    rows = []
    for (corp, year), g in wide.groupby(["corp_code", "bsns_year"]):
        rep = {r: row for r, row in zip(g["report"], g.to_dict("records"))}
        for q, key in ((1, "Q1"), (2, "H1"), (3, "Q3")):
            if key in rep:
                x = rep[key]
                rows.append({"corp_code": corp, "q": q, "end": x["end"], **{a: x.get(a) for a in FLOW},
                             "equity": x.get("자본총계"), "liabilities": x.get("부채총계")})
        if "FY" in rep and all(k in rep for k in ("Q1", "H1", "Q3")):
            fy = rep["FY"]
            q4 = {a: (fy.get(a) - sum(rep[k].get(a) or 0 for k in ("Q1", "H1", "Q3"))) if fy.get(a) is not None else None for a in FLOW}
            if q4["매출액"] is not None and q4["매출액"] < 0:      # 누적값을 낸 회사 등 → 그해 4분기 사용 안 함
                q4 = {a: None for a in FLOW}
            rows.append({"corp_code": corp, "q": 4, "end": fy["end"], **q4, "equity": fy.get("자본총계"), "liabilities": fy.get("부채총계")})
    q = pd.DataFrame(rows).dropna(subset=["end"])
    q["available"] = q["end"] + pd.to_timedelta(q["q"].map(LAG), unit="D")
    cmap = cmap.assign(financial=cmap["corp_code"].isin(fin))
    return q.sort_values(["corp_code", "end"]).reset_index(drop=True), cmap


def fundamentals_at(q: pd.DataFrame, when: pd.Timestamp) -> pd.DataFrame:
    """when 시점에 공개됐다고 볼 수 있는 분기만으로 TTM·자본·ROE·ROE 개선(4분기 전 대비)."""
    v = q[q["available"] <= when]
    out = []
    for corp, g in v.groupby("corp_code", sort=False):
        g = g.tail(8)
        if len(g) < 4:
            continue
        last4 = g.tail(4)
        span = (last4["end"].iloc[-1] - last4["end"].iloc[0]).days
        if not 250 <= span <= 300 or last4[list(FLOW)].isna().any().any():
            continue
        ttm = last4[list(FLOW)].sum()
        eq = last4["equity"].iloc[-1]
        row = {"corp_code": corp, "rev": ttm["매출액"], "op": ttm["영업이익"], "ni": ttm["당기순이익"], "equity": eq,
               "fund_end": last4["end"].iloc[-1], "roe_prev": np.nan}
        prev = g.iloc[:-4] if len(g) == 8 else None
        if prev is not None and len(prev) == 4 and not prev[list(FLOW)].isna().any().any() and prev["equity"].iloc[-1] and prev["equity"].iloc[-1] > 0:
            row["roe_prev"] = prev["당기순이익"].sum() / prev["equity"].iloc[-1]
        out.append(row)
    return pd.DataFrame(out)


# ── 월 패널 ─────────────────────────────────────────────────────────
def month_panel(px: pd.DataFrame) -> tuple[list, dict]:
    days = pd.DatetimeIndex(sorted(px["trade_date"].unique()))
    by_month = pd.Series(days, index=days).groupby(days.to_period("M"))
    ends, firsts = by_month.max(), by_month.min()
    return days, {"ends": ends, "firsts": firsts}


def build_universe(px: pd.DataFrame, form: pd.Timestamp, q: pd.DataFrame, cmap: pd.DataFrame, days) -> tuple[pd.DataFrame, dict]:
    start12 = form - pd.DateOffset(months=12)
    win = px[(px["trade_date"] > start12) & (px["trade_date"] <= form)]
    last = win[win["trade_date"] == form].set_index("symbol")
    stats = win.groupby("symbol").agg(n_days=("trade_date", "size"))
    tv20 = win[win["trade_date"] >= days[days <= form][-20]].groupby("symbol")["trading_value"].mean().rename("tv20")
    u = last.join(stats).join(tv20)
    u = u[(u["market"].isin(["KOSPI", "KOSDAQ"])) & u.index.str.endswith("0") & ~u["name"].str.contains("스팩|리츠", na=False)
          & ~u["section"].fillna("").str.contains("관리종목|SPAC")]
    u = u[(u["market_cap"] >= MIN_MCAP) & (u["tv20"] >= MIN_TV20) & (u["n_days"] >= MIN_DAYS)]
    u = u[~u.index.isin(set(cmap.loc[cmap["financial"], "symbol"]))]
    # 연결 비율(INVALID 판정용): 형성일 기준 최근 1년 안에 끝난 분기 자료가 공개돼 있는 법인의 시총 비중(계정 유무와 무관)
    recent = q[(q["available"] <= form) & (q["end"] > form - pd.DateOffset(years=1))]
    linked = set(cmap[cmap["corp_code"].isin(recent["corp_code"])]["symbol"])
    no_rev = set(cmap[cmap["corp_code"].isin(recent.groupby("corp_code")["매출액"].apply(lambda s: s.isna().all()).loc[lambda s: s].index)]["symbol"])
    mcap_before = u["market_cap"].sum()
    link_mcap = u[u.index.isin(linked)]["market_cap"].sum()
    u = u[~u.index.isin(no_rev)]                 # 매출액 계정이 없는 법인(주로 은행·금융지주) 제외 — 사전등록 §2
    n_base, mcap_base = len(u), u["market_cap"].sum()
    fund = fundamentals_at(q, form).merge(cmap, on="corp_code")
    u = u.join(fund.set_index("symbol"), how="inner")
    u = u[(u["equity"] > 0) & (u["rev"] > 0)]
    coverage = {"coverage_link_mcap": link_mcap / mcap_before if mcap_before else math.nan,
                "no_revenue_mcap": 1 - mcap_base / mcap_before if mcap_before else math.nan,
                "coverage_n": len(u) / n_base if n_base else math.nan,
                "coverage_mcap": u["market_cap"].sum() / mcap_base if mcap_base else math.nan}
    return u, coverage


def momentum(px: pd.DataFrame, i: int, ends: pd.Series) -> pd.Series:
    """12-1개월 수익률: i번째 월말 기준 1개월 전 월말 종가 / 12개월 전 월말 종가 − 1(분할 조정가)."""
    t1, t12 = ends.iloc[i - 1], ends.iloc[i - 12]
    a = px[px["trade_date"] == t1].set_index("symbol")["adj_close"]
    b = px[px["trade_date"] == t12].set_index("symbol")["adj_close"]
    return (a / b - 1).rename("mom")


def scores(u: pd.DataFrame) -> pd.DataFrame:
    u = u.copy()
    u["ep"] = np.where(u["ni"] > 0, u["ni"] / u["market_cap"], -np.inf)
    u["bp"] = u["equity"] / u["market_cap"]
    u["sp"] = u["rev"] / u["market_cap"]
    u["roe"] = u["ni"] / u["equity"]
    u["opm"] = u["op"] / u["rev"]
    u["droe"] = u["roe"] - u["roe_prev"]
    # 빈 값은 가장 낮은 순위(오름차순 순위에서 na_option="top" = 최하위 점수)
    rank = lambda c: u[c].rank(pct=True, na_option="top")  # noqa: E731
    u["V"] = (rank("ep") + rank("bp") + rank("sp")) / 3
    u["Q"] = (rank("roe") + rank("opm") + rank("droe")) / 3
    u["M"] = u["mom"].rank(pct=True, na_option="top")
    u["VQ"] = 0.5 * u["V"] + 0.5 * u["Q"]
    return u


def pick(u: pd.DataFrame, model: str) -> list[str]:
    if model == "VQM":
        pool = u[u["M"] > MOM_CUT]
        return list(pool.sort_values("VQ", ascending=False).index[:TOP_N])
    col = {"V": "V", "Q": "Q", "VQ": "VQ", "M": "M"}[model]
    return list(u.sort_values(col, ascending=False).index[:TOP_N])


def hold_return(px_idx: dict, symbols, entry: pd.Timestamp, exit_: pd.Timestamp, weights=None) -> float:
    rets, ws = [], []
    for i, s in enumerate(symbols):
        frame = px_idx.get(s)
        if frame is None or entry not in frame.index:
            continue
        e = frame.at[entry, "adj_open"]
        after = frame.loc[(frame.index >= entry)]
        if exit_ in frame.index:
            x = frame.at[exit_, "adj_open"]
        else:                                   # 상장폐지·거래정지: 기간 안 마지막 종가
            within = after[after.index < exit_]
            x = within["adj_close"].iloc[-1]
        if e > 0 and x > 0:
            rets.append(x / e - 1)
            ws.append(1.0 if weights is None else weights[i])
    if not rets:
        return math.nan
    ws = np.array(ws) / np.sum(ws)
    return float(np.dot(ws, rets))


def run(check_only: bool) -> dict:
    px, n_splits = adjust_splits(load_prices())
    q, cmap = load_quarters()
    days, cal = month_panel(px)
    ends, firsts = cal["ends"], cal["firsts"]
    px_idx = {s: g.set_index("trade_date")[["adj_open", "adj_close"]] for s, g in px.groupby("symbol")}
    form_months = [p for p in ends.index if FORMATION[0] <= str(p) <= FORMATION[1]]
    report = {"splits_adjusted": n_splits, "months": []}
    rows, prev = [], {m: [] for m in ("VQM", "V", "Q", "VQ", "M")}
    for p in form_months:
        form, i = ends[p], list(ends.index).index(p)
        nxt = p + 1                              # 보유 달: 그달 첫 거래일 시가 → 다음 달 첫 거래일 시가
        if nxt not in firsts.index:
            continue
        entry = firsts[nxt]
        exit_ = firsts[nxt + 1] if nxt + 1 in firsts.index else days[-1]
        u, coverage = build_universe(px, form, q, cmap, days)
        u = u.join(momentum(px, i, ends), how="left")
        u = scores(u)
        info = {"formation": str(form.date()), "universe": len(u), **{k: round(v, 3) for k, v in coverage.items()}}
        report["months"].append(info)
        if check_only:
            continue
        row = {"month": str(nxt), "universe": len(u)}
        row["EW"] = hold_return(px_idx, list(u.index), entry, exit_)
        row["CW"] = hold_return(px_idx, list(u.index), entry, exit_, list(u["market_cap"]))
        row["KODEX200"] = hold_return(px_idx, ["069500"], entry, exit_)
        for model in prev:
            names = pick(u, model)
            gross = hold_return(px_idx, names, entry, exit_)
            buys = len(set(names) - set(prev[model])) / TOP_N
            sells = len(set(prev[model]) - set(names)) / TOP_N if prev[model] else 0.0
            row[model] = gross - buys * BUY_COST - sells * SELL_COST
            if model == "VQM":
                row["turnover"] = buys
                row["names"] = names
                for s in names:                  # 분할 조정 실패 점검(사전등록 §4 INVALID): 보유 종목 월 수익률 ±80% 초과 기록
                    r1 = hold_return(px_idx, [s], entry, exit_)
                    if not math.isnan(r1) and abs(r1) > 0.8:
                        report.setdefault("extreme_holdings", []).append({"month": str(nxt), "symbol": s, "ret": round(r1, 4)})
            prev[model] = names
        rows.append(row)
    if check_only:
        cov = pd.DataFrame(report["months"])
        report["summary"] = {"months": len(cov), "universe_min": int(cov["universe"].min()), "universe_median": float(cov["universe"].median()),
                             "coverage_link_mcap_min": float(cov["coverage_link_mcap"].min()),
                             "months_link_below_80": int((cov["coverage_link_mcap"] < 0.8).sum()),
                             "no_revenue_mcap_median": float(cov["no_revenue_mcap"].median()),
                             "coverage_mcap_min": float(cov["coverage_mcap"].min()), "coverage_mcap_median": float(cov["coverage_mcap"].median()),
                             "months_coverage_below_80": int((cov["coverage_mcap"] < 0.8).sum())}
        return report
    return evaluate(pd.DataFrame(rows), report)


def evaluate(t: pd.DataFrame, report: dict) -> dict:
    t["ex"] = (t["VQM"] - t["EW"]) * 100
    ex = t["ex"].dropna().to_numpy()
    n = len(ex)
    tstat = ex.mean() / (ex.std(ddof=1) / math.sqrt(n))
    rng = random.Random(BOOT_SEED)
    means = []
    for _ in range(BOOT_DRAWS):
        sample = []
        while len(sample) < n:
            s = rng.randrange(0, n - BOOT_BLOCK + 1)
            sample.extend(ex[s:s + BOOT_BLOCK])
        means.append(float(np.mean(sample[:n])))
    means.sort()
    lo, hi = means[int(0.025 * BOOT_DRAWS)], means[int(0.975 * BOOT_DRAWS) - 1]
    t["year"] = t["month"].str[:4]
    yearly = t[t["year"] >= "2018"].groupby("year")[["VQM", "EW"]].apply(lambda g: (np.prod(1 + g["VQM"]) - np.prod(1 + g["EW"])) * 100)
    pos_years = int((yearly > 0).sum())
    stats = lambda col: {"total_pct": round((np.prod(1 + t[col].dropna()) - 1) * 100, 2),  # noqa: E731
                         "ann_pct": round((np.prod(1 + t[col].dropna()) ** (12 / t[col].notna().sum()) - 1) * 100, 2),
                         "vol_pct": round(t[col].std() * math.sqrt(12) * 100, 2),
                         "mdd_pct": round(float(((1 + t[col]).cumprod() / (1 + t[col]).cumprod().cummax() - 1).min() * 100), 2)}
    link_low = sum(m["coverage_link_mcap"] < 0.8 for m in report["months"])
    verdict = ("INVALID" if link_low > 12 else "REJECT" if ex.mean() <= 0 else
               "PASS" if (tstat >= 2.0 and lo > 0 and pos_years >= 6) else "FAIL")
    # ±80% 초과 보유 종목이 있으면 판정 전에 분할 여부를 사람이 확인한다(extreme_holdings, 사전등록 §4)
    report.update(verdict=verdict, months_link_below_80=link_low, extreme_holdings=report.get("extreme_holdings", []), months_evaluated=n, mean_excess_pct=round(ex.mean(), 3), t=round(tstat, 2),
                  bootstrap95=[round(lo, 3), round(hi, 3)], yearly_excess_pct={k: round(v, 2) for k, v in yearly.items()},
                  positive_years=f"{pos_years}/{len(yearly)}", avg_turnover=round(t["turnover"].mean(), 3),
                  series={c: stats(c) for c in ("VQM", "EW", "CW", "KODEX200", "V", "Q", "VQ", "M")},
                  ablation_mean_excess_vs_EW={m: round(((t[m] - t["EW"]) * 100).mean(), 3) for m in ("V", "Q", "VQ", "M")},
                  up_down={"EW_up": round(t.loc[t["EW"] > 0, "ex"].mean(), 3), "EW_down": round(t.loc[t["EW"] <= 0, "ex"].mean(), 3)})
    t.drop(columns=["names"]).to_csv(HERE / "MONTHLY.csv", index=False)
    pd.DataFrame([{"month": m, "symbols": ",".join(n)} for m, n in zip(t["month"], t["names"])]).to_csv(HERE / "HOLDINGS.csv", index=False)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        rep = run(check_only=True)
        print(json.dumps({k: v for k, v in rep.items() if k != "months"}, ensure_ascii=False, indent=1))
        print("coverage by month (first/last 3):", rep["months"][:3], rep["months"][-3:])
        return
    if RESULT.exists():
        raise RunRefused("RESULT.json exists: one run only")
    lock = json.loads(LOCK.read_text())
    current = {"prereg": sha(PREREG), "run": sha(HERE / "run.py"), "krx_daily": sha(DATA / "krx_daily.sqlite3"),
               "kr_fund": sha(DATA / FUND_DB), "kr_fill": sha(DATA / FILL_DB)}
    if lock != current:
        raise RunRefused(f"lock mismatch {[k for k in current if lock.get(k) != current[k]]}")
    rep = run(check_only=False)
    rep.update(lock=current, executed_at=datetime.now(timezone.utc).isoformat())
    RESULT.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: v for k, v in rep.items() if k != "months"}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
