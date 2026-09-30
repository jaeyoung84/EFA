#!/usr/bin/env python3
"""고정 추천 게시(기본) 또는 명시적인 legacy 브리지를 읽기 전용으로 점검한다."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backend.kqs_contract import KqsContractError, validate_recommendation_payload
from app.backend.kqs_recommendations import read_latest, snapshot_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("snapshot", "bridge"),
                        default=os.getenv("KQS_RECOMMENDATION_SOURCE", "snapshot"))
    parser.add_argument("--snapshot-db", type=Path)
    args = parser.parse_args()
    try:
        if args.source not in {"snapshot", "bridge"}:
            raise KqsContractError("KQS_RECOMMENDATION_SOURCE는 snapshot 또는 bridge여야 합니다.")
        if args.source == "snapshot":
            payload = read_latest(args.snapshot_db or snapshot_path())
        else:
            base_url = os.getenv("KQS_BRIDGE_URL", "http://127.0.0.1:18100").rstrip("/")
            request = urllib.request.Request(f"{base_url}/api/v1/recommendations",
                                             headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise KqsContractError("Recommendation exceeds 512 KiB.")
            payload = json.loads(raw.decode("utf-8"))
        validated = validate_recommendation_payload(payload)
    except (urllib.error.URLError, TimeoutError, ValueError, KqsContractError, OSError, KeyError, sqlite3.Error) as exc:
        print(f"KQS recommendation contract check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "source": args.source, "snapshot_id": validated.get("snapshot_id"),
        "schema_version": validated["schema_version"],
        "engine": validated["engine"],
        "generated_at": validated["generated_at"],
        "expires_at": validated["expires_at"],
        "selected_entry_count": validated["selected_entry_count"],
        "signal_count": len(validated["signals"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
