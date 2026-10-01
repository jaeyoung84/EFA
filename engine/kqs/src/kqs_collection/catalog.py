"""Instrument catalog for the website's name search (EINT-018).

Every KOSPI/KOSDAQ stock that has a row on the published session, with its name, market,
section and market cap, read from the collected KRX market DB. The website cannot reach KRX
KIND from AWS, so this file is its only full listing. Read-only on the market DB; written
atomically next to the other state files. Contains no prices beyond market cap and no keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

SCHEMA = "kqs-instrument-catalog/v1"
MARKETS = ("KOSPI", "KOSDAQ")


class CatalogError(ValueError):
    """The catalog could not be built from the market DB."""


def build_instrument_catalog(market_db: Path, as_of: str) -> dict:
    if date.fromisoformat(as_of).isoformat() != as_of:
        raise CatalogError("as_of must be YYYY-MM-DD.")
    try:
        resolved = Path(market_db).resolve(strict=True)
    except OSError as exc:
        raise CatalogError("Market DB does not exist.") from exc
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            "SELECT symbol, name, market, section, market_cap FROM daily_prices "
            "WHERE trade_date = ? AND market IN ('KOSPI', 'KOSDAQ') ORDER BY market_cap DESC, symbol",
            (as_of,)).fetchall()
    except sqlite3.Error as exc:
        raise CatalogError(f"Catalog query failed: {exc}") from exc
    finally:
        connection.close()
    items = [{"symbol": str(symbol), "name": str(name).strip(), "market": market, "section": str(section or ""),
              "market_cap": int(market_cap)}
             for symbol, name, market, section, market_cap in rows if str(name).strip()]
    if not items or {item["market"] for item in items} != set(MARKETS):
        raise CatalogError(f"No KOSPI/KOSDAQ listing for {as_of}.")
    digest = hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    return {"schema": SCHEMA, "as_of": as_of, "count": len(items), "items_sha256": digest, "items": items}


def write_instrument_catalog(path: Path, catalog: dict) -> Path:
    """Replace the catalog file atomically so the website never reads a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".instrument-catalog-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(catalog, stream, ensure_ascii=False, separators=(",", ":"))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path
