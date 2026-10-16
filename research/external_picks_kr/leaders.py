"""InvestingPro '한국 대장주 가치 투자주' 25개월(2024-10~2026-10) 명단 해석 — 로컬 전용(유료 원문, 커밋 안 함).

PDF 텍스트는 한 달치 종목이 한 줄로 이어져 있다: '…74.00%SK텔레콤 (A017670)KOSE 통신 서비스프런티어548.22 K…'.
'(A코드)' 위치로 자르고, 종목명은 앞 조각의 마지막 '%' 뒤, 지표는 study.py와 같은 순서로 읽는다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from study import UNIT  # noqa: E402

CODE = re.compile(r"\(A([0-9A-Z]{6})\)")                      # 보통 페이지: '종목명 (A017670)KOSE …'
BARE = re.compile(r"(?<![0-9A-Za-z(])A(\d{6})(?=[^\d)])")          # 2025-06 페이지: '종목명 A001040산업 …'
NUMK = re.compile(r"(-?[\d,]*\.?\d+)\s?([KMB])(?![A-Za-z])")       # 단위 뒤에 숫자가 바로 붙어도 읽는다
CELL = re.compile(r"(-?[\d,]+(?:\.\d+)?)(%|x)|(?<![\d.])-(?![\d.])")  # 비율 칸, 값 없는 칸은 '-'


def metrics(rest: str) -> dict:
    nums = list(NUMK.finditer(rest))[:4]
    if len(nums) < 4:
        return {}
    adv, mcap, revenue, income = (float(m.group(1).replace(",", "")) * UNIT[m.group(2)] for m in nums)
    cells = [None if m.group(0) == "-" else float(m.group(1).replace(",", "")) for m in CELL.finditer(rest[nums[-1].end():])]
    if len(cells) < 7:
        return {}
    keys = ("opm", "ps", "per", "fcf_yield", "roe", "cash_ratio", "pct_of_52w_high")
    return {"adv_usd": adv, "mcap_krw": mcap, "revenue_usd": revenue, "net_income_usd": income, **dict(zip(keys, cells[:7]))}


def parse(text: str) -> pd.DataFrame:
    rows = []
    for page in text.split("=====PAGE")[1:]:
        m = re.search(r"(\d{4})년\s*(\d{1,2})월", page)
        if not m:
            continue
        month = f"{m.group(1)}-{int(m.group(2)):02d}"
        body = page[page.find("%", page.find("52주")) + 1:] if "52주" in page else page
        body = body.replace("\n", "")
        pattern = CODE if CODE.search(body) else BARE
        parts = pattern.split(body)               # [앞, 코드, 뒤, 코드, 뒤, ...]
        for k in range(1, len(parts), 2):
            name = re.split(r"%|총부채/자본", parts[k - 1])[-1].strip()   # 2026-10 화면 형식은 머리글이 '총부채/자본'으로 끝남
            rest = parts[k + 1]
            if k + 2 < len(parts):                # 다음 종목명 앞까지(마지막 '%'까지)만
                rest = rest[: rest.rfind("%") + 1]
            exch = "KOSDAQ" if rest.startswith("KOSDAQ") else ("KOSPI" if rest.startswith("KOSE") else None)
            sector = re.match(r"(?:KOSE|KOSDAQ)?\s*(.+?)\s*프런티어", rest)
            rows.append({"month": month, "code": parts[k], "name": name or parts[k], "exchange": exch,
                         "sector": sector.group(1).strip() if sector else None, **metrics(rest)})
    return pd.DataFrame(rows).drop_duplicates(["month", "code"])


if __name__ == "__main__":
    df = parse(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(df.groupby("month").size().to_string())
    print("rows", len(df), "unique", df["code"].nunique(), "with metrics", int(df["per"].notna().sum()))
    print(df[df["month"] == "2026-10"][["code", "name", "sector", "mcap_krw", "per", "roe"]].head(25).to_string())
