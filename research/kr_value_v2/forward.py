"""KR-LV-v2 — 한국 대형 가치주 전향 검증 실행기(사전등록 PREREGISTRATION_KR_LV_v2.md).

명령:
  pick      --krx <krx.sqlite3> --fund <kr_fund.sqlite3> --fill <kr_fill.sqlite3> --form YYYY-MM-DD
            형성일 명단을 만들어 ledger/<형성월>.json에 기록(이미 있으면 거부). 잠금(LOCK.json) 일치 필요.
  evaluate  --krx <krx.sqlite3>
            ledger의 달마다 실현 수익(첫 거래일 시가 → 다음 달 첫 거래일 시가)을 계산하고 판정 상태를 FORWARD_RESULT.json에 쓴다.
  reference --krx ... --fund ... --fill ...
            같은 규칙의 과거(2017-09 ~ 2026-08 형성) 오염된 참고치 → REFERENCE_BACKTEST.json(한 번만, 잠금 뒤).

데이터 처리(분할 조정, DART 시점 규칙·계정명·빈 손익 보충)는 잠긴 v1 실행기(research/kr_value_quality_v1/run.py)를 그대로 쓴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "kr_value_quality_v1"))
import run as v1  # noqa: E402

PREREG = HERE / "PREREGISTRATION_KR_LV_v2.md"
LOCK = HERE / "LOCK.json"
LEDGER = HERE / "ledger"
TOP_MCAP, MIN_TV20, MIN_DAYS = 200, 5e9, 200
MIN_ROE, TOP_N, MAX_FIN = 0.05, 20, 5
BUY_COST, SELL_COST = 6.5 / 1e4, 24.5 / 1e4
FORWARD = ("2026-10", "2028-09")                 # 형성월(보유 2026-11 ~ 2028-10)
REFERENCE = ("2017-09", "2026-08")
EARLY_STOP = -15.0                               # 누적 초과 %p
FIN_ACCOUNTS = {"순이자손익", "순수수료손익", "보험계약부채", "예수부채"}
FIN_NAME = "증권|은행|보험|화재|해상|생명|캐피탈|카드|종금|금융|투자|뱅크|페이|코리안리"   # 금융 계정이 1개뿐일 때 이름으로 확인


class Refused(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_lock() -> None:
    if not LOCK.exists():
        raise Refused("LOCK.json 없음: 사전등록을 잠근 뒤 실행")
    lock = json.loads(LOCK.read_text())
    now = {"prereg": sha(PREREG), "forward": sha(HERE / "forward.py")}
    if {k: lock.get(k) for k in now} != now:
        raise Refused(f"잠금 불일치 {[k for k in now if lock.get(k) != now[k]]}")


# ── 데이터 ──────────────────────────────────────────────────────────
def load_prices(krx: Path) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{krx}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT trade_date, symbol, name, market, section, open, close, trading_value, market_cap, listed_shares "
                           "FROM daily_prices WHERE market IN ('KOSPI','KOSDAQ') OR symbol = '069500'", con)
    con.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df, _ = v1.adjust_splits(df.sort_values(["symbol", "trade_date"]).reset_index(drop=True))
    return df


def load_fund(fund: Path, fill: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    v1.DATA, v1.FUND_DB, v1.FILL_DB = fund.parent, fund.name, fill.name
    if fill.parent != fund.parent:
        raise Refused("fund와 fill은 같은 폴더에 둔다")
    q, cmap = v1.load_quarters()
    con = sqlite3.connect(f"file:{fund}?mode=ro", uri=True)
    acc = pd.read_sql_query("SELECT DISTINCT corp_code, account FROM fundamentals", con)
    con.close()
    n_fin = acc[acc["account"].isin(FIN_ACCOUNTS)].groupby("corp_code").size()
    return q, cmap.assign(n_fin_accounts=cmap["corp_code"].map(n_fin).fillna(0).astype(int))


def ttm_at(q: pd.DataFrame, when: pd.Timestamp) -> pd.DataFrame:
    """when까지 공개된 연속 4개 분기의 TTM 순이익과 최근 자본총계(매출·영업이익은 요구하지 않음 — 금융업 포함)."""
    v = q[q["available"] <= when]
    out = []
    for corp, g in v.groupby("corp_code", sort=False):
        last4 = g.tail(4)
        if len(last4) < 4 or last4["당기순이익"].isna().any():
            continue
        if not 250 <= (last4["end"].iloc[-1] - last4["end"].iloc[0]).days <= 300:
            continue
        out.append({"corp_code": corp, "ni": last4["당기순이익"].sum(), "equity": last4["equity"].iloc[-1], "fund_end": last4["end"].iloc[-1]})
    return pd.DataFrame(out, columns=["corp_code", "ni", "equity", "fund_end"])


# ── 선정 ────────────────────────────────────────────────────────────
def universe(px: pd.DataFrame, form: pd.Timestamp, days: pd.DatetimeIndex) -> pd.DataFrame:
    win = px[(px["trade_date"] > form - pd.DateOffset(months=12)) & (px["trade_date"] <= form)]
    last = win[win["trade_date"] == form].set_index("symbol")
    u = last[last["market"].isin(["KOSPI", "KOSDAQ"]) & last.index.str.endswith("0") & ~last["name"].str.contains("스팩|리츠", na=False)
             & ~last["section"].fillna("").str.contains("관리종목|SPAC")]
    u = u.sort_values("market_cap", ascending=False).head(TOP_MCAP)
    tv20 = win[win["trade_date"] >= days[days <= form][-20]].groupby("symbol")["trading_value"].mean()
    n_days = win.groupby("symbol").size()
    return u[(tv20.reindex(u.index) >= MIN_TV20) & (n_days.reindex(u.index) >= MIN_DAYS)]


def select(px, q, cmap, form, days) -> tuple[pd.DataFrame, list[str]]:
    u = universe(px, form, days)
    fund = ttm_at(q, form).merge(cmap, on="corp_code").drop_duplicates("symbol").set_index("symbol")
    u = u.join(fund[["ni", "equity", "fund_end", "n_fin_accounts"]], how="inner")
    u["financial"] = (u["n_fin_accounts"] >= 2) | ((u["n_fin_accounts"] == 1) & u["name"].str.contains(FIN_NAME, na=False))
    u = u[u["equity"] > 0]
    ok = u[(u["ni"] > 0) & (u["ni"] / u["equity"] >= MIN_ROE)].copy()
    ok["ep"] = ok["ni"] / ok["market_cap"]
    ok["bp"] = ok["equity"] / ok["market_cap"]
    ok["V"] = (ok["ep"].rank(pct=True) + ok["bp"].rank(pct=True)) / 2
    picks, n_fin = [], 0
    for s, r in ok.sort_values("V", ascending=False).iterrows():
        if r["financial"] and n_fin >= MAX_FIN:
            continue
        picks.append(s)
        n_fin += bool(r["financial"])
        if len(picks) == TOP_N:
            break
    return u.assign(V=ok["V"]), picks


# ── 수익 ────────────────────────────────────────────────────────────
def month_days(px: pd.DataFrame) -> tuple[pd.DatetimeIndex, pd.Series, pd.Series]:
    days = pd.DatetimeIndex(sorted(px["trade_date"].unique()))
    by = pd.Series(days, index=days).groupby(days.to_period("M"))
    return days, by.max(), by.min()


def realized(px_idx, symbols, entry, exit_, weights=None) -> float:
    return v1.hold_return(px_idx, symbols, entry, exit_, weights)


def costs(names: list[str], prev: list[str]) -> float:
    buys = len(set(names) - set(prev)) / TOP_N
    sells = len(set(prev) - set(names)) / TOP_N if prev else 0.0
    return buys * BUY_COST + sells * SELL_COST


def series_stats(t: pd.DataFrame, cols) -> dict:
    out = {}
    for c in cols:
        r = t[c].dropna()
        cum = (1 + r).cumprod()
        out[c] = {"total_pct": round((cum.iloc[-1] - 1) * 100, 2) if len(r) else None,
                  "mdd_pct": round(float((cum / cum.cummax() - 1).min() * 100), 2) if len(r) else None}
    return out


def returns_table(px, months: list[dict]) -> pd.DataFrame:
    days, ends, firsts = month_days(px)
    px_idx = {s: g.set_index("trade_date")[["adj_open", "adj_close"]] for s, g in px.groupby("symbol")}
    rows, prev = [], []
    for m in months:
        hold = pd.Period(m["formation"][:7], "M") + 1
        if hold + 1 not in firsts.index:              # 보유 달이 아직 끝나지 않음(다음 달 첫 거래일 시가가 필요)
            break
        entry, exit_ = firsts[hold], firsts[hold + 1]
        names = m["picks"]
        gross = realized(px_idx, names, entry, exit_)
        rows.append({"month": str(hold), "model": gross - costs(names, prev), "gross": gross,
                     "KODEX200": realized(px_idx, ["069500"], entry, exit_),
                     "EW": realized(px_idx, m["universe"], entry, exit_),
                     "CW": realized(px_idx, m["universe"], entry, exit_, m["universe_mcap"]),
                     "turnover": len(set(names) - set(prev)) / TOP_N})
        prev = names
    return pd.DataFrame(rows)


def verdict(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"months": 0, "status": "관측 전"}
    e = (t["model"] - t["KODEX200"]) * 100
    cum_ex = ((1 + t["model"]).prod() - (1 + t["KODEX200"]).prod()) * 100
    n = len(e)
    tstat = e.mean() / (e.std(ddof=1) / math.sqrt(n)) if n > 1 and e.std(ddof=1) > 0 else math.nan
    path = ((1 + t["model"]).cumprod() - (1 + t["KODEX200"]).cumprod()) * 100
    out = {"months": n, "mean_excess_pct": round(e.mean(), 3), "t": round(tstat, 2) if not math.isnan(tstat) else None,
           "positive_months": int((e > 0).sum()), "cum_excess_pctp": round(cum_ex, 2), "worst_cum_excess_pctp": round(path.min(), 2)}
    stop = path[path <= EARLY_STOP]
    if len(stop):
        out.update(status="REJECT(조기 중단)", stopped_at=t["month"].iloc[stop.index[0]])
    elif n < 24:
        out["status"] = "관측 중" + (" — 12개월 중간 보고 시점" if n == 12 else "")
    else:
        e24 = e.iloc[:24]
        t24 = e24.mean() / (e24.std(ddof=1) / math.sqrt(24))
        out["status"] = ("REJECT" if e24.mean() <= 0 else
                         "PASS" if (t24 >= 1.5 and (e24 > 0).sum() >= 13) else "FAIL")
    return out


# ── 명령 ────────────────────────────────────────────────────────────
def cmd_pick(a) -> None:
    check_lock()
    form = pd.Timestamp(a.form)
    if not FORWARD[0] <= form.strftime("%Y-%m") <= FORWARD[1]:
        raise Refused(f"형성월 {form:%Y-%m}은 전향 기간 {FORWARD} 밖")
    out = LEDGER / f"{form:%Y-%m}.json"
    if out.exists():
        raise Refused(f"{out.name} 이미 있음: 명단은 달마다 한 번만")
    px = load_prices(Path(a.krx))
    days, ends, _ = month_days(px)
    if ends.get(form.to_period("M")) != form:
        raise Refused(f"{form.date()}는 그달 마지막 거래일이 아님(자료상 {ends.get(form.to_period('M'))})")
    q, cmap = load_fund(Path(a.fund), Path(a.fill))
    u, picks = select(px, q, cmap, form, days)
    rec = {"formation": str(form.date()), "generated_at": datetime.now(timezone.utc).isoformat(),
           "inputs": {"krx": sha(Path(a.krx)), "fund": sha(Path(a.fund)), "fill": sha(Path(a.fill))},
           "picks": picks, "names": [u.at[s, "name"] for s in picks],
           "financial": [bool(u.at[s, "financial"]) for s in picks], "V": [round(float(u.at[s, "V"]), 4) for s in picks],
           "universe": list(u.index), "universe_mcap": [float(x) for x in u["market_cap"]],
           "fund_end_min": str(u.loc[picks, "fund_end"].min().date()) if picks else None}
    LEDGER.mkdir(exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    print(f"{form.date()} 형성, 유니버스 {len(u)}개, 선정 {len(picks)}개(금융 {sum(rec['financial'])}):")
    for s, n, f in zip(picks, rec["names"], rec["financial"]):
        print(f"  {s} {n}{' (금융)' if f else ''}")


def cmd_evaluate(a) -> None:
    months = [json.loads(p.read_text()) for p in sorted(LEDGER.glob("*.json"))]
    t = returns_table(load_prices(Path(a.krx)), months)
    res = {"evaluated_at": datetime.now(timezone.utc).isoformat(), **verdict(t),
           "series": series_stats(t, ["model", "KODEX200", "EW", "CW"]) if len(t) else {},
           "monthly": t.round(5).to_dict("records")}
    (HERE / "FORWARD_RESULT.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: v for k, v in res.items() if k != "monthly"}, ensure_ascii=False, indent=1, default=str))


def cmd_reference(a) -> None:
    check_lock()
    out = HERE / "REFERENCE_BACKTEST.json"
    if out.exists():
        raise Refused("REFERENCE_BACKTEST.json 이미 있음: 한 번만")
    px = load_prices(Path(a.krx))
    days, ends, _ = month_days(px)
    q, cmap = load_fund(Path(a.fund), Path(a.fill))
    months = []
    for p in [p for p in ends.index if REFERENCE[0] <= str(p) <= REFERENCE[1]]:
        u, picks = select(px, q, cmap, ends[p], days)
        months.append({"formation": str(ends[p].date()), "picks": picks, "universe": list(u.index),
                       "universe_mcap": [float(x) for x in u["market_cap"]], "n_fin": int(u.loc[picks, "financial"].sum())})
    t = returns_table(px, months)
    e = (t["model"] - t["KODEX200"]) * 100
    res = {"note": "오염된 참고치 — 같은 기간을 v1에서 이미 봄. 판정에 쓰지 않는다(사전등록 §6).",
           "months": len(t), "mean_excess_vs_KODEX200_pct": round(e.mean(), 3),
           "t": round(e.mean() / (e.std(ddof=1) / math.sqrt(len(e))), 2),
           "mean_excess_vs_EW_pct": round(((t["model"] - t["EW"]) * 100).mean(), 3),
           "yearly_excess_vs_KODEX200_pctp": {y: round(((1 + g["model"]).prod() - (1 + g["KODEX200"]).prod()) * 100, 2)
                                             for y, g in t.groupby(t["month"].str[:4])},
           "series": series_stats(t, ["model", "KODEX200", "EW", "CW"]), "avg_turnover": round(t["turnover"].mean(), 3),
           "avg_financial_picks": round(float(np.mean([m["n_fin"] for m in months])), 2),
           "universe_size_median": float(np.median([len(m["universe"]) for m in months])),
           "lock": json.loads(LOCK.read_text()), "executed_at": datetime.now(timezone.utc).isoformat()}
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    t.to_csv(HERE / "REFERENCE_MONTHLY.csv", index=False)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pick", "reference"):
        s = sub.add_parser(name)
        s.add_argument("--krx", required=True)
        s.add_argument("--fund", required=True)
        s.add_argument("--fill", required=True)
        if name == "pick":
            s.add_argument("--form", required=True)
    sub.add_parser("evaluate").add_argument("--krx", required=True)
    a = ap.parse_args()
    {"pick": cmd_pick, "evaluate": cmd_evaluate, "reference": cmd_reference}[a.cmd](a)


if __name__ == "__main__":
    main()
