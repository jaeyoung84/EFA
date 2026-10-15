"""매달 재무 갱신용: 주요계정 DB에서 순이익이 빈 보고서를 찾아 fill_gaps.py 입력(gaps.csv)을 만든다.

v2는 TTM 순이익과 자본총계만 쓰므로 '당기순이익' 빈 칸만 대상으로 한다(최근 3개 사업연도).
usage: python make_gaps.py <kr_fund.sqlite3> <out_gaps.csv>
"""
import csv
import sqlite3
import sys

SYN = {"당기순이익(손실)": "당기순이익"}


def main(fund: str, out: str) -> None:
    con = sqlite3.connect(f"file:{fund}?mode=ro", uri=True)
    rows = con.execute("SELECT m.symbol, f.corp_code, f.bsns_year, f.report, f.fs_div, f.account FROM fundamentals f "
                       "JOIN corp_map m USING (corp_code) WHERE f.bsns_year >= CAST(strftime('%Y','now') - 2 AS TEXT)").fetchall()
    reports: dict[tuple, dict[str, set]] = {}
    for sym, corp, year, rep, fs, acc in rows:
        reports.setdefault((sym, corp, year, rep), {}).setdefault(fs, set()).add(SYN.get(acc, acc))
    n = 0
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "corp_code", "bsns_year", "report", "fs", "miss"])
        for (sym, corp, year, rep), by_fs in sorted(reports.items()):
            fs = "CFS" if "CFS" in by_fs else "OFS"          # run.py와 같은 연결 우선
            if "당기순이익" not in by_fs[fs]:
                w.writerow([sym, corp, year, rep, fs, "당기순이익"])
                n += 1
    print(f"gaps {n}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
