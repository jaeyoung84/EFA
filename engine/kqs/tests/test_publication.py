"""Publication regressions use synthetic runs and private temporary SQLite only."""

import copy
import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from test_runtime import create_db, synthetic_inputs
from kqs_runtime.recommendations import generate_recommendations
from kqs_publication.calendar import calendar_for_session
from kqs_publication.contract import KqsContractError, canonical_payload_digest, validate_recommendation_payload
from kqs_publication.publisher import build_payload
from kqs_publication.store import publish as raw_publish, read_latest
from kqs_publication.writer import WriterBusy, WriterLock


def publish(path, runtime, **kwargs):
    """Tests publish as the single writer: hold the lock for exactly this publication DB."""
    with WriterLock(path) as lock:
        return raw_publish(path, runtime, writer_lock=lock, **kwargs)


def run(kind="swing", offset=0):
    history, benchmarks, as_of = synthetic_inputs(kind)
    if offset:
        history["Date"] += timedelta(days=offset)
        benchmarks = {key: value.set_axis(value.index + timedelta(days=offset)) for key, value in benchmarks.items()}
        as_of = history["Date"].max().date().isoformat()
    return generate_recommendations(history, benchmarks, as_of=as_of)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "recommendations.sqlite3"
        self.runtime = run()
        self.generated = datetime.combine(date.fromisoformat(self.runtime["as_of"]) + timedelta(days=1),
                                          datetime.min.time(), tzinfo=ZoneInfo("Asia/Seoul")).replace(hour=7)
        self.expiry = self.generated + timedelta(hours=4)

    def publish(self, **kwargs):
        params = dict(expected_as_of=self.runtime["as_of"], generated_at=self.generated, expires_at=self.expiry)
        params.update(kwargs)
        return publish(self.path, self.runtime, **params)

    def test_repeated_gets_preserve_generated_expiry_and_database_bytes(self):
        payload = self.publish()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        first = read_latest(self.path, now=self.generated + timedelta(minutes=1))
        second = read_latest(self.path, now=self.generated + timedelta(hours=3))
        self.assertEqual(payload, first)
        self.assertEqual(first, second)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(sorted(path.name for path in Path(self.directory.name).iterdir()),
                         sorted([self.path.name, self.path.name + ".writer.lock"]))

    def test_rerun_cannot_renew_first_expiry(self):
        payload = self.publish()
        later = self.publish(generated_at=self.generated + timedelta(hours=5), expires_at=self.generated + timedelta(hours=9))
        self.assertEqual(payload, later)
        with self.assertRaisesRegex(KqsContractError, "만료"):
            read_latest(self.path, now=self.generated + timedelta(hours=6))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM kqs_recommendation_snapshots").fetchone()[0], 1)

    def test_concurrent_writers_are_exclusive_and_keep_the_first_snapshot(self):
        def attempt(_):
            try:
                return self.publish()
            except WriterBusy:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            outputs = [output for output in pool.map(attempt, range(8)) if output is not None]
        self.assertTrue(outputs)
        self.assertTrue(all(output == outputs[0] for output in outputs))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM kqs_recommendation_snapshots").fetchone()[0], 1)

    def test_second_writer_is_rejected_while_first_holds_the_lock(self):
        with WriterLock(self.path):
            with self.assertRaises(WriterBusy):
                WriterLock(self.path).acquire()
        with WriterLock(self.path) as again:
            self.assertTrue(again.held)

    def test_publication_requires_the_writer_lock_for_the_same_database(self):
        with self.assertRaisesRegex(KqsContractError, "writer"):
            raw_publish(self.path, self.runtime, expected_as_of=self.runtime["as_of"],
                        generated_at=self.generated, expires_at=self.expiry, writer_lock=None)
        other = Path(self.directory.name) / "other.sqlite3"
        with WriterLock(other) as lock, self.assertRaisesRegex(KqsContractError, "writer"):
            raw_publish(self.path, self.runtime, expected_as_of=self.runtime["as_of"],
                        generated_at=self.generated, expires_at=self.expiry, writer_lock=lock)
        unheld = WriterLock(self.path)
        with self.assertRaisesRegex(KqsContractError, "writer"):
            raw_publish(self.path, self.runtime, expected_as_of=self.runtime["as_of"],
                        generated_at=self.generated, expires_at=self.expiry, writer_lock=unheld)
        self.assertFalse(self.path.exists())

    def test_latest_expired_run_does_not_fall_back_to_older_valid_run(self):
        # EINT-028: 다음 세션 자료 공개(당일 21:30) 전까지가 상한이라 07:00 생성 + 14시간(21:00)으로 둔다.
        self.publish(expires_at=self.generated + timedelta(hours=14))
        newer = run("flat", offset=1)
        new_time = self.generated + timedelta(hours=1)
        # New data day requires generation in that KST session or later.
        new_time += timedelta(days=1)
        publish(self.path, newer, expected_as_of=newer["as_of"], generated_at=new_time,
                expires_at=new_time + timedelta(hours=1))
        with self.assertRaisesRegex(KqsContractError, "만료"):
            read_latest(self.path, now=new_time + timedelta(hours=2))

    def test_same_session_new_run_expired_does_not_fall_back(self):
        self.publish(expires_at=self.generated + timedelta(hours=14))
        alternative = run("flat")
        publish(self.path, alternative, expected_as_of=alternative["as_of"],
                generated_at=self.generated + timedelta(minutes=1),
                expires_at=self.generated + timedelta(hours=1))
        with self.assertRaisesRegex(KqsContractError, "만료"):
            read_latest(self.path, now=self.generated + timedelta(hours=2))

    def test_older_data_cannot_roll_back_current_publication(self):
        self.publish()
        older = run(offset=-1)
        older_generated = datetime.combine(date.fromisoformat(older["as_of"]) + timedelta(days=1),
                                           datetime.min.time(), tzinfo=ZoneInfo("Asia/Seoul")).replace(hour=7)
        with self.assertRaisesRegex(KqsContractError, "되돌릴"):
            publish(self.path, older, expected_as_of=older["as_of"], generated_at=older_generated,
                    expires_at=older_generated + timedelta(hours=4))

    def test_same_run_different_content_is_rejected(self):
        self.publish()
        tampered = copy.deepcopy(self.runtime)
        tampered["recommendations"][0]["features"]["rsi14"] += 1
        with self.assertRaisesRegex(KqsContractError, "다른 추천"):
            publish(self.path, tampered, expected_as_of=tampered["as_of"], generated_at=self.generated,
                    expires_at=self.expiry)

    def test_missing_store_is_not_created_by_read(self):
        with self.assertRaisesRegex(KqsContractError, "스냅샷"):
            read_latest(self.path)
        self.assertFalse(self.path.exists())

    def test_wrong_database_is_not_modified(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE daily_prices (close REAL)")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(KqsContractError, "다른 DB"):
            self.publish()
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_rows_cannot_be_updated_or_deleted(self):
        self.publish()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for statement in ("UPDATE kqs_recommendation_snapshots SET expires_at='2099-01-01'",
                              "DELETE FROM kqs_recommendation_snapshots"):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    connection.execute(statement)

    def test_corrupt_payload_is_detected_without_rewriting(self):
        self.publish()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DROP TRIGGER kqs_no_update")
            raw = connection.execute("SELECT payload_json FROM kqs_recommendation_snapshots").fetchone()[0]
            payload = json.loads(raw)
            payload["signals"][0]["name"] = "tampered"
            connection.execute("UPDATE kqs_recommendation_snapshots SET payload_json=?", (json.dumps(payload),))
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(KqsContractError, "무결성"):
            read_latest(self.path, now=self.generated)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_native_contract_keeps_recommendation_features_and_publication_ids(self):
        payload = self.publish()
        safe = validate_recommendation_payload(payload, now=self.generated)
        self.assertEqual(safe["snapshot_id"], "kqs-" + self.runtime["run_id"])
        self.assertEqual(safe["engine"]["package_version"], "0.7.0")
        self.assertIsNone(safe["engine"]["dirty"])
        self.assertEqual(safe["signals"][0]["features"], self.runtime["recommendations"][0]["features"])
        self.assertNotIn("audit", safe)
        self.assertNotIn("runtime_json", safe)

    def test_zero_recommendations_are_a_valid_publication(self):
        empty = run("flat")
        payload = publish(self.path, empty, expected_as_of=empty["as_of"], generated_at=self.generated,
                          expires_at=self.expiry)
        safe = validate_recommendation_payload(payload, now=self.generated)
        self.assertEqual(safe["status"], "no_recommendations")
        self.assertEqual(safe["signals"], [])

    def test_explicit_expected_session_is_required(self):
        with self.assertRaisesRegex(KqsContractError, "기준일"):
            self.publish(expected_as_of="2026-01-01")
        self.assertFalse(self.path.exists())

    def test_future_data_or_more_than_seven_day_old_data_cannot_be_reactivated(self):
        for time in (self.generated - timedelta(days=2), self.generated + timedelta(days=8)):
            with self.assertRaises(KqsContractError):
                self.publish(generated_at=time, expires_at=time + timedelta(hours=1))
        self.assertFalse(self.path.exists())

    def test_expiry_is_explicit_timezone_aware_and_at_most_24_hours(self):
        for expiry in (self.generated, self.generated + timedelta(hours=25), self.expiry.replace(tzinfo=None)):
            with self.assertRaises(KqsContractError):
                self.publish(expires_at=expiry)
        self.assertFalse(self.path.exists())

    def test_personalized_exclusion_is_not_published_as_global_recommendations(self):
        self.runtime["excluded_symbols"] = ["005930"]
        with self.assertRaisesRegex(KqsContractError, "제외 문맥"):
            self.publish()

    def test_run_or_config_fingerprint_mismatch_is_rejected(self):
        self.runtime["config"]["max_picks"] = 4
        with self.assertRaisesRegex(KqsContractError, "지문"):
            self.publish()

    def test_native_component_digest_cannot_ignore_changed_files(self):
        payload = self.publish()
        payload["engine"]["component_sha256"]["kqs_runtime/cli.py"] = "0" * 64
        payload["integrity"]["payload_sha256"] = canonical_payload_digest(payload)
        with self.assertRaisesRegex(KqsContractError, "코드 지문"):
            validate_recommendation_payload(payload, now=self.generated)

    def test_future_generation_and_boolean_duplicate_ranks_fail_closed(self):
        payload = self.publish()
        with self.assertRaisesRegex(KqsContractError, "미래"):
            validate_recommendation_payload(payload, now=self.generated - timedelta(hours=1))
        for invalid in (True, 2):
            changed = copy.deepcopy(payload)
            changed["signals"][0]["selection_rank"] = invalid
            changed["integrity"]["payload_sha256"] = canonical_payload_digest(changed)
            with self.assertRaises(KqsContractError):
                validate_recommendation_payload(changed, now=self.generated)

    def test_conflicting_publication_package_is_rejected(self):
        import kqs_publication.publisher
        from kqs_runtime.provenance import code_identity
        with patch.object(kqs_publication.publisher, "__file__", "/tmp/legacy/kqs_publication/publisher.py"):
            with self.assertRaisesRegex(ValueError, "Conflicting legacy"):
                code_identity()

    def test_legacy_dirty_guard_still_applies(self):
        payload = self.publish()
        payload["engine"].pop("trust_mode")
        payload["engine"]["dirty"] = True
        payload["integrity"]["payload_sha256"] = canonical_payload_digest(payload)
        with self.assertRaisesRegex(KqsContractError, "통합 엔진"):
            validate_recommendation_payload(payload, now=self.generated)
        legacy = {key: payload[key] for key in ("generated_at", "expires_at", "engine")}
        legacy.update(schema_version="kqs-recommendations/v1", signals=[], integrity={})
        legacy["integrity"]["payload_sha256"] = canonical_payload_digest(legacy)
        with self.assertRaisesRegex(KqsContractError, "dirty"):
            validate_recommendation_payload(legacy, now=self.generated)

    def test_publication_package_needs_no_site_packages_or_pandas(self):
        src = Path(__file__).resolve().parents[1] / "src"
        result = subprocess.run([sys.executable, "-S", "-c",
                                 f"import sys; sys.path.insert(0, {str(src)!r}); import kqs_publication.store; "
                                 "assert 'pandas' not in sys.modules; assert 'kqs_runtime' not in sys.modules"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_krx_chuseok_closures_and_reopening_are_reviewed_not_weekday_guessing(self):
        calendar = calendar_for_session(date(2026, 9, 23))
        self.assertTrue(calendar.is_session(date(2026, 9, 23)))
        self.assertFalse(calendar.is_session(date(2026, 9, 24)))
        self.assertFalse(calendar.is_session(date(2026, 9, 25)))
        self.assertEqual(calendar.next_session(date(2026, 9, 23)), date(2026, 9, 28))

    def test_krx_2027_calendar_uses_reviewed_closures_and_2026_law(self):
        calendar = calendar_for_session(date(2027, 2, 5))
        self.assertEqual(calendar.calendar_id, "krx-equity-2027")
        # 설날(2/7 일) 연휴 + 대체휴일(2/9): 2/5 금 다음 세션은 2/10 수
        self.assertEqual(calendar.next_session(date(2027, 2, 5)), date(2027, 2, 10))
        # 2026 개정법: 노동절(5/1 토)·제헌절(7/17 토) 대체휴일은 휴장
        for closed in (date(2027, 5, 3), date(2027, 7, 19), date(2027, 12, 27), date(2027, 12, 31)):
            self.assertFalse(calendar.is_session(closed), closed)
        self.assertEqual(calendar.next_session(date(2027, 9, 13)), date(2027, 9, 17))

    def test_krx_year_boundary_crosses_only_into_reviewed_adjacent_calendars(self):
        cal_2026 = calendar_for_session(date(2026, 12, 30))
        # 12/31 연말 휴장, 1/1 신정 → 다음 세션은 2027-01-04
        self.assertEqual(cal_2026.next_session(date(2026, 12, 30)), date(2027, 1, 4))
        kst = ZoneInfo("Asia/Seoul")
        # 2027-01-04 아침에는 1/4 자료가 아직 없으므로 가장 최신 공개 세션은 전년 12/30
        self.assertEqual(calendar_for_session(date(2027, 1, 4)).latest_available_session(
            datetime(2027, 1, 4, 8, 30, tzinfo=kst)), date(2026, 12, 30))
        self.assertEqual(calendar_for_session(date(2027, 1, 5)).latest_available_session(
            datetime(2027, 1, 5, 8, 30, tzinfo=kst)), date(2027, 1, 4))
        # 검토되지 않은 2028년으로는 넘어가지 않는다
        with self.assertRaisesRegex(Exception, "2028"):
            calendar_for_session(date(2027, 12, 30)).next_session(date(2027, 12, 30))
        with self.assertRaises(Exception):
            calendar_for_session(date(2028, 1, 3))

    def test_year_end_session_publishes_across_the_boundary_with_capped_expiry(self):
        offset = (date(2026, 12, 30) - date.fromisoformat(self.runtime["as_of"])).days
        runtime = run(offset=offset)
        kst = ZoneInfo("Asia/Seoul")
        generated = datetime(2027, 1, 4, 8, 30, tzinfo=kst)
        # EINT-029(v3): 다음 세션 2027-01-04 자료는 그날 22:00 KST에 공개 기대 → 만료 상한도 그 시각이다.
        payload = publish(self.path, runtime, expected_as_of="2026-12-30", generated_at=generated,
                          expires_at=datetime(2027, 1, 4, 21, 59, tzinfo=kst))
        self.assertEqual(payload["engine"]["data_as_of"], "2026-12-30")
        self.assertEqual(payload["publication"]["calendar"]["calendar_id"], "krx-equity-2026")
        with self.assertRaisesRegex(KqsContractError, "next expected KRX"):
            publish(Path(self.directory.name) / "late.sqlite3", runtime, expected_as_of="2026-12-30",
                    generated_at=generated, expires_at=datetime(2027, 1, 4, 22, 1, tzinfo=kst))

    def test_eint028_same_day_availability_and_v1_payloads_still_validate(self):
        """v2: 세션 자료는 같은 날 21:30 KST부터 공개 기대. v1(다음 날 06:00)로 이미 게시된 metadata는 계속 검증된다."""
        from kqs_publication.calendar import (POLICY_V1, POLICY_V2, CalendarError, calendar_for_session, load_calendar,
                                              validate_publication_calendar)
        kst = ZoneInfo("Asia/Seoul")
        v2 = calendar_for_session(date(2026, 10, 6), policy=POLICY_V2)   # EINT-029부터 기본은 v3
        self.assertEqual(v2.policy, POLICY_V2)
        self.assertEqual(v2.latest_available_session(datetime(2026, 10, 6, 21, 29, tzinfo=kst)), date(2026, 10, 2))   # 10/5 휴장
        self.assertEqual(v2.latest_available_session(datetime(2026, 10, 6, 21, 31, tzinfo=kst)), date(2026, 10, 6))
        self.assertEqual(v2.public_metadata()["data_available_time_kst"], "21:30")
        self.assertEqual(v2.public_metadata()["data_available_day"], "same_session_day")
        # 금요일 22:00 게시는 휴장 다음 세션(화) 21:30이 아니라 24시간 상한에 먼저 걸린다.
        self.assertEqual(v2.publication_window(date(2026, 10, 2), datetime(2026, 10, 2, 22, 0, tzinfo=kst)),
                         datetime(2026, 10, 6, 21, 30, tzinfo=kst))
        v1 = load_calendar("krx-equity-2026", policy=POLICY_V1)
        self.assertEqual(v1.latest_available_session(datetime(2026, 10, 7, 5, 59, tzinfo=kst)), date(2026, 10, 2))
        v1_meta = v1.public_metadata()
        self.assertNotIn("data_available_day", v1_meta)
        self.assertEqual(validate_publication_calendar(
            v1_meta, data_as_of=date(2026, 10, 2), generated_at=datetime(2026, 10, 6, 9, 28, tzinfo=kst),
            expires_at=datetime(2026, 10, 7, 6, 0, tzinfo=kst)), v1_meta)            # 실제 10/6 복구 게시와 같은 형태
        with self.assertRaises(CalendarError):                                       # v1 metadata를 v2 시각으로 위조
            validate_publication_calendar({**v1_meta, "data_available_time_kst": "21:30"}, data_as_of=date(2026, 10, 2),
                                          generated_at=datetime(2026, 10, 6, 9, 28, tzinfo=kst),
                                          expires_at=datetime(2026, 10, 7, 6, 0, tzinfo=kst))
        with self.assertRaises(CalendarError):
            load_calendar("krx-equity-2026", policy="krx-session-expiry/v9")
        with self.assertRaises(CalendarError):
            validate_publication_calendar({**v1_meta, "policy": "krx-session-expiry/v9"}, data_as_of=date(2026, 10, 2),
                                          generated_at=datetime(2026, 10, 6, 9, 28, tzinfo=kst),
                                          expires_at=datetime(2026, 10, 7, 6, 0, tzinfo=kst))

    def test_eint029_friday_evening_publication_lasts_until_the_next_session_evening(self):
        """v3: 금요일 22:00 게시는 24시간 상한 없이 다음 거래일(월) 22:00까지 유효. v2 metadata는 여전히 24시간 상한."""
        from kqs_publication.calendar import (POLICY_V2, POLICY_V3, CalendarError, calendar_for_session,
                                              validate_publication_calendar)
        kst = ZoneInfo("Asia/Seoul")
        friday, generated = date(2026, 10, 16), datetime(2026, 10, 16, 22, 3, tzinfo=kst)
        v3 = calendar_for_session(friday)
        self.assertEqual(v3.policy, POLICY_V3)
        self.assertEqual(v3.latest_available_session(datetime(2026, 10, 16, 21, 59, tzinfo=kst)), date(2026, 10, 15))
        self.assertEqual(v3.latest_available_session(datetime(2026, 10, 16, 22, 0, tzinfo=kst)), friday)
        monday_evening = datetime(2026, 10, 19, 22, 0, tzinfo=kst)
        self.assertEqual(v3.publication_window(friday, generated), monday_evening)
        meta = v3.public_metadata()
        self.assertEqual((meta["data_available_time_kst"], meta["expiry"]), ("22:00", "until_next_session_availability"))
        self.assertEqual(validate_publication_calendar(meta, data_as_of=friday, generated_at=generated,
                                                       expires_at=monday_evening), meta)            # 약 72시간
        with self.assertRaises(CalendarError):                                                      # 월요일 22:00을 넘을 수 없다
            validate_publication_calendar(meta, data_as_of=friday, generated_at=generated,
                                          expires_at=monday_evening + timedelta(minutes=1))
        v2_meta = calendar_for_session(friday, policy=POLICY_V2).public_metadata()
        with self.assertRaisesRegex(CalendarError, "ceiling"):                                      # v2는 24시간 상한 유지
            validate_publication_calendar(v2_meta, data_as_of=friday, generated_at=generated,
                                          expires_at=datetime(2026, 10, 19, 21, 30, tzinfo=kst))
        with self.assertRaises(CalendarError):                                                      # v3 metadata 위조(만료 표기 제거)
            validate_publication_calendar({k: v for k, v in meta.items() if k != "expiry"}, data_as_of=friday,
                                          generated_at=generated, expires_at=monday_evening)
        # 추석 같은 긴 휴장도 다음 세션 22:00까지(2026-09-23 → 09-28), 안전 상한 10일 안이다.
        chuseok = calendar_for_session(date(2026, 9, 23))
        self.assertEqual(chuseok.publication_window(date(2026, 9, 23), datetime(2026, 9, 23, 22, 5, tzinfo=kst)),
                         datetime(2026, 9, 28, 22, 0, tzinfo=kst))

    def test_krx_holiday_window_accepts_last_available_session_and_caps_expiry(self):
        offset = (date(2026, 9, 23) - date.fromisoformat(self.runtime["as_of"])).days
        runtime = run(offset=offset)
        generated = datetime(2026, 9, 28, 7, tzinfo=ZoneInfo("Asia/Seoul"))
        payload = publish(self.path, runtime, expected_as_of="2026-09-23", generated_at=generated,
                          expires_at=datetime(2026, 9, 28, 20, tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertEqual(payload["publication"]["calendar"]["calendar_id"], "krx-equity-2026")
        self.assertEqual(payload["engine"]["data_as_of"], "2026-09-23")
        with self.assertRaisesRegex(KqsContractError, "next expected KRX"):
            publish(Path(self.directory.name) / "beyond.sqlite3", runtime, expected_as_of="2026-09-23",
                    generated_at=generated, expires_at=datetime(2026, 9, 29, 6, 30, tzinfo=ZoneInfo("Asia/Seoul")))

    def test_krx_stale_or_holiday_data_and_calendar_tamper_fail_closed(self):
        offset = (date(2026, 9, 23) - date.fromisoformat(self.runtime["as_of"])).days
        runtime = run(offset=offset)
        with self.assertRaisesRegex(KqsContractError, "latest available KRX session"):
            publish(self.path, runtime, expected_as_of="2026-09-23",
                    generated_at=datetime(2026, 9, 29, 7, tzinfo=ZoneInfo("Asia/Seoul")),
                    expires_at=datetime(2026, 9, 29, 8, tzinfo=ZoneInfo("Asia/Seoul")))
        payload = publish(Path(self.directory.name) / "valid.sqlite3", runtime, expected_as_of="2026-09-23",
                          generated_at=datetime(2026, 9, 28, 5, tzinfo=ZoneInfo("Asia/Seoul")),
                          expires_at=datetime(2026, 9, 28, 20, tzinfo=ZoneInfo("Asia/Seoul")))
        payload["publication"]["calendar"]["calendar_sha256"] = "0" * 64
        payload["integrity"]["payload_sha256"] = canonical_payload_digest(payload)
        with self.assertRaisesRegex(KqsContractError, "calendar digest"):
            validate_recommendation_payload(payload, now=datetime(2026, 9, 28, 6, tzinfo=ZoneInfo("Asia/Seoul")))

if __name__ == "__main__":
    unittest.main()
