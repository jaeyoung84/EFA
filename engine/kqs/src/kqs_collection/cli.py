"""Explicit read-only market-input verification with optional immutable evidence write."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .quality import QualityError, inspect_market_inputs
from .store import publish


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify KRX input snapshot only; no collection, orders or fills.")
    parser.add_argument("--krx-db", required=True, type=Path, help="Existing market SQLite; opened mode=ro")
    parser.add_argument("--coverage-manifest", required=True, type=Path, help="Explicit expected market coverage JSON")
    parser.add_argument("--as-of", required=True, help="Explicit KRX data session, YYYY-MM-DD")
    parser.add_argument("--observed-at", required=True, help="Timezone-aware observation time, ISO-8601")
    parser.add_argument("--quality-db", type=Path, help="Separate immutable quality evidence SQLite")
    args = parser.parse_args(argv)
    try:
        if args.quality_db and (args.quality_db.resolve() == args.krx_db.resolve() or
                                (args.quality_db.exists() and args.krx_db.exists() and
                                 args.quality_db.samefile(args.krx_db))):
            raise QualityError("Quality evidence DB must be separate from the market DB.")
        report = inspect_market_inputs(args.krx_db, as_of=args.as_of, observed_at=args.observed_at,
                                       coverage_manifest=args.coverage_manifest)
        if args.quality_db:
            report = publish(args.quality_db, report)
    except (QualityError, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc), "orders_enabled": False}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
