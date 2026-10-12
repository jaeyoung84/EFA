"""Export the EINT-036 US data from a MongoDB copy into a hashed SQLite snapshot for US-PB-v1.

Reads only: us_equity_bars, us_index_membership, us_equity_runs (latest finished run) and
us_daily_bars (SPY). Writes one new SQLite file and refuses to overwrite. Intervals flagged by the
run's quality check (starts_late / no_bars) are marked excluded=1 (prereg §2).

usage: python research/us_pullback_v1/export_snapshot.py <mongodb-uri> <database> <out.sqlite3>
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import SNAPSHOT_TABLES, table_sha  # noqa: E402

EXCLUDED_KINDS = {"starts_late", "no_bars"}


def export(db, out: Path) -> dict:
    out = Path(out)
    if out.exists():
        raise SystemExit(f"{out} exists; snapshots are never overwritten")
    run = db.us_equity_runs.find_one({"status": {"$in": ["ok", "partial"]}, "quality": {"$exists": True}}, sort=[("started_at", -1)])
    if run is None:
        raise SystemExit("no finished us_equity_runs record")
    membership = db.us_index_membership.find_one({"_id": run["membership_id"]})
    flagged = {(g["symbol"], g["start"]) for g in run["quality"]["history_gaps"] if g["kind"] in EXCLUDED_KINDS}
    connection = sqlite3.connect(out)
    try:
        connection.executescript("""
            CREATE TABLE bars (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, split REAL,
                               PRIMARY KEY (symbol, date));
            CREATE TABLE spy (date TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL);
            CREATE TABLE intervals (symbol TEXT, start TEXT, end TEXT, excluded INTEGER, PRIMARY KEY (symbol, start));
            CREATE TABLE coverage (date TEXT PRIMARY KEY, members INTEGER, with_bar INTEGER, pct REAL);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        for doc in db.us_equity_bars.find({}, {"symbol": 1, "days": 1, "split": 1}).sort("_id", 1):
            split = doc.get("split") or {}
            connection.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)", [
                (doc["symbol"], day, bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"], split.get(day))
                for day, bar in sorted((doc.get("days") or {}).items())])
        connection.executemany("INSERT INTO spy VALUES (?,?,?,?,?,?)", [
            (doc["date"], doc["raw"]["open"], doc["raw"]["high"], doc["raw"]["low"], doc["raw"]["close"], doc["raw"]["volume"])
            for doc in db.us_daily_bars.find({"symbol": "SPY"}).sort("date", 1)])
        connection.executemany("INSERT INTO intervals VALUES (?,?,?,?)", [
            (row["symbol"], row["start"], row["end"], int((row["symbol"], row["start"]) in flagged))
            for row in membership["intervals"]])
        connection.executemany("INSERT INTO coverage VALUES (?,?,?,?)", [
            (row["date"], row["members"], row["with_bar"], row["pct"]) for row in run["quality"]["coverage"]])
        hashes = {table: table_sha(connection, table) for table in SNAPSHOT_TABLES}
        meta = {"table_sha256": json.dumps(hashes, sort_keys=True), "membership_id": run["membership_id"],
                "source_run_id": run["run_id"], "source_last_session": run["last_session"],
                "excluded_intervals": json.dumps(sorted(flagged))}
        connection.executemany("INSERT INTO meta VALUES (?,?)", sorted(meta.items()))
        connection.commit()
    finally:
        connection.close()
    return {"tables": hashes, **{k: v for k, v in meta.items() if k != "table_sha256"}}


if __name__ == "__main__":
    uri, database, target = sys.argv[1:4]
    print(json.dumps(export(MongoClient(uri)[database], Path(target)), indent=1))
