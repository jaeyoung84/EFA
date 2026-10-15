"""DART 단일회사 전체 재무제표(fnlttSinglAcntAll)로 주요계정 API의 빈 손익 항목(매출액·영업이익·당기순이익)만 채운다.

주요계정 API는 일부 회사의 손익 줄을 빼고 준다(예: 매출 대신 '영업수익'을 쓰는 회사, 연결 순이익 줄 누락).
대상: 유니버스 후보(형성 기간 중 시총 ≥ 5,000억, 금융업 양식 제외)에서 손익 항목이 빈 보고서만(gaps.csv).
원칙: 원래 자료와 같은 재무제표 구분(CFS/OFS), 손익계산서(IS) 우선·없으면 포괄손익계산서(CIS), 표준 계정 ID 우선·
없으면 정규화한 계정명 일치(norm). 기존 값은 덮어쓰지 않는다(run.py가 빈 칸에만 쓴다). 매칭된 계정 ID·이름을 남긴다.
금액: thstrm_amount(분기·반기 보고서는 3개월 값, 사업보고서는 연간 — 주요계정 API와 같은 기준).

usage: python fill_gaps.py <gaps.csv> <out.sqlite3>   (서버 백엔드 컨테이너 안에서 실행, DART 키는 환경변수)
"""
import csv
import os
import re
import sqlite3
import sys
import time

import requests

API = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
CODE = {"Q1": "11013", "H1": "11012", "Q3": "11014", "FY": "11011"}
IDS = {"매출액": ("ifrs-full_Revenue", "ifrs_Revenue"),
       "영업이익": ("dart_OperatingIncomeLoss",),
       "당기순이익": ("ifrs-full_ProfitLoss", "ifrs_ProfitLoss")}
# 계정명 비교는 norm() 뒤: 공백·번호 머리('VIII.')·'연결'·'(손실)'·'(이익)'을 지운다. 예) '연결분기순이익(손실)' → '분기순이익'
NAMES = {"매출액": ("매출액", "매출", "영업수익", "수익(매출액)", "매출액(영업수익)", "영업수익(매출액)"),
         "영업이익": ("영업이익", "영업손익"),
         "당기순이익": ("당기순이익", "분기순이익", "반기순이익", "당기순손익", "분기순손익", "반기순손익")}
PREFIX = re.compile(r"^(?:[IVX]+|\d+)\.")


def norm(name):
    n = PREFIX.sub("", (name or "").replace(" ", ""))
    return n.replace("연결", "").replace("(손실)", "").replace("(이익)", "")


def amount(x):
    try:
        return float(str(x.get("thstrm_amount") or "").replace(",", ""))
    except ValueError:
        return None


def match(items, acct):
    for sj in ("IS", "CIS"):
        rows = [x for x in items if x.get("sj_div") == sj]
        for how, ok in (("id", lambda x: (x.get("account_id") or "") in IDS[acct]),
                        ("name", lambda x: norm(x.get("account_nm")) in NAMES[acct])):
            for x in rows:
                if ok(x) and amount(x) is not None:
                    return x, f"{sj}:{how}"
    return None, None


def main(gaps_path, out):
    key = os.environ["DART_API_KEY"]
    db = sqlite3.connect(out)
    db.execute("""CREATE TABLE IF NOT EXISTS fill (corp_code TEXT, bsns_year TEXT, report TEXT, fs_div TEXT, account TEXT, amount REAL,
                  account_id TEXT, account_nm TEXT, how TEXT, PRIMARY KEY (corp_code, bsns_year, report, fs_div, account))""")
    db.execute("CREATE TABLE IF NOT EXISTS calls (corp_code TEXT, bsns_year TEXT, report TEXT, fs_div TEXT, status TEXT, n_items INTEGER)")
    gaps = list(csv.DictReader(open(gaps_path)))
    filled = calls = 0
    for i, g in enumerate(gaps):
        r = {"status": "ERR"}
        for _ in range(3):
            try:
                calls += 1
                time.sleep(0.2)
                r = requests.get(API, timeout=30, params={"crtfc_key": key, "corp_code": g["corp_code"], "bsns_year": g["bsns_year"],
                                                          "reprt_code": CODE[g["report"]], "fs_div": g["fs"]}).json()
                break
            except Exception:  # 일시 오류는 잠시 뒤 재시도
                time.sleep(3)
        items = r.get("list") or []
        db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?)", (g["corp_code"], g["bsns_year"], g["report"], g["fs"], r.get("status"), len(items)))
        for acct in [a for a in g["miss"].split(",") if a]:
            x, how = match(items, acct)
            if x is not None:
                db.execute("INSERT OR REPLACE INTO fill VALUES (?,?,?,?,?,?,?,?,?)",
                           (g["corp_code"], g["bsns_year"], g["report"], g["fs"], acct, amount(x), x.get("account_id"), x.get("account_nm"), how))
                filled += 1
        db.commit()
        if r.get("status") == "020":     # 일일 한도 초과
            print("limit reached at", i, flush=True)
            break
        if i % 100 == 0:
            print(f"{i}/{len(gaps)} filled {filled} calls {calls}", flush=True)
    print("done", len(gaps), "gaps, filled", filled, "calls", calls, flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
