"""EINT-036: 미국 S&P 500 개별주 일봉(미국 데이터 경로 2단계, docs/engine/US_DATA_POLICY_v1.md).

1. 위키백과 구성 이력 + Alpaca 티커 변경 기록으로 2016-01-04 이후 구성 구간을 만든다(판 번호가 같으면 재사용).
2. 구성 이력에 나온 모든 종목(편출·폐지 포함)의 일봉을 종목·연도 문서로 저장한다.
   원시 일봉은 처음 받은 값 고정(달라지면 raw_revisions 기록), 분할/분할·배당 조정은 일자별 계수만 저장.
3. 매 실행: 월말 coverage, 구성 기간 중 일봉 공백(history_gap), 고정 표본 20종목 Yahoo 교차검증을 기록한다.

수익률·신호·추천은 계산하지 않는다. 한국 엔진·원장·레버리지 DCA와 연결하지 않는다.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

import requests
from pymongo import UpdateOne

from .db import get_db
from .us_market_data import (
    ALPACA_DATA_URL, SOURCE, UsMarketDataError, _bar, _crosscheck, _headers, fetch_alpaca_bars, last_completed_session,
    query_end,
)
from . import us_universe as universe

logger = logging.getLogger("jy_investing.us_equity_data")

SCHEMA = "us-equity-data/v1"
MEMBERSHIP = "us_index_membership"
BARS = "us_equity_bars"
SYMBOLS = "us_equity_symbols"
RUNS = "us_equity_runs"
BATCH = 50          # 증분(최근 14일) 요청당 종목 수
BACKFILL_BATCH = 10  # 2016년부터 받는 백필은 메모리(서버 2GB)를 고려해 작게
RECENT_DAYS = 14
GAP_TOLERANCE_DAYS = 10
CROSSCHECK_SAMPLE = 20
CORPORATE_ACTIONS_URL = ALPACA_DATA_URL.replace("/v2/stocks/bars", "/v1/corporate-actions")


def policy_sha256() -> str | None:
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / universe.POLICY
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def fetch_name_changes(symbols: list[str]) -> list[dict]:
    """Alpaca 티커 변경 기록(옛 티커, 새 티커, 처리일). 종목 100개씩, 페이지 끝까지."""
    out, headers = [], _headers()
    for index in range(0, len(symbols), 100):
        params = {"types": "name_change", "symbols": ",".join(symbols[index:index + 100]), "start": "2016-01-01",
                  "end": date.today().isoformat(), "limit": 1000}
        for _ in range(20):
            response = requests.get(CORPORATE_ACTIONS_URL, headers=headers, params=params, timeout=30)
            if response.status_code >= 400:
                raise UsMarketDataError(f"Alpaca 기업행위 오류 {response.status_code}")
            body = response.json()
            out.extend((body.get("corporate_actions") or {}).get("name_changes") or [])
            if not body.get("next_page_token"):
                break
            params["page_token"] = body["next_page_token"]
    return out


def alias_map(changes: list[dict]) -> dict[str, list[tuple[str, str]]]:
    aliases: dict[str, set] = {}
    for item in changes:
        old, new, day = item.get("old_symbol"), item.get("new_symbol"), str(item.get("process_date") or "")[:10]
        if old and new and old != new and universe.TICKER.fullmatch(old) and universe.TICKER.fullmatch(new) and day:
            aliases.setdefault(old, set()).add((day, new))
    return {old: sorted(pairs) for old, pairs in aliases.items()}


def resolve_aliases(symbols: list[str], fetch=fetch_name_changes) -> tuple[dict, list[dict]]:
    """티커 변경을 끝까지 따라간다(새 티커가 다시 바뀐 경우)."""
    seen, pending, records = set(), sorted(set(symbols)), []
    while pending:
        seen.update(pending)
        batch = fetch(pending)
        records.extend(batch)
        pending = sorted({item.get("new_symbol") for item in batch if item.get("new_symbol")} - seen)
    unique = {item.get("id") or (item.get("old_symbol"), item.get("new_symbol"), item.get("process_date")): item for item in records}
    return alias_map(list(unique.values())), list(unique.values())


async def refresh_membership(*, fetch_page=universe.fetch_page, fetch_changes=fetch_name_changes, month_ends=None) -> dict:
    """두 위키백과 판이 이전과 같으면 저장된 구성 이력을 그대로 쓴다."""
    db = get_db()
    constituents = await asyncio.to_thread(fetch_page, universe.CONSTITUENTS_PAGE)
    changes = await asyncio.to_thread(fetch_page, universe.CHANGES_PAGE)
    key = f"{constituents[0]}:{changes[0]}"
    existing = await db[MEMBERSHIP].find_one({"_id": key})
    if existing:
        return existing
    current, rows = universe.parse_constituents(constituents[1]), universe.parse_changes(changes[1])
    wiki_symbols = sorted({row["symbol"] for row in current} | {c[k] for c in rows for k in ("added", "removed") if c[k]})
    aliases, records = await asyncio.to_thread(resolve_aliases, wiki_symbols, fetch_changes)
    ends = month_ends or await asyncio.to_thread(universe.nyse_month_ends, universe.START, date.today().isoformat())
    membership = universe.build_membership(constituents, changes, aliases, ends)
    membership.update(_id=key, aliases={old: [list(p) for p in pairs] for old, pairs in aliases.items()},
                      alias_records=len(records), created_at=datetime.now(timezone.utc))
    await db[MEMBERSHIP].insert_one(membership)
    return membership


def _year_docs(symbol: str, raw: dict[str, dict], split: dict[str, dict], total: dict[str, dict]) -> dict[int, dict]:
    years: dict[int, dict] = {}
    for day, bar in raw.items():
        doc = years.setdefault(int(day[:4]), {"days": {}, "split": {}, "total": {}})
        doc["days"][day] = bar
        if day in split:
            doc["split"][day] = round(split[day]["close"] / bar["close"], 10)
        if day in total:
            doc["total"][day] = round(total[day]["close"] / bar["close"], 10)
    return years


def _series(bars: list[dict], through: str) -> dict[str, dict]:
    out = {}
    for raw in bars or []:
        day = str(raw.get("t") or "")[:10]
        bar = _bar(raw)
        if bar and day and day <= through:
            out[day] = bar
    return out


async def _store(db, run_id: str, now: datetime, symbol: str, raw: dict, split: dict, total: dict) -> dict:
    """한 종목: 새 날짜만 추가(원시 불변), 계수는 받은 날짜 범위만 갱신. 연도 문서 단위."""
    inserted, revisions = 0, []
    operations = []
    for year, doc in _year_docs(symbol, raw, split, total).items():
        existing = await db[BARS].find_one({"_id": f"{symbol}:{year}"}, {"days": 1}) or {}
        stored = existing.get("days") or {}
        update = {"$setOnInsert": {"symbol": symbol, "year": year, "source": SOURCE, "created_run_id": run_id}, "$set": {}}
        for day, bar in doc["days"].items():
            if day not in stored:
                update["$set"][f"days.{day}"] = {**bar, "run": run_id}
                inserted += 1
            elif abs(stored[day]["close"] - bar["close"]) > 1e-6:
                revisions.append({"date": day, "stored_close": stored[day]["close"], "new_close": bar["close"]})
        for day, factor in doc["split"].items():
            update["$set"][f"split.{day}"] = factor
        for day, factor in doc["total"].items():
            update["$set"][f"total.{day}"] = factor
        update["$set"]["factors_run_id"] = run_id
        operations.append(UpdateOne({"_id": f"{symbol}:{year}"}, update, upsert=True))
    if operations:
        await db[BARS].bulk_write(operations, ordered=False)
    if raw:
        await db[SYMBOLS].update_one({"_id": symbol}, {"$min": {"first": min(raw)}, "$max": {"last": max(raw)},
                                                       "$set": {"updated_run_id": run_id, "updated_at": now}}, upsert=True)
    return {"inserted": inserted, "revisions": revisions}


async def _fetch_and_store(db, run, now, fetch, symbols: list[str], start: date, end: date, through: str,
                           batch_size: int = BATCH) -> dict:
    totals = {"inserted": 0, "revisions": [], "empty": []}
    for index in range(0, len(symbols), batch_size):
        batch = tuple(symbols[index:index + batch_size])
        raw = await asyncio.to_thread(fetch, batch, start, end, "raw")
        split = await asyncio.to_thread(fetch, batch, start, end, "split")
        total = await asyncio.to_thread(fetch, batch, start, end, "all")
        for symbol in batch:
            series = _series(raw.get(symbol), through)
            if not series:
                totals["empty"].append(symbol)
                continue
            result = await _store(db, run["run_id"], now, symbol, series, _series(split.get(symbol), through),
                                  _series(total.get(symbol), through))
            totals["inserted"] += result["inserted"]
            totals["revisions"].extend({"symbol": symbol, **item} for item in result["revisions"])
    return totals


async def _factor_changed(db, symbol: str, recent: dict[str, dict], raw: dict[str, dict]) -> bool:
    """최근 날짜의 분할·배당 계수가 저장값과 다르면(새 기업행위) 그 종목의 계수 전체를 다시 받는다."""
    for day in sorted(set(recent) & set(raw))[:3]:
        doc = await db[BARS].find_one({"_id": f"{symbol}:{day[:4]}"}, {f"total.{day}": 1})
        stored = ((doc or {}).get("total") or {}).get(day)
        if stored is not None and abs(stored - round(recent[day]["close"] / raw[day]["close"], 10)) > 1e-6:
            return True
    return False


def crosscheck_sample(symbols: list[str]) -> list[str]:
    """정책의 고정 규칙: 티커의 SHA-256 사전순 상위 20개."""
    return sorted(symbols, key=lambda s: hashlib.sha256(s.encode()).hexdigest())[:CROSSCHECK_SAMPLE]


async def _quality(db, membership: dict, through: str) -> dict:
    intervals = membership["intervals"]
    spans = {doc["_id"]: doc async for doc in db[SYMBOLS].find({}, {"first": 1, "last": 1})}
    gaps = []
    for row in intervals:
        span = spans.get(row["symbol"])
        end = row["end"] or through
        if not span or not span.get("first"):
            gaps.append({**{k: row[k] for k in ("symbol", "start", "end")}, "kind": "no_bars"})
            continue
        if span["first"] > (date.fromisoformat(row["start"]) + timedelta(days=GAP_TOLERANCE_DAYS)).isoformat():
            gaps.append({**{k: row[k] for k in ("symbol", "start", "end")}, "kind": "starts_late", "first_bar": span["first"]})
        last_needed = min(end, through)
        if span["last"] < (date.fromisoformat(last_needed) - timedelta(days=GAP_TOLERANCE_DAYS)).isoformat():
            gaps.append({**{k: row[k] for k in ("symbol", "start", "end")}, "kind": "ends_early", "last_bar": span["last"]})
    coverage = []
    for day in await asyncio.to_thread(universe.nyse_month_ends, universe.START, through):
        members = sorted(universe.members_on(intervals, day))
        have = await db[BARS].count_documents({"_id": {"$in": [f"{s}:{day[:4]}" for s in members]}, f"days.{day}": {"$exists": True}})
        coverage.append({"date": day, "members": len(members), "with_bar": have,
                         "pct": round(100 * have / len(members), 2) if members else None})
    low = [row for row in coverage if row["pct"] is not None and row["pct"] < 99]
    return {"history_gaps": gaps, "coverage": coverage, "coverage_review": [row["date"] for row in low],
            "coverage_min_pct": min((row["pct"] for row in coverage if row["pct"] is not None), default=None)}


async def collect_us_equity_data(*, now: datetime | None = None, fetch=fetch_alpaca_bars, crosscheck=_crosscheck,
                                 membership_loader=refresh_membership) -> dict:
    now = now or datetime.now(timezone.utc)
    db = get_db()
    run = {"run_id": uuid.uuid4().hex, "schema": SCHEMA, "source": SOURCE, "policy": universe.POLICY,
           "policy_sha256": policy_sha256(), "started_at": now, "status": "running", "errors": []}
    through = await asyncio.to_thread(last_completed_session, now)
    if through is None:
        run.update(status="skipped", reason="최근 완성된 NYSE 세션 없음", finished_at=datetime.now(timezone.utc))
        await db[RUNS].insert_one(run)
        return {"status": "skipped"}
    run["last_session"] = through
    try:
        membership = await membership_loader()
    except Exception as exc:
        run.update(status="failed", errors=[f"membership {type(exc).__name__}: {str(exc)[:160]}"], finished_at=datetime.now(timezone.utc))
        await db[RUNS].insert_one(run)
        logger.warning("미국 구성 이력 갱신 실패: %s", type(exc).__name__)
        return {"status": "failed", "run_id": run["run_id"]}
    run["membership_id"] = membership["_id"]
    run["membership"] = {"symbols": len(membership["symbols"]), "intervals": len(membership["intervals"]),
                         "anomalies": len(membership["anomalies"]), "review_months": membership["review_months"]}
    await db[BARS].create_index([("symbol", 1), ("year", 1)])
    symbols = membership["symbols"]
    known = {doc["_id"] async for doc in db[SYMBOLS].find({}, {"_id": 1})}
    new = [s for s in symbols if s not in known]
    end = query_end(through, now)   # 최근 15분 SIP 조회 403 방지(us_market_data.query_end)
    try:
        backfill = await _fetch_and_store(db, run, now, fetch, new, date.fromisoformat(universe.START), end, through, BACKFILL_BATCH)
        recent_start = date.fromisoformat(through) - timedelta(days=RECENT_DAYS)
        old = [s for s in symbols if s in known]
        recent = {"inserted": 0, "revisions": [], "empty": [], "refactored": []}
        for index in range(0, len(old), BATCH):
            batch = tuple(old[index:index + BATCH])
            raw = await asyncio.to_thread(fetch, batch, recent_start, end, "raw")
            total = await asyncio.to_thread(fetch, batch, recent_start, end, "all")
            for symbol in batch:
                series = _series(raw.get(symbol), through)
                if series and await _factor_changed(db, symbol, _series(total.get(symbol), through), series):
                    recent["refactored"].append(symbol)
            fresh = [s for s in batch if s not in recent["refactored"]]
            if fresh:
                part = await _fetch_and_store(db, run, now, fetch, fresh, recent_start, end, through)
                recent["inserted"] += part["inserted"]
                recent["revisions"].extend(part["revisions"])
                recent["empty"].extend(part["empty"])
        if recent["refactored"]:
            part = await _fetch_and_store(db, run, now, fetch, recent["refactored"], date.fromisoformat(universe.START), end, through,
                                          BACKFILL_BATCH)
            recent["inserted"] += part["inserted"]
            recent["revisions"].extend(part["revisions"])
    except Exception as exc:
        run.update(status="failed", errors=[f"bars {type(exc).__name__}: {str(exc)[:160]}"], finished_at=datetime.now(timezone.utc))
        await db[RUNS].insert_one(run)
        logger.warning("미국 개별주 일봉 수집 실패: %s", type(exc).__name__)
        return {"status": "failed", "run_id": run["run_id"]}
    # 백필 때 일봉이 없던 종목은 기록해 두고 이후 증분 조회만 한다(매일 2016년부터 다시 받지 않는다). 공백은 history_gap에 남는다.
    for symbol in backfill["empty"]:
        await db[SYMBOLS].update_one({"_id": symbol}, {"$set": {"no_bars_run_id": run["run_id"], "updated_at": now}}, upsert=True)
    run["backfill"] = {"symbols": len(new), "inserted": backfill["inserted"], "empty": backfill["empty"]}
    run["recent"] = {"symbols": len(symbols) - len(new), "inserted": recent["inserted"], "refactored": recent["refactored"],
                     "empty": recent["empty"][:50]}
    revisions = backfill["revisions"] + recent["revisions"]
    run["raw_revisions"] = len(revisions)
    run["raw_revision_samples"] = revisions[:20]
    run["quality"] = await _quality(db, membership, through)
    current = sorted(universe.members_on(membership["intervals"], through))
    sample = crosscheck_sample(current)
    checks = {}
    for symbol in sample:
        rows = {}
        for year in (int(through[:4]) - 1, int(through[:4])):     # 1월에도 최근 90일이 겹치도록 전년도 문서까지
            doc = await db[BARS].find_one({"_id": f"{symbol}:{year}"}, {"days": 1})
            rows.update((doc or {}).get("days") or {})
        checks[symbol] = await asyncio.to_thread(crosscheck, symbol, rows, through) if rows else {"status": "no_bars"}
    run["crosscheck"] = checks
    run["crosscheck_review"] = [s for s, c in checks.items() if c.get("status") == "review_required"]
    quality = run["quality"]
    clean = not (run["crosscheck_review"] or quality["coverage_review"] or membership["review_months"])
    run["status"] = "ok" if clean else "partial"
    run["finished_at"] = datetime.now(timezone.utc)
    await db[RUNS].insert_one(run)
    return {"status": run["status"], "run_id": run["run_id"], "last_session": through}


async def us_equity_status() -> dict:
    run = await get_db()[RUNS].find_one({}, {"_id": 0, "raw_revision_samples": 0}, sort=[("started_at", -1)])
    if not run:
        return {"available": False}
    quality = run.get("quality") or {}
    summary = {key: run.get(key) for key in ("run_id", "status", "last_session", "membership_id", "membership", "backfill",
                                              "recent", "raw_revisions", "crosscheck_review", "errors", "reason", "policy_sha256")}
    summary["quality"] = {"coverage_min_pct": quality.get("coverage_min_pct"), "coverage_review": quality.get("coverage_review"),
                          "history_gaps": len(quality.get("history_gaps") or []),
                          "history_gap_samples": (quality.get("history_gaps") or [])[:20]}
    summary["crosscheck"] = {s: (c or {}).get("status") for s, c in (run.get("crosscheck") or {}).items()}
    for key in ("started_at", "finished_at"):
        value = run.get(key)
        summary[key] = value.replace(tzinfo=timezone.utc).isoformat() if isinstance(value, datetime) else value
    return {"available": True, **summary}
