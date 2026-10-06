"""EINT-030 연간 캘린더 준비 도구: 천문연 응답 형태, KRX 규칙, 생성·갱신·확정·변경 없음, 실행 중인 해 보호, 엔진 등록."""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import kqs_calendar_prepare as prep  # noqa: E402


def kasi_month(items):
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                         "body": {"items": items, "totalCount": 0}}}


class FakeKasi:
    """월별 응답: 여러 건(목록), 한 건(객체), 없음(빈 문자열)을 섞어 돌려준다."""

    def __init__(self, by_month):
        self.by_month, self.urls = by_month, []

    def __call__(self, url):
        self.urls.append(url)
        month = int(url.split("solMonth=")[1][:2])
        rows = self.by_month.get(month, [])
        if not rows:
            return kasi_month("")
        if len(rows) == 1:
            return kasi_month({"item": rows[0]})
        return kasi_month({"item": rows})


def row(day, name, holiday="Y"):
    return {"locdate": int(day.replace("-", "")), "dateName": name, "isHoliday": holiday}


class CalendarPrepareTests(unittest.TestCase):
    def test_kasi_response_shapes_and_errors(self):
        fake = FakeKasi({1: [row("2028-01-01", "1월1일")],
                         2: [row("2028-01-26", "설날"), row("2028-02-05", "기념일", holiday="N")]})
        found = prep.kasi_holidays(2028, "KEY", fetch=fake)
        self.assertEqual(found, {date(2028, 1, 1): "1월1일", date(2028, 1, 26): "설날"})   # isHoliday=N(기념일)은 제외
        self.assertEqual(len(fake.urls), 12)
        self.assertTrue(all("ServiceKey=KEY" in url and "_type=json" in url for url in fake.urls))
        with self.assertRaisesRegex(prep.PrepareError, "KEY가 없습니다|KASI_HOLIDAY_API_KEY"):
            prep.kasi_holidays(2028, "", fetch=fake)
        with self.assertRaisesRegex(prep.PrepareError, "돌려주지 않았습니다"):
            prep.kasi_holidays(2028, "KEY", fetch=FakeKasi({}))
        error = {"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE KEY IS NOT REGISTERED ERROR."}}}
        with self.assertRaisesRegex(prep.PrepareError, "30"):
            prep.kasi_holidays(2028, "KEY", fetch=lambda url: error)

    def test_krx_rules_weekdays_labor_day_year_end_and_source_differences(self):
        official = {date(2027, 1, 1): "1월1일", date(2027, 2, 6): "설날(토)", date(2027, 5, 5): "어린이날",
                    date(2027, 6, 3): "임시공휴일(선거)"}
        library = {date(2027, 1, 1): "신정", date(2027, 5, 5): "어린이날", date(2027, 8, 16): "광복절 대체"}
        closures, notes = prep.krx_closures(2027, official, library)
        self.assertNotIn("2027-02-06", closures)                              # 주말은 넣지 않음
        self.assertIn("2027-06-03", closures)                                 # 공식 임시공휴일은 휴장
        self.assertNotIn("2027-08-16", closures)                              # 라이브러리에만 있으면 넣지 않고 표시
        self.assertEqual(closures["2027-12-31"], "연말 휴장(그해 마지막 평일)")
        kinds = {(n["date"], n["kind"]) for n in notes}
        self.assertIn(("2027-08-16", "only_library"), kinds)
        self.assertIn(("2027-06-03", "only_official"), kinds)
        self.assertNotIn("2027-05-01", closures)                              # 2027-05-01은 토요일
        closures_2026, _ = prep.krx_closures(2026, {date(2026, 1, 1): "1월1일"}, {})
        self.assertEqual(closures_2026["2026-05-01"], "노동절")               # 평일 노동절은 KRX 규칙으로 추가
        self.assertEqual(prep.last_weekday(2028), date(2028, 12, 29))          # 2028-12-31 일요일 → 29일(금)

    def test_existing_2027_provisional_file_is_reproduced_by_the_rules(self):
        """2026-09-30에 손으로 만든 2027 잠정본 16일을 같은 규칙으로 그대로 재현한다(라이브러리 결과를 두 출처로 사용)."""
        try:
            lib = prep.library_holidays(2027)
        except prep.PrepareError:
            self.skipTest("python-holidays 미설치(CI 연간 작업에서는 설치)")
        closures, _ = prep.krx_closures(2027, lib, lib)
        existing = json.loads((prep.CALENDAR_DIR / "krx_equity_2027.json").read_text())["closures"]
        self.assertEqual(sorted(closures), sorted(existing))

    def test_prepare_create_update_confirm_noop_and_current_year_guard(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work)
        cal_dir = work / "calendars"
        cal_dir.mkdir()
        calendar_py = work / "calendar.py"
        calendar_py.write_text('REVIEWED_CALENDARS = {\n    "krx-equity-2026": "krx_equity_2026.json",\n}\n')
        official = {date(2028, 1, 3): "임시", date(2028, 3, 1): "삼일절"}
        today = date(2027, 12, 15)
        created = prep.prepare(2028, official=official, library=official, today=today, apply=True,
                               calendar_dir=cal_dir, calendar_py=calendar_py)
        self.assertEqual((created["action"], created["registered"]), ("create", True))
        document = json.loads((cal_dir / "krx_equity_2028.json").read_text())
        self.assertEqual(document["calendar_id"], "krx-equity-2028")
        self.assertEqual(sorted(document["closures"]), ["2028-01-03", "2028-03-01", "2028-05-01", "2028-12-29"])
        self.assertIn('"krx-equity-2028": "krx_equity_2028.json",', calendar_py.read_text())
        same = prep.prepare(2028, official=official, library=official, today=today, apply=True,
                            calendar_dir=cal_dir, calendar_py=calendar_py)
        self.assertEqual(same["action"], "none")                                # 공식 확인본과 같으면 PR 없음
        changed = {**official, date(2028, 4, 12): "임시공휴일(선거)"}
        updated = prep.prepare(2028, official=changed, library=official, today=today, apply=False,
                               calendar_dir=cal_dir, calendar_py=calendar_py)
        self.assertEqual((updated["action"], updated["added"], updated["removed"]), ("update", ["2028-04-12"], []))
        provisional = dict(document, source={**document["source"], "review_status": "provisional-before-krx"})
        (cal_dir / "krx_equity_2028.json").write_text(json.dumps(provisional))
        confirm = prep.prepare(2028, official=official, library=official, today=today, apply=False,
                               calendar_dir=cal_dir, calendar_py=calendar_py)
        self.assertEqual(confirm["action"], "confirm_provisional")             # 날짜가 같아도 잠정본이면 확정 PR
        self.assertEqual(calendar_py.read_text().count("krx-equity-2028"), 1)   # 이미 등록된 것은 다시 넣지 않는다
        with self.assertRaisesRegex(prep.PrepareError, "실행 중인 해"):
            prep.prepare(2027, official=official, library=official, today=today, apply=True,
                         calendar_dir=cal_dir, calendar_py=calendar_py)


if __name__ == "__main__":
    unittest.main()
