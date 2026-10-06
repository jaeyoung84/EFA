"""버전·출처·품질 정보를 보존하는 일봉 OHLCV 데이터 계층.

화면이 공급자 라이브러리를 직접 호출하지 않도록 Yahoo와 선택적 Stooq 공급자를
분리한다. 정상 수집 데이터는 MongoDB에 upsert하고, 외부 공급자가 실패하면 마지막
검증 통과 데이터를 명시적으로 ``cached`` 출처로 반환한다.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
from time import monotonic
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from pymongo import ASCENDING, DESCENDING, UpdateOne

try:
    from .db import get_db
    from .factor_engine import FACTOR_VERSION, calculate_daily_factors, rank_factor_universe
except ImportError:
    from db import get_db  # type: ignore
    from factor_engine import FACTOR_VERSION, calculate_daily_factors, rank_factor_universe  # type: ignore


DAILY_INTERVAL = "1d"
SCHEMA_VERSION = 1
MAX_FETCH_DAYS = 3650
CORE_MARKET_TICKERS = ("^KS11", "^KQ11", "KRW=X")
OPERATIONS_TIMEZONE = ZoneInfo("Asia/Seoul")


class MarketDataError(RuntimeError):
    """모든 공급자가 가격 데이터를 반환하지 못했을 때 사용한다."""


@dataclass(frozen=True)
class ProviderResult:
    source: str
    source_version: str
    bars: list[dict]
    actions: list[dict] | None = None


class DailyProvider(Protocol):
    name: str

    def fetch_daily(self, ticker: str, start: date, end: date, adjusted: bool) -> ProviderResult: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """Motor가 반환하는 timezone 없는 BSON UTC 값을 명시적 UTC로 정규화한다."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _as_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _review_rejection_pct() -> float:
    """보조 공급자 교차검증을 시작할 거부 봉 비율(0.1~100%)."""
    try:
        return min(100.0, max(0.1, float(os.getenv("MARKET_DATA_REVIEW_REJECTION_PCT", "1"))))
    except ValueError:
        return 1.0


def assess_ingestion_quality(received_count: int, rejected: list[dict]) -> dict:
    """수집 단계의 거부 비율을 판정해 교차검증 필요 여부를 반환한다."""
    rejected_count = len(rejected)
    rejection_rate_pct = round(rejected_count / max(received_count, 1) * 100, 4)
    requires_secondary = rejected_count > 0 and rejection_rate_pct > _review_rejection_pct()
    return {
        "status": "review_required" if requires_secondary else "passed_with_minor_rejections" if rejected_count else "passed",
        "rejected": rejected_count,
        "rejection_rate_pct": rejection_rate_pct,
        "secondary_verification_required": requires_secondary,
    }


# Yahoo 환율(=X) 일봉은 종가·시가가 고가·저가 범위를 조금 벗어난 채 오는 경우가 잦다(2026-10-06 KRW=X 1년치
# 260봉 중 39봉, 최대 0.5%). 이 정도는 범위를 시가·종가까지 넓혀 받아들이고, 그보다 크면 그대로 기각한다.
FX_RANGE_REPAIR_MAX_PCT = 1.0


def _repairable_range(values: dict) -> bool:
    body_high, body_low = max(values["open"], values["close"]), min(values["open"], values["close"])
    overshoot = max(body_high - values["high"], values["low"] - body_low, 0.0)
    return overshoot <= body_low * FX_RANGE_REPAIR_MAX_PCT / 100


def validate_daily_bars(bars: list[dict], *, repair_fx_range: bool = False) -> tuple[list[dict], list[dict]]:
    """일봉의 필수값·OHLC 관계·중복 시각을 검증한다. repair_fx_range면 환율의 작은 범위 어긋남을 고친다."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    for raw in bars:
        timestamp = str(raw.get("timestamp", ""))[:10]
        try:
            date.fromisoformat(timestamp)
        except ValueError:
            rejected.append({"reason": "invalid_timestamp", "bar": raw})
            continue
        values = {key: _as_number(raw.get(key)) for key in ("open", "high", "low", "close")}
        volume = _as_number(raw.get("volume"))
        if timestamp in seen:
            rejected.append({"reason": "duplicate_timestamp", "bar": raw})
        elif any(value is None or value <= 0 for value in values.values()):
            rejected.append({"reason": "missing_or_nonpositive_ohlc", "bar": raw})
        elif (values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"])) \
                and not (repair_fx_range and _repairable_range(values)):
            rejected.append({"reason": "invalid_ohlc_range", "bar": raw})
        elif volume is not None and volume < 0:
            rejected.append({"reason": "negative_volume", "bar": raw})
        else:
            seen.add(timestamp)
            values["high"] = max(values["high"], values["open"], values["close"])
            values["low"] = min(values["low"], values["open"], values["close"])
            accepted.append({
                "timestamp": timestamp,
                "open": round(values["open"], 8), "high": round(values["high"], 8),
                "low": round(values["low"], 8), "close": round(values["close"], 8),
                "volume": int(volume or 0),
            })
    accepted.sort(key=lambda item: item["timestamp"])
    return accepted, rejected


def inspect_daily_quality(bars: list[dict], *, as_of: date | None = None, max_stale_business_days: int = 5, extreme_return_pct: float = 30.0) -> dict:
    """일봉의 최신성·평일 공백·급격한 수익률을 관측한다.

    거래소 휴일은 이 함수가 알 수 없으므로 평일 공백은 점검 후보일 뿐 오류로 단정하지 않는다.
    """
    if not bars:
        return {"status": "critical", "bar_count": 0, "findings": [{"code": "no_bars", "severity": "critical", "message": "검증할 일봉 데이터가 없습니다."}]}
    ordered = sorted(bars, key=lambda item: item["timestamp"])
    findings: list[dict] = []
    latest, today = date.fromisoformat(ordered[-1]["timestamp"]), (as_of or date.today())
    stale_days, cursor = 0, latest
    while cursor < today:
        cursor += timedelta(days=1)
        stale_days += int(cursor.weekday() < 5)
    if stale_days > max_stale_business_days:
        findings.append({"code": "stale_data", "severity": "warning", "message": f"최근 일봉이 {stale_days} 영업일 전 데이터입니다.", "business_days": stale_days})
    gap_count = 0
    extreme = []
    for previous, current in zip(ordered, ordered[1:]):
        start, end = date.fromisoformat(previous["timestamp"]), date.fromisoformat(current["timestamp"])
        probe = start + timedelta(days=1)
        while probe < end:
            gap_count += int(probe.weekday() < 5)
            probe += timedelta(days=1)
        before, after = float(previous["close"]), float(current["close"])
        change = (after / before - 1) * 100 if before > 0 else 0.0
        if abs(change) >= extreme_return_pct:
            extreme.append({"timestamp": current["timestamp"], "return_pct": round(change, 4)})
    if gap_count:
        findings.append({"code": "weekday_gaps", "severity": "info", "message": f"평일 데이터 공백 {gap_count}일이 있습니다(거래소 휴일 여부 확인 필요).", "gap_count": gap_count})
    if extreme:
        findings.append({"code": "extreme_return", "severity": "warning", "message": f"절대 일간 수익률 {extreme_return_pct:g}% 이상 구간이 있습니다(분할·배당 포함 확인 필요).", "events": extreme[:20]})
    severity_rank = {"info": 1, "warning": 2, "critical": 3}
    maximum = max((severity_rank[item["severity"]] for item in findings), default=0)
    return {"status": {0: "passed", 1: "review", 2: "warning", 3: "critical"}[maximum], "bar_count": len(ordered), "latest_timestamp": latest.isoformat(), "findings": findings}


class YahooDailyProvider:
    name = "yahoo"

    def fetch_daily(self, ticker: str, start: date, end: date, adjusted: bool) -> ProviderResult:
        import yfinance as yf

        frame = yf.download(
            ticker, start=start.isoformat(), end=end.isoformat(), interval=DAILY_INTERVAL,
            auto_adjust=adjusted, actions=True, progress=False, threads=False,
        )
        if frame.empty:
            raise MarketDataError(f"Yahoo가 {ticker} 일봉을 반환하지 않았습니다.")
        if hasattr(frame.columns, "nlevels") and frame.columns.nlevels > 1:
            frame.columns = frame.columns.get_level_values(0)
        bars, actions = [], []
        for index, row in frame.iterrows():
            timestamp = str(index)[:10]
            bars.append({
                "timestamp": timestamp, "open": row.get("Open"), "high": row.get("High"),
                "low": row.get("Low"), "close": row.get("Close"), "volume": row.get("Volume"),
            })
            dividend = _as_number(row.get("Dividends")) or 0.0
            split = _as_number(row.get("Stock Splits")) or 0.0
            if dividend:
                actions.append({"effective_date": timestamp, "action_type": "dividend", "value": dividend})
            if split and split != 1.0:
                actions.append({"effective_date": timestamp, "action_type": "split", "value": split})
        return ProviderResult(self.name, "yfinance", bars, actions)


class StooqDailyProvider:
    """Stooq CSV의 일봉 대체 공급자. 종목 매핑은 ST00Q_SYMBOL_MAP에서 관리한다."""
    name = "stooq"

    def _symbol(self, ticker: str) -> str:
        mapping = os.getenv("STOOQ_SYMBOL_MAP", "")
        pairs = (part.split("=", 1) for part in mapping.split(",") if "=" in part)
        configured = {left.strip().upper(): right.strip().lower() for left, right in pairs}
        if ticker.upper() in configured:
            return configured[ticker.upper()]
        # 미국 일반 주식만 보수적으로 기본 변환한다. 한국 지수는 명시적 매핑이 필요하다.
        if ticker.replace("-", "").isalnum() and "." not in ticker and "^" not in ticker and "=" not in ticker:
            return f"{ticker.lower()}.us"
        raise MarketDataError(f"Stooq 심볼 매핑이 없습니다: {ticker}")

    def fetch_daily(self, ticker: str, start: date, end: date, adjusted: bool) -> ProviderResult:
        symbol = self._symbol(ticker)
        params = urllib.parse.urlencode({"s": symbol, "i": "d", "d1": start.strftime("%Y%m%d"), "d2": end.strftime("%Y%m%d")})
        request = urllib.request.Request(f"https://stooq.com/q/d/l/?{params}", headers={"User-Agent": "JY-Investing/2.0"})
        with urllib.request.urlopen(request, timeout=12) as response:
            text = response.read().decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows or "No data" in text:
            raise MarketDataError(f"Stooq가 {ticker} 일봉을 반환하지 않았습니다.")
        return ProviderResult(self.name, "stooq-csv", [
            {"timestamp": row.get("Date"), "open": row.get("Open"), "high": row.get("High"),
             "low": row.get("Low"), "close": row.get("Close"), "volume": row.get("Volume")}
            for row in rows
        ])


class FredKrwDailyProvider:
    """Fed H.10의 KRW/USD 일일 fixing을 Yahoo 데이터의 독립 교차검증에 사용한다."""
    name = "fred"
    verification_only = True

    def _series(self, ticker: str) -> str:
        pairs = (part.rsplit("=", 1) for part in os.getenv("FRED_SERIES_MAP", "KRW=X=DEXKOUS").split(",") if "=" in part)
        mapping = {left.strip().upper(): right.strip().upper() for left, right in pairs}
        series = mapping.get(ticker.upper())
        if not series:
            raise MarketDataError(f"FRED 시리즈 매핑이 없습니다: {ticker}")
        return series

    def fetch_daily(self, ticker: str, start: date, end: date, adjusted: bool) -> ProviderResult:
        series = self._series(ticker)
        params = urllib.parse.urlencode({"id": series, "cos": start.isoformat(), "coe": end.isoformat()})
        request = urllib.request.Request(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?{params}", headers={"User-Agent": "JY-Investing/2.0"},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
        bars: list[dict] = []
        for row in rows:
            timestamp = str(row.get("observation_date", ""))[:10]
            value = _as_number(row.get(series))
            if value is None or value <= 0 or not (start.isoformat() <= timestamp < end.isoformat()):
                continue
            # H.10은 일중 OHLC가 아닌 New York noon fixing이다. 범위 정보가 없는
            # 사실을 보존하기 위해 flat bar와 bar_kind를 명시하고 교차검증 전용으로 쓴다.
            bars.append({"timestamp": timestamp, "open": value, "high": value, "low": value, "close": value,
                         "volume": 0, "bar_kind": "daily_fixing"})
        if not bars:
            raise MarketDataError(f"FRED가 {ticker} 일일 fixing을 반환하지 않았습니다.")
        return ProviderResult(self.name, f"fred-{series}", bars)


def _provider_chain() -> list[DailyProvider]:
    available: dict[str, DailyProvider] = {"yahoo": YahooDailyProvider(), "stooq": StooqDailyProvider(), "fred": FredKrwDailyProvider()}
    names = [name.strip().lower() for name in os.getenv("MARKET_DATA_PROVIDERS", "yahoo,stooq,fred").split(",")]
    return [available[name] for name in names if name in available]


def _crosscheck_max_deviation_pct() -> float:
    try:
        return min(20.0, max(0.1, float(os.getenv("FX_CROSSCHECK_MAX_DEVIATION_PCT", "3"))))
    except ValueError:
        return 3.0


def verify_close_overlap(primary_bars: list[dict], reference_bars: list[dict]) -> dict:
    """두 독립 소스의 같은 날짜 종가를 비교한다. fixing/종가의 차이는 보수적으로 허용한다."""
    reference = {str(bar["timestamp"])[:10]: float(bar["close"]) for bar in reference_bars if _as_number(bar.get("close"))}
    deviations = [abs(float(bar["close"]) / reference[str(bar["timestamp"])[:10]] - 1) * 100
                  for bar in primary_bars if str(bar["timestamp"])[:10] in reference and reference[str(bar["timestamp"])[:10]] > 0]
    if not deviations:
        return {"status": "insufficient_overlap", "overlap_count": 0, "max_deviation_pct": None, "verified": False}
    maximum = round(max(deviations), 4)
    median = round(sorted(deviations)[len(deviations) // 2], 4)
    return {"status": "verified" if len(deviations) >= 20 and maximum <= _crosscheck_max_deviation_pct() else "review_required",
            "overlap_count": len(deviations), "max_deviation_pct": maximum, "median_deviation_pct": median,
            "verified": len(deviations) >= 20 and maximum <= _crosscheck_max_deviation_pct()}


def _provider_supports_ticker(provider: DailyProvider, ticker: str) -> bool:
    """네트워크 요청 전에 명시적 심볼 매핑이 있는지 확인한다."""
    try:
        if isinstance(provider, StooqDailyProvider):
            provider._symbol(ticker)
        elif isinstance(provider, FredKrwDailyProvider):
            provider._series(ticker)
    except MarketDataError:
        return False
    return True


def _provider_backoff_minutes(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


async def record_provider_attempt_outcome(provider: str, status: str, error: str = "") -> None:
    """실제 공급자 오류에는 지수형 재시도 대기 시간을 적용한다."""
    if status == "not_applicable":
        return
    db = get_db()
    now = _utcnow()
    if status == "error":
        previous = await db.market_provider_backoff.find_one({"provider": provider}) or {}
        streak = int(previous.get("failure_streak", 0)) + 1
        base = _provider_backoff_minutes("MARKET_DATA_PROVIDER_BACKOFF_BASE_MINUTES", 5, 1, 1440)
        maximum = _provider_backoff_minutes("MARKET_DATA_PROVIDER_BACKOFF_MAX_MINUTES", 120, base, 10_080)
        delay_minutes = min(maximum, base * (2 ** min(streak - 1, 12)))
        await db.market_provider_backoff.update_one(
            {"provider": provider},
            {"$set": {"provider": provider, "failure_streak": streak, "last_status": status,
                      "last_error": error[:300], "last_attempt_at": now, "retry_after": now + timedelta(minutes=delay_minutes),
                      "updated_at": now}, "$setOnInsert": {"created_at": now}}, upsert=True,
        )
        return
    if status in {"accepted", "verified"}:
        await db.market_provider_backoff.update_one(
            {"provider": provider},
            {"$set": {"provider": provider, "failure_streak": 0, "last_status": status, "last_error": "",
                      "last_attempt_at": now, "retry_after": now, "updated_at": now}, "$setOnInsert": {"created_at": now}}, upsert=True,
        )


async def provider_routing_plan(ticker: str) -> tuple[list[DailyProvider], list[dict]]:
    """호환성·현재 backoff·최근 품질/응답 시간을 반영한 공급자 시도 순서를 만든다."""
    now = _utcnow()
    configured = _provider_chain()
    db = get_db()
    snapshot = await db.market_provider_health_snapshots.find_one({}, {"_id": 0}, sort=[("captured_at", -1)])
    health_by_provider = {row.get("provider"): row for row in (snapshot or {}).get("providers", [])}
    names = [provider.name for provider in configured]
    backoffs = await db.market_provider_backoff.find({"provider": {"$in": names}}, {"_id": 0}).to_list(len(names))
    backoff_by_provider = {row["provider"]: row for row in backoffs}
    eligible: list[tuple[int, int, int, float, int, DailyProvider]] = []
    events: list[dict] = []
    for index, provider in enumerate(configured):
        if not _provider_supports_ticker(provider, ticker):
            events.append({"provider": provider.name, "action": "not_applicable_skip"})
            continue
        backoff = backoff_by_provider.get(provider.name, {})
        retry_after = _as_utc(backoff.get("retry_after"))
        if retry_after and retry_after > now:
            events.append({"provider": provider.name, "action": "cooldown_skip", "retry_after": retry_after,
                           "failure_streak": backoff.get("failure_streak", 0)})
            continue
        health = health_by_provider.get(provider.name, {})
        # 품질 저하 공급자는 건강한 공급자 뒤로, 교차검증 전용 공급자는 일반 가격
        # 공급자 뒤로 둔다. 나머지는 관측된 평균 응답 시간이 짧은 순으로 시도한다.
        latency = _as_number(health.get("average_latency_ms"))
        eligible.append((int(health.get("status") == "degraded"),
                         int(bool(getattr(provider, "verification_only", False))),
                         int(health.get("performance_status") == "slow"),
                         latency if latency is not None else float("inf"), index, provider))
    eligible.sort(key=lambda item: item[:5])
    for priority, item in enumerate(eligible, start=1):
        latency = item[3]
        events.append({"provider": item[5].name, "action": "priority", "priority": priority,
                       "basis": "quality_and_latency",
                       "average_latency_ms": None if latency == float("inf") else round(latency, 2)})
    return [item[5] for item in eligible], events


async def market_provider_routing_state() -> dict:
    """운영 화면용 현재 공급자 cooldown 상태다."""
    now = _utcnow()
    rows = await get_db().market_provider_backoff.find({}, {"_id": 0}).sort("provider", ASCENDING).to_list(100)
    for row in rows:
        retry_after = _as_utc(row.get("retry_after"))
        row["retry_after"] = retry_after
        row["cooling_down"] = bool(retry_after and retry_after > now)
    return {"generated_at": now.isoformat(), "providers": rows,
            "cooling_down_providers": [row["provider"] for row in rows if row["cooling_down"]]}


async def ensure_market_data_indexes() -> None:
    db = get_db()
    await db.market_ohlcv.create_index(
        [("ticker", ASCENDING), ("interval", ASCENDING), ("timestamp", ASCENDING)], unique=True,
        name="ticker_interval_timestamp_unique",
    )
    await db.market_ohlcv.create_index([("ticker", ASCENDING), ("interval", ASCENDING), ("timestamp", DESCENDING)])
    await db.market_corporate_actions.create_index(
        [("ticker", ASCENDING), ("effective_date", ASCENDING), ("action_type", ASCENDING), ("source", ASCENDING)],
        unique=True, name="ticker_action_source_unique",
    )
    await db.market_data_runs.create_index([("started_at", DESCENDING)])
    await db.market_data_runs.create_index([("ticker", ASCENDING), ("finished_at", DESCENDING)])
    await db.market_data_refresh_jobs.create_index([("started_at", DESCENDING)])
    await db.market_data_refresh_jobs.create_index([("job_id", ASCENDING)], unique=True)
    await db.market_data_alerts.create_index([("condition_active", ASCENDING), ("updated_at", DESCENDING)])
    await db.market_data_alerts.create_index([("fingerprint", ASCENDING), ("condition_active", ASCENDING)])
    await db.market_provider_health_snapshots.create_index([("snapshot_date", DESCENDING), ("period_days", ASCENDING)], unique=True)
    await db.market_provider_backoff.create_index([("provider", ASCENDING)], unique=True)
    await db.market_factor_snapshots.create_index(
        [("ticker", ASCENDING), ("as_of", DESCENDING), ("factor_version", ASCENDING)], unique=True,
    )
    await db.market_factor_universe_snapshots.create_index(
        [("universe_id", ASCENDING), ("as_of", DESCENDING), ("factor_version", ASCENDING)], unique=True,
    )
    await db.market_data_lineage_snapshots.create_index(
        [("snapshot_date", ASCENDING), ("universe_id", ASCENDING)], unique=True,
    )
    await db.market_data_lineage_snapshots.create_index([("captured_at", DESCENDING)])


async def _read_cached(ticker: str, start: date, end: date) -> list[dict]:
    cursor = get_db().market_ohlcv.find(
        {"ticker": ticker, "interval": DAILY_INTERVAL, "timestamp": {"$gte": start.isoformat(), "$lt": end.isoformat()}},
        {"_id": 0},
    ).sort("timestamp", ASCENDING)
    return await cursor.to_list(None)


def _lineage_fingerprint(bar: dict) -> str:
    """Return the stored bar digest, or a deterministic legacy fallback without mutating data."""
    if bar.get("fingerprint"):
        return str(bar["fingerprint"])
    payload = [str(bar.get(key, "")) for key in ("ticker", "interval", "timestamp", "open", "high", "low", "close", "volume")]
    return hashlib.sha256("|".join(payload).encode("utf-8")).hexdigest()


async def market_data_lineage_report(tickers: list[str], as_of: str | None = None) -> dict:
    """Describe the exact stored daily-bar inputs used for reproducible research."""
    normalized = list(dict.fromkeys(ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()))
    if not normalized:
        raise MarketDataError("데이터 계보를 만들 종목이 없습니다.")
    if len(normalized) > 50:
        raise MarketDataError("데이터 계보는 한 번에 최대 50개 종목까지 확인할 수 있습니다.")
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError as exc:
            raise MarketDataError("기준일은 YYYY-MM-DD 형식이어야 합니다.") from exc

    db = get_db()
    items: list[dict] = []
    for ticker in sorted(normalized):
        criteria: dict = {"ticker": ticker, "interval": DAILY_INTERVAL}
        if as_of:
            criteria["timestamp"] = {"$lte": as_of}
        bars = await db.market_ohlcv.find(
            criteria,
            {"_id": 0, "ticker": 1, "interval": 1, "timestamp": 1, "open": 1, "high": 1, "low": 1, "close": 1,
             "volume": 1, "fingerprint": 1, "source": 1, "source_version": 1, "dataset_version": 1, "fetched_at": 1},
        ).sort("timestamp", ASCENDING).to_list(10_000)
        if not bars:
            items.append({"ticker": ticker, "status": "missing", "bar_count": 0, "dataset_fingerprint": None,
                          "first_timestamp": None, "last_timestamp": None, "sources": [], "dataset_versions": [],
                          "missing_provenance_count": 0, "last_fetched_at": None})
            continue
        records = [{
            "timestamp": str(bar.get("timestamp", ""))[:10], "fingerprint": _lineage_fingerprint(bar),
            "source": str(bar.get("source") or ""), "source_version": str(bar.get("source_version") or ""),
            "dataset_version": str(bar.get("dataset_version") or ""),
        } for bar in bars]
        missing_provenance = sum(not record["source"] or not record["dataset_version"] or not bar.get("fingerprint") for record, bar in zip(records, bars))
        canonical = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fetched = [_as_utc(bar.get("fetched_at")) for bar in bars if bar.get("fetched_at")]
        sources = sorted({f"{record['source']}@{record['source_version'] or 'unknown'}" for record in records if record["source"]})
        versions = sorted({record["dataset_version"] for record in records if record["dataset_version"]})
        items.append({
            "ticker": ticker, "status": "reproducible" if not missing_provenance else "legacy_metadata_incomplete",
            "bar_count": len(bars), "first_timestamp": records[0]["timestamp"], "last_timestamp": records[-1]["timestamp"],
            "dataset_fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "sources": sources,
            "dataset_versions": versions, "missing_provenance_count": missing_provenance,
            "last_fetched_at": max(fetched).isoformat() if fetched else None,
        })
    report_payload = [{key: value for key, value in item.items() if key != "last_fetched_at"} for item in items]
    report_fingerprint = hashlib.sha256(json.dumps(report_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "report_version": "market-data-lineage-v1", "generated_at": _utcnow().isoformat(), "as_of": as_of,
        "tickers": sorted(normalized), "items": items, "report_fingerprint": report_fingerprint,
        "summary": {"ticker_count": len(items), "reproducible_count": sum(item["status"] == "reproducible" for item in items),
                    "incomplete_count": sum(item["status"] == "legacy_metadata_incomplete" for item in items),
                    "missing_count": sum(item["status"] == "missing" for item in items)},
        "disclaimer": "저장된 일봉의 출처·버전·봉 지문을 재현성 점검용으로 요약한 읽기 전용 자료입니다. 과거 데이터를 보정하거나 새 시세를 요청하지 않으며, 수익·주문·전략 유효성을 보장하지 않습니다.",
    }


async def capture_market_data_lineage_snapshot(tickers: list[str]) -> dict:
    """Persist one daily lineage evidence snapshot for the refreshed universe only."""
    report = await market_data_lineage_report(tickers)
    now = _utcnow()
    snapshot_date = now.astimezone(OPERATIONS_TIMEZONE).date().isoformat()
    universe_id = hashlib.sha256(",".join(report["tickers"]).encode("utf-8")).hexdigest()[:24]
    await get_db().market_data_lineage_snapshots.update_one(
        {"snapshot_date": snapshot_date, "universe_id": universe_id},
        {"$set": {"snapshot_date": snapshot_date, "universe_id": universe_id, "captured_at": now, "report": report}}, upsert=True,
    )
    return {"snapshot_date": snapshot_date, "universe_id": universe_id, "ticker_count": len(report["items"]),
            "report_fingerprint": report["report_fingerprint"], "captured_at": now.isoformat()}


async def market_factor_snapshot(ticker: str, as_of: str | None = None) -> dict:
    """저장된 검증 일봉만 사용해 기준일 팩터 스냅샷을 계산·보존한다."""
    ticker = ticker.strip().upper()
    criteria: dict = {"ticker": ticker, "interval": DAILY_INTERVAL}
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError as exc:
            raise MarketDataError("기준일은 YYYY-MM-DD 형식이어야 합니다.") from exc
        criteria["timestamp"] = {"$lte": as_of}
    bars = await get_db().market_ohlcv.find(
        criteria, {"_id": 0, "timestamp": 1, "close": 1, "volume": 1, "fingerprint": 1, "source": 1, "dataset_version": 1},
    ).sort("timestamp", DESCENDING).to_list(130)
    bars.reverse()
    result = calculate_daily_factors(bars)
    provenance = {"source": bars[-1].get("source") if bars else None,
                  "dataset_version": bars[-1].get("dataset_version") if bars else None,
                  "dataset_versions": sorted({str(row.get("dataset_version")) for row in bars if row.get("dataset_version")})}
    snapshot = {"ticker": ticker, **result, "provenance": provenance, "computed_at": _utcnow()}
    if not result["available"]:
        return snapshot
    await get_db().market_factor_snapshots.update_one(
        {"ticker": ticker, "as_of": result["as_of"], "factor_version": result["factor_version"]},
        {"$set": snapshot, "$setOnInsert": {"created_at": _utcnow()}}, upsert=True,
    )
    return snapshot


async def market_factor_universe_snapshot(tickers: list[str], as_of: str | None = None) -> dict:
    """공통 기준일을 지키는 종목군 팩터 비교 스냅샷을 만든다."""
    unique_tickers = list(dict.fromkeys(ticker.strip().upper() for ticker in tickers if ticker.strip()))
    if not unique_tickers:
        raise MarketDataError("최소 한 개 티커가 필요합니다.")
    if len(unique_tickers) > 25:
        raise MarketDataError("팩터 비교는 한 번에 최대 25개 티커까지 가능합니다.")
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError as exc:
            raise MarketDataError("기준일은 YYYY-MM-DD 형식이어야 합니다.") from exc
        common_as_of = as_of
    else:
        latest_dates = []
        for ticker in unique_tickers:
            latest = await get_db().market_ohlcv.find_one(
                {"ticker": ticker, "interval": DAILY_INTERVAL}, {"_id": 0, "timestamp": 1}, sort=[("timestamp", DESCENDING)],
            )
            if latest and latest.get("timestamp"):
                latest_dates.append(str(latest["timestamp"])[:10])
        if not latest_dates:
            raise MarketDataError("비교할 저장 일봉이 없습니다.")
        common_as_of = min(latest_dates)
    available: list[dict] = []
    excluded: list[dict] = []
    for ticker in unique_tickers:
        snapshot = await market_factor_snapshot(ticker, common_as_of)
        if not snapshot["available"]:
            excluded.append({"ticker": ticker, "reason": snapshot["reason"]})
        elif snapshot.get("as_of") != common_as_of:
            excluded.append({"ticker": ticker, "reason": "공통 기준일의 일봉이 없어 비교에서 제외했습니다.",
                             "latest_factor_as_of": snapshot.get("as_of")})
        else:
            available.append({"ticker": ticker, "factors": snapshot["factors"], "observations": snapshot["observations"],
                              "input_fingerprint": snapshot["input_fingerprint"],
                              "provenance": snapshot["provenance"]})
    ranked = rank_factor_universe(available)
    universe_id = hashlib.sha256(",".join(sorted(unique_tickers)).encode()).hexdigest()[:24]
    snapshot = {"universe_id": universe_id, "tickers": unique_tickers, "as_of": common_as_of,
                "factor_version": FACTOR_VERSION, "available_count": len(ranked), "excluded": excluded,
                "items": ranked, "computed_at": _utcnow(),
                "disclaimer": "동일 기준일의 과거 관측 팩터 비교이며, 매수·매도 신호나 투자 권유가 아닙니다."}
    await get_db().market_factor_universe_snapshots.update_one(
        {"universe_id": universe_id, "as_of": common_as_of, "factor_version": FACTOR_VERSION},
        {"$set": snapshot, "$setOnInsert": {"created_at": _utcnow()}}, upsert=True,
    )
    return snapshot


async def _persist(ticker: str, result: ProviderResult, bars: list[dict], rejected: list[dict], adjusted: bool,
                   started_at: datetime, provider_attempts: list[dict] | None = None,
                   quality: dict | None = None, secondary_verification: dict | None = None,
                   provider_routing: list[dict] | None = None) -> dict:
    db = get_db()
    fetched_at = _utcnow()
    dataset_version = f"{fetched_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    operations = []
    for bar in bars:
        fingerprint = hashlib.sha256(
            f"{ticker}|{DAILY_INTERVAL}|{bar['timestamp']}|{bar['open']}|{bar['high']}|{bar['low']}|{bar['close']}|{bar['volume']}".encode()
        ).hexdigest()
        document = {
            **bar, "ticker": ticker, "interval": DAILY_INTERVAL, "adjusted": adjusted,
            "source": result.source, "source_version": result.source_version,
            "schema_version": SCHEMA_VERSION, "dataset_version": dataset_version,
            "fetched_at": fetched_at, "fingerprint": fingerprint,
        }
        operations.append(UpdateOne(
            {"ticker": ticker, "interval": DAILY_INTERVAL, "timestamp": bar["timestamp"]},
            {"$set": document, "$setOnInsert": {"created_at": fetched_at}}, upsert=True,
        ))
    if operations:
        await db.market_ohlcv.bulk_write(operations, ordered=False)
    action_operations = []
    for action in result.actions or []:
        action_operations.append(UpdateOne(
            {"ticker": ticker, "effective_date": action["effective_date"], "action_type": action["action_type"], "source": result.source},
            {"$set": {**action, "ticker": ticker, "source": result.source, "source_version": result.source_version,
                      "dataset_version": dataset_version, "fetched_at": fetched_at, "schema_version": SCHEMA_VERSION},
             "$setOnInsert": {"created_at": fetched_at}}, upsert=True,
        ))
    if action_operations:
        await db.market_corporate_actions.bulk_write(action_operations, ordered=False)
    quality = quality or assess_ingestion_quality(len(result.bars), rejected)
    run = {
        "run_id": uuid.uuid4().hex, "ticker": ticker, "interval": DAILY_INTERVAL,
        "source": result.source, "source_version": result.source_version, "adjusted": adjusted,
        "started_at": started_at, "finished_at": fetched_at, "dataset_version": dataset_version,
        "bars_received": len(result.bars), "bars_valid": len(bars), "bars_rejected": len(rejected),
        "corporate_actions": len(result.actions or []),
        "quality_status": quality["status"], "rejection_rate_pct": quality["rejection_rate_pct"],
        "secondary_verification_required": quality["secondary_verification_required"],
        "rejections": [{"reason": item["reason"], "timestamp": str(item.get("bar", {}).get("timestamp", ""))[:10]} for item in rejected[:20]],
        "provider_attempts": provider_attempts or [],
        "secondary_verification": secondary_verification,
        "provider_routing": provider_routing or [],
    }
    await db.market_data_runs.insert_one(run)
    return run


# 화면용 캐시 읽기(2026-10-06): 저장본을 무조건 쓰면 자동 수집 대상이 아닌 차트(금·유가·달러지수·10년물·
# 비트코인·다우)가 9월 10일 값에 멈췄다. 저장본은 15분 안에 새로 받은 적이 있을 때만 그대로 쓰고,
# 그보다 오래되면 화면은 저장본을 먼저 보여 주며 뒤에서 새로 받는다(오늘 봉 최대 15분 지연).
# 마지막 봉이 CACHE_STALE_DAYS일보다 오래된 저장본은 보여 주지 않고 그 자리에서 새로 받는다.
CACHE_REFRESH_SECONDS = 15 * 60
CACHE_STALE_DAYS = 4
_cache_refreshed_at: dict[str, float] = {}
_cache_refresh_locks: dict[str, asyncio.Lock] = {}
_background_refreshes: dict[str, asyncio.Task] = {}


def _cached_response(bars: list[dict]) -> dict:
    return {
        "bars": bars, "source": "cached", "source_version": "mongo",
        "dataset_version": bars[-1].get("dataset_version"), "cached": True,
        "quality": {"status": "last_verified_cache", "rejected": 0, "rejection_rate_pct": 0.0,
                    "secondary_verification_required": False},
    }


def _recently_refreshed(ticker: str) -> bool:
    started = _cache_refreshed_at.get(ticker)
    return started is not None and monotonic() - started < CACHE_REFRESH_SECONDS


def _bars_are_stale(bars: list[dict], today: date | None = None) -> bool:
    last = date.fromisoformat(str(bars[-1]["timestamp"])[:10])
    return ((today or date.today()) - last).days > CACHE_STALE_DAYS


def _refresh_in_background(ticker: str, start: date, end: date, adjusted: bool) -> None:
    running = _background_refreshes.get(ticker)
    if running and not running.done():
        return

    async def _run() -> None:
        try:
            await get_daily_ohlcv(ticker, start, end, adjusted=adjusted)
        except Exception:
            pass   # 다음 화면 요청이 저장본으로 계속 표시되고, 15분 뒤 다시 시도한다.

    _background_refreshes[ticker] = asyncio.get_running_loop().create_task(_run())


async def _cached_for_display(ticker: str, start: date, end: date, adjusted: bool) -> dict | None:
    """화면용 일봉. None이면 저장본이 없어 호출자가 공급자를 조회한다."""
    cached = await _read_cached(ticker, start, end)
    if not cached:
        return None
    if _recently_refreshed(ticker):
        return _cached_response(cached)
    if not _bars_are_stale(cached):
        _refresh_in_background(ticker, start, end, adjusted)
        return _cached_response(cached)
    lock = _cache_refresh_locks.setdefault(ticker, asyncio.Lock())
    async with lock:   # 같은 종목을 동시에 열어도 공급자는 한 번만 부른다
        if _recently_refreshed(ticker):
            refreshed = await _read_cached(ticker, start, end)
            return _cached_response(refreshed or cached)
        return await get_daily_ohlcv(ticker, start, end, adjusted=adjusted)   # 실패하면 저장본으로 대신한다


async def get_daily_ohlcv(ticker: str, start: date, end: date, *, adjusted: bool = True, prefer_cached: bool = False) -> dict:
    """공급자에서 일봉을 수집·검증·저장한 뒤, 실패 시 저장된 마지막 데이터를 반환한다.

    화면 차트처럼 즉시 응답이 중요한 읽기 요청은 ``prefer_cached``로 마지막 검증
    캐시를 먼저 사용할 수 있다. 명시적 수집 작업은 기본값을 유지해 공급자를 호출한다.
    """
    if end <= start or (end - start).days > MAX_FETCH_DAYS:
        raise MarketDataError(f"수집 기간은 1~{MAX_FETCH_DAYS}일이어야 합니다.")
    ticker = ticker.strip().upper()
    started_at = _utcnow()
    if prefer_cached:
        cached = await _cached_for_display(ticker, start, end, adjusted)
        if cached:
            return cached
    _cache_refreshed_at[ticker] = monotonic()
    providers, provider_routing = await provider_routing_plan(ticker)
    errors: list[str] = [f"{event['provider']}: 재시도 대기 중" for event in provider_routing if event["action"] == "cooldown_skip"]
    provider_attempts: list[dict] = []
    review_candidate: tuple[ProviderResult, list[dict], list[dict], dict] | None = None
    for provider in providers:
        request_started = monotonic()
        try:
            result = await asyncio.to_thread(provider.fetch_daily, ticker, start, end, adjusted)
            latency_ms = round((monotonic() - request_started) * 1000, 2)
            bars, rejected = validate_daily_bars(result.bars, repair_fx_range=ticker.endswith("=X"))
            if not bars:
                raise MarketDataError("품질 검사를 통과한 OHLCV가 없습니다.")
            quality = assess_ingestion_quality(len(result.bars), rejected)
            if getattr(provider, "verification_only", False):
                if not review_candidate:
                    errors.append(f"{provider.name}: 검증 전용 공급자이며 교차검증할 기본 데이터가 없어 건너뜀")
                    continue
                primary_result, primary_bars, primary_rejected, primary_quality = review_candidate
                verification = verify_close_overlap(primary_bars, bars) | {"reference_provider": provider.name,
                                                                            "reference_source_version": result.source_version}
                provider_attempts.append({"provider": provider.name, "bars_received": len(result.bars), "bars_valid": len(bars),
                                          "bars_rejected": len(rejected), "rejection_rate_pct": quality["rejection_rate_pct"],
                                          "status": verification["status"], "verification": verification,
                                          "latency_ms": latency_ms})
                if verification["verified"]:
                    verified_quality = {**primary_quality, "status": "passed_with_cross_verification",
                                        "secondary_verification_required": False}
                    await record_provider_attempt_outcome(provider.name, "verified")
                    run = await _persist(ticker, primary_result, primary_bars, primary_rejected, adjusted, started_at,
                                         provider_attempts, quality=verified_quality, secondary_verification=verification,
                                         provider_routing=provider_routing)
                    return {"bars": primary_bars, "source": primary_result.source, "source_version": primary_result.source_version,
                            "dataset_version": run["dataset_version"], "cached": False,
                            "quality": {"status": run["quality_status"], "rejected": len(primary_rejected),
                                        "rejection_rate_pct": run["rejection_rate_pct"],
                                        "secondary_verification_required": False,
                                        "secondary_verification": verification}, "provider_attempts": provider_attempts,
                            "provider_routing": provider_routing}
                errors.append(f"{provider.name}: 교차검증 미통과 ({verification['status']})")
                continue
            provider_attempts.append({"provider": provider.name, "bars_received": len(result.bars), "bars_valid": len(bars),
                                      "bars_rejected": len(rejected), "rejection_rate_pct": quality["rejection_rate_pct"],
                                      "status": "review_candidate" if quality["secondary_verification_required"] else "accepted",
                                      "latency_ms": latency_ms})
            if quality["secondary_verification_required"]:
                review_candidate = (result, bars, rejected, quality)
                errors.append(f"{provider.name}: 거부 봉 비율 {quality['rejection_rate_pct']}%로 교차검증 필요")
                continue
            await record_provider_attempt_outcome(provider.name, "accepted")
            run = await _persist(ticker, result, bars, rejected, adjusted, started_at, provider_attempts,
                                 provider_routing=provider_routing)
            return {"bars": bars, "source": result.source, "source_version": result.source_version,
                    "dataset_version": run["dataset_version"], "cached": False,
                    "quality": {"status": run["quality_status"], "rejected": len(rejected),
                                "rejection_rate_pct": run["rejection_rate_pct"],
                                "secondary_verification_required": run["secondary_verification_required"]},
                    "provider_attempts": provider_attempts, "provider_routing": provider_routing}
        except Exception as exc:
            latency_ms = round((monotonic() - request_started) * 1000, 2)
            errors.append(f"{provider.name}: {exc}")
            attempt_status = _provider_attempt_status(exc)
            provider_attempts.append({"provider": provider.name, "status": attempt_status, "error": str(exc)[:300],
                                      "latency_ms": latency_ms})
            await record_provider_attempt_outcome(provider.name, attempt_status, str(exc))
    if review_candidate:
        result, bars, rejected, _quality = review_candidate
        run = await _persist(ticker, result, bars, rejected, adjusted, started_at, provider_attempts,
                             provider_routing=provider_routing)
        return {"bars": bars, "source": result.source, "source_version": result.source_version,
                "dataset_version": run["dataset_version"], "cached": False,
                "quality": {"status": run["quality_status"], "rejected": len(rejected),
                            "rejection_rate_pct": run["rejection_rate_pct"],
                            "secondary_verification_required": run["secondary_verification_required"]},
                "provider_attempts": provider_attempts, "provider_routing": provider_routing, "provider_errors": errors}
    cached = await _read_cached(ticker, start, end)
    if cached:
        return {"bars": cached, "source": "cached", "source_version": "mongo", "dataset_version": cached[-1].get("dataset_version"),
                "cached": True, "quality": {"status": "last_verified_cache", "rejected": 0, "rejection_rate_pct": 0.0,
                                                "secondary_verification_required": False}, "provider_routing": provider_routing,
                "provider_errors": errors}
    raise MarketDataError("; ".join(errors) or "사용 가능한 시세 공급자가 없습니다.")


async def market_data_status(ticker: str) -> dict:
    ticker = ticker.strip().upper()
    latest = await get_db().market_ohlcv.find_one(
        {"ticker": ticker, "interval": DAILY_INTERVAL}, {"_id": 0}, sort=[("timestamp", DESCENDING)],
    )
    run = await get_db().market_data_runs.find_one({"ticker": ticker}, {"_id": 0}, sort=[("finished_at", DESCENDING)])
    action = await get_db().market_corporate_actions.find_one({"ticker": ticker}, {"_id": 0}, sort=[("effective_date", DESCENDING)])
    bars = await get_db().market_ohlcv.find({"ticker": ticker, "interval": DAILY_INTERVAL}, {"_id": 0, "timestamp": 1, "close": 1}).sort("timestamp", DESCENDING).to_list(500)
    return {"ticker": ticker, "latest_bar": latest, "latest_corporate_action": action, "last_run": run, "quality_monitor": inspect_daily_quality(bars)}


def _business_days_since(latest: str | None, today: date) -> int | None:
    """마지막 일봉 이후의 평일 수. 거래소 휴일은 경고 검토 대상으로만 취급한다."""
    try:
        cursor = date.fromisoformat(str(latest)[:10])
    except ValueError:
        return None
    days = 0
    while cursor < today:
        cursor += timedelta(days=1)
        days += int(cursor.weekday() < 5)
    return days


async def core_market_freshness() -> dict[str, dict]:
    """홈·운영 리포트가 공유하는 핵심 지수의 마지막 검증 일봉 상태다."""
    db = get_db()
    rows = await db.market_ohlcv.aggregate([
        {"$match": {"ticker": {"$in": list(CORE_MARKET_TICKERS)}, "interval": DAILY_INTERVAL}},
        {"$sort": {"ticker": 1, "timestamp": -1}},
        {"$group": {"_id": "$ticker", "latest": {"$first": "$timestamp"}, "source": {"$first": "$source"}}},
    ]).to_list(len(CORE_MARKET_TICKERS))
    available = {str(row["_id"]): row for row in rows}
    today = datetime.now(timezone.utc).date()
    report: dict[str, dict] = {}
    for ticker in CORE_MARKET_TICKERS:
        row = available.get(ticker, {})
        latest = row.get("latest")
        business_days = _business_days_since(latest, today)
        report[ticker] = {
            "latest": latest,
            "source": row.get("source"),
            "business_days_since": business_days,
            "stale": business_days is None or business_days > 2,
        }
    return report


def _provider_health_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _provider_health_pct(name: str, default: float) -> float:
    try:
        return min(100.0, max(0.1, float(os.getenv(name, str(default)))))
    except ValueError:
        return default


async def provider_latency_trends(health: dict) -> dict:
    """당일 지연 시간과 과거 일일 스냅샷 기준선을 비교한다.

    절대 지연 한도와 별개로 급격한 성능 저하를 조기에 관측한다. 당일 스냅샷은
    비교 대상에서 제외하므로 같은 날 재실행해도 기준선이 스스로 오염되지 않는다.
    """
    baseline_days = _provider_health_int("MARKET_DATA_PROVIDER_LATENCY_BASELINE_DAYS", 7, 2, 31)
    minimum_snapshots = _provider_health_int("MARKET_DATA_PROVIDER_LATENCY_MIN_BASELINE_SNAPSHOTS", 3, 1, baseline_days)
    regression_pct = _provider_health_pct("MARKET_DATA_PROVIDER_LATENCY_REGRESSION_PCT", 75.0)
    today = _utcnow().astimezone(OPERATIONS_TIMEZONE).date().isoformat()
    snapshots = await get_db().market_provider_health_snapshots.find(
        {"snapshot_date": {"$lt": today}, "period_days": health["period_days"]}, {"_id": 0, "providers": 1},
    ).sort("snapshot_date", DESCENDING).to_list(baseline_days)
    history: dict[str, list[float]] = {}
    for snapshot in snapshots:
        for provider in snapshot.get("providers", []):
            latency = _as_number(provider.get("average_latency_ms"))
            if latency is not None and latency >= 0:
                history.setdefault(str(provider.get("provider", "unknown")), []).append(latency)
    trends: list[dict] = []
    for provider in health["providers"]:
        name = provider["provider"]
        current = _as_number(provider.get("average_latency_ms"))
        baseline_samples = history.get(name, [])
        baseline = round(sum(baseline_samples) / len(baseline_samples), 2) if baseline_samples else None
        enough_current = provider.get("latency_sample_count", 0) >= health["thresholds"]["minimum_attempts"]
        enough_baseline = len(baseline_samples) >= minimum_snapshots
        change_pct = round((current / baseline - 1) * 100, 2) if current is not None and baseline and baseline > 0 else None
        if not enough_current or not enough_baseline or change_pct is None:
            status = "insufficient_history"
        elif change_pct >= regression_pct:
            status = "regressed"
        elif change_pct <= -regression_pct:
            status = "improved"
        else:
            status = "stable"
        trends.append({"provider": name, "status": status, "current_average_latency_ms": current,
                       "baseline_average_latency_ms": baseline, "change_pct": change_pct,
                       "current_latency_sample_count": provider.get("latency_sample_count", 0),
                       "baseline_snapshot_count": len(baseline_samples)})
    trends.sort(key=lambda row: (row["status"] != "regressed", -(row["change_pct"] or -10_000), row["provider"]))
    return {"baseline_days": baseline_days, "minimum_baseline_snapshots": minimum_snapshots,
            "regression_pct": regression_pct, "reference_snapshot_count": len(snapshots),
            "providers": trends, "regressed_providers": [row for row in trends if row["status"] == "regressed"]}


def _provider_attempt_status(error: Exception) -> str:
    """지원하지 않는 심볼은 공급자 장애가 아닌 적용 불가로 기록한다."""
    message = str(error)
    if "심볼 매핑이 없습니다" in message or "시리즈 매핑이 없습니다" in message:
        return "not_applicable"
    return "error"


async def market_provider_health(days: int = 7) -> dict:
    """최근 수집 시도에서 공급자별 품질과 응답 시간 지표를 집계한다."""
    period_days = max(1, min(31, int(days)))
    cutoff = _utcnow() - timedelta(days=period_days)
    runs = await get_db().market_data_runs.find({"finished_at": {"$gte": cutoff}}, {"_id": 0}).to_list(10_000)
    minimum_attempts = _provider_health_int("MARKET_DATA_PROVIDER_MIN_ATTEMPTS", 3, 1, 1000)
    maximum_error_rate = _provider_health_pct("MARKET_DATA_PROVIDER_MAX_ERROR_RATE_PCT", 20.0)
    maximum_rejection_rate = _provider_health_pct("MARKET_DATA_PROVIDER_MAX_REJECTION_RATE_PCT", 5.0)
    slow_latency_ms = _provider_health_int("MARKET_DATA_PROVIDER_SLOW_LATENCY_MS", 5000, 100, 600_000)
    totals: dict[str, dict] = {}
    for run in runs:
        attempts = run.get("provider_attempts") or [{
            "provider": run.get("source", "unknown"), "status": "accepted", "bars_received": run.get("bars_received", 0),
            "bars_valid": run.get("bars_valid", 0), "bars_rejected": run.get("bars_rejected", 0),
        }]
        for attempt in attempts:
            provider = str(attempt.get("provider", "unknown"))
            row = totals.setdefault(provider, {"provider": provider, "attempt_count": 0, "not_applicable_count": 0, "accepted_count": 0,
                                               "verified_count": 0, "error_count": 0, "review_candidate_count": 0,
                                               "bars_received": 0, "bars_valid": 0, "bars_rejected": 0,
                                               "_latencies_ms": []})
            status = attempt.get("status", "unknown")
            if status == "error" and ("심볼 매핑이 없습니다" in str(attempt.get("error", "")) or "시리즈 매핑이 없습니다" in str(attempt.get("error", ""))):
                status = "not_applicable"
            row["not_applicable_count"] += int(status == "not_applicable")
            row["attempt_count"] += int(status != "not_applicable")
            row["accepted_count"] += int(status == "accepted")
            row["verified_count"] += int(status == "verified")
            row["error_count"] += int(status == "error")
            row["review_candidate_count"] += int(status == "review_candidate")
            for key in ("bars_received", "bars_valid", "bars_rejected"):
                row[key] += int(attempt.get(key, 0) or 0)
            latency_ms = _as_number(attempt.get("latency_ms"))
            if latency_ms is not None and latency_ms >= 0 and status != "not_applicable":
                row["_latencies_ms"].append(latency_ms)
    providers: list[dict] = []
    for row in totals.values():
        attempts = row["attempt_count"]
        latencies = sorted(row.pop("_latencies_ms"))
        row["latency_sample_count"] = len(latencies)
        row["average_latency_ms"] = round(sum(latencies) / len(latencies), 2) if latencies else None
        row["p95_latency_ms"] = round(latencies[(len(latencies) * 95 + 99) // 100 - 1], 2) if latencies else None
        row["error_rate_pct"] = round(row["error_count"] / attempts * 100, 4) if attempts else 0.0
        row["rejection_rate_pct"] = round(row["bars_rejected"] / max(row["bars_received"], 1) * 100, 4)
        reasons: list[str] = []
        if attempts >= minimum_attempts and row["error_rate_pct"] >= maximum_error_rate:
            reasons.append("error_rate")
        if attempts >= minimum_attempts and row["rejection_rate_pct"] >= maximum_rejection_rate:
            reasons.append("rejection_rate")
        row["status"] = "degraded" if reasons else "healthy"
        row["degradation_reasons"] = reasons
        row["performance_status"] = "slow" if (
            attempts >= minimum_attempts and row["average_latency_ms"] is not None and row["average_latency_ms"] >= slow_latency_ms
        ) else "normal"
        providers.append(row)
    providers.sort(key=lambda item: (item["status"] != "degraded", -item["attempt_count"], item["provider"]))
    return {"period_days": period_days, "period_start": cutoff.isoformat(), "run_count": len(runs),
            "thresholds": {"minimum_attempts": minimum_attempts, "maximum_error_rate_pct": maximum_error_rate,
                           "maximum_rejection_rate_pct": maximum_rejection_rate, "slow_latency_ms": slow_latency_ms},
            "providers": providers, "degraded_providers": [row for row in providers if row["status"] == "degraded"],
            "slow_providers": [row for row in providers if row["performance_status"] == "slow"]}


async def record_market_provider_health_snapshot(health: dict) -> dict:
    """하루에 한 번 갱신되는 공급자 상태 스냅샷. 재실행 시 마지막 관측값으로 갱신한다."""
    now = _utcnow()
    snapshot_date = now.astimezone(OPERATIONS_TIMEZONE).date().isoformat()
    payload = {"snapshot_date": snapshot_date, "period_days": health["period_days"], "captured_at": now,
               "run_count": health["run_count"], "providers": health["providers"], "degraded_provider_count": len(health["degraded_providers"])}
    await get_db().market_provider_health_snapshots.update_one(
        {"snapshot_date": snapshot_date, "period_days": health["period_days"]},
        {"$set": payload, "$setOnInsert": {"created_at": now}}, upsert=True,
    )
    return payload


def _provider_health_findings(health: dict, latency_trends: dict | None = None) -> list[dict]:
    findings: list[dict] = []
    for provider in health["degraded_providers"]:
        reasons = ", ".join(provider["degradation_reasons"])
        findings.append({
            "fingerprint": f"provider_degraded:{provider['provider']}", "type": "provider_degraded", "severity": "warning",
            "title": "시세 공급자 품질 저하", "message": f"{provider['provider']} 최근 {provider['attempt_count']}회 시도에서 {reasons} 임계치를 넘었습니다.",
            "context": {"provider": provider["provider"], "period_days": health["period_days"], "metrics": provider,
                        "thresholds": health["thresholds"]},
        })
    for provider in (latency_trends or {}).get("regressed_providers", []):
        findings.append({
            "fingerprint": f"provider_latency_regression:{provider['provider']}", "type": "provider_latency_regression", "severity": "warning",
            "title": "시세 공급자 응답 지연 악화",
            "message": f"{provider['provider']} 평균 응답 시간이 기준선보다 {provider['change_pct']}% 늘었습니다.",
            "context": {"provider": provider["provider"], "latency_trend": provider,
                        "thresholds": {"regression_pct": latency_trends["regression_pct"],
                                       "minimum_baseline_snapshots": latency_trends["minimum_baseline_snapshots"]}},
        })
    return findings


def _refresh_alert_findings(job: dict, freshness: dict[str, dict]) -> list[dict]:
    """수집 결과를 사람 검토용 경고 후보로만 바꾼다. 주문·원장은 변경하지 않는다."""
    findings: list[dict] = []
    job_id = str(job.get("job_id", "unknown"))
    for item in job.get("items", []):
        ticker = str(item.get("ticker", "unknown"))
        if item.get("status") != "ok":
            findings.append({
                "fingerprint": f"refresh_failure:{ticker}", "type": "refresh_failure", "severity": "warning",
                "title": "일봉 자동 수집 실패", "message": f"{ticker} 일봉을 수집하지 못했습니다. 마지막 검증 캐시와 공급자 상태를 확인하세요.",
                "context": {"ticker": ticker, "job_id": job_id, "error": item.get("error", "")},
            })
        elif item.get("source") == "cached":
            findings.append({
                "fingerprint": f"cached_fallback:{ticker}", "type": "cached_fallback", "severity": "warning",
                "title": "검증 캐시 폴백", "message": f"{ticker}은 외부 공급자 대신 마지막 검증 캐시로 유지되었습니다.",
                "context": {"ticker": ticker, "job_id": job_id, "dataset_version": item.get("dataset_version")},
            })
        elif item.get("quality", {}).get("secondary_verification_required") or item.get("quality", {}).get("status") == "passed_with_rejections":
            findings.append({
                "fingerprint": f"quality_review:{ticker}", "type": "quality_review", "severity": "warning",
                "title": "일봉 품질 검토 필요", "message": f"{ticker} 일봉 품질 검토가 필요합니다.",
                "context": {"ticker": ticker, "job_id": job_id, "quality": item.get("quality", {}),
                            "provider_attempts": item.get("provider_attempts", [])},
            })
    for ticker, status in freshness.items():
        if status["stale"]:
            findings.append({
                "fingerprint": f"core_stale:{ticker}", "type": "core_stale", "severity": "warning",
                "title": "핵심 지수 최신성 확인 필요", "message": f"{ticker} 마지막 일봉의 최신성을 확인하세요.",
                "context": {"ticker": ticker, "latest": status["latest"], "business_days_since": status["business_days_since"]},
            })
    return findings


async def sync_market_data_alerts(job: dict) -> dict:
    """현재 수집 조건을 idempotent 경고 이력으로 동기화한다.

    경고 해소는 자동 주문·삭제가 아니라 ``resolved`` 이벤트 추가일 뿐이며, 이전
    실행 이력은 보존한다.
    """
    db = get_db()
    now = _utcnow()
    freshness = await core_market_freshness()
    provider_health = await market_provider_health()
    await record_market_provider_health_snapshot(provider_health)
    latency_trends = await provider_latency_trends(provider_health)
    findings = _refresh_alert_findings(job, freshness) + _provider_health_findings(provider_health, latency_trends)
    active_fingerprints = {finding["fingerprint"] for finding in findings}
    existing = await db.market_data_alerts.find({"condition_active": True}).to_list(500)
    resolved = 0
    for alert in existing:
        if alert.get("fingerprint") not in active_fingerprints:
            await db.market_data_alerts.update_one(
                {"_id": alert["_id"]},
                {"$set": {"condition_active": False, "status": "resolved", "updated_at": now},
                 "$push": {"events": {"at": now, "action": "system_resolved", "note": "최근 자동 수집에서 경고 조건이 해소되었습니다."}}},
            )
            resolved += 1
    created = updated = 0
    for finding in findings:
        active = await db.market_data_alerts.find_one({"fingerprint": finding["fingerprint"], "condition_active": True})
        if active:
            await db.market_data_alerts.update_one({"_id": active["_id"]}, {"$set": {**finding, "updated_at": now}})
            updated += 1
        else:
            await db.market_data_alerts.insert_one({
                **finding, "status": "open", "condition_active": True,
                "created_at": now, "updated_at": now,
                "events": [{"at": now, "action": "created", "note": "자동 데이터 품질 점검"}],
            })
            created += 1
    return {"created": created, "updated": updated, "resolved": resolved, "active_conditions": len(findings),
            "core_freshness": freshness, "provider_health": provider_health, "provider_latency_trends": latency_trends}


def _serialize_market_alert(alert: dict) -> dict:
    return {
        "type": alert.get("type"), "severity": alert.get("severity"), "title": alert.get("title"),
        "message": alert.get("message"), "status": alert.get("status"), "condition_active": bool(alert.get("condition_active")),
        "context": alert.get("context", {}),
        "created_at": alert.get("created_at").isoformat() if alert.get("created_at") else None,
        "updated_at": alert.get("updated_at").isoformat() if alert.get("updated_at") else None,
        "events": [{**event, "at": event.get("at").isoformat() if event.get("at") else None} for event in alert.get("events", [])],
    }


async def market_data_operations_report(days: int = 7) -> dict:
    """일봉 수집 실행·품질 경고·핵심 지수 최신성을 묶는 읽기 전용 운영 리포트다."""
    period_days = max(1, min(31, int(days)))
    generated_at = _utcnow()
    cutoff = generated_at - timedelta(days=period_days)
    db = get_db()
    jobs = await db.market_data_refresh_jobs.find({"started_at": {"$gte": cutoff}}, {"_id": 0}).sort("started_at", -1).to_list(200)
    alerts = await db.market_data_alerts.find({"updated_at": {"$gte": cutoff}}, {"_id": 0}).sort("updated_at", -1).to_list(500)
    active_alerts = await db.market_data_alerts.find({"condition_active": True}, {"_id": 0}).sort("updated_at", -1).to_list(500)
    freshness = await core_market_freshness()
    provider_health = await market_provider_health(period_days)
    provider_latency = await provider_latency_trends(provider_health)
    provider_routing = await market_provider_routing_state()
    provider_history = await db.market_provider_health_snapshots.find({}, {"_id": 0}).sort("snapshot_date", -1).to_list(8)
    daily: dict[str, dict] = {}
    for job in jobs:
        started_at = job.get("started_at")
        day = started_at.astimezone(OPERATIONS_TIMEZONE).date().isoformat() if started_at else "unknown"
        row = daily.setdefault(day, {"date": day, "runs": 0, "target_count": 0, "success_count": 0, "failure_count": 0, "statuses": {}})
        row["runs"] += 1
        row["target_count"] += len(job.get("tickers", []))
        row["success_count"] += int(job.get("success_count", 0))
        row["failure_count"] += int(job.get("failure_count", 0))
        status = job.get("status", "unknown")
        row["statuses"][status] = row["statuses"].get(status, 0) + 1
    latest_job = jobs[0] if jobs else None
    return {
        "report_version": "market-data-operations-report-v1", "generated_at": generated_at.isoformat(),
        "period_days": period_days, "period_start": cutoff.isoformat(),
        "latest_job": {
            "job_id": latest_job.get("job_id"), "status": latest_job.get("status"),
            "started_at": latest_job.get("started_at").isoformat() if latest_job and latest_job.get("started_at") else None,
            "finished_at": latest_job.get("finished_at").isoformat() if latest_job and latest_job.get("finished_at") else None,
            "target_count": len(latest_job.get("tickers", [])) if latest_job else 0,
            "success_count": latest_job.get("success_count", 0) if latest_job else 0,
            "failure_count": latest_job.get("failure_count", 0) if latest_job else 0,
        } if latest_job else None,
        "summary": {"run_count": len(jobs), "active_alert_count": len(active_alerts), "recent_alert_count": len(alerts),
                    "stale_core_tickers": [ticker for ticker, status in freshness.items() if status["stale"]]},
        "core_freshness": freshness, "provider_health": provider_health, "provider_latency_trends": provider_latency,
        "provider_routing": provider_routing,
        "provider_health_history": provider_history,
        "daily_runs": sorted(daily.values(), key=lambda row: row["date"], reverse=True),
        "alerts": [_serialize_market_alert(alert) for alert in alerts],
        "disclaimer": "이 리포트는 시세 데이터 품질·운영 점검용이며 주문을 실행하거나 포트폴리오 원장을 변경하지 않습니다.",
    }
