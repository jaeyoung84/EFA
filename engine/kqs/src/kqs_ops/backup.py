"""Consistent SQLite backups with a restore check, SHA-256 manifest and retention.

Uses the SQLite online backup API, so a copy is consistent even while the worker writes.
Every copy is reopened read-only and must pass PRAGMA integrity_check and show the same
tables and row counts as the source at copy time. Off-host upload (S3) is a separate,
explicit operator step; this module never sends data anywhere.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST = "backup-manifest.json"
NAME = re.compile(r"\d{8}T\d{6}Z")
LABEL = re.compile(r"[a-z][a-z0-9_-]{0,40}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_counts(connection: sqlite3.Connection) -> dict:
    names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}


def _check_copy(path: Path) -> dict:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        counts = _table_counts(connection)
    finally:
        connection.close()
    return {"integrity": result, "tables": counts}


def backup_databases(sources: dict[str, Path], dest_root: Path, *, now: datetime | None = None, keep: int = 3) -> dict:
    if keep < 1:
        raise ValueError("keep must be at least 1.")
    for label in sources:
        if not LABEL.fullmatch(label):
            raise ValueError(f"Invalid backup label: {label}")
    now = now or datetime.now(timezone.utc)
    dest_root = dest_root.resolve()
    target = dest_root / now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.mkdir(mode=0o700)
    files, missing = {}, []
    for label, source in sources.items():
        if not source.is_file():
            missing.append(label)
            continue
        copy = target / f"{label}.sqlite3"
        src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
        dst = sqlite3.connect(copy)
        try:
            src.execute("BEGIN")
            expected = _table_counts(src)
            src.backup(dst)
            src.execute("COMMIT")
        finally:
            dst.close()
            src.close()
        os.chmod(copy, 0o600)
        checked = _check_copy(copy)
        if checked["integrity"] != "ok" or checked["tables"] != expected:
            raise RuntimeError(f"Backup restore check failed for {label}.")
        files[label] = {"file": copy.name, "source": str(source.resolve()), "bytes": copy.stat().st_size,
                        "sha256": _sha256(copy), "integrity": "ok", "tables": checked["tables"]}
    manifest = {"schema": "kqs-state-backup/v1", "created_at": now.isoformat(), "directory": target.name,
                "files": files, "missing": missing, "uploaded": False}
    (target / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.chmod(target / MANIFEST, 0o600)
    manifest["pruned"] = prune(dest_root, keep=keep)
    return manifest


def verify_backup(directory: Path) -> dict:
    manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    problems = []
    for label, entry in manifest["files"].items():
        copy = directory / entry["file"]
        if not copy.is_file() or _sha256(copy) != entry["sha256"]:
            problems.append(f"{label}: missing or SHA-256 mismatch")
            continue
        checked = _check_copy(copy)
        if checked["integrity"] != "ok" or checked["tables"] != entry["tables"]:
            problems.append(f"{label}: integrity or table counts differ")
    return {"directory": directory.name, "ok": not problems, "problems": problems}


def prune(dest_root: Path, *, keep: int) -> list[str]:
    """Delete only our own older backup directories (timestamp name + manifest), newest kept."""
    ours = sorted(path for path in dest_root.iterdir()
                  if path.is_dir() and NAME.fullmatch(path.name) and (path / MANIFEST).is_file())
    removed = []
    for path in ours[:-keep]:
        shutil.rmtree(path)
        removed.append(path.name)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up KQS SQLite state locally with a restore check.")
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--market-db", required=True, type=Path)
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--verify", type=Path, help="Only verify an existing backup directory")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            result = verify_backup(args.verify)
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 0 if result["ok"] else 1
        from kqs_ops.paths import state_paths
        paths = state_paths(args.state_dir)
        sources = {"market": args.market_db, "quality": paths["quality_db"], "snapshots": paths["snapshot_db"],
                   "attempts": paths["attempts_db"], "pipeline_runs": paths["pipeline_runs_db"]}
        result = backup_databases(sources, args.backup_dir, keep=args.keep)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
