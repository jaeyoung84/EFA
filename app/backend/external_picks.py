"""외부 추천 기록(인베스팅닷컴 ProPicks·저평가 스크리너 등)과 전향 성과 추적.

사용자가 매달 내려받거나 복사한 추천 명단을 올리면 티커를 인식해 날짜와 함께 그대로 보관하고,
기록한 날 다음 거래일 시가에 동일 비중으로 샀다고 보고 이후 수익률을 벤치마크(미국 SPY, 국내 KODEX 200)와
비교한다. 같은 출처의 명단을 매달 갈아탄 경우의 연결 성과도 계산한다.

- 과거 추천 기록이 없으므로 백테스트가 아니라 **기록 시점 이후의 전향 관측**만 한다(사후 수정 불가, 삭제만 가능).
- 외부 사이트를 자동 수집하지 않는다. 명단은 사용자가 직접 올리며 본인 계정에만 보인다.
- 수익률은 분할·배당 조정 가격, 매매비용 미반영(연결 성과만 교체당 0.1% 차감). 투자 권유가 아니다.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .db import get_db

logger = logging.getLogger("jy_investing.external_picks")

LISTS = "external_pick_lists"
PRICES = "external_pick_prices"
DIRECTORY = "us_asset_directory"
KST = ZoneInfo("Asia/Seoul")
BENCHMARK = {"US": "SPY", "KR": "069500.KS"}
HORIZONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
SWITCH_COST_PCT = 0.1
MAX_TICKERS = 60
DIRECTORY_MAX_AGE = timedelta(days=7)
TICKER_COLUMNS = {"symbol", "ticker", "심볼", "티커", "종목코드", "code", "종목 코드"}
US_TOKEN = re.compile(r"(?<![A-Za-z0-9.])([A-Z]{1,5}(?:[.-][A-Z])?)(?![A-Za-z0-9])")
KR_TOKEN = re.compile(r"(?<!\d)(\d{6})(?!\d)")
# 화면 복사본에 흔한 대문자 단어. 실제 티커와 겹쳐도(AI, ON 등) 머리글 열이 없을 때는 후보에서 뺀다.
NOISE = {"AI", "USD", "KRW", "EPS", "PE", "PER", "PBR", "ROE", "ROA", "CEO", "CFO", "ETF", "NYSE", "NASDAQ", "AMEX", "OTC",
         "THE", "AND", "FOR", "NA", "TTM", "YOY", "QOQ", "FY", "Q", "A", "I", "IT", "ON", "OR", "BY", "AT", "TO", "IN",
         "IS", "BE", "ALL", "NEW", "NOW", "GO", "CAN", "PRO", "AM", "PM", "EST", "KST", "UTC", "US", "UK", "EU", "GDP",
         "CPI", "FED", "IPO", "M", "B", "K", "T", "N", "S", "P", "SP", "DJIA", "VIX", "FX"}


class ExternalPicksError(ValueError):
    """명단을 저장할 수 없을 때(빈 명단, 종목 수 초과 등)."""


# ── 1. 종목 목록(티커 검증용) ──────────────────────────────────────

def _fetch_alpaca_assets() -> dict[str, str]:
    from .us_market_data import _headers
    response = requests.get("https://paper-api.alpaca.markets/v2/assets", headers=_headers(), timeout=30,
                            params={"status": "active", "asset_class": "us_equity"})
    response.raise_for_status()
    return {str(row["symbol"]).upper(): str(row.get("name") or row["symbol"]) for row in response.json()
            if row.get("tradable") and row.get("symbol")}


async def us_directory(fetch=_fetch_alpaca_assets) -> dict[str, str]:
    """Alpaca 거래 가능 미국 주식·ETF 목록(티커 -> 이름). 7일마다 새로 받는다(키는 사용하되 응답에 넣지 않음)."""
    collection = get_db()[DIRECTORY]
    doc = await collection.find_one({"_id": "alpaca"})
    fetched = doc.get("fetched_at") if doc else None
    if isinstance(fetched, datetime) and fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    if doc and fetched and datetime.now(timezone.utc) - fetched < DIRECTORY_MAX_AGE:
        return doc["symbols"]
    try:
        symbols = await asyncio.to_thread(fetch)
    except Exception:
        logger.warning("Alpaca 종목 목록 갱신 실패", exc_info=True)
        return (doc or {}).get("symbols") or {}
    # MongoDB 필드 이름에 '.'을 쓸 수 없어 BRK.B 같은 티커는 '·'로 바꿔 저장한다.
    await collection.update_one({"_id": "alpaca"}, {"$set": {"symbols": {k.replace(".", "·"): v for k, v in symbols.items()},
                                                             "fetched_at": datetime.now(timezone.utc)}}, upsert=True)
    return {k.replace(".", "·"): v for k, v in symbols.items()}


def _krx_lookup(code: str) -> dict | None:
    try:
        from .instrument_catalog import get_krx_catalog
    except ImportError:  # pragma: no cover
        from instrument_catalog import get_krx_catalog  # type: ignore
    return next((row for row in get_krx_catalog() if row["code"] == code), None)


# ── 2. 명단 해석 ───────────────────────────────────────────────────

def _column_values(text: str) -> list[str] | None:
    """머리글에 티커 열(Symbol, Ticker, 종목코드 …)이 있는 CSV·TSV면 그 열 값만."""
    sample = text.strip().splitlines()
    if len(sample) < 2:
        return None
    delimiter = "\t" if sample[0].count("\t") >= max(1, sample[0].count(",")) else ","
    rows = list(csv.reader(io.StringIO(text.strip()), delimiter=delimiter))
    header = [cell.strip().lower() for cell in rows[0]]
    index = next((i for i, cell in enumerate(header) if cell in TICKER_COLUMNS), None)
    if index is None:
        return None
    return [row[index].strip() for row in rows[1:] if len(row) > index and row[index].strip()]


def parse_candidates(text: str, directory: dict[str, str], krx=_krx_lookup) -> dict:
    """붙여 넣은 글·CSV에서 종목 후보를 뽑아 인식/미인식으로 나눈다(저장 전 미리보기용)."""
    column = _column_values(text)
    mode = "column" if column is not None else "tokens"
    raw_tokens = column if column is not None else [m.group(1) for m in US_TOKEN.finditer(text)] + KR_TOKEN.findall(text)
    recognized, unrecognized, seen = [], [], set()
    for token in raw_tokens:
        token = re.sub(r"^(NASDAQ|NYSE|AMEX|KRX|KOSPI|KOSDAQ)[:\s]+", "", token.strip().upper())
        token = re.sub(r"\.(KS|KQ)$", "", token)
        if not token or token in seen:
            continue
        seen.add(token)
        if re.fullmatch(r"\d{6}", token):
            row = krx(token)
            if row:
                recognized.append({"ticker": row["ticker"], "name": row["name"], "market": "KR"})
            else:
                unrecognized.append(token)
            continue
        key = token.replace("-", ".").replace(".", "·")
        if mode == "tokens" and token in NOISE:
            continue
        if key in directory:
            recognized.append({"ticker": token.replace("-", "."), "name": directory[key], "market": "US"})
        elif mode == "column":
            unrecognized.append(token)
    return {"mode": mode, "recognized": recognized[:MAX_TICKERS], "unrecognized": unrecognized[:30],
            "truncated": len(recognized) > MAX_TICKERS}


# ── 3. 저장·삭제 ───────────────────────────────────────────────────

async def save_list(user_id, *, source: str, list_name: str, as_of: str, items: list[dict], raw_text: str = "") -> dict:
    source, list_name = source.strip()[:60], list_name.strip()[:80]
    if not source:
        raise ExternalPicksError("출처를 입력하세요.")
    try:
        day = date.fromisoformat(as_of)
    except ValueError as exc:
        raise ExternalPicksError("기록 날짜 형식이 올바르지 않습니다.") from exc
    if day > datetime.now(KST).date():
        raise ExternalPicksError("미래 날짜로는 기록할 수 없습니다.")
    clean, seen = [], set()
    for item in items:
        ticker, market = str(item.get("ticker") or "").strip().upper(), item.get("market")
        if market not in BENCHMARK or not re.fullmatch(r"[A-Z0-9.]{1,12}", ticker) or ticker in seen:
            continue
        seen.add(ticker)
        clean.append({"ticker": ticker, "market": market, "name": str(item.get("name") or ticker)[:80]})
    if not clean:
        raise ExternalPicksError("인식된 종목이 없습니다.")
    if len(clean) > MAX_TICKERS:
        raise ExternalPicksError(f"한 명단은 최대 {MAX_TICKERS}종목입니다.")
    document = {"user_id": user_id, "source": source, "list_name": list_name or source, "as_of": day.isoformat(),
                "items": clean, "created_at": datetime.now(timezone.utc),
                "raw_sha256": hashlib.sha256(raw_text.encode()).hexdigest() if raw_text else None}
    result = await get_db()[LISTS].insert_one(document)
    return {**document, "_id": result.inserted_id}


async def delete_list(user_id, list_id) -> bool:
    return (await get_db()[LISTS].delete_one({"_id": list_id, "user_id": user_id})).deleted_count == 1


# ── 4. 가격 수집(스케줄러·저장 직후) ───────────────────────────────

def _fetch_us(symbols: list[str], start: date, now: datetime) -> dict[str, dict[str, list[float]]]:
    from .us_market_data import fetch_alpaca_bars, last_completed_session, query_end
    through = last_completed_session(now)
    if through is None:
        return {}
    out: dict[str, dict[str, list[float]]] = {}
    for index in range(0, len(symbols), 50):
        bars = fetch_alpaca_bars(tuple(symbols[index:index + 50]), start, query_end(through, now), "all")
        for symbol, rows in bars.items():
            out[symbol] = {str(r["t"])[:10]: [float(r["o"]), float(r["c"])] for r in rows if r.get("o") and r.get("c")}
    return out


async def _fetch_kr(ticker: str, start: date) -> dict[str, list[float]]:
    from .market_data import get_daily_ohlcv
    result = await get_daily_ohlcv(ticker, start, date.today() + timedelta(days=1), adjusted=True)
    return {str(b["timestamp"])[:10]: [float(b["open"]), float(b["close"])] for b in result.get("bars") or []
            if b.get("open") and b.get("close")}


async def collect_prices(only: set[str] | None = None, *, now: datetime | None = None, fetch_us=_fetch_us, fetch_kr=_fetch_kr) -> dict:
    """모든 명단(또는 지정 티커)의 일봉 시가·종가를 첫 기록일 10일 전부터 받아 덮어쓴다(조정가는 과거가 바뀔 수 있다)."""
    now = now or datetime.now(timezone.utc)
    wanted: dict[str, tuple[str, str]] = {}
    async for doc in get_db()[LISTS].find({}, {"items": 1, "as_of": 1}):
        for item in doc["items"]:
            if only is None or item["ticker"] in only:
                market = item["market"]
                first = min(doc["as_of"], wanted.get(item["ticker"], (market, doc["as_of"]))[1])
                wanted[item["ticker"]] = (market, first)
                bench = BENCHMARK[market]
                wanted[bench] = (market, min(first, wanted.get(bench, (market, first))[1]))
    if not wanted:
        return {"tickers": 0}
    stored, failed = 0, []
    us = [t for t, (m, _) in wanted.items() if m == "US"]
    if us:
        start = date.fromisoformat(min(wanted[t][1] for t in us)) - timedelta(days=10)
        try:
            bars = await asyncio.to_thread(fetch_us, us, start, now)
        except Exception as exc:
            logger.warning("외부 추천 미국 시세 수집 실패: %s", type(exc).__name__)
            bars, failed = {}, us[:]
        for ticker in us:
            if bars.get(ticker):
                await get_db()[PRICES].update_one({"_id": ticker}, {"$set": {"market": "US", "bars": bars[ticker], "updated_at": now}}, upsert=True)
                stored += 1
            elif ticker not in failed:
                failed.append(ticker)
    for ticker in [t for t, (m, _) in wanted.items() if m == "KR"]:
        try:
            bars = await fetch_kr(ticker, date.fromisoformat(wanted[ticker][1]) - timedelta(days=10))
            await get_db()[PRICES].update_one({"_id": ticker}, {"$set": {"market": "KR", "bars": bars, "updated_at": now}}, upsert=True)
            stored += 1
        except Exception as exc:
            logger.warning("외부 추천 국내 시세 수집 실패 %s: %s", ticker, type(exc).__name__)
            failed.append(ticker)
    return {"tickers": len(wanted), "stored": stored, "failed": failed}


_pending: set[str] = set()


def schedule_prices(tickers: set[str]) -> None:
    """저장 직후 새 종목 시세만 바로 한 번 받는다(정기 수집을 기다리지 않게)."""
    new = tickers - _pending
    if not new:
        return
    _pending.update(new)

    async def run() -> None:
        try:
            await collect_prices(new)
        except Exception:
            logger.warning("외부 추천 시세 즉시 수집 실패", exc_info=True)
        finally:
            _pending.difference_update(new)

    asyncio.get_running_loop().create_task(run())


# ── 5. 성과(읽기 시 계산) ──────────────────────────────────────────

def position_path(bars: dict[str, list[float]], as_of: str, exit_date: str | None = None) -> dict | None:
    """기록일 다음 거래일 시가 진입. exit_date가 있으면 그날 시가에 청산(다음 명단으로 교체), 없으면 최신 종가."""
    days = sorted(day for day in bars if day > as_of)
    if not days:
        return None
    entry_day = days[0]
    entry = bars[entry_day][0]
    held = [day for day in days if exit_date is None or day < exit_date]
    if exit_date is not None and exit_date in bars:
        last_day, last = exit_date, bars[exit_date][0]
    else:
        last_day, last = held[-1], bars[held[-1]][1]
    horizons = {}
    for label, sessions in HORIZONS.items():
        if len(held) > sessions:
            horizons[label] = round((bars[held[sessions]][1] / entry - 1) * 100, 2)
    return {"entry_date": entry_day, "entry_price": round(entry, 4), "last_date": last_day, "last_price": round(last, 4),
            "return_pct": round((last / entry - 1) * 100, 2), "sessions": len(held), "horizons": horizons}


def list_performance(doc: dict, prices: dict[str, dict], exit_date: str | None = None) -> dict:
    rows, pending = [], []
    for item in doc["items"]:
        path = position_path((prices.get(item["ticker"]) or {}).get("bars") or {}, doc["as_of"], exit_date)
        bench_path = position_path((prices.get(BENCHMARK[item["market"]]) or {}).get("bars") or {}, doc["as_of"], exit_date)
        if path is None:
            pending.append(item["ticker"])
            continue
        excess = round(path["return_pct"] - bench_path["return_pct"], 2) if bench_path else None
        rows.append({**item, **path, "benchmark_return_pct": bench_path["return_pct"] if bench_path else None, "excess_pct": excess})
    priced = [row for row in rows if row["benchmark_return_pct"] is not None]
    summary = None
    if priced:
        mean = lambda key: round(sum(row[key] for row in priced) / len(priced), 2)  # noqa: E731
        summary = {"return_pct": mean("return_pct"), "benchmark_return_pct": mean("benchmark_return_pct"),
                   "excess_pct": mean("excess_pct"), "beat_rate_pct": round(100 * sum(row["excess_pct"] > 0 for row in priced) / len(priced), 1),
                   "priced": len(priced), "entry_date": min(row["entry_date"] for row in priced),
                   "sessions": max(row["sessions"] for row in priced)}
    return {"positions": sorted(rows, key=lambda row: -row["return_pct"]), "pending": pending, "summary": summary}


def source_track(docs: list[dict], prices: dict[str, dict]) -> dict:
    """같은 출처의 명단을 기록 순서대로 갈아탄 연결 성과(교체당 SWITCH_COST_PCT 차감)."""
    docs = sorted(docs, key=lambda d: (d["as_of"], d["created_at"]))
    periods, growth, bench_growth = [], 1.0, 1.0
    for index, doc in enumerate(docs):
        next_entry = None
        if index + 1 < len(docs):
            nxt = list_performance(docs[index + 1], prices)["summary"]
            next_entry = nxt["entry_date"] if nxt else None
            if next_entry is None:
                break
        perf = list_performance(doc, prices, next_entry)["summary"]
        if not perf:
            break
        net = perf["return_pct"] - SWITCH_COST_PCT
        growth *= 1 + net / 100
        bench_growth *= 1 + perf["benchmark_return_pct"] / 100
        periods.append({"as_of": doc["as_of"], "list_name": doc["list_name"], "until": next_entry or "현재",
                        "return_pct": round(net, 2), "benchmark_return_pct": perf["benchmark_return_pct"]})
    return {"periods": periods, "return_pct": round((growth - 1) * 100, 2), "benchmark_return_pct": round((bench_growth - 1) * 100, 2),
            "excess_pct": round((growth - bench_growth) * 100, 2), "months": len(periods)}


async def user_report(user_id) -> dict:
    docs = await get_db()[LISTS].find({"user_id": user_id}).sort([("as_of", -1), ("created_at", -1)]).to_list(500)
    tickers = {item["ticker"] for doc in docs for item in doc["items"]} | {BENCHMARK[i["market"]] for d in docs for i in d["items"]}
    prices = {doc["_id"]: doc async for doc in get_db()[PRICES].find({"_id": {"$in": sorted(tickers)}})}
    missing = {t for t in tickers if t not in prices}
    if missing:
        schedule_prices(missing)
    lists = []
    for doc in docs:
        perf = list_performance(doc, prices)
        lists.append({"id": str(doc["_id"]), "source": doc["source"], "list_name": doc["list_name"], "as_of": doc["as_of"],
                      "count": len(doc["items"]), "created_at": doc["created_at"].replace(tzinfo=timezone.utc).isoformat(), **perf})
    by_source: dict[str, list[dict]] = {}
    for doc in docs:
        by_source.setdefault(doc["source"], []).append(doc)
    updated = [doc.get("updated_at") for doc in prices.values() if doc.get("updated_at")]
    return {"lists": lists, "tracks": {source: source_track(items, prices) for source, items in by_source.items()},
            "prices_updated_at": max(updated).replace(tzinfo=timezone.utc).isoformat() if updated else None,
            "note": ("기록한 날 다음 거래일 시가에 동일 비중으로 샀다고 본 전향 관측입니다. 미국은 SPY, 국내는 KODEX 200과 비교하며 "
                     "분할·배당 조정 가격, 매매비용 미반영(연결 성과는 교체당 0.1% 차감). 표본이 적을 때는 판단 근거로 쓰지 마세요. 투자 권유가 아닙니다.")}
