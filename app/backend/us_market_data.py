"""EINT-035: 미국 일봉 데이터 경로 1단계 — 섹터 ETF 일봉 수집과 미국 섹터 순환(RRG) 참고 자료.

한국 엔진의 시장 DB(KRX)와 섞지 않는 별도 경로다. Alpaca 시장데이터(SIP, 무료 플랜의 과거 일봉)를
1차 원천으로 쓰고 Yahoo 원시 종가로 교차검증한다.

- 원시(무조정) 일봉은 처음 받은 값을 그대로 고정한다($setOnInsert). 같은 날짜 값이 나중에 달라지면
  덮어쓰지 않고 실행 기록에 ``raw_revisions``로 남긴다(사후 수정 추적).
- 분할·배당 조정 일봉은 공급자가 과거를 다시 계산하므로 매 실행 갱신하고, 실행별 해시를 남긴다.
- 마감 후 20분이 지난 NYSE 정규장 세션만 저장한다(장중 미완성 일봉 차단).

섹터 순환 계산식은 한국 화면(EINT-021)과 같은 엔진 함수를 그대로 쓴다. 매매 신호·추천이 아니며
주문·원장·레버리지 DCA와 연결되지 않는다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from pymongo import UpdateOne

try:
    from kqs_collection.sector_rotation import LONG, MOMENTUM, SHORT, TAIL_WEEKS, WARMUP, quadrant, rrg_series, weekly_closes
except ModuleNotFoundError as exc:
    if exc.name != "kqs_collection":
        raise
    from engine.kqs.src.kqs_collection.sector_rotation import (  # type: ignore
        LONG, MOMENTUM, SHORT, TAIL_WEEKS, WARMUP, quadrant, rrg_series, weekly_closes,
    )

from .db import get_db
from .market_calendar import get_market_close_kst
from .market_data import YahooDailyProvider, verify_close_overlap

logger = logging.getLogger("jy_investing.us_market_data")

SCHEMA = "us-market-data/v1"
ROTATION_SCHEMA = "us-sector-rotation/v1"
BARS = "us_daily_bars"
RUNS = "us_market_runs"
ROTATIONS = "us_sector_rotation_snapshots"
ALPACA_DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
FEED = "sip"
SOURCE = "alpaca-sip"
# Alpaca SIP 일봉 시작일. SPY는 미국 연구(US-PB-v1)의 시장 게이트라 개별주(EINT-036)와 같은 기간이 필요하다.
HISTORY_START = date(2016, 1, 4)
CROSSCHECK_DAYS = 90
SETTLE_MINUTES = 20          # 정규장 마감 뒤 이 시간이 지나야 그 세션을 완성된 일봉으로 본다
NY = ZoneInfo("America/New_York")
QUADRANTS = {"leading": "주도", "weakening": "약화", "lagging": "부진", "improving": "개선"}
BENCHMARK = ("SPY", "S&P 500 (SPY)")
SECTORS = (          # (key, 표시 이름, ETF) — S&P 500 GICS 11개 섹터 SPDR + 반도체 산업 ETF
    ("tech", "기술", "XLK"),
    ("comm", "커뮤니케이션", "XLC"),
    ("discretionary", "경기소비재", "XLY"),
    ("staples", "필수소비재", "XLP"),
    ("health", "헬스케어", "XLV"),
    ("financials", "금융", "XLF"),
    ("industrials", "산업재", "XLI"),
    ("energy", "에너지", "XLE"),
    ("materials", "소재", "XLB"),
    ("utilities", "유틸리티", "XLU"),
    ("real_estate", "부동산", "XLRE"),
    ("semis", "반도체(산업)", "SMH"),
)
ETF_NAMES = {
    "SPY": "SPDR S&P 500 ETF", "XLK": "Technology Select Sector SPDR", "XLC": "Communication Services Select Sector SPDR",
    "XLY": "Consumer Discretionary Select Sector SPDR", "XLP": "Consumer Staples Select Sector SPDR",
    "XLV": "Health Care Select Sector SPDR", "XLF": "Financial Select Sector SPDR", "XLI": "Industrial Select Sector SPDR",
    "XLE": "Energy Select Sector SPDR", "XLB": "Materials Select Sector SPDR", "XLU": "Utilities Select Sector SPDR",
    "XLRE": "Real Estate Select Sector SPDR", "SMH": "VanEck Semiconductor ETF",
}
SYMBOLS = (BENCHMARK[0], *(symbol for _, _, symbol in SECTORS))


class UsMarketDataError(RuntimeError):
    """미국 일봉을 받거나 계산하지 못했을 때."""


def _headers() -> dict:
    key, secret = os.getenv("ALPACA_PAPER_API_KEY", ""), os.getenv("ALPACA_PAPER_API_SECRET", "")
    if not (key and secret):
        raise UsMarketDataError("Alpaca 키가 없어 미국 일봉을 받지 않았습니다.")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def query_end(through: str, now: datetime) -> datetime:
    """조회 끝 시각: 마지막 완성 세션 다음 날 0시(UTC)와 '지금 − 20분' 중 이른 쪽.

    무료 플랜은 최근 15분 SIP 조회를 403으로 막는다. 아침 자동 수집 때는 다음 날 0시가 아직 미래라
    2026-10-12~14 자동 실행이 모두 403으로 실패했다(손으로 돌린 첫 수집은 그 시각이 지나 성공).
    """
    next_day = datetime.combine(date.fromisoformat(through) + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return min(next_day, now.astimezone(timezone.utc) - timedelta(minutes=20))


def _alpaca_time(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value.isoformat()


def fetch_alpaca_bars(symbols: tuple[str, ...], start: date, end: date | datetime, adjustment: str) -> dict[str, list[dict]]:
    """Alpaca 일봉(여러 종목, 페이지 끝까지). 키 값은 오류 문구에도 넣지 않는다."""
    params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start.isoformat(), "end": _alpaca_time(end),
              "adjustment": adjustment, "feed": FEED, "limit": 10000}
    out: dict[str, list[dict]] = {}
    headers = _headers()
    for _ in range(50):
        response = requests.get(ALPACA_DATA_URL, headers=headers, params=params, timeout=20)
        if response.status_code >= 400:
            raise UsMarketDataError(f"Alpaca 시장데이터 오류 {response.status_code}")
        body = response.json()
        for symbol, bars in (body.get("bars") or {}).items():
            out.setdefault(symbol, []).extend(bars)
        token = body.get("next_page_token")
        if not token:
            return out
        params["page_token"] = token
    raise UsMarketDataError("Alpaca 페이지가 너무 많습니다.")


def last_completed_session(now: datetime) -> str | None:
    """now 기준으로 마감 후 SETTLE_MINUTES가 지난 가장 최근 NYSE 정규장 날짜(뉴욕 날짜)."""
    ny_today = now.astimezone(NY).date()
    for back in range(0, 10):
        day = ny_today - timedelta(days=back)
        close = get_market_close_kst(day)
        if close is not None and close + timedelta(minutes=SETTLE_MINUTES) <= now:
            return day.isoformat()
    return None


def _bar(raw: dict) -> dict | None:
    try:
        values = {"open": float(raw["o"]), "high": float(raw["h"]), "low": float(raw["l"]), "close": float(raw["c"]),
                  "volume": int(raw.get("v") or 0)}
    except (KeyError, TypeError, ValueError):
        return None
    if min(values["open"], values["high"], values["low"], values["close"]) <= 0 or values["high"] < values["low"]:
        return None
    return values


def _normalize(series: dict[str, list[dict]], through: str) -> dict[str, dict[str, dict]]:
    """{symbol: {date: bar}} — 완성 세션까지만, 형식 이상 행은 버린다."""
    out: dict[str, dict[str, dict]] = {}
    for symbol, bars in series.items():
        for raw in bars:
            day = str(raw.get("t") or "")[:10]
            bar = _bar(raw)
            if bar and day and day <= through:
                out.setdefault(symbol, {})[day] = bar
    return out


def _sha(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _crosscheck(symbol: str, raw: dict[str, dict], through: str) -> dict:
    """Yahoo 무조정 종가와 같은 날짜 종가 비교(최근 CROSSCHECK_DAYS). 실패해도 수집은 막지 않는다."""
    end = date.fromisoformat(through) + timedelta(days=1)
    try:
        # Yahoo는 클래스 주식 구분자로 '-'를 쓴다(BRK.B -> BRK-B).
        reference = YahooDailyProvider().fetch_daily(symbol.replace(".", "-"), end - timedelta(days=CROSSCHECK_DAYS), end,
                                                     adjusted=False).bars
    except Exception as exc:   # 외부 공급자 장애는 교차검증 불가로만 기록한다
        return {"status": "unavailable", "error_type": type(exc).__name__}
    since = (end - timedelta(days=CROSSCHECK_DAYS)).isoformat()
    primary = [{"timestamp": day, "close": bar["close"]} for day, bar in raw.items() if day >= since]
    return verify_close_overlap(primary, reference)


async def collect_us_market_data(*, now: datetime | None = None, fetch=fetch_alpaca_bars, crosscheck=_crosscheck) -> dict:
    """완성된 세션의 미국 일봉을 저장하고 섹터 순환 스냅샷을 갱신한다. 한 번의 실행 = 한 개의 실행 기록."""
    now = now or datetime.now(timezone.utc)
    db = get_db()
    run = {"run_id": uuid.uuid4().hex, "schema": SCHEMA, "source": SOURCE, "started_at": now, "status": "running",
           "symbols": {}, "errors": []}
    through = await asyncio.to_thread(last_completed_session, now)
    if through is None:
        run.update(status="skipped", reason="최근 완성된 NYSE 세션 없음", finished_at=datetime.now(timezone.utc))
        await db[RUNS].insert_one(run)
        return {"status": "skipped"}
    run["last_session"] = through
    start = HISTORY_START
    end = query_end(through, now)
    try:
        raw = _normalize(await asyncio.to_thread(fetch, SYMBOLS, start, end, "raw"), through)
        adjusted = _normalize(await asyncio.to_thread(fetch, SYMBOLS, start, end, "all"), through)
    except Exception as exc:
        run.update(status="failed", errors=[f"{type(exc).__name__}: {str(exc)[:160]}"], finished_at=datetime.now(timezone.utc))
        await db[RUNS].insert_one(run)
        logger.warning("미국 일봉 수집 실패: %s", type(exc).__name__)
        return {"status": "failed", "run_id": run["run_id"]}

    await db[BARS].create_index([("symbol", 1), ("date", 1)], unique=True)
    for symbol in SYMBOLS:
        rows, adj_rows = raw.get(symbol, {}), adjusted.get(symbol, {})
        if not rows:
            run["errors"].append(f"{symbol}: 일봉 없음")
            run["symbols"][symbol] = {"bars": 0}
            continue
        existing = {doc["date"]: doc["raw"] async for doc in db[BARS].find({"symbol": symbol}, {"date": 1, "raw": 1})}
        revisions = [{"date": day, "stored_close": existing[day]["close"], "new_close": bar["close"]}
                     for day, bar in rows.items() if day in existing and abs(existing[day]["close"] - bar["close"]) > 1e-6]
        operations = [UpdateOne(
            {"_id": f"{symbol}:{day}"},
            {"$setOnInsert": {"symbol": symbol, "date": day, "raw": bar, "source": SOURCE, "first_seen_at": now,
                              "first_run_id": run["run_id"]},
             "$set": {"adj": adj_rows.get(day), "adj_run_id": run["run_id"]}},
            upsert=True) for day, bar in sorted(rows.items())]
        result = await db[BARS].bulk_write(operations, ordered=False)
        run["symbols"][symbol] = {
            "bars": len(rows), "first": min(rows), "last": max(rows), "inserted": result.upserted_count,
            "missing_adjusted": sum(1 for day in rows if day not in adj_rows), "raw_revisions": len(revisions),
            "raw_revision_samples": revisions[:5],
            "crosscheck": await asyncio.to_thread(crosscheck, symbol, rows, through),
        }
    run["adjusted_sha256"] = _sha({symbol: {day: bar["close"] for day, bar in sorted(adjusted.get(symbol, {}).items())}
                                   for symbol in SYMBOLS})
    stale = [symbol for symbol, info in run["symbols"].items() if info.get("last") != through]
    run["stale_symbols"] = stale
    try:
        rotation = build_us_sector_rotation({symbol: {day: bar["close"] for day, bar in adjusted.get(symbol, {}).items()}
                                             for symbol in SYMBOLS}, through)
        rotation.update(run_id=run["run_id"], generated_at=datetime.now(timezone.utc), adjusted_sha256=run["adjusted_sha256"])
        await db[ROTATIONS].update_one({"_id": through}, {"$set": rotation}, upsert=True)
        run["rotation"] = {"status": "ok", "sectors": len(rotation["sectors"]), "skipped": len(rotation["skipped"])}
    except UsMarketDataError as exc:
        run["rotation"] = {"status": "failed", "reason": str(exc)}
    reviews = [symbol for symbol, info in run["symbols"].items()
               if (info.get("crosscheck") or {}).get("status") == "review_required"]
    run["crosscheck_review"] = reviews
    run["status"] = "ok" if not (run["errors"] or stale or reviews) and run["rotation"]["status"] == "ok" else "partial"
    run["finished_at"] = datetime.now(timezone.utc)
    await db[RUNS].insert_one(run)
    return {"status": run["status"], "run_id": run["run_id"], "last_session": through}


def _pct(series: list[float], weeks: int) -> float | None:
    if len(series) <= weeks or series[-1 - weeks] <= 0:
        return None
    return round((series[-1] / series[-1 - weeks] - 1) * 100, 2)


def build_us_sector_rotation(closes: dict[str, dict[str, float]], as_of: str) -> dict:
    """조정 종가로 한국 화면과 같은 RRG를 만든다(주간 종가, EMA 4/13/4주)."""
    bench = weekly_closes(sorted(closes.get(BENCHMARK[0], {}).items()))
    if len(bench) < WARMUP + 2 or max(bench) != as_of:
        raise UsMarketDataError("SPY 주간 이력이 짧거나 기준일 일봉이 없습니다.")
    weeks = sorted(bench)
    sectors, skipped = [], []
    for key, label, symbol in SECTORS:
        series = weekly_closes(sorted(closes.get(symbol, {}).items()))
        common = [day for day in weeks if day in series]
        if not series or max(series) != weeks[-1]:
            skipped.append({"key": key, "name": label, "symbol": symbol, "reason": "기준일 시세 없음"})
            continue
        if len(common) < WARMUP + 2:
            skipped.append({"key": key, "name": label, "symbol": symbol, "reason": "주간 시세 이력 부족"})
            continue
        sector_series = [series[day] for day in common]
        bench_series = [bench[day] for day in common]
        trail = [{"week": day, "rs_ratio": point[0], "rs_momentum": point[1]}
                 for day, point in zip(common, rrg_series(sector_series, bench_series)) if point is not None][-TAIL_WEEKS:]
        if len(trail) < 2:
            skipped.append({"key": key, "name": label, "symbol": symbol, "reason": "지표 계산 구간 부족"})
            continue
        now, before = trail[-1], trail[-5] if len(trail) >= 5 else trail[0]
        relative = [100.0 * s / b for s, b in zip(sector_series, bench_series)]
        sectors.append({
            "key": key, "name": label, "symbol": symbol, "etf_name": ETF_NAMES.get(symbol, ""),
            "quadrant": quadrant(now["rs_ratio"], now["rs_momentum"]),
            "quadrant_4w_ago": quadrant(before["rs_ratio"], before["rs_momentum"]),
            "rs_ratio": now["rs_ratio"], "rs_momentum": now["rs_momentum"],
            "return_4w_pct": _pct(sector_series, 4), "return_12w_pct": _pct(sector_series, 12),
            "relative_4w_pct": _pct(relative, 4), "relative_12w_pct": _pct(relative, 12),
            "trail": trail,
        })
    if not sectors:
        raise UsMarketDataError("이력이 충분한 섹터 ETF가 없습니다.")
    return {"schema": ROTATION_SCHEMA, "as_of": as_of, "latest_week": weeks[-1],
            "benchmark": {"symbol": BENCHMARK[0], "name": BENCHMARK[1]},
            "smoothing_weeks": {"short": SHORT, "long": LONG, "momentum": MOMENTUM}, "tail_weeks": TAIL_WEEKS,
            "quadrant_labels": QUADRANTS, "sectors": sectors, "skipped": skipped, "items_sha256": _sha(sectors)}


async def read_us_sector_rotation() -> dict:
    """웹은 저장된 최신 스냅샷만 읽는다(GET이 Alpaca를 부르지 않는다)."""
    doc = await get_db()[ROTATIONS].find_one({"schema": ROTATION_SCHEMA}, sort=[("_id", -1)])
    if not doc:
        return {"available": False, "detail": "미국 섹터 순환 자료가 아직 없습니다. 미국 장 마감 뒤 첫 수집(KST 07:30) 후 생성됩니다."}
    keys = ("as_of", "latest_week", "benchmark", "smoothing_weeks", "tail_weeks", "quadrant_labels", "sectors", "skipped")
    generated = doc.get("generated_at")
    return {
        "available": True, "market": "us", **{key: doc[key] for key in keys},
        "generated_at": generated.replace(tzinfo=timezone.utc).isoformat() if isinstance(generated, datetime) else generated,
        "source": "Alpaca 시장데이터(SIP) 분할·배당 조정 일봉, Yahoo 원시 종가 교차검증",
        "disclaimer": ("미국 섹터 ETF(SPDR 11개 섹터와 반도체 SMH)를 SPY와 비교한 주간 상대강도 그림(RRG)입니다. 지금 어느 섹터가 "
                       "시장보다 강하고 그 힘이 커지는지 보여 주는 참고 자료이며, 매수·매도 신호나 미래 수익 예측이 아닙니다."),
    }


async def us_market_data_status() -> dict:
    """운영 확인용: 최근 실행 기록 요약(인증 사용자 전용 API에서 사용)."""
    run = await get_db()[RUNS].find_one({}, {"_id": 0}, sort=[("started_at", -1)])
    if not run:
        return {"available": False}
    summary = {key: run.get(key) for key in ("run_id", "status", "last_session", "stale_symbols", "crosscheck_review",
                                              "adjusted_sha256", "rotation", "errors", "reason")}
    summary["symbols"] = {symbol: {k: v for k, v in info.items() if k != "raw_revision_samples"}
                          for symbol, info in (run.get("symbols") or {}).items()}
    for key in ("started_at", "finished_at"):
        value = run.get(key)
        summary[key] = value.replace(tzinfo=timezone.utc).isoformat() if isinstance(value, datetime) else value
    return {"available": True, **summary}
