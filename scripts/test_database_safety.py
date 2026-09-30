"""Fail-closed target checks for destructive test fixtures; standard library only.

This is not a MongoDB client and never connects, creates, drops or clears a database.
Only a dedicated local test namespace is accepted. directConnection prevents replica-set
discovery from redirecting a local seed to a different server.
"""
from dataclasses import dataclass
import re
from typing import Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

DEFAULT_URL = "mongodb://127.0.0.1:27017"
DEFAULT_DATABASE = "jy_investing_test"
DATABASE_PATTERN = re.compile(r"jy_investing_test(?:_[a-z0-9_]{1,40})?\Z")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "mongo", "kqs-test-mongo"})


class UnsafeTestDatabase(RuntimeError):
    """The test target is not provably in the permitted local test namespace."""


@dataclass(frozen=True)
class TestDatabaseTarget:
    url: str
    database: str


def validate_test_target(url: str, database: str) -> TestDatabaseTarget:
    # Never include the supplied URI (which may contain credentials) in diagnostics.
    if not isinstance(database, str) or not DATABASE_PATTERN.fullmatch(database):
        raise UnsafeTestDatabase("테스트 DB는 jy_investing_test 또는 jy_investing_test_<소문자 식별자>만 허용합니다. 운영 DB 정리는 차단됩니다.")
    try:
        parts = urlsplit(url)
        if (parts.scheme != "mongodb" or parts.fragment or not parts.netloc
                or parts.username is not None or parts.password is not None
                or "," in parts.netloc or "%" in parts.netloc
                or parts.hostname not in LOCAL_HOSTS
                or (parts.port is not None and not 1 <= parts.port <= 65535)):
            raise ValueError("unapproved connection")
        if unquote(parts.path) not in ("", "/", "/" + database):
            raise ValueError("URI database differs")
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        # Replicas, alternate auth databases and arbitrary options are deliberately not allowed.
        if pairs and pairs != [("directConnection", "true")]:
            raise ValueError("unapproved connection options")
    except (ValueError, TypeError, AttributeError) as exc:
        raise UnsafeTestDatabase("테스트 MongoDB는 인증정보 없는 단일 로컬 주소만 허용합니다. URI의 DB와 옵션도 확인하세요.") from exc
    safe_url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                          urlencode({"directConnection": "true"}), ""))
    return TestDatabaseTarget(safe_url, database)


def target_from_environment(environ: Mapping[str, str]) -> TestDatabaseTarget:
    return validate_test_target(environ.get("MONGODB_URL", DEFAULT_URL),
                                environ.get("MONGODB_DB", DEFAULT_DATABASE))


def assert_cleanup_target(expected: TestDatabaseTarget, environ: Mapping[str, str],
                          actual_database: str | None = None) -> None:
    current = target_from_environment(environ)
    if current != expected:
        raise UnsafeTestDatabase("테스트 시작 후 DB 대상이 변경됐습니다. 데이터 정리는 차단됩니다.")
    if actual_database is not None and actual_database != expected.database:
        raise UnsafeTestDatabase("실제 MongoDB 핸들이 승인된 테스트 DB와 다릅니다. 데이터 정리는 차단됩니다.")
