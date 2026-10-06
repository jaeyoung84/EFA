#!/usr/bin/env python3
"""EINT-030: 다음 해 KRX 휴장일 캘린더를 준비한다(매년 12월 15일 GitHub Actions, 사람이 PR을 승인해야 반영).

출처
- 한국천문연구원 특일 정보(공공데이터포털 getRestDeInfo, isHoliday=Y): 공휴일·대체공휴일·임시공휴일 공식 데이터(주 출처).
- python-holidays(KR): 교차 확인용. 두 출처가 다르면 날짜·이름을 보고서와 PR에 남긴다(자동으로 고르지 않는다).
KRX 규칙: 평일 공휴일(대체·임시 포함) + 노동절(5/1, 2026년 법 개정 후 공휴일) + 그해 마지막 평일(연말 휴장).

동작
- 다음 해 파일이 없으면 새 캘린더 파일을 만들고 엔진 등록(REVIEWED_CALENDARS)에 추가한다(--apply).
- 이미 있으면(예: 잠정본) 공식 데이터와 대조해, 휴장일이 다르거나 아직 잠정 표기면 파일을 갱신한다(--apply).
- 실행 중인 해의 파일은 바꾸지 않는다(게시된 추천의 캘린더 SHA가 달라지므로). 다음 해 파일 변경은 그해 1월 첫 게시
  전까지만 쓰이므로 안전하다.
보고서(JSON)는 항상 쓴다. 엔진 개정 기록(EINT)·테스트·CHANGES 초안은 워크플로가 이어서 만든다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALENDAR_DIR = ROOT / "engine/kqs/src/kqs_publication/calendars"
CALENDAR_PY = ROOT / "engine/kqs/src/kqs_publication/calendar.py"
KASI_URL = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
KRX_RULE_URL = "https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp"
KRX_SCHEDULE_URL = "https://global.krx.co.kr/contents/GLB/05/0501/0501060000/GLB0501060000T3.jsp"


class PrepareError(RuntimeError):
    pass


def _items(payload: dict) -> list[dict]:
    """천문연 응답의 items.item은 여러 건이면 목록, 한 건이면 객체, 없으면 빈 문자열이다."""
    header = (payload.get("response") or {}).get("header") or {}
    if str(header.get("resultCode")) not in {"00", "0"}:
        raise PrepareError(f"천문연 API 오류: {header.get('resultCode')} {header.get('resultMsg', '')}".strip())
    items = (((payload.get("response") or {}).get("body") or {}).get("items")) or {}
    item = items.get("item") if isinstance(items, dict) else None
    if not item:
        return []
    return item if isinstance(item, list) else [item]


def kasi_holidays(year: int, service_key: str, fetch=None) -> dict[date, str]:
    """천문연 특일 정보에서 그해 공휴일(isHoliday=Y). 월별로 조회한다."""
    if not service_key:
        raise PrepareError("KASI_HOLIDAY_API_KEY가 없습니다(공공데이터포털 '한국천문연구원_특일 정보' 활용 신청 키).")

    def default_fetch(url: str) -> dict:
        with urllib.request.urlopen(url, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    fetch = fetch or default_fetch
    found: dict[date, str] = {}
    for month in range(1, 13):
        query = urllib.parse.urlencode({"solYear": year, "solMonth": f"{month:02d}", "_type": "json", "numOfRows": 50})
        # 공공데이터포털 키는 이미 인코딩된 값으로 발급되는 경우가 있어 그대로 붙인다.
        for item in _items(fetch(f"{KASI_URL}?{query}&ServiceKey={service_key}")):
            if str(item.get("isHoliday", "")).upper() != "Y":
                continue
            text = str(item.get("locdate", ""))
            if not re.fullmatch(r"\d{8}", text):
                raise PrepareError(f"천문연 응답의 날짜 형식이 이상합니다: {text!r}")
            day = date(int(text[:4]), int(text[4:6]), int(text[6:]))
            if day.year == year:
                found[day] = str(item.get("dateName") or "공휴일").strip()
    if not found:
        raise PrepareError(f"천문연 API가 {year}년 공휴일을 돌려주지 않았습니다(아직 공표 전일 수 있음).")
    return found


def library_holidays(year: int) -> dict[date, str]:
    try:
        import holidays  # type: ignore
    except ImportError as exc:
        raise PrepareError("python-holidays가 설치되어 있지 않습니다(pip install holidays).") from exc
    return {day: str(name) for day, name in holidays.KR(years=year, language="ko").items()}


def last_weekday(year: int) -> date:
    day = date(year, 12, 31)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def krx_closures(year: int, official: dict[date, str], library: dict[date, str]) -> tuple[dict[str, str], list[dict]]:
    """평일 휴장일과 두 출처의 차이. 휴장일은 공식(천문연) 기준에 KRX 고유 규칙을 더한다."""
    closures: dict[date, str] = {day: name for day, name in official.items() if day.weekday() < 5}
    labor_day = date(year, 5, 1)
    notes: list[dict] = []
    if labor_day.weekday() < 5 and labor_day not in closures:
        closures[labor_day] = "노동절"
        notes.append({"date": labor_day.isoformat(), "kind": "krx_rule", "note": "노동절(KRX 휴장 규칙)이 천문연 목록에 없어 추가"})
    year_end = last_weekday(year)
    closures.setdefault(year_end, "연말 휴장(그해 마지막 평일)")
    for day in sorted(set(official) | set(library)):
        if day.weekday() >= 5:
            continue
        if day in official and day not in library:
            notes.append({"date": day.isoformat(), "kind": "only_official", "official": official[day]})
        elif day in library and day not in official:
            notes.append({"date": day.isoformat(), "kind": "only_library", "library": library[day],
                          "note": "공식(천문연) 목록에 없어 휴장일로 넣지 않음 — 검토 필요"})
    return {day.isoformat(): name for day, name in sorted(closures.items())}, notes


def calendar_document(year: int, closures: dict[str, str], notes: list[dict], verified_on: date) -> dict:
    return {
        "schema": "krx-equity-calendar/v1", "calendar_id": f"krx-equity-{year}", "year": year, "timezone": "Asia/Seoul",
        "session_weekdays": [0, 1, 2, 3, 4], "data_available_time_kst": "06:00",
        "source": {
            "rule_url": KRX_RULE_URL, "schedule_url": KRX_SCHEDULE_URL, "verified_at": verified_on.isoformat(),
            "review_status": "official-kasi-holidays-auto-prepared-pending-review",
            "notes": ("Prepared by scripts/kqs_calendar_prepare.py (EINT-030) from the Korea Astronomy and Space Science "
                      "Institute special-day API (getRestDeInfo, isHoliday=Y; primary) cross-checked with python-holidays (KR), "
                      "plus KRX rules: weekday public holidays incl. substitute/temporary holidays, Labor Day (May 1) and the "
                      "last weekday of the year. A reviewer must compare with the official KRX schedule before merging. "
                      f"Source differences: {json.dumps(notes, ensure_ascii=False)}"),
        },
        "closures": closures,
    }


def register(calendar_id: str, filename: str, path: Path = CALENDAR_PY) -> bool:
    text = path.read_text()
    if f'"{calendar_id}"' in text:
        return False
    match = re.search(r"REVIEWED_CALENDARS = \{\n(.*?)\n\}", text, re.S)
    if not match:
        raise PrepareError("calendar.py에서 REVIEWED_CALENDARS를 찾지 못했습니다.")
    entries = match.group(1) + f'\n    "{calendar_id}": "{filename}",'
    path.write_text(text[:match.start(1)] + entries + text[match.end(1):])
    return True


def prepare(year: int, *, official: dict[date, str], library: dict[date, str], today: date, apply: bool,
            calendar_dir: Path = CALENDAR_DIR, calendar_py: Path = CALENDAR_PY) -> dict:
    current_year = today.year
    if year <= current_year:
        raise PrepareError(f"실행 중인 해({current_year}) 이하의 캘린더는 이 도구로 바꾸지 않습니다(게시본 SHA 보호).")
    closures, notes = krx_closures(year, official, library)
    calendar_id, filename = f"krx-equity-{year}", f"krx_equity_{year}.json"
    path = calendar_dir / filename
    existing = json.loads(path.read_text()) if path.is_file() else None
    report = {"year": year, "calendar_id": calendar_id, "closures": closures, "source_differences": notes,
              "existing_file": existing is not None, "action": "none"}
    if existing is not None:
        old = existing.get("closures") or {}
        report["added"] = sorted(set(closures) - set(old))
        report["removed"] = sorted(set(old) - set(closures))
        provisional = str((existing.get("source") or {}).get("review_status", "")).startswith("provisional")
        if not report["added"] and not report["removed"] and not provisional:
            return report
        report["action"] = "update" if (report["added"] or report["removed"]) else "confirm_provisional"
    else:
        report["action"] = "create"
    if apply:
        document = calendar_document(year, closures, notes, today)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        report["registered"] = register(calendar_id, filename, calendar_py)
        report["written"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, help="준비할 해(기본: 내년)")
    parser.add_argument("--apply", action="store_true", help="캘린더 파일을 쓰고 엔진에 등록한다")
    parser.add_argument("--report", type=Path, help="보고서 JSON 경로")
    args = parser.parse_args(argv)
    today = datetime.now(timezone(timedelta(hours=9))).date()
    year = args.year or today.year + 1
    try:
        report = prepare(year, official=kasi_holidays(year, os.getenv("KASI_HOLIDAY_API_KEY", "").strip()),
                         library=library_holidays(year), today=today, apply=args.apply)
    except PrepareError as exc:
        report = {"year": year, "action": "error", "error": str(exc)}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(text + "\n")
    print(text)
    return 1 if report["action"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
