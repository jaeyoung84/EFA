"""Health check for monitoring: is the latest expected KRX session published and valid?

Exit code 0 = healthy, 1 = unhealthy (alert), so cron/systemd/CloudWatch can alarm on it.
A missing publication is tolerated only for `grace_hours` after the session becomes available.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kqs_ops.paths import state_paths
from kqs_publication.calendar import KST, CalendarError, calendar_for_session
from kqs_publication.contract import KqsContractError
from kqs_publication.store import read_latest


def check_health(state_dir: Path, *, now: datetime | None = None, grace_hours: float = 6.0) -> dict:
    now = now or datetime.now(timezone.utc)
    paths = state_paths(state_dir)
    report = {"checked_at": now.isoformat(), "healthy": False, "expected_session": None,
              "published_session": None, "last_run_status": None, "reason": None}
    if paths["status_file"].is_file():
        last = json.loads(paths["status_file"].read_text(encoding="utf-8"))
        report["last_run_status"] = last.get("status")
        report["last_run_finished_at"] = last.get("finished_at")
    try:
        calendar = calendar_for_session(now.astimezone(KST).date())
        expected = calendar.latest_available_session(now)
        report["expected_session"] = expected.isoformat()
        due_at = calendar.availability_at(expected)
    except CalendarError as exc:
        report["reason"] = f"KRX calendar cannot determine the expected session: {exc}"
        return report
    try:
        published = read_latest(paths["snapshot_db"], now=now)
        report["published_session"] = published["engine"]["data_as_of"]
        report["published_expires_at"] = published["expires_at"]
    except (KqsContractError, OSError, ValueError, KeyError) as exc:
        report["reason"] = f"No valid publication: {exc}"
    if report["published_session"] == report["expected_session"]:
        report["healthy"] = True
        report["reason"] = None
    elif now < due_at + timedelta(hours=grace_hours):
        report["healthy"] = True
        report["reason"] = f"Within {grace_hours}h grace after {expected.isoformat()} data became due."
    elif report["reason"] is None:
        report["reason"] = f"Latest publication {report['published_session']} is not expected session {expected.isoformat()}."
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KQS publication health check for monitoring.")
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--grace-hours", type=float, default=6.0)
    args = parser.parse_args(argv)
    report = check_health(args.state_dir, grace_hours=args.grace_hours)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
