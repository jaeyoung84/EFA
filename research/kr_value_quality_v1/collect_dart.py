"""DART 다중회사 주요계정(fnlttMultiAcnt)을 2015~2026 연도·보고서별로 받아 SQLite에 저장 — 연구용 1회 수집.

운영 서버 백엔드 컨테이너 안에서 실행(DART 키는 환경변수, 출력에 넣지 않음). 운영 DB에는 쓰지 않는다.
대상: corpCode.xml의 상장사(종목코드 있음) + KRX 일봉 DB에 있던 상장폐지 종목 중 회사명이 정확히 같은 법인.
주의: API는 정정 후 최신 보고서 값을 준다(접수번호 날짜 = 정정일). 시점 판단은 법정 기한 규칙으로 한다(사전등록).

usage: python collect_dart.py <krx_names.json> <out.sqlite3> [첫해 마지막해]   (기본 2015 2026)
"""
import io
import json
import os
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

import requests

API = "https://opendart.fss.or.kr/api"
REPORTS = {"11013": "Q1", "11012": "H1", "11014": "Q3", "11011": "FY"}
# v2(2026-10-14): 계정 이름 필터를 없앤다(카카오 '영업수익', 반기·분기 '반기순이익' 등 변형). 이름 매핑은 분석 단계에서.


def main(names_path: str, out: str, first: int = 2015, last: int = 2026) -> None:
    key = os.environ["DART_API_KEY"]
    z = zipfile.ZipFile(io.BytesIO(requests.get(f"{API}/corpCode.xml", params={"crtfc_key": key}, timeout=60).content))
    corps = [(c.findtext("corp_code"), (c.findtext("stock_code") or "").strip(), (c.findtext("corp_name") or "").strip())
             for c in ET.fromstring(z.read(z.namelist()[0])).iter("list")]
    krx = json.load(open(names_path))                       # [[symbol, name], ...] 2016~ KRX 일봉의 모든 주식
    by_stock = {s: c for c, s, n in corps if s}
    unlisted_by_name = {}
    for c, s, n in corps:
        if not s:
            unlisted_by_name.setdefault(n, []).append(c)
    mapping = {}
    for symbol, name in krx:
        if symbol in by_stock:
            mapping[symbol] = (by_stock[symbol], "stock_code")
        elif len(unlisted_by_name.get(name, [])) == 1:
            mapping[symbol] = (unlisted_by_name[name][0], "name_match")
    db = sqlite3.connect(out)
    db.executescript("""CREATE TABLE IF NOT EXISTS corp_map (symbol TEXT PRIMARY KEY, corp_code TEXT, how TEXT);
        CREATE TABLE IF NOT EXISTS fundamentals (corp_code TEXT, stock_code TEXT, bsns_year TEXT, report TEXT, fs_div TEXT,
            account TEXT, amount REAL, period_end TEXT, rcept_no TEXT, PRIMARY KEY (corp_code, bsns_year, report, fs_div, account));
        CREATE TABLE IF NOT EXISTS log (bsns_year TEXT, report TEXT, batch INTEGER, status TEXT, rows INTEGER);""")
    db.executemany("INSERT OR REPLACE INTO corp_map VALUES (?,?,?)", [(s, c, h) for s, (c, h) in mapping.items()])
    db.commit()
    corp_codes = sorted({c for c, _ in mapping.values()})
    print(f"KRX 주식 {len(krx)}개 중 법인 연결 {len(mapping)}개 (종목코드 {sum(h == 'stock_code' for _, h in mapping.values())}, "
          f"회사명 {sum(h == 'name_match' for _, h in mapping.values())}), 법인 {len(corp_codes)}개", flush=True)
    calls = 0

    def fetch(batch, year, report):
        nonlocal calls
        for attempt in range(3):
            try:
                calls += 1
                time.sleep(0.2)
                return requests.get(f"{API}/fnlttMultiAcnt.json", timeout=30, params={
                    "crtfc_key": key, "corp_code": ",".join(batch), "bsns_year": str(year), "reprt_code": report}).json()
            except Exception as exc:  # 일시 오류는 잠시 뒤 재시도
                time.sleep(3)
        return {"status": "ERR"}

    def collect(batch, year, report, depth=0):
        """묶음이 '013(데이터 없음)'이면 10개, 1개 단위로 쪼개 다시 조회한다(한 회사 때문에 묶음 전체가 비는 경우)."""
        r = fetch(batch, year, report)
        # '013'은 10개 단위까지만 쪼갠다(분기 전체가 비어 있는 해에 1개 단위 호출이 일일 한도 20,000회를 다 쓰는 것 방지)
        if r.get("status") == "000" or len(batch) == 1 or (r.get("status") == "013" and depth >= 1):
            return r.get("list") or [], [(r.get("status"), len(batch))]
        size = 10 if len(batch) > 10 else 1
        rows, statuses = [], []
        for i in range(0, len(batch), size):
            got, st = collect(batch[i:i + size], year, report, depth + 1)
            rows += got
            statuses += st
        return rows, statuses

    for year in range(first, last + 1):
        for report in REPORTS:
            total, empty = 0, 0
            for i in range(0, len(corp_codes), 100):
                got, statuses = collect(corp_codes[i:i + 100], year, report)
                rows = []
                for x in got:
                    try:
                        amount = float(str(x.get("thstrm_amount") or "").replace(",", ""))
                    except ValueError:
                        continue
                    rows.append((x["corp_code"], x.get("stock_code"), str(year), REPORTS[report], x.get("fs_div"),
                                 x.get("account_nm", "").strip(), amount, x.get("thstrm_dt"), x.get("rcept_no")))
                db.executemany("INSERT OR REPLACE INTO fundamentals VALUES (?,?,?,?,?,?,?,?,?)", rows)
                db.execute("INSERT INTO log VALUES (?,?,?,?,?)", (str(year), report, i // 100, ",".join(sorted({s for s, _ in statuses})), len(rows)))
                db.commit()
                total += len(rows)
                empty += sum(n for s, n in statuses if s != "000")
            print(f"{year} {REPORTS[report]}: {total} rows, 자료 없는 법인 {empty} (calls {calls})", flush=True)
    print("done", calls, "calls")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], *map(int, sys.argv[3:5]))
