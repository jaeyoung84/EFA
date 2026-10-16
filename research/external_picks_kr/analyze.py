"""외부(InvestingPro) 한국 추천 명단의 월별 성과 분석 — 로컬 전용, 사이트·배포와 무관.

입력 파일: '## YYYY-MM' 제목 줄 아래에 그달 명단(표·글 무엇이든)을 붙여 넣는다. 종목은 6자리 코드(A000270 형태 포함)로 인식.
계산: 그달 첫 거래일 시가 → 마지막 거래일 종가(진행 중인 달은 최신 종가), 동일 비중, 분할·배당 조정(Yahoo).
비교: KODEX 200(069500), KODEX 200 동일가중(252650), 코스닥150(229200) 같은 기간 같은 방식.
주의: 과거 명단은 '소급 입력'이다. 당시 실제 발표 명단인지 확인할 수 없고, 비용·세금 미반영.

usage: python research/external_picks_kr/analyze.py research/external_picks_kr/propicks.txt
"""
from __future__ import annotations

import math
import re
import statistics
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
BENCH = {"KODEX200": "069500.KS", "KODEX200_EW": "252650.KS", "KOSDAQ150": "229200.KS"}
HEADER = re.compile(r"^##\s*(\d{4})-(\d{2})\s*$", re.M)
CODE = re.compile(r"(?<!\d)A?(\d{6})(?!\d)")


def parse_months(text: str) -> dict[str, list[str]]:
    marks = list(HEADER.finditer(text))
    out = {}
    for i, m in enumerate(marks):
        body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        codes = list(dict.fromkeys(CODE.findall(body)))
        if codes:
            out[f"{m.group(1)}-{m.group(2)}"] = codes
    return out


def download(tickers: list[str], start: date) -> dict[str, pd.DataFrame]:
    df = yf.download(tickers, start=start.isoformat(), end=(date.today() + timedelta(days=1)).isoformat(),
                     auto_adjust=True, progress=False, threads=False, group_by="ticker")
    out = {}
    for t in tickers:
        try:   # group_by='ticker'면 종목 1개여도 (티커, 항목) 2단 열이다
            frame = (df[t] if isinstance(df.columns, pd.MultiIndex) else df)[["Open", "Close"]].dropna()
        except KeyError:
            continue
        if len(frame):
            out[t] = frame
    return out


def resolve(codes: set[str], start: date) -> dict[str, pd.DataFrame]:
    """코드마다 .KS·.KQ를 모두 받아 더 최근까지·더 길게 있는 쪽을 쓴다(Yahoo는 틀린 시장 접미사에도 오래된 값을 줄 때가 있다)."""
    ks = download([c + ".KS" for c in sorted(codes)], start)
    kq = download([c + ".KQ" for c in sorted(codes)], start)
    found = {}
    for c in codes:
        options = [f for f in (ks.get(c + ".KS"), kq.get(c + ".KQ")) if f is not None]
        if options:
            found[c] = max(options, key=lambda f: (f.index.max(), len(f)))
    return found


def month_return(frame: pd.DataFrame, first: pd.Timestamp, last: pd.Timestamp) -> float | None:
    if first not in frame.index or last not in frame.index:
        return None
    return (frame.loc[last, "Close"] / frame.loc[first, "Open"] - 1) * 100


def main(path: str) -> None:
    months = parse_months(Path(path).read_text(encoding="utf-8"))
    if not months:
        sys.exit("'## YYYY-MM' 제목과 종목코드가 있는 명단이 없습니다.")
    start = date.fromisoformat(min(months) + "-01") - timedelta(days=10)
    prices = resolve({c for codes in months.values() for c in codes}, start)
    bench = download(list(BENCH.values()), start)
    calendar = bench[BENCH["KODEX200"]].index
    rows, stock_rows = [], []
    for month, codes in sorted(months.items()):
        days = calendar[(calendar.strftime("%Y-%m") == month)]
        if not len(days):
            continue
        first, last = days[0], days[-1]
        rets = {c: month_return(prices[c], first, last) for c in codes if c in prices}
        rets = {c: r for c, r in rets.items() if r is not None}
        missing = [c for c in codes if c not in rets]
        b = {k: month_return(bench[t], first, last) for k, t in BENCH.items() if t in bench}
        ew = statistics.mean(rets.values()) if rets else math.nan
        rows.append({"month": month, "first": first.date(), "last": last.date(), "n": len(rets), "missing": ",".join(missing),
                     "picks": ew, **b, "excess_200": ew - b["KODEX200"], "excess_ew": ew - b["KODEX200_EW"],
                     "beat_200": sum(r > b["KODEX200"] for r in rets.values()) / len(rets) * 100 if rets else math.nan,
                     "complete": last == calendar[-1] and month == date.today().strftime("%Y-%m")})
        stock_rows += [{"month": month, "code": c, "ret": r} for c, r in rets.items()]
    table = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(table.round(2).to_string(index=False))
    done = table[~table["complete"]] if table["complete"].any() else table
    if len(done) >= 2:
        def chain(col): return ((1 + done[col] / 100).prod() - 1) * 100
        ex = done["excess_ew"]
        t_stat = ex.mean() / (ex.std(ddof=1) / math.sqrt(len(ex))) if ex.std(ddof=1) > 0 else math.nan
        print(f"\n완료된 {len(done)}개월 누적: 명단 {chain('picks'):+.1f}% | KODEX200 {chain('KODEX200'):+.1f}% | 200 동일가중 {chain('KODEX200_EW'):+.1f}% | 코스닥150 {chain('KOSDAQ150'):+.1f}%")
        print(f"월평균 초과(동일가중 대비) {ex.mean():+.2f}%p, 표준편차 {ex.std(ddof=1):.2f}, t={t_stat:.2f}, 초과 양수 달 {(ex > 0).sum()}/{len(ex)}, "
              f"최악 달 {done['excess_ew'].min():+.2f}%p, 최고 달 {done['excess_ew'].max():+.2f}%p")
        print("참고: t가 2 안팎이어야 우연을 넘는다고 보기 시작한다(표본 24개월이면 월 초과가 꽤 커야 함). 소급 입력·비용 미반영.")
    out = Path(path).with_suffix(".result.csv")
    table.to_csv(out, index=False)
    pd.DataFrame(stock_rows).to_csv(Path(path).with_suffix(".stocks.csv"), index=False)
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main(sys.argv[1])
