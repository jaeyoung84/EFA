"""Reviewed, year-bound KRX equity session policy for publication freshness.

This is deliberately not a generic business-day helper.  The source configuration
must cover the year being published and carries the KRX closure exceptions used by
the worker and the web reader.  A new year requires a reviewed configuration.

Session walks (next session, latest available session) may cross into the adjacent
year, but only through that year's own reviewed file; an unreviewed year fails closed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
SCHEMA = "krx-equity-calendar/v1"
# v1(EINT-004): 세션 자료는 다음 날 calendar 파일의 data_available_time_kst(06:00)부터 공개된 것으로 본다.
# v2(EINT-028): 세션 자료는 같은 날 21:30 KST부터 공개된 것으로 본다(장 마감 뒤 저녁 게시). 새 게시는 v2를 쓰고,
#   이미 v1로 게시된 추천은 그 metadata의 정책으로 계속 검증한다. 휴장일 파일(SHA)은 바꾸지 않는다.
POLICY_V1 = "krx-session-expiry/v1"
POLICY_V2 = "krx-session-expiry/v2"
POLICY = POLICY_V2
SUPPORTED_POLICIES = frozenset({POLICY_V1, POLICY_V2})
SAME_DAY_AVAILABLE_TIME = time(21, 30)
REVIEWED_CALENDARS = {
    "krx-equity-2026": "krx_equity_2026.json",
    "krx-equity-2027": "krx_equity_2027.json",
}
# Upper bound for a backwards/forwards walk; KRX never closes longer than this.
_MAX_WALK_DAYS = 31


class CalendarError(ValueError):
    """Raised when a publication cannot be tied to a reviewed KRX session."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_hhmm(value: object) -> time:
    if not isinstance(value, str):
        raise CalendarError("KRX calendar data_available_time_kst is missing.")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise CalendarError("KRX calendar data_available_time_kst is invalid.") from exc


@dataclass(frozen=True)
class KRXCalendar:
    calendar_id: str
    year: int
    closures: frozenset[date]
    available_time: time
    digest: str
    policy: str = POLICY

    def _covered(self, value: date) -> None:
        if value.year != self.year:
            raise CalendarError(f"KRX calendar {self.calendar_id} does not cover {value.isoformat()}.")

    def is_session(self, value: date) -> bool:
        self._covered(value)
        return value.weekday() < 5 and value not in self.closures

    def require_session(self, value: date) -> None:
        if not self.is_session(value):
            raise CalendarError(f"{value.isoformat()} is not a reviewed KRX equity session.")

    @property
    def policy_available_time(self) -> time:
        return SAME_DAY_AVAILABLE_TIME if self.policy == POLICY_V2 else self.available_time

    def availability_at(self, session: date) -> datetime:
        self.require_session(session)
        day = session if self.policy == POLICY_V2 else session + timedelta(days=1)
        return datetime.combine(day, self.policy_available_time, tzinfo=KST).astimezone(timezone.utc)

    def _for(self, value: date) -> "KRXCalendar":
        """This calendar for its own year, otherwise the adjacent year's reviewed calendar."""
        if value.year == self.year:
            return self
        if abs(value.year - self.year) != 1:
            raise CalendarError(f"KRX calendar {self.calendar_id} does not cover {value.isoformat()}.")
        try:
            return load_calendar(f"krx-equity-{value.year}", policy=self.policy)
        except CalendarError as exc:
            raise CalendarError(f"{value.isoformat()} requires a reviewed KRX calendar for {value.year}.") from exc

    def next_session(self, session: date) -> date:
        self.require_session(session)
        candidate = session + timedelta(days=1)
        for _ in range(_MAX_WALK_DAYS):
            if self._for(candidate).is_session(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise CalendarError("No KRX session found after the reviewed closure window.")

    def latest_available_session(self, generated_at: datetime) -> date:
        current = generated_at.astimezone(KST).date()
        for _ in range(_MAX_WALK_DAYS):
            calendar = self._for(current)
            if calendar.is_session(current) and calendar.availability_at(current) <= generated_at:
                return current
            current -= timedelta(days=1)
        raise CalendarError("No reviewed KRX session is available before publication time.")

    def publication_window(self, session: date, generated_at: datetime) -> datetime:
        expected = self.latest_available_session(generated_at)
        if session != expected:
            raise CalendarError(
                f"data_as_of {session.isoformat()} is not the latest available KRX session "
                f"{expected.isoformat()} at publication time."
            )
        available_at = self.availability_at(session)
        if generated_at < available_at:
            raise CalendarError("KRX data publication delay has not elapsed for data_as_of.")
        following = self.next_session(session)
        return self._for(following).availability_at(following)

    def public_metadata(self) -> dict:
        metadata = {"calendar_id": self.calendar_id, "calendar_sha256": self.digest,
                    "policy": self.policy, "data_available_time_kst": self.policy_available_time.strftime("%H:%M")}
        if self.policy == POLICY_V2:
            metadata["data_available_day"] = "same_session_day"
        return metadata


def _path(calendar_id: str) -> Path:
    filename = REVIEWED_CALENDARS.get(calendar_id)
    if filename is None:
        raise CalendarError("Unknown reviewed KRX calendar.")
    return Path(__file__).with_name("calendars") / filename


def load_calendar(calendar_id: str, *, policy: str = POLICY) -> KRXCalendar:
    if policy not in SUPPORTED_POLICIES:
        raise CalendarError("Unknown KRX publication availability policy.")
    path = _path(calendar_id)
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise CalendarError("Reviewed KRX calendar could not be loaded.") from exc
    if raw.get("schema") != SCHEMA or raw.get("calendar_id") != calendar_id or raw.get("timezone") != "Asia/Seoul":
        raise CalendarError("Reviewed KRX calendar metadata is invalid.")
    if raw.get("session_weekdays") != [0, 1, 2, 3, 4] or type(raw.get("year")) is not int:
        raise CalendarError("Reviewed KRX calendar session rule is invalid.")
    closures_raw = raw.get("closures")
    if not isinstance(closures_raw, dict) or not closures_raw:
        raise CalendarError("Reviewed KRX calendar closures are missing.")
    try:
        closures = frozenset(date.fromisoformat(value) for value in closures_raw)
    except (TypeError, ValueError) as exc:
        raise CalendarError("Reviewed KRX calendar closure date is invalid.") from exc
    if any(value.year != raw["year"] for value in closures):
        raise CalendarError("Reviewed KRX calendar contains an out-of-year closure.")
    return KRXCalendar(calendar_id, raw["year"], closures, _parse_hhmm(raw.get("data_available_time_kst")), _sha256(path),
                       policy)


def calendar_for_session(session: date, *, policy: str = POLICY) -> KRXCalendar:
    return load_calendar(f"krx-equity-{session.year}", policy=policy)


def validate_publication_calendar(metadata: object, *, data_as_of: date, generated_at: datetime,
                                  expires_at: datetime) -> dict:
    if not isinstance(metadata, dict) or metadata.get("policy") not in SUPPORTED_POLICIES:
        raise CalendarError("Reviewed KRX publication calendar metadata is missing.")
    calendar_id = metadata.get("calendar_id")
    if not isinstance(calendar_id, str):
        raise CalendarError("Reviewed KRX calendar id is missing.")
    calendar = load_calendar(calendar_id, policy=metadata["policy"])   # 게시 당시 정책으로 검증
    if metadata.get("calendar_sha256") != calendar.digest:
        raise CalendarError("Reviewed KRX calendar digest does not match the installed calendar.")
    expected_metadata = calendar.public_metadata()
    if any(metadata.get(key) != expected_metadata.get(key) for key in ("data_available_time_kst", "data_available_day")):
        raise CalendarError("Reviewed KRX calendar availability policy does not match.")
    next_available = calendar.publication_window(data_as_of, generated_at)
    if expires_at > generated_at + timedelta(hours=24):
        raise CalendarError("Publication expiry exceeds the 24 hour ceiling.")
    if expires_at > next_available:
        raise CalendarError("Publication expiry crosses the next expected KRX data availability time.")
    return calendar.public_metadata()


def validate_observation_calendar(metadata: object, *, data_as_of: date, observed_at: datetime) -> dict:
    """Validate a read-only collector observation without inventing an expiry."""
    if not isinstance(metadata, dict):
        raise CalendarError("Reviewed KRX observation calendar metadata is missing.")
    calendar_id = metadata.get("calendar_id")
    if not isinstance(calendar_id, str) or metadata.get("policy") not in SUPPORTED_POLICIES:
        raise CalendarError("Reviewed KRX observation calendar id is missing.")
    calendar = load_calendar(calendar_id, policy=metadata["policy"])
    if metadata != calendar.public_metadata():
        raise CalendarError("Reviewed KRX observation calendar metadata does not match the installed calendar.")
    expected = calendar.latest_available_session(observed_at)
    if data_as_of != expected:
        raise CalendarError(
            f"data_as_of {data_as_of.isoformat()} is not the latest available KRX session "
            f"{expected.isoformat()} at observation time."
        )
    return calendar.public_metadata()
