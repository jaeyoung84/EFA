"""Daily KQS pipeline: collect -> coverage -> recorded quality run -> single-writer publish.

Runs once per invocation (a scheduler calls it). Every run, including failures, is appended
to an immutable run log and mirrored to a latest-status file for monitoring. It never creates
orders or fills and never touches personal ledgers or the legacy automatic paper ledger.
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kqs_collection.coverage import build_coverage_manifest, write_manifest
from kqs_collection.krx import KrxApiError, KrxOpenApiClient, MarketStore, collect_session
from kqs_collection.quality import QualityError, inspect_market_inputs
from kqs_collection.store import publish as record_quality
from kqs_ops.paths import state_paths
from kqs_ops.runlog import SUCCESS, record_run, write_status
from kqs_publication.calendar import KST, CalendarError, calendar_for_session
from kqs_runtime.worker import require_separate_paths, run_publication

HISTORY_DAYS = 730
MAX_EXPIRY = timedelta(hours=24)
PUBLICATION_STATUS = {"published": "published", "already_published": "already_published",
                      "blocked_quality": "blocked_quality", "writer_busy": "writer_busy", "failed": "publish_failed"}


def run_daily_pipeline(*, state_dir: Path, market_db: Path, client: KrxOpenApiClient,
                       now: datetime | None = None, refresh: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    paths = state_paths(state_dir)
    require_separate_paths(market_db=market_db, quality_db=paths["quality_db"], snapshot_db=paths["snapshot_db"],
                           attempts_db=paths["attempts_db"], pipeline_runs_db=paths["pipeline_runs_db"])
    run = {"run_id": uuid.uuid4().hex, "started_at": now.isoformat(), "session": None, "steps": {},
           "error": None, "orders_enabled": False}

    def finish(status: str, error: str | None = None) -> dict:
        run.update(status=status, error=error, finished_at=datetime.now(timezone.utc).isoformat())
        record_run(paths["pipeline_runs_db"], run)
        write_status(paths["status_file"], run)
        return run

    try:
        calendar = calendar_for_session(now.astimezone(KST).date())
        session = calendar.latest_available_session(now)
        ceiling = calendar.publication_window(session, now)
    except CalendarError as exc:
        return finish("failed", f"KRX calendar: {exc}")
    as_of = session.isoformat()
    run["session"] = as_of

    try:
        run["steps"]["collection"] = collect_session(client, MarketStore(market_db), session, refresh=refresh)
    except (KrxApiError, ValueError, OSError, sqlite3.Error) as exc:
        return finish("collect_failed", str(exc))

    try:
        manifest_path = write_manifest(paths["coverage_dir"] / f"coverage-{as_of}.json",
                                       build_coverage_manifest(market_db, as_of))
        run["steps"]["coverage"] = {"path": manifest_path.name}
    except (QualityError, OSError) as exc:
        return finish("coverage_rejected", str(exc))

    try:
        report = record_quality(paths["quality_db"], inspect_market_inputs(
            market_db, as_of=as_of, observed_at=now.isoformat(), coverage_manifest=manifest_path))
    except (QualityError, OSError, sqlite3.Error) as exc:
        return finish("quality_rejected", str(exc))
    run["steps"]["quality"] = {"run_id": report["run_id"], "status": report["status"], "issues": report["issues"]}
    if report["status"] != "ready":
        return finish("quality_rejected", "; ".join(report["issues"]))

    expires_at = min(now + MAX_EXPIRY, ceiling)
    try:
        result = run_publication(
            krx_db=market_db, history_start=(session - timedelta(days=HISTORY_DAYS)).isoformat(), as_of=as_of,
            expected_as_of=as_of, expires_at=expires_at, snapshot_db=paths["snapshot_db"],
            quality_db=paths["quality_db"], quality_run_id=report["run_id"], coverage_manifest=manifest_path,
            attempts_db=paths["attempts_db"], now=now)
    except (ValueError, OSError, sqlite3.Error) as exc:
        return finish("publish_failed", str(exc))
    attempt = result["attempt"]
    run["steps"]["publication"] = {"attempt_id": attempt["attempt_id"], "status": attempt["status"],
                                   "snapshot_id": attempt.get("snapshot_id"), "expires_at": expires_at.isoformat()}
    return finish(PUBLICATION_STATUS[attempt["status"]], attempt.get("error"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Daily KQS collect->verify->publish run; no orders, fills or personal ledgers.")
    parser.add_argument("--state-dir", required=True, type=Path, help="Private state directory (not in Git)")
    parser.add_argument("--market-db", required=True, type=Path, help="Market SQLite in a writable directory")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="D-31 fallback for KRX_AUTH_KEY")
    parser.add_argument("--refresh", action="store_true", help="Re-collect the session even if logged")
    args = parser.parse_args(argv)
    try:
        client = KrxOpenApiClient.from_env(env_file=args.env_file)
        run = run_daily_pipeline(state_dir=args.state_dir, market_db=args.market_db, client=client,
                                 refresh=args.refresh)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc), "orders_enabled": False}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(run, ensure_ascii=False, sort_keys=True))
    if run["status"] in SUCCESS:
        return 0
    return 3 if run["status"] == "writer_busy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
