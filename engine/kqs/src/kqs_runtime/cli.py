"""Explicitly dated, read-only preview CLI. JSON is written only to stdout.

Publishing is done only by the single writer `kqs-publish-worker` (kqs_runtime.worker).
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from pandas.errors import DatabaseError

from kqs_runtime.recommendations import generate_recommendations
from kqs_runtime.sqlite_reader import read_inputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="KQS recommendations preview only; no publication, orders or fills. "
                    "Publish with kqs-publish-worker.")
    parser.add_argument("--krx-db", required=True, type=Path, help="Existing SQLite DB; opened mode=ro")
    parser.add_argument("--history-start", required=True, help="Explicit history boundary, YYYY-MM-DD")
    parser.add_argument("--as-of", required=True, help="Data session, not today's wall-clock date")
    parser.add_argument("--exclude-symbol", action="append", default=[],
                        help="Explicit exclusion context; never loads an automatic paper ledger")
    args = parser.parse_args(argv)
    try:
        history, benchmarks = read_inputs(args.krx_db, history_start=args.history_start, as_of=args.as_of)
        result = generate_recommendations(history, benchmarks, as_of=args.as_of,
                                          excluded_symbols=tuple(args.exclude_symbol))
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (ValueError, OSError, sqlite3.Error, DatabaseError) as exc:
        print(json.dumps({"error": str(exc), "orders_enabled": False}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
