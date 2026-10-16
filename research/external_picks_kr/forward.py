"""KR-PRO-CONSENSUS-v1 전향 실행기(PREREGISTRATION_KR_PRO_CONSENSUS_v1.md).

  freeze   --month YYYY-MM --lists <사이트 외부 추천 기록 내보내기 JSON> --krx <krx.sqlite3>
           두 명단(V·L)을 확정해 private/forward/<월>/snapshot.json(비공개)에 쓰고 해시를 ledger_hashes.json(공개)에 추가.
  evaluate --krx <krx.sqlite3>   확정된 달의 S·V·L·보조 팔·ETF 비용 후 월수익과 일별 평가 → FORWARD_RESULT.json(집계만)
  judge    --krx <krx.sqlite3>   24개월이 모두 완결되면 사전등록 판정(한 번)

내보내기 JSON 형식: [{"source", "as_of", "created_at"(UTC ISO), "raw_sha256", "items": [{"ticker"}...]}, ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIVATE = HERE / "private" / "forward"
LEDGER = HERE / "ledger_hashes.json"
RESULT = HERE / "FORWARD_RESULT.json"
LOCK = HERE / "LOCK.json"
PREREG = HERE / "PREREGISTRATION_KR_PRO_CONSENSUS_v1.md"
KST = timezone(timedelta(hours=9))
SOURCES = {"V": "InvestingPro 한국 저평가 가치", "L": "InvestingPro 한국 대장주 가치"}
FIRST_MONTH, LAST_MONTH = "2026-11", "2028-10"
BUY, SELL = 6.5 / 1e4, 24.5 / 1e4
ETFS = {"KODEX200": "069500", "KODEX200_EW": "252650"}
ALPHA, NW_LAGS = 0.05, 3


class Refused(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_lock() -> None:
    lock = json.loads(LOCK.read_text())
    now = {f: sha((HERE / f).read_bytes()) for f in lock["files"]}
    if now != lock["files"]:
        raise Refused(f"잠금 불일치: {[f for f in now if now[f] != lock['files'][f]]}")


def code_of(ticker: str) -> str | None:
    digits = "".join(ch for ch in str(ticker) if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else None


def months_between(a: str, b: str) -> list[str]:
    y, m = map(int, a.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def sessions(krx: Path) -> list[str]:
    with sqlite3.connect(f"file:{krx}?mode=ro", uri=True) as c:
        return [r[0] for r in c.execute("SELECT DISTINCT trade_date FROM daily_prices WHERE market IN ('KOSPI','KOSDAQ') ORDER BY 1")]


def entry_session(month: str, received_utc: list[str], days: list[str]) -> tuple[str | None, bool]:
    """그달 첫 거래일 09:00 KST 전에 두 명단을 모두 받았으면 그날, 아니면 수신 뒤 첫 거래일 시가(같은 달 안)."""
    month_days = [d for d in days if d[:7] == month]
    if not month_days or len(received_utc) < 2:
        return None, True
    last = max(datetime.fromisoformat(r.replace("Z", "+00:00")).astimezone(KST) for r in received_utc)
    for d in month_days:
        if last <= datetime.combine(datetime.fromisoformat(d).date(), time(9, 0), KST):
            return d, d != month_days[0]
    return None, True


def pick_list(docs: list[dict], source: str, month: str) -> dict | None:
    """그달 기준일(as_of 그달)·해당 출처 중 가장 먼저 받은 것(정정은 나중 수신으로 남지만 확정에는 첫 수신만)."""
    cands = [d for d in docs if d.get("source") == source and str(d.get("as_of", ""))[:7] == month]
    return min(cands, key=lambda d: d["created_at"]) if cands else None


def cmd_freeze(a) -> None:
    check_lock()
    month = a.month
    if not FIRST_MONTH <= month <= LAST_MONTH:
        raise Refused(f"{month}은 관측 기간 밖")
    out = PRIVATE / month / "snapshot.json"
    if out.exists():
        raise Refused(f"{month} 이미 확정됨")
    docs = json.loads(Path(a.lists).read_text(encoding="utf-8"))
    chosen = {k: pick_list(docs, src, month) for k, src in SOURCES.items()}
    if not all(chosen.values()):
        raise Refused(f"{month} 명단 없음: {[k for k, v in chosen.items() if not v]}")
    days = sessions(Path(a.krx)) if a.krx else []
    entry, late = entry_session(month, [chosen["V"]["created_at"], chosen["L"]["created_at"]], days) if days else (None, False)
    sets = {k: sorted({c for c in (code_of(i.get("ticker", "")) for i in v["items"]) if c}) for k, v in chosen.items()}
    prev_path = PRIVATE / months_between("2000-01", month)[-2] / "snapshot.json" if month != FIRST_MONTH else None
    prev = json.loads(prev_path.read_text()) if prev_path and prev_path.exists() else None
    snap = {"month": month, "frozen_at": datetime.now(timezone.utc).isoformat(), "entry_session": entry, "late": late,
            "received": {k: v["created_at"] for k, v in chosen.items()}, "raw_sha256": {k: v.get("raw_sha256") for k, v in chosen.items()},
            "V": sets["V"], "L": sets["L"], "S": sorted(set(sets["V"]) & set(sets["L"]))}
    if prev:   # 연속 편입 기록(선정 미사용)
        snap["holdover"] = {"V": sorted(set(sets["V"]) & set(prev["V"])), "L": sorted(set(sets["L"]) & set(prev["L"])),
                            "S": sorted(set(snap["S"]) & set(prev["S"]))}
    out.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(snap, ensure_ascii=False, sort_keys=True, indent=1).encode()
    out.write_bytes(body)
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else []
    ledger.append({"month": month, "snapshot_sha256": sha(body), "frozen_at": snap["frozen_at"], "entry_session": entry,
                   "late": late, "n_V": len(sets["V"]), "n_L": len(sets["L"]), "n_S": len(snap["S"])})
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n")
    print(f"{month} 확정: 진입 {entry}{' (지연)' if late else ''}, V {len(sets['V'])}·L {len(sets['L'])}·공통 {len(snap['S'])} → 해시를 커밋하세요")


def load_bars(krx: Path, codes: set[str], start: str) -> dict[str, dict[str, tuple[float, float]]]:
    if not codes:
        return {}
    with sqlite3.connect(f"file:{krx}?mode=ro", uri=True) as c:
        q = ",".join("?" * len(codes))
        rows = c.execute(f"SELECT symbol, trade_date, open, close, listed_shares FROM daily_prices WHERE symbol IN ({q}) AND trade_date >= ? "
                         "ORDER BY symbol, trade_date", (*codes, start)).fetchall()
    bars: dict[str, dict[str, tuple[float, float]]] = {}
    for sym, d, o, cl, sh in rows:
        bars.setdefault(sym, {})[d] = (float(o or 0), float(cl or 0), float(sh or 0))
    for sym, series in bars.items():      # 분할·병합 보정: 상장주식수 비율 r과 가격 비율이 1/r ±25%면 이전 가격을 r로 나눔
        days = sorted(series)
        factor = 1.0
        adj = {}
        for i in range(len(days) - 1, -1, -1):
            d = days[i]
            o, cl, sh = series[d]
            adj[d] = (o * factor, cl * factor)
            if i > 0:
                po, pc, psh = series[days[i - 1]]
                if psh > 0 and sh > 0 and pc > 0 and cl > 0:
                    r = sh / psh
                    if (r >= 1.5 or r <= 1 / 1.5) and abs(cl / pc * r - 1) <= 0.25:
                        factor /= r
        bars[sym] = adj
    return bars


def run_arm(targets: list[tuple[str, list[str]]], days: list[str], bars: dict, end_session: str, cost: bool = True) -> tuple[list[float], list[float], list[str]]:
    """targets: [(진입일, 종목들)]. 반환: 기간별 수익(진입 시가→다음 진입 시가), 일별 평가(종가), 문제 기록."""
    value, holdings, period_rets, daily, issues = 1.0, {}, [], [], []
    last_px: dict[str, float] = {}
    for k, (entry, names) in enumerate(targets):
        nxt = targets[k + 1][0] if k + 1 < len(targets) else end_session
        # 진입일 시가로 기존 보유 평가
        def px(sym, d, field):
            b = bars.get(sym, {}).get(d)
            v = (b[0] if field == "open" else b[1]) if b else 0
            if v > 0:
                last_px[sym] = v
                return v
            return last_px.get(sym)
        start_value = sum(sh * (px(s, entry, "open") or 0) for s, sh in holdings.items()) + holdings.get("_cash", 0) if holdings else value
        cur_w = {s: sh * (px(s, entry, "open") or 0) / start_value for s, sh in holdings.items() if s != "_cash"} if holdings else {}
        tradable = [s for s in names if px(s, entry, "open")]
        issues += [f"{entry} {s} 진입 시가 없음(현금)" for s in names if s not in tradable]
        tgt = {s: 1 / len(names) for s in tradable} if names else {}
        if cost:
            c = sum(max(tgt.get(s, 0) - cur_w.get(s, 0), 0) for s in set(tgt) | set(cur_w)) * BUY + \
                sum(max(cur_w.get(s, 0) - tgt.get(s, 0), 0) for s in set(tgt) | set(cur_w)) * SELL
        else:
            c = 0.0
        v0 = start_value * (1 - c)
        holdings = {s: v0 * w / px(s, entry, "open") for s, w in tgt.items()}
        holdings["_cash"] = v0 * (1 - sum(tgt.values()))
        for d in [x for x in days if entry <= x < nxt]:
            daily.append(sum(sh * (px(s, d, "close") or 0) for s, sh in holdings.items() if s != "_cash") + holdings["_cash"])
        end_value = sum(sh * (px(s, nxt, "open") or px(s, nxt, "close") or 0) for s, sh in holdings.items() if s != "_cash") + holdings["_cash"]
        if k + 1 == len(targets) and cost:      # 마지막 청산 비용
            end_value *= 1 - SELL * (1 - holdings["_cash"] / end_value if end_value else 0)
        period_rets.append(end_value / start_value - 1 if k else end_value / value - 1)
        value = end_value
    return period_rets, daily, issues


def newey_west_t(x: list[float], lags: int = NW_LAGS) -> float:
    n = len(x)
    m = sum(x) / n
    e = [v - m for v in x]
    s = sum(v * v for v in e) / n
    for l in range(1, lags + 1):
        g = sum(e[i] * e[i - l] for i in range(l, n)) / n
        s += 2 * (1 - l / (lags + 1)) * g
    return m / math.sqrt(s / n) if s > 0 else math.nan


def evaluate(krx: Path) -> dict:
    snaps = [json.loads(p.read_text()) for p in sorted(PRIVATE.glob("*/snapshot.json"))]
    days = sessions(krx)
    if not snaps:
        return {"months": 0}
    codes = {c for s in snaps for k in ("V", "L") for c in s[k]} | set(ETFS.values())
    bars = load_bars(krx, codes, snaps[0]["entry_session"] or snaps[0]["month"] + "-01")
    frozen = {s["month"]: s for s in snaps}
    last_month = max(frozen)
    out = {"missing_months": [m for m in months_between(FIRST_MONTH, last_month) if not (frozen.get(m) or {}).get("entry_session")]}
    completed = [frozen[m] for m in sorted(frozen) if frozen[m]["entry_session"]]
    # 기간 끝 = 다음 확정 달의 진입 시가. 마지막 달은 다음 달 첫 거래일 시가가 자료에 있어야 완결.
    after = [d for d in days if d[:7] > completed[-1]["month"]] if completed else []
    if after:
        end = after[0]
        out["open_last_period"] = False
    else:
        end = completed[-1]["entry_session"] if completed else None
        completed = completed[:-1]
        out["open_last_period"] = True
    out["months"] = [s["month"] for s in completed]
    if not completed:
        return out
    arms = {"S": "S", "V": "V", "L": "L"}
    res = {}
    for name, key in arms.items():
        r, daily, issues = run_arm([(s["entry_session"], s[key]) for s in completed], days, bars, end)
        res[name] = {"monthly": r, "daily": daily, "issues": issues}
    for name in ("L_hold", "S_hold"):        # 보조 관측(판정 미사용)
        key = name[0]
        tg = [(s["entry_session"], s.get("holdover", {}).get(key, [])) for s in completed]
        r, daily, issues = run_arm(tg, days, bars, end)
        res[name] = {"monthly": r, "daily": daily, "issues": issues}
    for name, sym in ETFS.items():
        r, daily, issues = run_arm([(s["entry_session"], [sym]) for s in completed], days, bars, end, cost=False)
        res[name] = {"monthly": r, "daily": daily, "issues": issues}
    def mdd(d):
        peak, worst = -1, 0.0
        for v in d:
            peak = max(peak, v)
            worst = min(worst, v / peak - 1)
        return worst
    out["arms"] = {k: {"monthly_pct": [round(x * 100, 4) for x in v["monthly"]], "cum_pct": round((math.prod(1 + x for x in v["monthly"]) - 1) * 100, 3),
                       "daily_mdd_pct": round(mdd(v["daily"]) * 100, 3), "issues": v["issues"]} for k, v in res.items()}
    out["n_S"] = [len(s["S"]) for s in completed]
    return out


def judge(ev: dict) -> dict:
    from scipy.stats import t as tdist
    if ev.get("missing_months"):
        return {"verdict": "INVALID", "reason": "명단 결측 월", "missing_months": ev["missing_months"]}
    a = ev.get("arms") or {}
    n = len(a.get("S", {}).get("monthly_pct", []))
    if n < 24:
        return {"verdict": "not_due", "months": n}
    if any(x["issues"] for x in a.values()):
        return {"verdict": "INVALID", "reason": "평가 자료 문제", "issues": {k: v["issues"] for k, v in a.items() if v["issues"]}}
    tests = {}
    for other in ("V", "L"):
        d = [s - o for s, o in zip(a["S"]["monthly_pct"][:24], a[other]["monthly_pct"][:24])]
        t = newey_west_t(d)
        tests[other] = {"mean_pctp": round(sum(d) / 24, 4), "t_nw": round(t, 3), "p_one_sided": float(1 - tdist.cdf(t, 23))}
    if any(v["mean_pctp"] <= 0 for v in tests.values()):
        return {"verdict": "REJECT", "tests": tests}
    ps = sorted(tests.items(), key=lambda kv: kv[1]["p_one_sided"])
    holm = ps[0][1]["p_one_sided"] <= ALPHA / 2 and ps[1][1]["p_one_sided"] <= ALPHA
    market = a["S"]["cum_pct"] >= a["KODEX200"]["cum_pct"] and a["S"]["daily_mdd_pct"] >= a["KODEX200"]["daily_mdd_pct"]
    return {"verdict": "PASS_REVIEW_ONLY" if holm and market else "INCONCLUSIVE", "tests": tests, "holm_pass": holm, "market_pass": market}


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--month", required=True)
    f.add_argument("--lists", required=True)
    f.add_argument("--krx", required=True)
    for name in ("evaluate", "judge"):
        sub.add_parser(name).add_argument("--krx", required=True)
    a = ap.parse_args()
    if a.cmd == "freeze":
        return cmd_freeze(a)
    check_lock()
    ev = evaluate(Path(a.krx))
    if a.cmd == "evaluate":
        RESULT.write_text(json.dumps({**ev, "evaluated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=1) + "\n")
        print(json.dumps({k: v for k, v in ev.items() if k != "arms"}, ensure_ascii=False))
        return
    print(json.dumps(judge(ev), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
