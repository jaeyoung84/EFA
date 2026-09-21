"""실행 중인 로컬 퀀트 플랫폼의 핵심 기능을 한 번에 점검한다."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backend.db import get_db


BASE_URL = os.getenv("BASELINE_BASE_URL", f"http://127.0.0.1:{os.getenv('APP_PORT', '8000')}").rstrip("/")


def request_json(path: str, payload: dict | None = None, token: str = "") -> dict:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status >= 400:
            raise RuntimeError(f"{path}: HTTP {response.status}")
        return json.loads(response.read().decode())


async def cleanup_probe(email: str) -> None:
    db = get_db()
    user = await db.users.find_one({"email": email})
    if not user:
        return
    await db.auth_sessions.delete_many({"user_id": user["_id"]})
    await db.portfolios.delete_many({"user_id": user["_id"]})
    await db.users.delete_one({"_id": user["_id"]})


def main() -> int:
    suffix = uuid.uuid4().hex[:10]
    email = f"baseline-probe-{suffix}@example.com"
    password = "BaselineProbe123!"
    checks: list[tuple[str, bool, str]] = []
    try:
        health = request_json("/api/health")
        checks.append(("backend", health.get("status") == "ok", str(health)))

        candle = request_json("/api/home/market-candle?market=kospi&period=3mo")
        candle_ok = candle.get("ticker") == "^KS11" and bool(candle.get("ohlcv")) and not candle.get("is_simulated")
        checks.append(("kospi", candle_ok, f"bars={len(candle.get('ohlcv', []))}, simulated={candle.get('is_simulated')}"))

        snapshot = request_json("/api/market/snapshot", {"tickers": ["^KS11", "^IXIC", "KRW=X"]})
        snapshot_ok = len(snapshot.get("items", [])) == 3 and all(item.get("status") == "ok" for item in snapshot["items"])
        checks.append(("market_snapshot", snapshot_ok, str([(item.get("ticker"), item.get("status")) for item in snapshot.get("items", [])])))

        signup = request_json("/api/auth/signup", {"email": email, "username": f"probe{suffix}", "password": password})
        login = request_json("/api/auth/login", {"email": email, "password": password})
        token = login.get("token", "")
        checks.append(("auth", bool(signup.get("token") and token), "signup/login"))

        portfolio = request_json("/api/portfolio", token=token)
        checks.append(("portfolio", "holdings" in portfolio, "authenticated read"))

        rag = request_json("/api/rag/status")
        qdrant = rag.get("qdrant", {})
        hybrid = rag.get("hybrid_search", {})
        rag_ok = bool(qdrant.get("available") and qdrant.get("collection_available") and hybrid.get("meilisearch_available"))
        checks.append(("rag", rag_ok, f"points={qdrant.get('points_count')}, status={qdrant.get('status')}"))
    except Exception as exc:
        checks.append(("unexpected_error", False, repr(exc)))
    finally:
        asyncio.run(cleanup_probe(email))

    for name, passed, detail in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = [name for name, passed, _ in checks if not passed]
    if failed:
        print(f"Baseline failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("Baseline smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
