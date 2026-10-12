"""EINT-036: S&P 500 시점별 구성 이력(미국 데이터 경로 2단계, docs/engine/US_DATA_POLICY_v1.md).

위키백과 현재 구성표와 변경표로 2016-01-04 이후 종목별 구성 구간 [시작, 종료)를 되돌려 만든다.
티커는 Alpaca 티커 변경 기록(별칭)으로 현재 티커로 바꿔 같은 회사가 두 번 들어가지 않게 한다.
공식 S&P 자료가 아니므로 월말 구성 종목 수가 495~510을 벗어난 달은 검토 대상으로 표시한다.
수익률·신호·추천은 계산하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

POLICY = "docs/engine/US_DATA_POLICY_v1.md"
START = "2016-01-04"
CONSTITUENTS_PAGE = "List_of_S&P_500_companies"
CHANGES_PAGE = "Historical_components_of_the_S&P_500"
WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "EFA-research/1.0 (https://efa-quant.com; S&P 500 membership history for research)"
COUNT_RANGE = (495, 510)
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class UniverseError(RuntimeError):
    """구성 이력을 만들 수 없을 때."""


def fetch_page(title: str) -> tuple[int, str]:
    """위키백과 문서의 (판 번호, 본문 HTML)."""
    response = requests.get(WIKI_API, timeout=30, headers={"User-Agent": USER_AGENT}, params={
        "action": "parse", "page": title, "prop": "text|revid", "format": "json", "formatversion": 2})
    if response.status_code >= 400:
        raise UniverseError(f"위키백과 응답 오류 {response.status_code}")
    parsed = response.json().get("parse") or {}
    if not parsed.get("revid") or not parsed.get("text"):
        raise UniverseError("위키백과 문서를 읽지 못했습니다.")
    return int(parsed["revid"]), parsed["text"]


def _grid(table) -> list[list[str]]:
    """rowspan/colspan을 펼친 표(머리글 포함). 각주 표시([1])는 지운다."""
    rows: list[list[str]] = []
    pending: dict[int, tuple[int, str]] = {}
    for tr in table.find_all("tr"):
        row: list[str] = []
        cells = iter(tr.find_all(["th", "td"]))
        column = 0
        while True:
            if column in pending:
                left, text = pending[column]
                row.append(text)
                pending[column] = (left - 1, text)
                if left - 1 <= 0:
                    del pending[column]
                column += 1
                continue
            cell = next(cells, None)
            if cell is None:
                break
            for sup in cell.find_all("sup"):
                sup.decompose()
            text = cell.get_text(" ", strip=True)
            span, down = int(cell.get("colspan") or 1), int(cell.get("rowspan") or 1)
            for _ in range(span):
                row.append(text)
                if down > 1:
                    pending[column] = (down - 1, text)
                column += 1
        rows.append(row)
    return rows


def _ticker(value: str) -> str | None:
    value = (value or "").strip().upper().replace(" ", "")
    return value if TICKER.fullmatch(value) else None


def parse_constituents(html: str) -> list[dict]:
    table = BeautifulSoup(html, "html.parser").find("table", id="constituents")
    if table is None:
        raise UniverseError("현재 구성표(id=constituents)가 없습니다.")
    rows = _grid(table)
    header = rows[0]
    index = {name: header.index(name) for name in ("Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "Date added")}
    out = []
    for row in rows[1:]:
        symbol = _ticker(row[index["Symbol"]]) if len(row) > index["Symbol"] else None
        if symbol:
            out.append({"symbol": symbol, "name": row[index["Security"]], "sector": row[index["GICS Sector"]],
                        "sub_industry": row[index["GICS Sub-Industry"]], "date_added": row[index["Date added"]][:10]})
    if len(out) < 450:
        raise UniverseError(f"현재 구성 종목이 너무 적습니다({len(out)}).")
    return out


def _date(value: str) -> str | None:
    value = re.sub(r"\s+", " ", (value or "").strip())
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_changes(html: str) -> list[dict]:
    """변경표 행: 날짜, 편입 티커, 편출 티커, 사유. 두 줄 머리글을 건너뛴다."""
    table = BeautifulSoup(html, "html.parser").find("table", id="changes")
    if table is None:
        raise UniverseError("변경표(id=changes)가 없습니다.")
    out = []
    for row in _grid(table)[2:]:
        if len(row) < 6:
            continue
        day = _date(row[0])
        if not day:
            continue
        added, removed = _ticker(row[1]), _ticker(row[3])
        if added or removed:
            out.append({"date": day, "added": added, "added_name": row[2] if added else "",
                        "removed": removed, "removed_name": row[4] if removed else "", "reason": row[5][:200]})
    if len(out) < 100:
        raise UniverseError(f"변경 행이 너무 적습니다({len(out)}).")
    return out


def resolve(symbol: str | None, on: str, aliases: dict[str, list[tuple[str, str]]]) -> str | None:
    """on 날짜의 티커를 현재 티커로. aliases: 옛 티커 -> [(변경일, 새 티커)] (변경일 당일부터 새 티커)."""
    seen = set()
    while symbol and symbol not in seen:
        seen.add(symbol)
        later = sorted((day, new) for day, new in aliases.get(symbol, []) if day > on)
        if not later:
            return symbol
        on, symbol = later[0]
    return symbol


def reconstruct(current: list[dict], changes: list[dict], aliases: dict | None = None, start: str = START) -> dict:
    """현재 구성에서 변경을 날짜 역순으로 되돌려 종목별 구성 구간을 만든다."""
    aliases = aliases or {}
    members: dict[str, str | None] = {row["symbol"]: None for row in current}
    names = {row["symbol"]: row["name"] for row in current}
    wiki_symbol: dict[str, set] = {row["symbol"]: {row["symbol"]} for row in current}
    intervals, anomalies = [], []
    for change in sorted((c for c in changes if c["date"] > start), key=lambda c: c["date"], reverse=True):
        day = change["date"]
        added = resolve(change["added"], day, aliases)
        removed = resolve(change["removed"], day, aliases)
        if added:
            if added in members:
                intervals.append({"symbol": added, "start": day, "end": members.pop(added),
                                  "wiki_symbols": sorted(wiki_symbol.pop(added, {added}) | {change["added"]})})
            else:
                anomalies.append({"date": day, "kind": "added_not_member", "symbol": change["added"], "resolved": added})
        if removed:
            if removed in members:
                anomalies.append({"date": day, "kind": "removed_already_member", "symbol": change["removed"], "resolved": removed})
            else:
                members[removed] = day
                names.setdefault(removed, change["removed_name"])
                wiki_symbol[removed] = {change["removed"]}
    for symbol, end in members.items():
        intervals.append({"symbol": symbol, "start": start, "end": end, "wiki_symbols": sorted(wiki_symbol.get(symbol, {symbol}))})
    for row in intervals:
        row["name"] = names.get(row["symbol"], "")
    intervals.sort(key=lambda row: (row["symbol"], row["start"]))
    return {"intervals": intervals, "anomalies": anomalies}


def members_on(intervals: list[dict], day: str) -> set[str]:
    return {row["symbol"] for row in intervals if row["start"] <= day and (row["end"] is None or day < row["end"])}


def month_end_report(intervals: list[dict], month_ends: list[str]) -> list[dict]:
    low, high = COUNT_RANGE
    out = []
    for day in month_ends:
        count = len(members_on(intervals, day))
        out.append({"date": day, "members": count, "review": not (low <= count <= high)})
    return out


def table_sha(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def nyse_month_ends(start: str, end: str) -> list[str]:
    """start~end 사이 각 달의 마지막 NYSE 거래일."""
    from .market_calendar import _NYSE
    sessions = [day.date().isoformat() for day in _NYSE.schedule(start_date=start, end_date=end).index]
    last: dict[str, str] = {}
    for day in sessions:
        last[day[:7]] = day
    return [last[key] for key in sorted(last)]


def build_membership(constituents: tuple[int, str], changes: tuple[int, str], aliases: dict, month_ends: list[str]) -> dict:
    current, rows = parse_constituents(constituents[1]), parse_changes(changes[1])
    rebuilt = reconstruct(current, rows, aliases)
    report = month_end_report(rebuilt["intervals"], month_ends)
    return {
        "policy": POLICY, "start": START, "built_at": date.today().isoformat(),
        "sources": {"constituents": {"page": CONSTITUENTS_PAGE, "revid": constituents[0], "rows": len(current), "sha256": table_sha(current)},
                    "changes": {"page": CHANGES_PAGE, "revid": changes[0], "rows": len(rows), "sha256": table_sha(rows)}},
        "current": current, "intervals": rebuilt["intervals"], "anomalies": rebuilt["anomalies"],
        "month_ends": report, "review_months": [row["date"] for row in report if row["review"]],
        "symbols": sorted({row["symbol"] for row in rebuilt["intervals"]}),
    }
