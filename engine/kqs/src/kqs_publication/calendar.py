"""Reviewed, year-bound KRX equity session policy for publication freshness.

This is deliberately not a generic business-day helper.  The source configuration
must cover the year being published and carries the KRX closure exceptions used by
the worker and the web reader.  A new year requires a reviewed configuration.
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
POLICY = "krx-session-expiry/v1"


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

    def _covered(self, value: date) -> None:
        if value.year != self.year:
            raise CalendarError(f"KRX calendar {self.calendar_id} does not cover {value.isoformat()}.")

    def is_session(self, value: date) -> bool:
        self._covered(value)
        return value.weekday() < 5 and value not in self.closures

    def require_session(self, value: date) -> None:
        if not self.is_session(value):
            raise CalendarError(f"{value.isoformat()} is not a reviewed KRX equity session.")

    def availability_at(self, session: date) -> datetime:
        self.require_session(session)
        return datetime.combine(session + timedelta(days=1), self.available_time, tzinfo=KST).astimezone(timezone.utc)

    def next_session(self, session: date) -> date:
        self.require_session(session)
        candidate = session + timedelta(days=1)
        while candidate.year == self.year:
            if self.is_session(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise CalendarError("The next KRX session requires a reviewed calendar for the following year.")

    def latest_available_session(self, generated_at: datetime) -> date:
        current = generated_at.astimezone(KST).date()
        self._covered(current)
        while current.year == self.year:
            if self.is_session(current) and self.availability_at(current) <= generated_at:
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
        return self.availability_at(self.next_session(session))

    def public_metadata(self) -> dict:
        return {"calendar_id": self.calendar_id, "calendar_sha256": self.digest,
                "policy": POLICY, "data_available_time_kst": self.available_time.strftime("%H:%M")}


def _path(calendar_id: str) -> Path:
    if calendar_id != "krx-equity-2026":
        raise CalendarError("Unknown reviewed KRX calendar.")
    return Path(__file__).with_name("calendars") / "krx_equity_2026.json"


def load_calendar(calendar_id: str) -> KRXCalendar:
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
    return KRXCalendar(calendar_id, raw["year"], closures, _parse_hhmm(raw.get("data_available_time_kst")), _sha256(path))


def calendar_for_session(session: date) -> KRXCalendar:
    return load_calendar(f"krx-equity-{session.year}")


def validate_publication_calendar(metadata: object, *, data_as_of: date, generated_at: datetime,
                                  expires_at: datetime) -> dict:
    if not isinstance(metadata, dict) or metadata.get("policy") != POLICY:
        raise CalendarError("Reviewed KRX publication calendar metadata is missing.")
    calendar_id = metadata.get("calendar_id")
    if not isinstance(calendar_id, str):
        raise CalendarError("Reviewed KRX calendar id is missing.")
    calendar = load_calendar(calendar_id)
    if metadata.get("calendar_sha256") != calendar.digest:
        raise CalendarError("Reviewed KRX calendar digest does not match the installed calendar.")
    if metadata.get("data_available_time_kst") != calendar.available_time.strftime("%H:%M"):
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
    if not isinstance(calendar_id, str):
        raise CalendarError("Reviewed KRX observation calendar id is missing.")
    calendar = load_calendar(calendar_id)
    if metadata != calendar.public_metadata():
        raise CalendarError("Reviewed KRX observation calendar metadata does not match the installed calendar.")
    expected = calendar.latest_available_session(observed_at)
    if data_as_of != expected:
        raise CalendarError(
            f"data_as_of {data_as_of.isoformat()} is not the latest available KRX session "
            f"{expected.isoformat()} at observation time."
        )
    return calendar.public_metadata()
