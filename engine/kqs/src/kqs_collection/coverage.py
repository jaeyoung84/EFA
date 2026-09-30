"""Derive the per-session coverage manifest from collection evidence, with a provider sanity band.

Expected counts come from `collection_log.row_count`, i.e. the number of rows KRX returned
for that session. This proves the stored session is complete relative to the response; it is
not independent of the provider. The band against the previous collected session (observed
day-to-day change 2025-2026: KOSPI at most 2, KOSDAQ at most 3 symbols) catches gross
provider truncation, not a small silent omission.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from pathlib import Path

from .quality import BENCHMARKS, MARKETS, QualityError

SOURCE_ID = "krx-openapi-collection-log/v1"
BAND_RATIO = 0.01
BAND_MINIMUM = 5


def allowed_change(previous: int) -> int:
    return max(BAND_MINIMUM, math.ceil(previous * BAND_RATIO))


def build_coverage_manifest(market_db: Path, as_of: str) -> dict:
    try:
        resolved = market_db.resolve(strict=True)
    except OSError as exc:
        raise QualityError("Market DB does not exist.") from exc
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        markets: dict[str, dict] = {}
        derivation: dict[str, dict] = {}
        for market in MARKETS:
            current = connection.execute(
                "SELECT row_count FROM collection_log WHERE trade_date=? AND market=?", (as_of, market)).fetchone()
            if current is None or current[0] <= 0:
                raise QualityError(f"{market} has no positive collection record for {as_of}.")
            previous = connection.execute(
                "SELECT trade_date, row_count FROM collection_log WHERE market=? AND trade_date<? AND row_count>0 "
                "ORDER BY trade_date DESC LIMIT 1", (market, as_of)).fetchone()
            if previous is not None and abs(current[0] - previous[1]) > allowed_change(previous[1]):
                raise QualityError(
                    f"{market} count {current[0]} changed more than {allowed_change(previous[1])} from "
                    f"{previous[1]} on {previous[0]}; possible provider truncation, manifest not built.")
            markets[market] = {"expected_symbol_count": int(current[0])}
            derivation[market] = {"previous_session": previous[0] if previous else None,
                                  "previous_count": int(previous[1]) if previous else None,
                                  "allowed_change": allowed_change(previous[1]) if previous else None}
    except sqlite3.Error as exc:
        raise QualityError(f"Coverage derivation failed: {exc}") from exc
    finally:
        connection.close()
    return {"schema": "kqs-market-coverage/v1", "source_id": SOURCE_ID, "as_of": as_of,
            "markets": markets, "benchmarks": dict(BENCHMARKS), "derivation": derivation}


def write_manifest(path: Path, manifest: dict) -> Path:
    """Deterministic bytes (same inputs -> same file and quality identity); atomic replace."""
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == encoded:
        return path
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)
    return path
