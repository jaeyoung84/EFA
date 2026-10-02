from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import io
import json
import asyncio
import os
import re
import time
from threading import Lock
from time import monotonic
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from .openapi_docs import install_openapi
except ImportError:
    from openapi_docs import install_openapi

try:
    from .db import get_db
except ImportError:
    from db import get_db


try:
    from .dart_market_activity import ensure_market_activity_indexes, latest_market_activity
except ImportError:
    from dart_market_activity import ensure_market_activity_indexes, latest_market_activity  # type: ignore
try:
    from .sector_rotation import read_sector_rotation
    from .stock_sector import sector_status
    from .stock_risk import DISCLOSURE_WINDOW_DAYS, risk_summary
    from . import market_movers
    from .kis_quotes import kis_service
    from .live_quotes import provider_status as live_quote_provider_status, resolve_quotes as resolve_live_quotes
    from .instrument_catalog import SEARCH_ALIASES, get_krx_catalog, resolve_krx_name, search_krx_instruments
except ImportError:
    from sector_rotation import read_sector_rotation  # type: ignore
    from kis_quotes import kis_service  # type: ignore
    from live_quotes import provider_status as live_quote_provider_status, resolve_quotes as resolve_live_quotes  # type: ignore
    from stock_sector import sector_status  # type: ignore
    from stock_risk import DISCLOSURE_WINDOW_DAYS, risk_summary  # type: ignore
    import market_movers  # type: ignore
    from instrument_catalog import SEARCH_ALIASES, get_krx_catalog, resolve_krx_name, search_krx_instruments  # type: ignore

try:
    from .market_data import (MarketDataError, core_market_freshness, ensure_market_data_indexes,
                              get_daily_ohlcv, market_data_operations_report, market_data_status, market_factor_snapshot,
                              market_factor_universe_snapshot)
except ImportError:
    from market_data import (MarketDataError, core_market_freshness, ensure_market_data_indexes,
                             get_daily_ohlcv, market_data_operations_report, market_data_status, market_factor_snapshot,
                             market_factor_universe_snapshot)  # type: ignore

try:
    import orjson
except ImportError:
    DEFAULT_RESPONSE_CLASS = JSONResponse
else:
    class FastORJSONResponse(Response):
        media_type = "application/json"

        def render(self, content: object) -> bytes:
            return orjson.dumps(content)

    DEFAULT_RESPONSE_CLASS = FastORJSONResponse

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "app" / "frontend"
GENERATED_DIR = ROOT_DIR / "app" / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
_MATPLOTLIB_FONT_CONFIGURED = False
ACTIVE_VISITOR_TTL_SECONDS = 90
_active_visitors: dict[str, float] = {}
_active_visitors_lock = Lock()

def _no_market_data_detail(subject: str, reason: str) -> str:
    """Error text for a failed quote fetch: no synthetic data, the cause and when it was tried."""
    tried_at = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")
    return f"{subject} 데이터 없음 · 원인: {reason[:200]} · 조회 시각: {tried_at}"


def configure_matplotlib_korean_font(plt) -> None:
    """Use an installed Korean font when Matplotlib renders Korean labels."""
    global _MATPLOTLIB_FONT_CONFIGURED
    if _MATPLOTLIB_FONT_CONFIGURED:
        return

    import matplotlib.font_manager as fm

    candidates = [
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for font_path in candidates:
        if font_path.exists():
            fm.fontManager.addfont(str(font_path))
            font_name = fm.FontProperties(fname=str(font_path)).get_name()
            plt.rcParams["font.family"] = font_name
            break
    plt.rcParams["axes.unicode_minus"] = False
    _MATPLOTLIB_FONT_CONFIGURED = True


app = FastAPI(
    title="EFA API",
    version="2.0.0",
    description=(
        "EFA(Economic Freedom Automation) 개인 퀀트 리서치 및 포트폴리오 API"
    ),
    default_response_class=DEFAULT_RESPONSE_CLASS,
)

app.add_middleware(GZipMiddleware, minimum_size=1024)

# 화면은 같은 도메인에서 API를 부르므로 다른 사이트의 브라우저 호출만 허용 목록으로 제한한다.
# 로그인은 Authorization 헤더(Bearer)라 쿠키 자격 증명은 넘기지 않는다.
CORS_ALLOW_ORIGINS = [origin.strip() for origin in os.getenv(
    "CORS_ALLOW_ORIGINS", "https://efa-quant.com,https://www.efa-quant.com").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 화면이 실제로 쓰는 출처만 허용한다: 자체 JS·API, ApexCharts(jsDelivr 고정 버전), 글꼴·아이콘 CSS(jsDelivr·Google·cdnjs),
# Cloudflare가 자동으로 넣는 방문 통계(Web Analytics) 스크립트와 그 전송 주소.
# 인라인 스크립트는 허용하지 않는다(XSS로 로그인 토큰을 빼 가는 것을 막는다). 인라인 style 속성은 화면 전반에 쓰여 허용한다.
# 응답이 자체 CSP를 정하면(네이버 로그인 결과 페이지의 nonce) 그것을 그대로 둔다.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net/npm/apexcharts@3.54.0/ https://static.cloudflareinsights.com",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net/gh/orioncactus/pretendard/ https://fonts.googleapis.com "
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/",
    "font-src 'self' data: https://cdn.jsdelivr.net/gh/orioncactus/pretendard/ https://fonts.gstatic.com "
    "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/",
    "img-src 'self' data: blob:",
    "connect-src 'self' https://cloudflareinsights.com",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "upgrade-insecure-requests",
])
SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}
# FastAPI 기본 API 문서 화면은 인라인 스크립트와 CDN을 써서 CSP에서 뺀다(로그인 토큰을 다루지 않는다).
CSP_EXEMPT_PREFIXES = ("/docs", "/redoc")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    exempt = request.url.path.startswith(CSP_EXEMPT_PREFIXES)
    for name, value in SECURITY_HEADERS.items():
        if exempt and name == "Content-Security-Policy":
            continue
        response.headers.setdefault(name, value)
    return response


try:
    from .routers.auth import router as auth_router, current_user
    from .routers.portfolio import router as portfolio_router
    from .routers.strategy import router as strategy_router
    from .routers.kqs import router as kqs_router
    from .routers.research import router as research_router
except ImportError:  # Allows `uvicorn main:app` from app/backend.
    from routers.auth import router as auth_router, current_user  # type: ignore
    from routers.portfolio import router as portfolio_router  # type: ignore
    from routers.strategy import router as strategy_router  # type: ignore
    from routers.kqs import router as kqs_router  # type: ignore
    from routers.research import router as research_router  # type: ignore
app.include_router(auth_router)
app.include_router(portfolio_router)
app.include_router(strategy_router)
app.include_router(kqs_router)
app.include_router(research_router)
# Routers registered below are also included before the schema is first requested;
# the OpenAPI factory is installed at the bottom of this module.

try:
    from .scheduler import start_scheduler, stop_scheduler
except ImportError:
    from scheduler import start_scheduler, stop_scheduler  # type: ignore


@app.on_event("startup")
async def _start_background_scheduler() -> None:
    try:
        await ensure_market_data_indexes()
    except Exception as exc:
        # 시세 DB가 잠시 준비되지 않아도 기존 화면과 스케줄러 기동은 유지한다.
        print(f"Market-data indexes not ready during startup: {exc}")
    start_scheduler()
    try:
        await ensure_market_activity_indexes()
    except Exception as exc:
        print(f"DART market-activity indexes not ready during startup: {exc}")
    kis_service().start()   # 한투 키가 없으면 아무것도 하지 않는다


@app.on_event("shutdown")
async def _stop_background_scheduler() -> None:
    stop_scheduler()
    await kis_service().stop()

@app.middleware("http")
async def no_cache_static_assets(request, call_next):
    """Always fetch the latest local HTML, JavaScript, and CSS on page load.

    StaticFiles normally uses ETag/Last-Modified revalidation. The learning
    app is deployed as a single image, so an old shell or module can otherwise
    survive a deployment in a browser cache. ``no-store`` plus legacy
    revalidation headers makes local frontend assets non-cacheable for both
    browsers and intermediary proxies.
    """
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


class DartCompanySearchRequest(BaseModel):
    company_name: str = Field(default="삼성전자", min_length=1, max_length=80)
    limit: int = Field(default=10, ge=1, le=30)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


def _read_cpu_times() -> tuple[int, int]:
    """Return total and idle CPU ticks from Linux procfs."""
    with open("/proc/stat", encoding="utf-8") as proc_stat:
        first_line = proc_stat.readline().split()
    values = [int(value) for value in first_line[1:]]
    total = sum(values)
    # Linux reports idle and iowait separately; both mean the CPU was not
    # executing application work for this interval.
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return total, idle


def _memory_usage() -> tuple[int, int]:
    """Return total and used memory bytes, preferring MemAvailable on Linux."""
    meminfo: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as proc_meminfo:
            for line in proc_meminfo:
                key, raw_value, *_ = line.split()
                meminfo[key.rstrip(":")] = int(raw_value) * 1024
        total = meminfo["MemTotal"]
        available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
        return total, max(total - available, 0)
    except (FileNotFoundError, KeyError, ValueError):
        page_size = os.sysconf("SC_PAGE_SIZE")
        total = os.sysconf("SC_PHYS_PAGES") * page_size
        available = os.sysconf("SC_AVPHYS_PAGES") * page_size
        return total, max(total - available, 0)


@app.get("/api/system/resources")
def system_resources() -> dict[str, object]:
    """Return host CPU, memory and root-disk utilization for the admin view."""
    try:
        total_before, idle_before = _read_cpu_times()
        time.sleep(0.1)
        total_after, idle_after = _read_cpu_times()
        total_delta = total_after - total_before
        idle_delta = idle_after - idle_before
        cpu_used = 0.0 if total_delta <= 0 else (1 - idle_delta / total_delta) * 100

        memory_total, memory_used = _memory_usage()
        disk = os.statvfs("/")
        disk_total = disk.f_frsize * disk.f_blocks
        disk_free = disk.f_frsize * disk.f_bavail
        disk_used = max(disk_total - disk_free, 0)

        def usage(total: int, used: int) -> dict[str, int | float]:
            percent = (used / total * 100) if total else 0.0
            return {"total_bytes": total, "used_bytes": used,
                    "free_bytes": max(total - used, 0), "used_percent": round(percent, 1)}

        return {
            "cpu": {"used_percent": round(max(0.0, min(cpu_used, 100.0)), 1),
                    "logical_cores": os.cpu_count() or 1},
            "memory": usage(memory_total, memory_used),
            "disk": usage(disk_total, disk_used),
            "disk_mount": "/",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"서버 리소스 정보를 읽을 수 없습니다: {exc}") from exc


class VisitorHeartbeatRequest(BaseModel):
    visitor_id: str = Field(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


@app.post("/api/visitors/heartbeat")
def visitor_heartbeat(payload: VisitorHeartbeatRequest) -> dict[str, int]:
    """Register one browser briefly and return the current active-browser count.

    This is an in-memory presence indicator, not an analytics counter. A browser
    remains active for 90 seconds after its latest heartbeat, so closed tabs
    disappear without retaining personally identifiable visit history.
    """
    now = monotonic()
    with _active_visitors_lock:
        expired = [
            visitor_id
            for visitor_id, last_seen in _active_visitors.items()
            if now - last_seen > ACTIVE_VISITOR_TTL_SECONDS
        ]
        for visitor_id in expired:
            _active_visitors.pop(visitor_id, None)
        _active_visitors[payload.visitor_id] = now
        count = len(_active_visitors)
    return {"active_visitors": count}


def _dart_api_key() -> str:
    key = os.getenv("DART_API_KEY") or os.getenv("OPENDART_API_KEY")
    if not key:
        raise HTTPException(
            status_code=503,
            detail="DART_API_KEY 또는 OPENDART_API_KEY 환경변수를 설정하세요.",
        )
    return key


_DART_CORP_LOCK = Lock()


def _load_dart_corp_codes() -> list[dict[str, str]]:
    """DART 회사코드 목록(약 10만 개). 프로세스당 한 번만 받는다.

    처음 쓰일 때 여러 요청이 동시에 오면(추천 종목 섹터·위험 공시를 병렬 조회) 스레드마다 같은 대용량 XML을
    따로 내려받고 해석해 메모리가 바닥났다(2026-10-02 장애). 잠금으로 한 스레드만 만들고 나머지는 그 결과를 기다린다.
    """
    with _DART_CORP_LOCK:
        return _load_dart_corp_codes_once()


@lru_cache(maxsize=1)
def _load_dart_corp_codes_once() -> list[dict[str, str]]:
    key = _dart_api_key()
    url = "https://opendart.fss.or.kr/api/corpCode.xml?" + urllib.parse.urlencode({"crtfc_key": key})
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = response.read()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DART 회사코드 목록 수신 실패: {exc}") from exc

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            xml_name = zf.namelist()[0]
            xml_bytes = zf.read(xml_name)
        del payload
    except zipfile.BadZipFile as exc:
        message = payload[:400].decode("utf-8", errors="ignore")
        status = re.search(r"<status>(\d+)</status>", message)
        text = re.search(r"<message>(.*?)</message>", message)
        if status and status.group(1) in {"010", "011", "012", "901"}:
            raise HTTPException(status_code=503, detail=(
                f"DART 인증키를 확인해야 합니다(OpenDART {status.group(1)}: {text.group(1) if text else ''}). "
                "관리자가 서버의 DART_API_KEY를 갱신하면 다시 검색됩니다.")) from exc
        if status:
            raise HTTPException(status_code=502, detail=f"DART 응답 오류(OpenDART {status.group(1)}: "
                                                        f"{text.group(1) if text else ''})") from exc
        raise HTTPException(status_code=502, detail="DART 회사코드 목록을 해석하지 못했습니다.") from exc

    # 전체 트리를 메모리에 올리지 않고 회사 하나씩 읽고 버린다(최대 메모리 사용량을 줄인다).
    rows: list[dict[str, str]] = []
    for _, item in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
        if item.tag != "list":
            continue
        corp_code = (item.findtext("corp_code") or "").strip()
        corp_name = (item.findtext("corp_name") or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        modify_date = (item.findtext("modify_date") or "").strip()
        item.clear()
        if corp_code and corp_name:
            rows.append({
                "corp_code": corp_code,
                "corp_name": corp_name,
                "stock_code": stock_code,
                "modify_date": modify_date,
            })
    return rows


_load_dart_corp_codes.cache_clear = _load_dart_corp_codes_once.cache_clear  # type: ignore[attr-defined]


@lru_cache(maxsize=4096)
def _resolve_krx_yahoo_ticker(stock_code: str) -> dict[str, object]:
    if not stock_code:
        return {"ticker": None, "candidates": []}

    candidates = [f"{stock_code}.KS", f"{stock_code}.KQ"]
    found: list[str] = []
    for ticker in candidates:
        chart_url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            + urllib.parse.quote(ticker)
            + "?range=5d&interval=1d"
        )
        try:
            with urllib.request.urlopen(chart_url, timeout=4) as response:
                text = response.read(5000).decode("utf-8", errors="ignore")
            if '"regularMarketPrice"' in text or '"timestamp"' in text:
                found.append(ticker)
        except Exception:
            continue

    return {"ticker": found[0] if found else f"{stock_code}.KS", "candidates": found or candidates}


@app.post("/api/dart/company-search")
def dart_company_search(req: DartCompanySearchRequest) -> dict[str, object]:
    query = req.company_name.strip()
    normalized = query.replace(" ", "").lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="회사명을 입력하세요.")
    names = {normalized} | {target.replace(" ", "").lower() for alias, target in SEARCH_ALIASES.items()
                            if alias.replace(" ", "").lower() == normalized}

    rows = _load_dart_corp_codes()
    # EINT-018: 현재 상장 종목 목록(엔진 게시)이 있으면 상장폐지 종목을 빼고 시장(.KS/.KQ)을 그대로 쓴다.
    listed_now = {item["code"]: item for item in get_krx_catalog()}
    listed = [row for row in rows if row["stock_code"] and (not listed_now or row["stock_code"] in listed_now)]
    key = lambda row: row["corp_name"].replace(" ", "").lower()
    exact = [row for row in listed if key(row) in names]
    partial = [row for row in listed if any(name in key(row) for name in names) and row not in exact]
    partial.sort(key=lambda row: -int(listed_now.get(row["stock_code"], {}).get("market_cap") or 0))
    matches = (exact + partial)[: req.limit]

    results = []
    for row in matches:
        known = listed_now.get(row["stock_code"])
        if known:
            ticker, candidates, market = known["ticker"], [known["ticker"]], known.get("market", "")
        else:
            ticker_info = _resolve_krx_yahoo_ticker(row["stock_code"])
            ticker, candidates, market = ticker_info["ticker"], ticker_info["candidates"], ""
        results.append({
            **row, "ticker": ticker, "ticker_candidates": candidates, "market": market,
            "display": f'{ticker or row["stock_code"]}, {row["corp_name"]}',
        })

    return {
        "query": query,
        "count": len(results),
        "results": results,
        "source": "OpenDART corpCode.xml" + (" + KRX 상장 목록" if listed_now else ""),
        "notes": [
            "DART 회사코드 목록에서 현재 상장된 기업만 검색합니다." if listed_now else
            "DART 회사코드 목록에서 상장기업(stock_code 보유 기업)만 검색합니다.",
            "시장(.KS 코스피 / .KQ 코스닥)은 KRX 일봉 수집본의 상장 목록 기준입니다." if listed_now else
            "DART는 .KS/.KQ suffix를 제공하지 않아 조회 가능한 Yahoo ticker 후보로 보완 표시합니다.",
        ],
    }


@app.get("/api/market/sector-rotation")

@app.get("/api/dart/market-activity")
async def dart_market_activity() -> dict:
    """Latest scheduled, read-only DART treasury-stock and ownership snapshot."""
    return await latest_market_activity()

def market_sector_rotation() -> JSONResponse:
    """섹터 순환(RRG) 참고 자료: 엔진이 매 거래일 계산한 섹터 ETF의 상대강도·모멘텀과 최근 이동 궤적."""
    return JSONResponse(content=read_sector_rotation(), headers={"Cache-Control": "no-cache"})


@lru_cache(maxsize=10000)
def _stock_ksic(stock_code: str) -> str | None:
    """종목코드 → DART 업종코드(KSIC). 실패하면 None(다음 프로세스 재시작까지 캐시)."""
    corp = next((row for row in _load_dart_corp_codes() if row.get("stock_code") == stock_code), None)
    if not corp:
        return None
    return (_fetch_company_detail(corp["corp_code"]) or {}).get("induty_code") or None


@app.get("/api/market/stock-sectors")
async def market_stock_sectors(symbols: str = "", user: dict = Depends(current_user)) -> dict:
    """EINT-022: 종목(6자리 코드, 쉼표로 최대 30개)의 업종 섹터와 섹터 순환 현재 구역. 참고 표시용."""
    codes = list(dict.fromkeys(code.strip() for code in symbols.split(",") if re.fullmatch(r"[0-9A-Z]{6}", code.strip())))[:30]
    rotation = read_sector_rotation()

    def lookup(code: str) -> dict:
        try:
            ksic = _stock_ksic(code)
        except HTTPException:
            ksic = None
        return sector_status(code, ksic, rotation)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as pool:
        items = await asyncio.get_running_loop().run_in_executor(None, lambda: list(pool.map(lookup, codes)))
    return {"items": items, "rotation_as_of": rotation.get("latest_week"),
            "note": "업종은 DART 기업개황의 업종코드(한국표준산업분류) 기준, 구역은 섹터 순환(RRG) 기준입니다. 참고용입니다."}


@lru_cache(maxsize=1)
def _dart_corp_by_stock() -> dict[str, str]:
    return {row["stock_code"]: row["corp_code"] for row in _load_dart_corp_codes() if row.get("stock_code")}


def _dart_recent_disclosures(stock_code: str, today) -> list[dict]:
    """최근 30일 DART 공시 목록(제목·접수일·접수번호). 공시가 없으면 빈 목록, 조회 실패는 예외."""
    corp_code = _dart_corp_by_stock().get(stock_code)
    if not corp_code:
        raise RuntimeError("DART 기업 코드를 찾지 못했습니다.")
    query = urllib.parse.urlencode({"crtfc_key": _dart_api_key(), "corp_code": corp_code, "page_count": 100,
                                    "bgn_de": (today - timedelta(days=DISCLOSURE_WINDOW_DAYS)).strftime("%Y%m%d"),
                                    "end_de": today.strftime("%Y%m%d")})
    with urllib.request.urlopen("https://opendart.fss.or.kr/api/list.json?" + query, timeout=8) as response:
        data = json.loads(response.read())
    if data.get("status") == "013":   # 조회된 공시 없음
        return []
    if data.get("status") != "000":
        raise RuntimeError(f"DART 응답 {data.get('status')}: {data.get('message', '')}")
    return data.get("list") or []


_RISK_CACHE: dict[str, tuple[float, dict]] = {}
RISK_CACHE_SECONDS = 1800


@app.get("/api/market/stock-risk")
async def market_stock_risk(symbols: str = "", user: dict = Depends(current_user)) -> dict:
    """EINT-025: 종목(6자리 코드, 쉼표로 최대 30개)의 최근 30일 위험 공시와 거래소 지정 상태. 참고 표시용."""
    codes = list(dict.fromkeys(code.strip() for code in symbols.split(",") if re.fullmatch(r"[0-9A-Z]{6}", code.strip())))[:30]
    today = datetime.now(timezone(timedelta(hours=9))).date()
    service = kis_service()
    dart_gate = asyncio.Semaphore(4)   # DART 동시 조회 수 제한(스레드·메모리 보호)

    async def dart(code: str) -> list[dict]:
        async with dart_gate:
            return await asyncio.to_thread(_dart_recent_disclosures, code, today)

    async def one(code: str) -> dict:
        hit = _RISK_CACHE.get(code)
        if hit and monotonic() - hit[0] < RISK_CACHE_SECONDS and hit[1]["checked_on"] == today.isoformat():
            return hit[1]
        errors: dict[str, str] = {}
        try:
            disclosures = await dart(code)
        except HTTPException as exc:
            disclosures, errors["dart"] = None, f"DART 공시 조회 실패: {exc.detail}"
        except Exception as exc:
            disclosures, errors["dart"] = None, f"DART 공시 조회 실패: {exc}"
        status = None
        if service.enabled:
            try:
                status = await service.stock_status(f"{code}.KS")   # 한투 국내 현재가는 시장 구분 없이 코드로 조회
            except Exception as exc:
                errors["krx"] = f"거래소 지정 상태 조회 실패: {exc}"
        else:
            errors["krx"] = "한투 키가 없어 거래소 지정 상태는 확인하지 않았습니다."
        result = risk_summary(code, disclosures=disclosures, status_output=status, errors=errors, checked_on=today)
        if not errors:
            _RISK_CACHE[code] = (monotonic(), result)
        return result

    items = await asyncio.gather(*(one(code) for code in codes))
    return {"items": items, "window_days": DISCLOSURE_WINDOW_DAYS,
            "note": "최근 30일 DART 공시 제목과 거래소 지정 상태(한투)에서 찾은 위험 신호입니다. 참고용이며 추천 규칙과 무관합니다."}


@app.get("/api/market/instruments")
def market_instruments(q: str = "", limit: int = 10) -> dict[str, object]:
    """국내 상장 종목을 종목명·별칭·종목코드로 검색한다(엔진이 매일 게시하는 KRX 상장 목록)."""
    query = q.strip()
    if not query:
        return {"items": [], "as_of": None}
    items = search_krx_instruments(query, limit=max(1, min(limit, 20)))
    return {"items": [{"ticker": i["ticker"], "code": i["code"], "name": i["name"], "market": i.get("market") or i.get("exchange"),
                       "source": i.get("source")} for i in items],
            "as_of": items[0].get("as_of") if items else None}


def _resolve_ticker_or_name(value: str) -> str:
    """'005930.KS'·'AAPL' 같은 티커는 그대로, 한글/종목명은 국내 상장 목록으로 티커를 찾는다."""
    text = value.strip()
    if re.fullmatch(r"[A-Za-z0-9.^=\-]{1,24}", text) and not re.fullmatch(r"\d{6}", text):
        return text.upper()
    found = resolve_krx_name(text)
    if found is None:
        raise HTTPException(status_code=404, detail=f"'{text}' 종목을 찾지 못했습니다. 종목명을 정확히 입력하거나 목록에서 선택하세요.")
    return found["ticker"]


@lru_cache(maxsize=10000)
def _fetch_company_detail(corp_code: str) -> dict:
    """Fetch company overview (address, bizr_no) from DART company.json."""
    key = _dart_api_key()
    url = ("https://opendart.fss.or.kr/api/company.json?"
           + urllib.parse.urlencode({"crtfc_key": key, "corp_code": corp_code}))
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read())
        return data if data.get("status") == "000" else {}
    except Exception:
        return {}


# ── 산업 경쟁력 분석 ─────────────────────────────────────────────────────────

class SectorRequest(BaseModel):
    tickers: list[str] = ["SOXX", "XLE", "XLF", "XLV", "XLK", "XLI"]
    period:  str       = "1y"

class PeerRequest(BaseModel):
    tickers: dict[str, str] = {
        "삼성전자": "005930.KS",
        "SK하이닉스": "000660.KS",
        "엔비디아": "NVDA",
        "인텔": "INTC",
    }

SECTOR_LABELS = {
    "SOXX": "반도체 (SOXX)", "XLE": "에너지 (XLE)", "XLF": "금융 (XLF)",
    "XLV":  "헬스케어 (XLV)", "XLK": "기술 (XLK)",  "XLI": "산업재 (XLI)",
    "XLY":  "소비재경기 (XLY)","XLP": "소비재필수 (XLP)","XLB": "소재 (XLB)",
    "XLRE": "부동산 (XLRE)",  "XLU": "유틸리티 (XLU)","IBB": "바이오 (IBB)",
}

@app.post("/api/industry/sector")
def industry_sector(req: SectorRequest) -> dict[str, object]:
    import yfinance as yf
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    import numpy as np
    import pandas as pd
    import io, base64
    configure_matplotlib_korean_font(plt)

    DARK, SURF, BORDER, TEXT, MUTED = "#0f172a","#1e293b","#334155","#e2e8f0","#64748b"
    COLORS = ["#3b82f6","#22c55e","#f59e0b","#ef4444","#a855f7",
              "#06b6d4","#f97316","#84cc16","#ec4899","#14b8a6","#8b5cf6","#fb923c"]

    raw: dict[str, pd.Series] = {}
    for t in req.tickers:
        try:
            df = yf.download(t, period=req.period, progress=False, auto_adjust=True)
            if df.empty: continue
            s = df["Close"]
            if isinstance(s, pd.DataFrame): s = s.iloc[:, 0]
            s = s.dropna()
            if len(s) > 5: raw[t] = s
        except Exception:
            pass

    if not raw:
        raise HTTPException(status_code=503, detail="데이터를 가져올 수 없습니다.")

    fig = plt.figure(figsize=(14, 10), facecolor=DARK)
    gs  = gridspec.GridSpec(2, 2, figure=fig, hspace=0.42, wspace=0.32,
                            left=0.07, right=0.97, top=0.92, bottom=0.07)

    # Panel 1: 정규화 수익률
    ax1 = fig.add_subplot(gs[0, :], facecolor=SURF)
    for i, (t, s) in enumerate(raw.items()):
        norm = (s / s.iloc[0] - 1) * 100
        ax1.plot(norm.index, norm.values, color=COLORS[i % len(COLORS)],
                 lw=1.8, label=SECTOR_LABELS.get(t, t))
    ax1.axhline(0, color=MUTED, lw=0.8, ls="--")
    ax1.set_title("섹터 ETF 정규화 누적 수익률 (%)", color=TEXT, fontsize=10, pad=6)
    ax1.tick_params(colors=TEXT, labelsize=7)
    ax1.spines[:].set_color(BORDER)
    ax1.legend(fontsize=7, facecolor=SURF, labelcolor=TEXT,
               ncol=4, loc="upper left", framealpha=0.7)
    ax1.tick_params(axis="x", rotation=30)
    for lbl in ax1.get_xticklabels(): lbl.set_fontsize(6)

    # Panel 2: 기간 수익률 바
    ax2 = fig.add_subplot(gs[1, 0], facecolor=SURF)
    names, returns, cols = [], [], []
    for i, (t, s) in enumerate(raw.items()):
        r = (s.iloc[-1] / s.iloc[0] - 1) * 100
        names.append(SECTOR_LABELS.get(t, t))
        returns.append(r)
        cols.append(COLORS[i % len(COLORS)])
    order = sorted(range(len(returns)), key=lambda x: returns[x], reverse=True)
    names_s  = [names[i] for i in order]
    returns_s = [returns[i] for i in order]
    cols_s   = [cols[i] for i in order]
    bars = ax2.barh(names_s, returns_s, color=cols_s, height=0.6)
    ax2.axvline(0, color=MUTED, lw=0.8)
    for bar, v in zip(bars, returns_s):
        ax2.text(v + (0.3 if v >= 0 else -0.3), bar.get_y() + bar.get_height()/2,
                 f"{v:+.1f}%", va="center", ha="left" if v >= 0 else "right",
                 fontsize=7, color=TEXT)
    ax2.set_title(f"기간 수익률 순위 ({req.period})", color=TEXT, fontsize=10, pad=6)
    ax2.tick_params(colors=TEXT, labelsize=7)
    ax2.spines[:].set_color(BORDER)

    # Panel 3: 변동성 vs 수익률 (리스크-리턴 산점도)
    ax3 = fig.add_subplot(gs[1, 1], facecolor=SURF)
    for i, (t, s) in enumerate(raw.items()):
        ret  = (s.iloc[-1] / s.iloc[0] - 1) * 100
        vol  = s.pct_change().std() * (252**0.5) * 100
        ax3.scatter(vol, ret, color=COLORS[i % len(COLORS)], s=100, zorder=3)
        ax3.text(vol + 0.3, ret, SECTOR_LABELS.get(t, t).split("(")[0].strip(),
                 fontsize=6.5, color=TEXT)
    ax3.axhline(0, color=MUTED, lw=0.8, ls="--")
    ax3.set_xlabel("연환산 변동성 (%)", color=MUTED, fontsize=8)
    ax3.set_ylabel(f"수익률 ({req.period}) %", color=MUTED, fontsize=8)
    ax3.set_title("리스크-리턴 산점도", color=TEXT, fontsize=10, pad=6)
    ax3.tick_params(colors=TEXT, labelsize=7)
    ax3.spines[:].set_color(BORDER)
    ax3.grid(color=BORDER, lw=0.5, alpha=0.5)

    fig.suptitle(f"섹터 주가 비교 분석  |  {req.period}",
                 color=TEXT, fontsize=12, fontweight="bold", y=0.97)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, facecolor=DARK, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    img = "data:image/png;base64," + base64.b64encode(buf.read()).decode()

    summary = {}
    for t, s in raw.items():
        summary[SECTOR_LABELS.get(t, t)] = {
            "return_pct": round((s.iloc[-1]/s.iloc[0]-1)*100, 2),
            "annual_vol":  round(s.pct_change().std()*(252**0.5)*100, 2),
        }
    return {"image": img, "summary": summary}


@app.post("/api/industry/peer")
def industry_peer(req: PeerRequest) -> dict[str, object]:
    import math

    import yfinance as yf

    if not req.tickers:
        raise HTTPException(status_code=400, detail="비교할 종목을 1개 이상 입력하세요.")
    if len(req.tickers) > 12:
        raise HTTPException(status_code=400, detail="Peer Comparison은 최대 12개 종목까지 지원합니다.")

    def as_float(value):
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(number) or math.isinf(number):
            return None
        return number

    rows: list[dict[str, object]] = []
    for name, ticker in req.tickers.items():
        label = (name or ticker).strip()[:40]
        symbol = (ticker or "").strip().upper()
        if not symbol:
            # EINT-018: 티커 없이 종목명만 주면 국내 상장 목록으로 찾는다.
            found = resolve_krx_name(name or "")
            if found is None:
                rows.append({"company": label, "ticker": "", "error": "종목을 찾지 못했습니다. 정확한 종목명을 입력하세요."})
                continue
            symbol, label = found["ticker"], found["name"]
        try:
            info = yf.Ticker(symbol).info
        except Exception as exc:
            rows.append({
                "company": label,
                "ticker": symbol,
                "error": f"데이터 수신 실패: {exc}",
            })
            continue

        market_cap = as_float(info.get("marketCap"))
        revenue_growth = as_float(info.get("revenueGrowth"))
        operating_margin = as_float(info.get("operatingMargins"))
        debt_to_equity = as_float(info.get("debtToEquity"))
        roe = as_float(info.get("returnOnEquity"))
        per = as_float(info.get("trailingPE"))
        pbr = as_float(info.get("priceToBook"))

        rows.append({
            "company": label,
            "ticker": symbol,
            "market_cap_krw_100m": round(market_cap / 1e8, 0) if market_cap is not None else None,
            "revenue_growth_pct": round(revenue_growth * 100, 1) if revenue_growth is not None else None,
            "operating_margin_pct": round(operating_margin * 100, 1) if operating_margin is not None else None,
            "per": round(per, 1) if per is not None else None,
            "pbr": round(pbr, 2) if pbr is not None else None,
            "debt_to_equity_pct": round(debt_to_equity, 1) if debt_to_equity is not None else None,
            "roe_pct": round(roe * 100, 1) if roe is not None else None,
            "currency": info.get("currency"),
            "sector": info.get("sector"),
        })

    if not rows:
        raise HTTPException(status_code=400, detail="유효한 종목 코드가 없습니다.")

    valid_rows = [r for r in rows if not r.get("error")]
    leader = None
    if valid_rows:
        leader = max(
            valid_rows,
            key=lambda r: (
                r.get("operating_margin_pct") if r.get("operating_margin_pct") is not None else -999,
                r.get("roe_pct") if r.get("roe_pct") is not None else -999,
            ),
        ).get("company")

    return {
        "rows": rows,
        "leader": leader,
        "notes": [
            "동종 기업 여부를 먼저 확인한 뒤 멀티플 차이를 해석하세요.",
            "PER/PBR은 성장률, 수익성, 재무건전성과 함께 봐야 합니다.",
            "Yahoo Finance 항목 누락 시 일부 값은 빈칸으로 표시됩니다.",
        ],
    }


# ── 거시경제현황 1: yfinance 실시간 ──────────────────────────────────────────

class MacroRealtimeRequest(BaseModel):
    tickers: list[str] = ["^TNX", "CL=F", "^GSPC", "^KS11", "GC=F", "EURUSD=X"]
    period:  str       = "1y"   # 1mo 3mo 6mo 1y 2y 5y


class MarketSnapshotRequest(BaseModel):
    tickers: list[str] = ["^KS11", "^IXIC", "KRW=X"]


class LiveQuotesRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=40)


class MarketDataRefreshRequest(BaseModel):
    tickers: list[str] = Field(default_factory=lambda: ["^KS11", "^KQ11", "^GSPC", "^IXIC", "KRW=X"], min_length=1, max_length=10)
    days: int = Field(default=370, ge=1, le=3650)


class PortfolioCombinationRequest(BaseModel):
    ticker_a: str = Field(default="AAPL", min_length=1, max_length=20)
    ticker_b: str = Field(default="JNJ", min_length=1, max_length=20)
    period: str = Field(default="1y", pattern=r"^(3mo|6mo|1y|2y)$")


MARKET_SNAPSHOT_LABELS = {
    "^KS11": "KOSPI",
    "^IXIC": "NASDAQ",
    "KRW=X": "USD/KRW",
}

TICKER_LABELS = {
    "^TNX":     "미국 10년물 금리",
    "CL=F":     "WTI 유가",
    "^GSPC":    "S&P 500",
    "^KS11":    "KOSPI",
    "GC=F":     "금 (Gold)",
    "EURUSD=X": "EUR/USD",
    "BTC-USD":  "Bitcoin",
    "^IRX":     "미국 단기금리(3M)",
    "^VIX":     "VIX 공포지수",
    "DX-Y.NYB": "달러 인덱스",
}

def _extract_close_series(frame):
    close = frame["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    return close.dropna()


@app.post("/api/market/portfolio-combination")
def portfolio_combination(req: PortfolioCombinationRequest) -> dict[str, object]:
    """Compare two real tickers and return a plain-language diversification signal."""
    import pandas as pd
    import yfinance as yf

    def clean_ticker(raw: str) -> str:
        ticker = raw.strip().upper()
        if not re.fullmatch(r"[A-Z0-9.^=\-]{1,20}", ticker):
            raise HTTPException(status_code=422, detail="올바른 종목 코드를 입력해 주세요.")
        return ticker

    ticker_a = clean_ticker(req.ticker_a)
    ticker_b = clean_ticker(req.ticker_b)
    if ticker_a == ticker_b:
        raise HTTPException(status_code=422, detail="서로 다른 두 종목을 선택해 주세요.")

    prices: dict[str, pd.Series] = {}
    unavailable: list[str] = []
    for ticker in (ticker_a, ticker_b):
        try:
            frame = yf.download(ticker, period=req.period, interval="1d", progress=False,
                                auto_adjust=True, threads=False)
            close = _extract_close_series(frame)
            if len(close) < 21:
                unavailable.append(ticker)
            else:
                prices[ticker] = close
        except Exception:
            unavailable.append(ticker)

    if unavailable:
        names = ", ".join(unavailable)
        raise HTTPException(status_code=422, detail=f"{names}의 충분한 가격 데이터를 찾지 못했습니다.")

    # 거래일이 겹치는 구간의 일간 변화만 이용한다. 화면에는 수식 대신 신호와 문장만 노출한다.
    aligned_prices = pd.concat(prices, axis=1, join="inner").dropna()
    daily_moves = aligned_prices.pct_change(fill_method=None).dropna()
    if len(daily_moves) < 20:
        raise HTTPException(status_code=422, detail="두 종목의 함께 비교할 수 있는 거래일이 부족합니다.")
    relationship = float(daily_moves.corr().iloc[0, 1])

    if relationship < 0.30:
        signal = "green"
        summary = "최근 흐름이 비교적 다르게 나타났습니다. 함께 담을 때 한 종목에만 의존하는 정도를 낮추는 데 도움이 될 수 있습니다."
        hint = "두 종목의 움직임이 겹치는 정도가 낮은 편입니다. 업종과 보유 비중도 함께 확인해 보세요."
    elif relationship < 0.70:
        signal = "yellow"
        summary = "최근에는 일부 구간에서 함께 움직였습니다. 분산 효과는 기대할 수 있지만 크기는 제한적일 수 있습니다."
        hint = "조합의 균형은 보통 수준입니다. 다른 업종이나 자산을 더하면 포트폴리오 폭을 넓힐 수 있습니다."
    else:
        signal = "red"
        summary = "최근 가격 흐름이 자주 같은 방향으로 움직였습니다. 두 종목을 함께 담아도 분산 효과가 작을 수 있습니다."
        hint = "한 종목의 영향이 다른 종목에도 이어질 수 있습니다. 업종이 다른 종목이나 다른 자산을 함께 검토해 보세요."

    period_labels = {"3mo": "최근 3개월", "6mo": "최근 6개월", "1y": "최근 1년", "2y": "최근 2년"}
    latest_data_at = pd.Timestamp(daily_moves.index[-1]).isoformat()
    # 두 종목의 가격 단위가 달라도 흐름을 한 차트에서 비교할 수 있도록 출발선을 맞춘다.
    chart_base = aligned_prices / aligned_prices.iloc[0] * 100
    chart_points = [
        {
            "date": pd.Timestamp(index).date().isoformat(),
            "a": round(float(row[ticker_a]), 4),
            "b": round(float(row[ticker_b]), 4),
        }
        for index, row in chart_base.iterrows()
    ]
    return {
        "ticker_a": ticker_a,
        "ticker_b": ticker_b,
        "period_label": period_labels[req.period],
        "signal": signal,
        "summary": summary,
        "portfolio_hint": hint,
        "latest_data_at": latest_data_at,
        "chart_points": chart_points,
    }


@app.post("/api/market/snapshot")
def market_snapshot(req: MarketSnapshotRequest) -> dict[str, object]:
    import pandas as pd
    import yfinance as yf

    if not req.tickers:
        raise HTTPException(status_code=400, detail="최소 1개 종목을 선택하세요.")

    fetched_at = pd.Timestamp.utcnow()
    items: list[dict[str, object]] = []

    for ticker in req.tickers:
        label = MARKET_SNAPSHOT_LABELS.get(ticker, ticker)
        try:
            tk = yf.Ticker(ticker)
            fi = tk.fast_info

            # fast_info provides near-realtime last_price (15-min delayed for most exchanges)
            current  = float(fi.last_price)
            previous = float(fi.previous_close) if fi.previous_close else current
            change_pct = ((current / previous) - 1) * 100 if previous else 0.0

            items.append({
                "ticker": ticker,
                "label": label,
                "value": round(current, 4),
                "change_pct": round(change_pct, 2),
                "latest_data_at": fetched_at.isoformat(),
                "status": "ok",
            })
        except Exception as exc:
            # fallback: last daily close
            try:
                df = yf.download(ticker, period="5d", interval="1d",
                                 progress=False, auto_adjust=False, threads=False)
                close = _extract_close_series(df)
                current  = float(close.iloc[-1])
                previous = float(close.iloc[-2]) if len(close) > 1 else current
                change_pct = ((current / previous) - 1) * 100 if previous else 0.0
                items.append({
                    "ticker": ticker, "label": label,
                    "value": round(current, 4),
                    "change_pct": round(change_pct, 2),
                    "latest_data_at": pd.Timestamp(close.index[-1]).isoformat(),
                    "status": "ok",
                })
            except Exception as exc2:
                items.append({"ticker": ticker, "label": label,
                              "status": "error", "error": str(exc2)})

    return {
        "items": items,
        "fetched_at": fetched_at.isoformat(),
    }


@app.post("/api/market/data/refresh")
async def refresh_market_data(req: MarketDataRefreshRequest) -> dict[str, object]:
    """지정 종목의 검증된 일봉을 수집·버전 기록 후 저장한다.

    로컬 운영자용 API이며 호출당 종목 수와 기간을 제한해 공급자 과부하를 막는다.
    """
    import datetime as _dt

    end = _dt.date.today() + _dt.timedelta(days=1)
    start = end - _dt.timedelta(days=req.days)
    items: list[dict[str, object]] = []
    for raw_ticker in req.tickers:
        ticker = raw_ticker.strip().upper()
        if not re.fullmatch(r"[A-Z0-9.^=\-]{1,24}", ticker):
            items.append({"ticker": raw_ticker, "status": "error", "error": "유효하지 않은 티커입니다."})
            continue
        try:
            result = await get_daily_ohlcv(ticker, start, end)
            items.append({"ticker": ticker, "status": "ok", "bars": len(result["bars"]), "source": result["source"],
                          "dataset_version": result["dataset_version"], "quality": result["quality"],
                          "provider_attempts": result.get("provider_attempts", []),
                          "provider_routing": result.get("provider_routing", [])})
        except MarketDataError as exc:
            items.append({"ticker": ticker, "status": "error", "error": str(exc)})
    return {"start": start.isoformat(), "end": end.isoformat(), "items": items}


@app.get("/api/market/data-status")
async def get_market_data_status(ticker: str = "^KS11") -> dict[str, object]:
    """저장된 최신 OHLCV와 마지막 수집 실행의 출처·품질·버전을 반환한다."""
    if not re.fullmatch(r"[A-Za-z0-9.^=\-]{1,24}", ticker):
        raise HTTPException(status_code=400, detail="유효하지 않은 티커입니다.")
    return await market_data_status(ticker)


@app.get("/api/market/factors")
async def get_market_factors(ticker: str = "^KS11", as_of: str | None = None) -> dict[str, object]:
    """저장된 검증 일봉의 관측 팩터와 데이터 기준일을 읽는다."""
    if not re.fullmatch(r"[A-Za-z0-9.^=\-]{1,24}", ticker):
        raise HTTPException(status_code=400, detail="유효하지 않은 티커입니다.")
    try:
        snapshot = await market_factor_snapshot(ticker, as_of)
    except MarketDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    snapshot["computed_at"] = snapshot["computed_at"].isoformat()
    return snapshot


@app.get("/api/market/factor-universe")
async def get_market_factor_universe(tickers: str, as_of: str | None = None) -> dict[str, object]:
    """공통 기준일을 만족하는 종목만 관측 팩터 상대순위로 비교한다."""
    names = [ticker.strip().upper() for ticker in tickers.split(",") if ticker.strip()]
    if not names or any(not re.fullmatch(r"[A-Z0-9.^=\-]{1,24}", ticker) for ticker in names):
        raise HTTPException(status_code=400, detail="쉼표로 구분한 유효 티커를 입력하세요.")
    try:
        snapshot = await market_factor_universe_snapshot(names, as_of)
    except MarketDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    snapshot["computed_at"] = snapshot["computed_at"].isoformat()
    return snapshot


@app.get("/api/market/refresh-status")
async def get_market_refresh_status() -> dict[str, object]:
    """최근 자동 일봉 수집 결과와 핵심 시장 캐시 최신성을 반환한다."""
    db = get_db()
    job = await db.market_data_refresh_jobs.find_one({}, {"_id": 0}, sort=[("started_at", -1)])
    freshness = await core_market_freshness()
    cache = {ticker: {"latest": status["latest"], "source": status["source"]} for ticker, status in freshness.items()}
    active_alerts = await db.market_data_alerts.find(
        {"condition_active": True}, {"_id": 0, "title": 1, "severity": 1, "type": 1},
    ).sort("updated_at", -1).to_list(20)
    return {"last_job": job, "core_cache": cache,
            "stale_tickers": [ticker for ticker, status in freshness.items() if status["stale"]],
            "active_alert_count": len(active_alerts), "active_alerts": active_alerts,
            "automatic_action": "일봉 수집 실패 시 마지막 검증 캐시를 유지하며, 주문·포트폴리오 원장은 변경하지 않습니다."}


@app.get("/api/market/operations-report")
async def get_market_operations_report(days: int = 7) -> dict[str, object]:
    """최근 일봉 수집·품질 경고·핵심 지수 최신성의 읽기 전용 운영 리포트다."""
    return await market_data_operations_report(days)


@app.post("/api/macro/realtime")
def macro_realtime(req: MacroRealtimeRequest) -> dict[str, object]:
    import yfinance as yf
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    import numpy as np
    import pandas as pd
    import io, base64
    configure_matplotlib_korean_font(plt)

    DARK   = "#0f172a"
    SURF   = "#1e293b"
    BORDER = "#334155"
    TEXT   = "#e2e8f0"
    MUTED  = "#64748b"
    COLORS = ["#3b82f6","#22c55e","#f59e0b","#ef4444","#a855f7","#06b6d4","#f97316","#84cc16"]

    if not req.tickers:
        raise HTTPException(status_code=400, detail="최소 1개 종목을 선택하세요.")

    # ── 데이터 fetch ──────────────────────────────────────────────────────────
    raw: dict[str, pd.Series] = {}
    fetch_error: str | None = None
    fetched_at = pd.Timestamp.utcnow()

    for t in req.tickers:
        try:
            df = yf.download(t, period=req.period, progress=False, auto_adjust=True)
            if df.empty:
                continue
            close = _extract_close_series(df)
            if len(close) > 0:
                raw[t] = close
        except Exception as e:
            fetch_error = str(e)

    # 실제 시세가 없으면 가짜 시세를 만들지 않고 실패 원인과 함께 데이터 없음을 알린다.
    if not raw:
        raise HTTPException(status_code=503, detail=_no_market_data_detail(
            "거시 지표", fetch_error or "공급자가 빈 응답을 반환했습니다."))

    labels_used = [TICKER_LABELS.get(t, t) for t in raw]

    # ── 정규화 수익률 ─────────────────────────────────────────────────────────
    norm: dict[str, pd.Series] = {}
    for t, s in raw.items():
        norm[t] = (s / s.iloc[0] - 1) * 100

    # ── 공통 날짜로 상관관계 DataFrame ────────────────────────────────────────
    combined = pd.DataFrame({TICKER_LABELS.get(t, t): s for t, s in raw.items()})
    combined = combined.dropna()
    corr = combined.pct_change().dropna().corr()

    # ── Figure ────────────────────────────────────────────────────────────────
    n = len(raw)
    fig = plt.figure(figsize=(14, 11), facecolor=DARK)
    gs  = gridspec.GridSpec(2, 2, figure=fig, hspace=0.4, wspace=0.35,
                            left=0.07, right=0.97, top=0.93, bottom=0.07)

    # Panel 1: 원시 가격 추세
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_facecolor(SURF)
    for i, (t, s) in enumerate(raw.items()):
        ax2_ = ax1.twinx() if i > 0 else ax1
        col  = COLORS[i % len(COLORS)]
        lbl  = TICKER_LABELS.get(t, t)
        if i == 0:
            ax1.plot(s.index, s.values, color=col, lw=1.5, label=lbl)
        # 정규화 차트가 더 유용하므로 여기선 첫 종목만 왼쪽 축에 표시
    ax1.tick_params(colors=TEXT, labelsize=7)
    ax1.set_title("가격 추이 (첫 번째 종목 기준)", color=TEXT, fontsize=9, pad=6)
    ax1.spines[:].set_color(BORDER)
    ax1.set_xlabel("")
    ax1.tick_params(axis='x', rotation=30)
    for label in ax1.get_xticklabels(): label.set_fontsize(6)

    # Panel 2: 정규화 수익률 (누적 %)
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_facecolor(SURF)
    for i, (t, s) in enumerate(norm.items()):
        ax2.plot(s.index, s.values, color=COLORS[i % len(COLORS)],
                 lw=1.5, label=TICKER_LABELS.get(t, t))
    ax2.axhline(0, color=MUTED, lw=0.8, ls="--")
    ax2.set_title("정규화 누적 수익률 (%)", color=TEXT, fontsize=9, pad=6)
    ax2.tick_params(colors=TEXT, labelsize=7)
    ax2.spines[:].set_color(BORDER)
    ax2.legend(fontsize=6, facecolor=SURF, labelcolor=TEXT,
               loc="upper left", framealpha=0.7)
    ax2.tick_params(axis='x', rotation=30)
    for label in ax2.get_xticklabels(): label.set_fontsize(6)

    # Panel 3: 상관관계 히트맵
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.set_facecolor(SURF)
    if len(corr) > 1:
        cmat = corr.values
        im = ax3.imshow(cmat, cmap="RdYlGn", vmin=-1, vmax=1, aspect="auto")
        ax3.set_xticks(range(len(corr.columns)))
        ax3.set_yticks(range(len(corr.columns)))
        ax3.set_xticklabels(corr.columns, rotation=45, ha="right",
                            fontsize=7, color=TEXT)
        ax3.set_yticklabels(corr.columns, fontsize=7, color=TEXT)
        for ii in range(len(cmat)):
            for jj in range(len(cmat)):
                v = cmat[ii, jj]
                ax3.text(jj, ii, f"{v:.2f}", ha="center", va="center",
                         fontsize=7, color="white" if abs(v) > 0.5 else TEXT)
        plt.colorbar(im, ax=ax3, fraction=0.04, pad=0.02).ax.tick_params(
            labelcolor=TEXT, labelsize=7)
    else:
        ax3.text(0.5, 0.5, "2개 이상 선택 시\n상관관계 표시", ha="center",
                 va="center", color=MUTED, transform=ax3.transAxes, fontsize=9)
    ax3.set_title("수익률 상관관계 히트맵", color=TEXT, fontsize=9, pad=6)
    ax3.spines[:].set_color(BORDER)

    # Panel 4: 최근 수익률 바 차트 (1M / 3M / 기간 전체)
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.set_facecolor(SURF)
    period_returns = {}
    for t, s in raw.items():
        lbl = TICKER_LABELS.get(t, t)
        period_returns[lbl] = (s.iloc[-1] / s.iloc[0] - 1) * 100
    names  = list(period_returns.keys())
    values = list(period_returns.values())
    bar_colors = [COLORS[i % len(COLORS)] for i in range(len(names))]
    bars = ax4.barh(names, values, color=bar_colors, height=0.55)
    ax4.axvline(0, color=MUTED, lw=0.8)
    for bar, v in zip(bars, values):
        ax4.text(v + (0.5 if v >= 0 else -0.5), bar.get_y() + bar.get_height()/2,
                 f"{v:+.1f}%", va="center", ha="left" if v >= 0 else "right",
                 fontsize=7, color=TEXT)
    ax4.set_title(f"기간 전체 수익률 ({req.period})", color=TEXT, fontsize=9, pad=6)
    ax4.tick_params(colors=TEXT, labelsize=7)
    ax4.spines[:].set_color(BORDER)

    title_suffix = "  (Yahoo Finance)"
    fig.suptitle(f"거시경제현황 — 실시간 데이터{title_suffix}", color=TEXT,
                 fontsize=12, fontweight="bold", y=0.97)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, facecolor=DARK)
    plt.close(fig)
    buf.seek(0)
    img_b64 = "data:image/png;base64," + base64.b64encode(buf.read()).decode()

    # ── 요약 통계 ─────────────────────────────────────────────────────────────
    summary = {}
    for t, s in raw.items():
        lbl = TICKER_LABELS.get(t, t)
        ret  = (s.iloc[-1] / s.iloc[0] - 1) * 100
        vol  = s.pct_change().std() * (252 ** 0.5) * 100
        summary[lbl] = {
            "current": round(float(s.iloc[-1]), 4),
            "return_pct": round(ret, 2),
            "annual_vol_pct": round(vol, 2),
            "latest_data_at": pd.Timestamp(s.index[-1]).isoformat(),
        }

    return {"image": img_b64, "summary": summary, "period": req.period,
            "n_tickers": len(raw),
            "is_simulated": False,
            "fetched_at": fetched_at.isoformat(),
            "warning": None}


# ── KOSPI 섹터/종목 제외 지수 ──────────────────────────────────────────────────

KOSPI_COMPONENTS = [
    {"ticker": "005930.KS", "name": "삼성전자",        "sector": "반도체",    "weight": 0.210},
    {"ticker": "000660.KS", "name": "SK하이닉스",      "sector": "반도체",    "weight": 0.075},
    {"ticker": "373220.KS", "name": "LG에너지솔루션",  "sector": "배터리",    "weight": 0.035},
    {"ticker": "207940.KS", "name": "삼성바이오로직스", "sector": "바이오",    "weight": 0.028},
    {"ticker": "005380.KS", "name": "현대차",          "sector": "자동차",    "weight": 0.025},
    {"ticker": "000270.KS", "name": "기아",            "sector": "자동차",    "weight": 0.022},
    {"ticker": "105560.KS", "name": "KB금융",          "sector": "금융",      "weight": 0.018},
    {"ticker": "035420.KS", "name": "NAVER",           "sector": "IT/플랫폼", "weight": 0.013},
    {"ticker": "055550.KS", "name": "신한지주",        "sector": "금융",      "weight": 0.015},
    {"ticker": "006400.KS", "name": "삼성SDI",         "sector": "배터리",    "weight": 0.012},
    {"ticker": "086790.KS", "name": "하나금융지주",    "sector": "금융",      "weight": 0.012},
    {"ticker": "012330.KS", "name": "현대모비스",      "sector": "자동차",    "weight": 0.009},
    {"ticker": "051910.KS", "name": "LG화학",          "sector": "화학",      "weight": 0.010},
    {"ticker": "032830.KS", "name": "삼성생명",        "sector": "금융",      "weight": 0.008},
    {"ticker": "035720.KS", "name": "카카오",          "sector": "IT/플랫폼", "weight": 0.008},
    {"ticker": "316140.KS", "name": "우리금융지주",    "sector": "금융",      "weight": 0.007},
    {"ticker": "068270.KS", "name": "셀트리온",        "sector": "바이오",    "weight": 0.010},
    {"ticker": "005490.KS", "name": "POSCO홀딩스",     "sector": "철강",      "weight": 0.015},
    {"ticker": "017670.KS", "name": "SK텔레콤",        "sector": "통신",      "weight": 0.010},
    {"ticker": "030200.KS", "name": "KT",              "sector": "통신",      "weight": 0.008},
    {"ticker": "018260.KS", "name": "삼성SDS",         "sector": "IT/플랫폼", "weight": 0.005},
    {"ticker": "096770.KS", "name": "SK이노베이션",    "sector": "에너지",    "weight": 0.006},
    {"ticker": "034730.KS", "name": "SK",              "sector": "에너지",    "weight": 0.006},
    {"ticker": "003550.KS", "name": "LG",              "sector": "지주회사",  "weight": 0.005},
    {"ticker": "090430.KS", "name": "아모레퍼시픽",    "sector": "소비재",    "weight": 0.004},
    {"ticker": "034220.KS", "name": "LG디스플레이",    "sector": "디스플레이","weight": 0.004},
    {"ticker": "011170.KS", "name": "롯데케미칼",      "sector": "화학",      "weight": 0.003},
    {"ticker": "000120.KS", "name": "CJ대한통운",      "sector": "물류",      "weight": 0.003},
]

# 반도체 가치사슬의 소재·부품·장비를 대표하는 국내 상장 종목이다. 전체 소부장
# 시장을 포괄하는 스크리너가 아니라, 같은 기준으로 매일 비교하는 학습용 표본이다.
SOBUJANG_COMPONENTS = [
    {"ticker": "042700.KS", "name": "한미반도체", "sector": "반도체 장비"},
    {"ticker": "240810.KQ", "name": "원익IPS", "sector": "반도체 장비"},
    {"ticker": "036930.KQ", "name": "주성엔지니어링", "sector": "반도체 장비"},
    {"ticker": "084370.KQ", "name": "유진테크", "sector": "반도체 장비"},
    {"ticker": "095610.KQ", "name": "테스", "sector": "반도체 장비"},
    {"ticker": "403870.KQ", "name": "HPSP", "sector": "반도체 장비"},
    {"ticker": "319660.KQ", "name": "피에스케이", "sector": "반도체 장비"},
    {"ticker": "281820.KS", "name": "케이씨텍", "sector": "반도체 장비"},
    {"ticker": "005290.KQ", "name": "동진쎄미켐", "sector": "반도체 소재"},
    {"ticker": "357780.KQ", "name": "솔브레인", "sector": "반도체 소재"},
    {"ticker": "074600.KQ", "name": "원익QnC", "sector": "반도체 소재"},
    {"ticker": "102710.KQ", "name": "이엔에프테크놀로지", "sector": "반도체 소재"},
    {"ticker": "093370.KS", "name": "후성", "sector": "반도체 소재"},
    {"ticker": "011790.KS", "name": "SKC", "sector": "반도체 소재"},
    {"ticker": "095340.KQ", "name": "ISC", "sector": "반도체 부품"},
    {"ticker": "067310.KQ", "name": "하나마이크론", "sector": "반도체 부품"},
    {"ticker": "222800.KQ", "name": "심텍", "sector": "반도체 부품"},
    {"ticker": "353200.KS", "name": "대덕전자", "sector": "반도체 부품"},
    {"ticker": "195870.KS", "name": "해성디에스", "sector": "반도체 부품"},
    {"ticker": "058470.KQ", "name": "리노공업", "sector": "반도체 부품"},
]

# 거래소 전체 업종지수 대신, 매일 같은 기준으로 조회할 수 있는 대표 종목 표본을
# 섹터별로 묶는다. 타일의 면적은 해당 표본의 당일 거래대금 합계다.
def _market_top_ranked_components(
    components: list[dict[str, object]], limit: int, universe: str,
) -> dict[str, object]:
    """대표 종목 표본의 당일 등락률 순위를 반환한다."""
    import pandas as pd
    import yfinance as yf

    limit = max(1, min(limit, len(components)))
    fetched_at = pd.Timestamp.utcnow()
    valid: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []

    for stock in components:
        ticker = str(stock["ticker"])
        try:
            fi = yf.Ticker(ticker).fast_info
            current = float(fi.last_price)
            previous = float(fi.previous_close) if fi.previous_close else current
            change_pct = ((current / previous) - 1) * 100 if previous else 0.0
            valid.append({
                "ticker": ticker, "name": stock["name"], "sector": stock["sector"],
                "price": round(current, 2), "change_pct": round(change_pct, 2),
            })
        except Exception as exc:
            excluded.append({
                "ticker": ticker, "name": stock["name"],
                "reason": f"시세 조회 실패: {exc}",
            })

    # 예전에는 유효 종목이 5개 미만이면 선형합동생성기로 가격과 등락률을 지어내
    # 순위표를 채웠다(고정 시드라 매번 같은 가짜 순위가 나왔다). is_simulated 플래그를
    # 붙이긴 했지만, 존재하지 않는 시세로 "오늘의 상승 종목"을 만드는 것 자체가
    # portfolio.py가 지키는 "데이터가 부족하면 수치를 만들지 않는다" 규율과 정면으로
    # 어긋난다. 이제는 받은 만큼만 보여주고 실패한 종목은 사유와 함께 표면화한다.
    ranked = sorted(valid, key=lambda item: item["change_pct"], reverse=True)[:limit]
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    return {
        "items": ranked,
        "universe": universe,
        "universe_size": len(components),
        "fetched_at": fetched_at.isoformat(),
        "available": bool(ranked),
        "excluded": excluded,
        "is_simulated": False,
    }


# 같은 표본을 매 요청마다 30여 종목씩 다시 조회하면 9~12초가 걸린다. 60초 동안은 직전 결과를 재사용한다.
RANKING_CACHE_SECONDS = 60
_ranking_cache: dict[tuple[str, int], tuple[float, dict]] = {}
_ranking_cache_lock = Lock()


def _cached_ranking(key: str, limit: int, build) -> dict[str, object]:
    now = monotonic()
    with _ranking_cache_lock:
        hit = _ranking_cache.get((key, limit))
        if hit and now - hit[0] < RANKING_CACHE_SECONDS:
            return {**hit[1], "cached": True}
    result = build()
    with _ranking_cache_lock:
        _ranking_cache[(key, limit)] = (monotonic(), result)
    return {**result, "cached": False}


@app.get("/api/market/quote-provider-status")
def market_quote_provider_status() -> dict:
    """한투 시세 연결 상태(키·오류 내용 없음). 운영 확인용."""
    status = live_quote_provider_status()
    return {key: status[key] for key in ("enabled", "env", "rest_per_second", "stream_connected", "stream_subscribed",
                                         "stream_limit", "stream_session_open")}


@app.post("/api/market/live-quotes")
async def market_live_quotes(req: LiveQuotesRequest, user: dict = Depends(current_user)) -> JSONResponse:
    """화면용 현재가(최대 40종목). 국내 개별 종목은 한투 체결가(설정 시), 그 밖과 한투 실패 시에는 Yahoo 지연 시세.

    한투 호출 한도를 지키려고 로그인 사용자만 쓴다. 장중 화면은 3초마다 다시 부른다.
    """
    tickers = list(dict.fromkeys(t.strip().upper() for t in req.tickers if re.fullmatch(r"[A-Za-z0-9.^=-]{1,20}", t.strip())))
    if not tickers:
        raise HTTPException(status_code=422, detail="유효한 종목 코드가 없습니다.")
    quotes = await resolve_live_quotes(tickers)
    status = live_quote_provider_status()
    return JSONResponse(content={
        "items": [quotes[t] for t in tickers], "provider": status,
        "refresh_seconds": 3 if status["enabled"] and status["stream_session_open"] else 30,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }, headers={"Cache-Control": "no-store"})


@app.get("/api/market/top-gainers")
def market_top_gainers(market: str = "KOSPI", limit: int = 20, min_trading_value_krw: float = 0) -> JSONResponse:
    """코스피·코스닥 전 종목 중 금일 등락률 상위(주식만, 장중 지연 시세, 서버 30초 캐시)."""
    try:
        data = market_movers.top_gainers(market, limit, max(0.0, min_trading_value_krw))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except market_movers.MarketMoversError as exc:
        raise HTTPException(status_code=503, detail=_no_market_data_detail("금일 상승종목", str(exc))) from exc
    return JSONResponse(content=data, headers={"Cache-Control": "no-cache"})


@app.get("/api/market/top-sobujang")
def market_top_sobujang(limit: int = 20) -> dict[str, object]:
    """반도체 소부장 대표 종목 표본 중 금일 등락률 상위 종목(전체 소부장 시장 아님)."""
    return _cached_ranking("sobujang", limit, lambda: _market_top_ranked_components(
        SOBUJANG_COMPONENTS, limit, "국내 반도체 소부장 대표 종목"))

# ── 거시경제현황 2: GBM 시뮬레이션 대시보드 ──────────────────────────────────

class CompanyFinancialsRequest(BaseModel):
    ticker: str = Field(default="AAPL", min_length=1, max_length=30)
    period: str = Field(default="annual", pattern="^(annual|quarterly)$")


@app.post("/api/finance/company-financials")
def company_financials(req: CompanyFinancialsRequest) -> dict[str, object]:
    """Return structured financial data for a ticker using yfinance."""
    import math
    import yfinance as yf
    import pandas as pd

    def safe_float(v) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)
            return None if (math.isnan(f) or math.isinf(f)) else f
        except (TypeError, ValueError):
            return None

    def row(df: "pd.DataFrame", *keys: str) -> "pd.Series":
        for key in keys:
            if key in df.index:
                return df.loc[key]
        return pd.Series(dtype=float)

    def series_to_list(series: "pd.Series") -> list[dict]:
        result = []
        for idx, val in series.items():
            label = str(idx)[:7] if hasattr(idx, "strftime") else str(idx)[:10]
            result.append({"period": label, "value": safe_float(val)})
        return list(reversed(result))

    ticker_sym = _resolve_ticker_or_name(req.ticker)
    try:
        tk = yf.Ticker(ticker_sym)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"티커 오류: {exc}") from exc

    # Select annual vs quarterly statements
    if req.period == "annual":
        income  = tk.income_stmt
        balance = tk.balance_sheet
        cashflow = tk.cashflow
    else:
        income  = tk.quarterly_income_stmt
        balance = tk.quarterly_balance_sheet
        cashflow = tk.quarterly_cashflow

    if income is None or income.empty:
        raise HTTPException(status_code=404, detail=f"'{ticker_sym}' 재무데이터를 찾을 수 없습니다.")

    # ── Income statement rows ──────────────────────────────────────────────────
    revenue       = row(income, "Total Revenue")
    cogs          = row(income, "Cost Of Revenue")
    gross_profit  = row(income, "Gross Profit")
    op_expense    = row(income, "Operating Expense")
    op_income     = row(income, "Operating Income", "EBIT")
    other_income  = row(income, "Other Income Expense",
                        "Other Non Operating Income Expense",
                        "Non Operating Income")
    pretax        = row(income, "Pretax Income")
    tax           = row(income, "Tax Provision")
    net_income    = row(income, "Net Income")

    # ── Balance sheet rows ────────────────────────────────────────────────────
    total_debt = row(balance, "Total Debt", "Long Term Debt")
    cash       = row(balance,
                     "Cash And Cash Equivalents",
                     "Cash Cash Equivalents And Short Term Investments")

    # ── Cash flow rows ─────────────────────────────────────────────────────────
    op_cf  = row(cashflow, "Operating Cash Flow",
                 "Cash Flow From Continuing Operating Activities")
    capex  = row(cashflow, "Capital Expenditure")

    # Free Cash Flow = Operating CF + Capex (capex stored as negative)
    if not op_cf.empty and not capex.empty:
        shared_idx = op_cf.index.intersection(capex.index)
        fcf = op_cf.loc[shared_idx] + capex.loc[shared_idx]
    elif not op_cf.empty:
        fcf = op_cf
    else:
        fcf = pd.Series(dtype=float)

    # Net margin %
    margin_data: list[dict] = []
    for idx in revenue.index:
        r = safe_float(revenue.get(idx))
        n = safe_float(net_income.get(idx))
        label = str(idx)[:7]
        if r and n and r != 0:
            margin_data.append({"period": label, "value": round(n / r * 100, 2)})
    margin_data = list(reversed(margin_data))

    # ── Waterfall (most recent period) ────────────────────────────────────────
    def wf(series: "pd.Series") -> float | None:
        return safe_float(series.iloc[0]) if not series.empty else None

    waterfall = {
        "revenue":          wf(revenue),
        "cogs":             wf(cogs),
        "gross_profit":     wf(gross_profit),
        "operating_expense": wf(op_expense),
        "operating_income": wf(op_income),
        "other_income":     wf(other_income),
        "tax":              wf(tax),
        "net_income":       wf(net_income),
    }

    # ── Earnings history ──────────────────────────────────────────────────────
    earnings_data: list[dict] = []
    try:
        ed = tk.earnings_dates
        if ed is not None and not ed.empty:
            for idx, erow in list(ed.iterrows())[:20]:
                earnings_data.append({
                    "date":         str(idx)[:10],
                    "eps_estimate": safe_float(erow.get("EPS Estimate")),
                    "eps_actual":   safe_float(erow.get("Reported EPS")),
                    "surprise_pct": safe_float(erow.get("Surprise(%)")),
                })
            earnings_data.sort(key=lambda x: x["date"])
    except Exception:
        pass

    # ── Company info ─────────────────────────────────────────────────────────
    company_name = ticker_sym
    currency = "USD"
    try:
        info = tk.info or {}
        company_name = info.get("longName") or info.get("shortName") or ticker_sym
        currency = info.get("currency", "USD")
    except Exception:
        pass

    return {
        "ticker":   ticker_sym,
        "name":     company_name,
        "currency": currency,
        "period":   req.period,
        "performance": {
            "revenue":        series_to_list(revenue),
            "net_income":     series_to_list(net_income),
            "net_margin_pct": margin_data,
        },
        "waterfall": waterfall,
        "debt": {
            "total_debt": series_to_list(total_debt),
            "fcf":        series_to_list(fcf),
            "cash":       series_to_list(cash),
        },
        "earnings": earnings_data,
    }


PERIOD_DAYS = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365}
INTRADAY_PERIODS = {"1d"}
# 이동평균(MA20) 등 보조지표가 표시 구간 맨 앞부터 끊김 없이 나오도록, 실제 요청 기간보다
# 이만큼 앞선 과거 데이터까지 함께 가져와 지표를 계산한 뒤 표시 구간만 잘라서 내려준다.
MA_LOOKBACK_BUFFER_DAYS = 40
HOME_MARKETS = {
    "kospi":  {"ticker": "^KS11", "name": "KOSPI", "base_price": 2650.0, "seed": 42},
    "kosdaq": {"ticker": "^KQ11", "name": "KOSDAQ", "base_price": 850.0, "seed": 73},
    "nasdaq": {"ticker": "^IXIC", "name": "NASDAQ", "base_price": 18000.0, "seed": 109},
    "sp500":  {"ticker": "^GSPC", "name": "S&P 500", "base_price": 5200.0, "seed": 151},
    "dow":    {"ticker": "^DJI", "name": "다우존스", "base_price": 39000.0, "seed": 187},
    "gold":    {"ticker": "GC=F", "name": "국제 금 선물", "base_price": 2600.0, "seed": 211},
    "oil":     {"ticker": "CL=F", "name": "WTI 원유 선물", "base_price": 75.0, "seed": 233},
    "dxy":     {"ticker": "DX-Y.NYB", "name": "달러인덱스(DXY)", "base_price": 103.0, "seed": 255},
    "usdkrw":  {"ticker": "KRW=X", "name": "원/달러 환율", "base_price": 1380.0, "seed": 277},
    "ust10y":  {"ticker": "^TNX", "name": "미국 10년물 국채금리", "base_price": 42.0, "seed": 299},
    "bitcoin": {"ticker": "BTC-USD", "name": "비트코인(BTC/USD)", "base_price": 65000.0, "seed": 321},
}

# 검색 서버가 일시적으로 응답하지 않을 때도 찾을 수 있도록 제공하는 대표 종목이다.
MODAL_CHART_STOCKS = {
    "aapl": {"ticker": "AAPL", "name": "Apple", "base_price": 220.0, "seed": 401},
    "msft": {"ticker": "MSFT", "name": "Microsoft", "base_price": 450.0, "seed": 419},
    "googl": {"ticker": "GOOGL", "name": "Alphabet", "base_price": 180.0, "seed": 431},
    "amzn": {"ticker": "AMZN", "name": "Amazon", "base_price": 220.0, "seed": 443},
    "nvda": {"ticker": "NVDA", "name": "NVIDIA", "base_price": 180.0, "seed": 457},
    "meta": {"ticker": "META", "name": "Meta", "base_price": 750.0, "seed": 467},
    "tsla": {"ticker": "TSLA", "name": "Tesla", "base_price": 330.0, "seed": 479},
    "samsung": {"ticker": "005930.KS", "name": "삼성전자", "base_price": 75000.0, "seed": 491},
    "skhynix": {"ticker": "000660.KS", "name": "SK하이닉스", "base_price": 250000.0, "seed": 503},
    "lgenergy": {"ticker": "373220.KS", "name": "LG에너지솔루션", "base_price": 350000.0, "seed": 521},
    "samsungbio": {"ticker": "207940.KS", "name": "삼성바이오로직스", "base_price": 1000000.0, "seed": 541},
    "hyundai": {"ticker": "005380.KS", "name": "현대차", "base_price": 220000.0, "seed": 557},
    "kia": {"ticker": "000270.KS", "name": "기아", "base_price": 100000.0, "seed": 571},
    "naver": {"ticker": "035420.KS", "name": "NAVER", "base_price": 200000.0, "seed": 587},
}
MODAL_CHART_INSTRUMENTS = {**HOME_MARKETS, **MODAL_CHART_STOCKS}
INTRADAY_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h"}
CHART_TIMEFRAMES = {
    "1m": ("1m", 1), "3m": ("3m", 1), "5m": ("5m", 1), "15m": ("15m", 1),
    "30m": ("30m", 1), "1h": ("1h", 1), "1d": ("1d", 365), "2y": ("2y", 365 * 2), "5y": ("5y", 365 * 5), "1wk": ("1wk", 365 * 5),
    "1mo": ("1mo", 365 * 10), "1y": ("1y", 365 * 30),
}
# Yahoo 자동완성은 한글 회사명을 충분히 지원하지 않으므로, 국내에서 많이 조회하는
# 종목은 KRX 코드와 함께 보완한다. 이후 Yahoo 결과와 동일한 형식으로 반환된다.
KOREAN_SEARCH_ALIASES = {
    "삼성전자": ("005930.KS", "삼성전자"), "SK하이닉스": ("000660.KS", "SK하이닉스"),
    "LG에너지솔루션": ("373220.KS", "LG에너지솔루션"), "삼성바이오로직스": ("207940.KS", "삼성바이오로직스"),
    "현대차": ("005380.KS", "현대자동차"), "기아": ("000270.KS", "기아"), "NAVER": ("035420.KS", "NAVER"),
    "카카오": ("035720.KS", "카카오"), "셀트리온": ("068270.KS", "셀트리온"), "삼성물산": ("028260.KS", "삼성물산"),
    "삼성SDI": ("006400.KS", "삼성SDI"), "LG화학": ("051910.KS", "LG화학"), "KB금융": ("105560.KS", "KB금융"),
    "신한지주": ("055550.KS", "신한지주"), "POSCO홀딩스": ("005490.KS", "POSCO홀딩스"), "한화에어로스페이스": ("012450.KS", "한화에어로스페이스"),
    "두산에너빌리티": ("034020.KS", "두산에너빌리티"), "HD현대중공업": ("329180.KS", "HD현대중공업"),
    "알테오젠": ("196170.KQ", "알테오젠"), "에코프로비엠": ("247540.KQ", "에코프로비엠"), "에코프로": ("086520.KQ", "에코프로"),
    "HLB": ("028300.KQ", "HLB"), "펄어비스": ("263750.KQ", "펄어비스"), "JYP": ("035900.KQ", "JYP Ent."),
}


@app.get("/api/home/chart-search")
def home_chart_search(q: str = "") -> dict[str, object]:
    """KRX 전체 상장사와 Yahoo Finance 자동완성으로 종목을 검색한다."""
    query = q.strip()
    if len(query) < 1:
        return {"items": []}
    items: list[dict[str, str]] = []
    lowered = query.lower()
    # 국내 종목은 KIND 상장법인 목록을 우선 사용한다. 운영 환경에서 KIND 접근이
    # 제한되면 KRX_COMPANY_LIST_URL JSON 미러 또는 아래 정적 별칭으로 폴백한다.
    items.extend(search_krx_instruments(query, limit=12))
    for alias, (ticker, name) in KOREAN_SEARCH_ALIASES.items():
        if lowered in alias.lower() or lowered in name.lower():
            items.append({"ticker": ticker, "name": name, "exchange": "Korea Exchange", "source": "static_alias"})
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode({"q": query, "quotesCount": 12, "newsCount": 0})
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
        allowed_types = {"EQUITY", "ETF", "INDEX", "CRYPTOCURRENCY", "FUTURE", "MUTUALFUND"}
        for quote in payload.get("quotes", []):
            ticker = str(quote.get("symbol", "")).upper()
            if quote.get("quoteType") not in allowed_types or not re.fullmatch(r"[A-Z0-9.^=\-]{1,24}", ticker):
                continue
            items.append({"ticker": ticker, "name": str(quote.get("shortname") or quote.get("longname") or ticker), "exchange": str(quote.get("exchange", "")), "source": "yahoo_finance"})
    except Exception:
        pass
    if not items:
        for config in MODAL_CHART_INSTRUMENTS.values():
            if lowered in config["ticker"].lower() or lowered in config["name"].lower():
                items.append({"ticker": config["ticker"], "name": config["name"], "exchange": "", "source": "static_fallback"})
    deduplicated: list[dict[str, str]] = []
    seen_tickers: set[str] = set()
    for item in items:
        ticker = item["ticker"]
        if ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)
        deduplicated.append(item)
    return {"items": deduplicated[:12]}


@app.get("/api/home/market-candle")
async def home_market_candle(market: str = "kospi", period: str = "3mo", interval: str = "5m", ticker: str = "", timeframe: str = "") -> dict[str, object]:
    if period not in PERIOD_DAYS and period not in INTRADAY_PERIODS:
        period = "3mo"
    if interval not in INTRADAY_INTERVALS:
        interval = "5m"
    if ticker and re.fullmatch(r"[A-Za-z0-9.^=\-]{1,24}", ticker):
        config = {"ticker": ticker.upper(), "name": ticker.upper(), "base_price": 100.0, "seed": 911}
    else:
        config = MODAL_CHART_INSTRUMENTS.get(market, HOME_MARKETS["kospi"])
    if timeframe in CHART_TIMEFRAMES:
        interval, history_days = CHART_TIMEFRAMES[timeframe]
        intraday = interval in INTRADAY_INTERVALS
        display_days = history_days
        response_interval = interval
    else:
        intraday = period in INTRADAY_PERIODS
        display_days = PERIOD_DAYS.get(period, 90)
        # 기간 버튼(1M·3M 등)은 항상 일봉을 반환한다. 기본 interval=5m은
        # 1일 화면에서만 의미가 있으므로 일봉 응답에 그대로 노출하면 안 된다.
        response_interval = interval if intraday else "1d"
    import pandas as pd
    import datetime as _dt
    display_from: str | None = None
    try:
        import yfinance as yf
        if intraday:
            # Yahoo Finance에는 3분 간격이 없으므로 1분 OHLCV를 3분봉으로 집계한다.
            fetch_interval = "1m" if interval in {"1m", "3m"} else interval
            df = yf.download(config["ticker"], period="1d", interval=fetch_interval,
                             progress=False, auto_adjust=True, threads=False)
            if interval == "3m" and not df.empty:
                # yfinance 버전에 따라 단일 티커도 MultiIndex 열을 반환한다.
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.resample("3min", label="left", closed="left").agg({
                    "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
                }).dropna(subset=["Open", "High", "Low", "Close"])
        else:
            days = display_days
            end_date = _dt.date.today() + _dt.timedelta(days=1)
            start_date = end_date - _dt.timedelta(days=days + MA_LOOKBACK_BUFFER_DAYS)
            display_from = (end_date - _dt.timedelta(days=days)).isoformat()
            # 홈 차트는 마지막 검증 캐시가 있으면 즉시 표시한다. 명시적 데이터
            # 수집 API·배치는 prefer_cached=False로 최신 공급자를 계속 조회한다.
            stored = await get_daily_ohlcv(config["ticker"], start_date, end_date, adjusted=True, prefer_cached=True)
            frame = pd.DataFrame(stored["bars"])
            if frame.empty:
                raise ValueError("empty")
            frame.index = pd.to_datetime(frame["timestamp"])
            if timeframe in {"1wk", "1mo", "1y"}:
                rule = {"1wk": "W-FRI", "1mo": "ME", "1y": "YE"}[timeframe]
                frame = frame.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["open", "high", "low", "close"])
            ohlcv = [{"date": str(idx)[:10], "o": round(float(row["open"]), 2), "h": round(float(row["high"]), 2),
                      "l": round(float(row["low"]), 2), "c": round(float(row["close"]), 2), "v": int(row["volume"] or 0)}
                     for idx, row in frame.iterrows()]
            return {"market": market, "name": config["name"], "ticker": config["ticker"], "ohlcv": ohlcv,
                    "is_simulated": False, "display_from": display_from, "interval": response_interval,
                    "data_source": stored["source"], "dataset_version": stored["dataset_version"],
                    "data_quality": stored["quality"]}
        if df.empty:
            raise ValueError("empty")
        ohlcv = []
        for idx, row in df.iterrows():
            def _f(col):
                v = row.get(col)
                if v is None:
                    return None
                if hasattr(v, '__iter__') and not isinstance(v, (str, float, int)):
                    v = list(v)[0]
                return round(float(v), 2)
            ohlcv.append({
                "date": idx.isoformat(), "o": _f("Open"), "h": _f("High"),
                "l": _f("Low"), "c": _f("Close"), "v": int(_f("Volume") or 0),
            })
        return {"market": market, "name": config["name"], "ticker": config["ticker"], "ohlcv": ohlcv,
                "is_simulated": False, "display_from": display_from, "interval": response_interval, "data_source": "yahoo_intraday"}
    except Exception as exc:
        # 실제 시세가 없으면 가짜 캔들을 만들지 않는다. 화면은 데이터 없음과 원인을 표시한다.
        reason = "공급자가 빈 응답을 반환했습니다." if str(exc) == "empty" else f"{type(exc).__name__}: {exc}"
        raise HTTPException(status_code=503, detail=_no_market_data_detail(config["name"], reason)) from exc


# ─── DART Financial Analysis ─────────────────────────────────────────────────

class DartFinancialAnalysisRequest(BaseModel):
    corp_code:    str        = Field(min_length=8, max_length=8, description="DART 고유번호 (8자리)")
    # 둘 다 비우면 가장 최근에 공시된 정기보고서를 자동으로 찾는다. 연도만 주면 그 해의 가장 최근 보고서.
    bsns_year:    str | None = Field(default=None, pattern=r"^\d{4}$")
    reprt_code:   str | None = Field(default=None, pattern=r"^1101[1-4]$",
                                     description="11011=사업보고서 11012=반기 11013=1분기 11014=3분기")


# 보고서 코드 → (이름, 포함 개월 수, 짧은 이름, 기간 종료 월·일)
DART_REPORTS = {
    "11013": ("1분기보고서", 3, "1분기", (3, 31)),
    "11012": ("반기보고서", 6, "반기", (6, 30)),
    "11014": ("3분기보고서", 9, "3분기", (9, 30)),
    "11011": ("사업보고서", 12, "연간", (12, 31)),
}


def _dart_report_candidates(today, bsns_year: str | None, reprt_code: str | None) -> list[tuple[str, str]]:
    """확인할 (연도, 보고서) 순서. 기간이 끝나지 않은 보고서는 건너뛰고, 최근 기간부터 본다."""
    if bsns_year and reprt_code:
        return [(bsns_year, reprt_code)]
    if bsns_year:
        years = [int(bsns_year)]
        order = lambda y: [(str(y), "11011"), (str(y), "11014"), (str(y), "11012"), (str(y), "11013")]
    else:
        years = [today.year, today.year - 1]
        order = lambda y: [(str(y), "11014"), (str(y), "11012"), (str(y), "11013"), (str(y - 1), "11011")]
    out = []
    for year in years:
        for y, code in order(year):
            if reprt_code and code != reprt_code:
                continue
            month, day = DART_REPORTS[code][3]
            if datetime(int(y), month, day).date() < today and (y, code) not in out:
                out.append((y, code))
    return out


def _parse_dart_amounts(items: list[dict], cumulative: bool = False) -> dict[str, dict[str, float]]:
    """Extract key financial line items from DART fnlttSinglAcnt response.

    Returns a dict mapping account_nm → {current, prior}.
    분기·반기 보고서(cumulative=True)의 손익계산서 항목은 3개월 금액(thstrm_amount) 대신 당기 누적
    (thstrm_add_amount)과 전년 동기 누적(frmtrm_add_amount)을 쓴다. 재무상태표는 기말 잔액 그대로다.
    """
    ACCT_MAP: dict[str, list[str]] = {
        "current_assets":       ["유동자산"],
        "noncurrent_assets":    ["비유동자산"],
        "total_assets":         ["자산총계"],
        "current_liabilities":  ["유동부채"],
        "noncurrent_liabilities": ["비유동부채"],
        "total_liabilities":    ["부채총계"],
        "paid_in_capital":      ["자본금"],
        "retained_earnings":    ["이익잉여금"],
        "total_equity":         ["자본총계"],
        "revenue":              ["매출액", "영업수익", "수익(매출액)"],
        "op_income":            ["영업이익", "영업손익"],
        "pretax_income":        ["법인세차감전", "법인세비용차감전"],
        "net_income":           ["당기순이익(손실)", "당기순이익"],
        "comprehensive_income": ["총포괄손익"],
    }

    def _num(val: object) -> float:
        try:
            s = str(val or "").replace(",", "").strip()
            return float(s) if s and s not in ("-", "") else 0.0
        except (ValueError, TypeError):
            return 0.0

    result: dict[str, dict[str, float]] = {}
    for key, kws in ACCT_MAP.items():
        for item in items:
            nm = (item.get("account_nm") or "").strip()
            if any(kw in nm for kw in kws):
                use_cumulative = cumulative and item.get("sj_div") == "IS" and _num(item.get("thstrm_add_amount"))
                result[key] = {
                    "current": _num(item.get("thstrm_add_amount" if use_cumulative else "thstrm_amount")),
                    "prior":   _num(item.get("frmtrm_add_amount" if use_cumulative and item.get("frmtrm_add_amount") else "frmtrm_amount")),
                }
                break
        if key not in result:
            result[key] = {"current": 0.0, "prior": 0.0}
    return result


def _calc_dart_ratios(fin: dict[str, dict[str, float]], annualize: float = 1.0) -> dict[str, float | None]:
    """Compute financial ratios from parsed DART data.

    annualize: 분기·반기 누적 순이익을 1년치로 환산하는 배수(1분기 4, 반기 2, 3분기 4/3). ROE·ROA에만 쓴다.
    """

    def g(k: str, period: str = "current") -> float:
        return fin.get(k, {}).get(period, 0.0)

    def safe_r(a: float, b: float, mult: float = 100.0) -> float | None:
        return (a / b * mult) if b != 0 else None

    rev     = g("revenue")
    prev_rev = g("revenue", "prior")
    op_inc  = g("op_income")
    net_inc = g("net_income")
    assets  = g("total_assets")
    liab    = g("total_liabilities")
    equity  = g("total_equity")
    cur_a   = g("current_assets")
    cur_l   = g("current_liabilities")
    ret_e   = g("retained_earnings")
    prev_eq = g("total_equity", "prior")
    avg_equity = (equity + prev_eq) / 2 if prev_eq else equity

    return {
        "debt_equity_ratio": safe_r(liab, equity),
        "op_margin":         safe_r(op_inc, rev),
        "net_margin":        safe_r(net_inc, rev),
        "roe":               safe_r(net_inc * annualize, avg_equity),
        "roa":               safe_r(net_inc * annualize, assets),
        "current_ratio":     safe_r(cur_a, cur_l),
        "revenue_growth":    safe_r(rev - prev_rev, prev_rev) if prev_rev else None,
        "equity_growth":     safe_r(equity - prev_eq, prev_eq) if prev_eq else None,
        "retained_ratio":    safe_r(ret_e, equity),
        "debt_ratio":        safe_r(liab, assets),
    }


def _score_financial_health(ratios: dict) -> tuple[float, dict]:
    """Score financial health on 0-100 scale with breakdown."""
    breakdown: dict[str, dict] = {}
    total = 0.0

    def score_item(key: str, label: str, max_score: float,
                   thresholds: list[tuple[float, float]], value: float | None) -> float:
        if value is None:
            s = max_score * 0.5
        else:
            s = 0.0
            for limit, pts in thresholds:
                if value >= limit:
                    s = pts
                    break
        breakdown[label] = {"score": round(s, 1), "max": max_score, "value": value}
        return s

    # 부채비율 (20점) — 낮을수록 좋음. 화면에는 실제 부채비율을 보여 준다.
    # 자본총계가 음수(완전자본잠식)면 부채비율도 음수가 되는데, 이는 '부채가 적다'가 아니라 가장 위험한 상태라 0점이다.
    dr = ratios.get("debt_equity_ratio")
    if dr is None:
        dr_score = 20 * 0.5
    elif dr < 0:
        dr_score = 0.0
    else:
        dr_score = next(pts for limit, pts in [(50, 20), (100, 16), (200, 10), (300, 5), (float("inf"), 0)] if dr <= limit)
    breakdown["부채비율"] = {"score": round(dr_score, 1), "max": 20, "value": dr,
                          **({"note": "자본잠식"} if dr is not None and dr < 0 else {})}
    total += dr_score

    # 영업이익률 (20점)
    total += score_item("op_margin", "영업이익률", 20,
                        [(20, 20), (10, 16), (5, 10), (0, 5), (-1e9, 0)],
                        ratios.get("op_margin"))

    # ROE (15점)
    total += score_item("roe", "자기자본이익률(ROE)", 15,
                        [(20, 15), (10, 12), (5, 8), (0, 4), (-1e9, 0)],
                        ratios.get("roe"))

    # 유동비율 (15점)
    total += score_item("current_ratio", "유동비율", 15,
                        [(200, 15), (150, 12), (100, 8), (50, 4), (-1e9, 0)],
                        ratios.get("current_ratio"))

    # 매출 성장률 (15점)
    total += score_item("revenue_growth", "매출 성장률", 15,
                        [(15, 15), (5, 12), (0, 7), (-10, 3), (-1e9, 0)],
                        ratios.get("revenue_growth"))

    # 이익잉여금 비율 (15점)
    total += score_item("retained_ratio", "이익잉여금 비율", 15,
                        [(70, 15), (50, 12), (30, 8), (10, 4), (-1e9, 0)],
                        ratios.get("retained_ratio"))

    return round(total, 1), breakdown


def _generate_dart_analysis(
    company: dict, market: str, ratios: dict,
    score: float, grade: str, bsns_year: str,
) -> dict:
    """Generate rule-based AI financial analysis narrative."""
    corp_name = company.get("corp_name", "동 기업")

    market_ctx = {
        "KOSPI":  "유가증권시장(KOSPI)에 상장된",
        "KOSDAQ": "코스닥(KOSDAQ)에 상장된",
        "KONEX":  "코넥스(KONEX)에 상장된",
    }.get(market, "상장된")

    paragraphs: list[str] = []

    # Overall verdict
    if score >= 85:
        paragraphs.append(
            f"{corp_name}의 {bsns_year} 재무제표는 전반적으로 매우 우수한 건전성을 보입니다. "
            f"{market_ctx} 기업으로, 재무 안정성과 수익성 모두 업계 상위 수준입니다."
        )
    elif score >= 70:
        paragraphs.append(
            f"{corp_name}의 {bsns_year} 재무 상태는 양호한 수준입니다. "
            f"{market_ctx} 기업으로, 핵심 재무지표들이 안정적으로 관리되고 있습니다."
        )
    elif score >= 55:
        paragraphs.append(
            f"{corp_name}의 {bsns_year} 재무 상태는 보통 수준이며, 일부 지표에서 개선이 필요합니다. "
            f"{market_ctx} 기업으로, 선별적 모니터링이 권고됩니다."
        )
    else:
        paragraphs.append(
            f"{corp_name}의 {bsns_year} 재무 상태는 취약한 것으로 분석됩니다. "
            f"{market_ctx} 기업이나, 재무 리스크가 높아 투자에 각별한 주의가 필요합니다."
        )

    # Debt structure
    dr = ratios.get("debt_equity_ratio")
    if dr is not None:
        if dr < 0:
            paragraphs.append(
                "자본총계가 음수(완전자본잠식)라 부채비율을 정상적으로 해석할 수 없습니다. "
                "관리종목 지정·상장 유지 요건과 증자 등 재무 위험을 반드시 확인하세요."
            )
        elif dr < 50:
            paragraphs.append(
                f"부채비율 {dr:.1f}%는 매우 낮은 수준으로, 무차입 또는 보수적 재무 구조를 유지하고 있습니다. "
                "금리 상승기에도 재무적 부담이 경미합니다."
            )
        elif dr < 100:
            paragraphs.append(
                f"부채비율 {dr:.1f}%는 안정적 수준으로, 재무 레버리지가 건전하게 관리되고 있습니다."
            )
        elif dr < 200:
            paragraphs.append(
                f"부채비율 {dr:.1f}%는 업계 평균 수준(100~200%)에 해당하며, 레버리지 관리가 중요합니다."
            )
        else:
            paragraphs.append(
                f"부채비율 {dr:.1f}%는 높은 편입니다. 이자 부담 및 유동성 리스크를 면밀히 점검해야 합니다."
            )

    # Profitability
    om = ratios.get("op_margin")
    nm = ratios.get("net_margin")
    if om is not None:
        if om > 20:
            paragraphs.append(
                f"영업이익률 {om:.1f}%는 매우 높은 수익성을 입증합니다. "
                "강력한 가격 결정력 또는 원가 경쟁력을 보유한 것으로 판단됩니다."
            )
        elif om > 10:
            paragraphs.append(
                f"영업이익률 {om:.1f}%는 안정적 수익성을 나타냅니다."
                + (f" 순이익률 {nm:.1f}%까지 고려할 때 전반적 수익 구조가 건전합니다." if nm and nm > 5 else "")
            )
        elif om > 0:
            paragraphs.append(
                f"영업이익률 {om:.1f}%는 낮은 편으로, 수익성 개선이 향후 핵심 과제입니다."
            )
        else:
            paragraphs.append(
                f"영업이익 적자(영업이익률 {om:.1f}%)는 핵심 영업 활동에서의 손실을 의미합니다. "
                "사업 구조 재편 또는 비용 절감이 시급합니다."
            )

    # Capital efficiency
    roe = ratios.get("roe")
    roa = ratios.get("roa")
    if roe is not None:
        if roe > 15:
            paragraphs.append(
                f"ROE {roe:.1f}%는 자본 효율성이 탁월함을 보여줍니다."
                + (f" ROA {roa:.1f}%도 양호해 자산 운용 효율이 높습니다." if roa and roa > 5 else "")
            )
        elif roe > 5:
            paragraphs.append(f"ROE {roe:.1f}%는 적정 수준의 자본 수익성을 나타냅니다.")
        else:
            paragraphs.append(
                f"ROE {roe:.1f}%는 낮은 자본 효율성을 시사합니다. "
                "수익 모델 개선 또는 자본 재구조화 여지를 검토할 필요가 있습니다."
            )

    # Growth
    rg = ratios.get("revenue_growth")
    if rg is not None:
        if rg > 20:
            paragraphs.append(f"전년 대비 매출이 {rg:.1f}% 급성장하며 강한 성장 모멘텀을 보여줍니다.")
        elif rg > 5:
            paragraphs.append(f"매출 성장률 {rg:.1f}%는 안정적 성장세를 나타냅니다.")
        elif rg >= 0:
            paragraphs.append(f"매출 성장률 {rg:.1f}%로 소폭 성장에 그쳤습니다. 성장 동력 강화가 필요합니다.")
        else:
            paragraphs.append(
                f"매출이 전년 대비 {abs(rg):.1f}% 감소했습니다. "
                "수요 약화 또는 경쟁 심화 여부를 면밀히 파악해야 합니다."
            )

    # Liquidity
    cr = ratios.get("current_ratio")
    if cr is not None:
        if cr > 200:
            paragraphs.append(f"유동비율 {cr:.0f}%는 단기 채무 상환 능력이 매우 충분함을 나타냅니다.")
        elif cr > 100:
            paragraphs.append(f"유동비율 {cr:.0f}%는 단기 유동성이 적정 수준입니다.")
        else:
            paragraphs.append(
                f"유동비율 {cr:.0f}%는 단기 유동성이 다소 취약합니다. "
                "단기 차입 의존도를 낮추는 전략이 필요합니다."
            )

    # Outlook
    if score >= 75:
        outlook       = "매수(Buy)"
        outlook_eng   = "BUY"
        outlook_color = "green"
        outlook_reason = (
            f"재무 건전성 종합점수 {score:.0f}점(등급: {grade}) — "
            "견실한 재무구조·수익성을 바탕으로 중장기 투자 매력이 높습니다."
        )
    elif score >= 55:
        outlook       = "중립(Hold)"
        outlook_eng   = "HOLD"
        outlook_color = "yellow"
        outlook_reason = (
            f"재무 건전성 종합점수 {score:.0f}점(등급: {grade}) — "
            "일부 지표의 개선 여부를 모니터링하면서 보유 또는 소규모 분할 접근이 권고됩니다."
        )
    else:
        outlook       = "관망(Sell/Wait)"
        outlook_eng   = "SELL"
        outlook_color = "red"
        outlook_reason = (
            f"재무 건전성 종합점수 {score:.0f}점(등급: {grade}) — "
            "재무 리스크가 높아 투자에 신중을 기하고 실적 개선 확인 후 재검토를 권고합니다."
        )

    return {
        "paragraphs":     paragraphs,
        "outlook":        outlook,
        "outlook_eng":    outlook_eng,
        "outlook_color":  outlook_color,
        "outlook_reason": outlook_reason,
        "disclaimer":     (
            "본 분석은 DART 공시 재무제표를 기반으로 한 자동화 AI 분석이며, "
            "투자 권유가 아닙니다. 실제 투자 판단은 전문가와 상담하시기 바랍니다."
        ),
    }


@app.post("/api/dart/financial-analysis")
def dart_financial_analysis(req: DartFinancialAnalysisRequest) -> dict:
    """Fetch DART financial statements and run AI-powered financial health analysis."""
    key = _dart_api_key()

    # ── 1. Company meta-data ─────────────────────────────────────────────────
    company = _fetch_company_detail(req.corp_code)
    if not company:
        raise HTTPException(status_code=404, detail="DART 기업 정보를 조회할 수 없습니다.")

    corp_cls = company.get("corp_cls", "")
    market   = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}.get(corp_cls, "비상장/기타")

    # ── 2. Financial statements: 요청 보고서, 또는 가장 최근 공시 보고서 ──────────
    def _fetch_fin(bsns_year: str, reprt_code: str) -> dict:
        # fnlttSinglAcnt는 fs_div를 받지 않고 연결(CFS)·별도(OFS) 행을 함께 돌려준다.
        url = ("https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?"
               + urllib.parse.urlencode({
                   "crtfc_key":  key,
                   "corp_code":  req.corp_code,
                   "bsns_year":  bsns_year,
                   "reprt_code": reprt_code,
               }))
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception:
            return {}

    today = datetime.now(timezone(timedelta(hours=9))).date()
    candidates = _dart_report_candidates(today, req.bsns_year, req.reprt_code)
    fin_data, bsns_year, reprt_code, tried = {}, "", "", []
    for year, code in candidates:
        fin_data = _fetch_fin(year, code)
        tried.append(f"{year}년 {DART_REPORTS[code][2]}")
        if fin_data.get("status") == "000" and fin_data.get("list"):
            bsns_year, reprt_code = year, code
            break
    else:
        message = fin_data.get("message", "알 수 없음") if fin_data else "응답 없음"
        raise HTTPException(
            status_code=404,
            detail=f"공시된 재무제표를 찾지 못했습니다(확인: {', '.join(tried) or '없음'}). DART 응답: {message}",
        )

    rows = fin_data.get("list", [])
    consolidated = [row for row in rows if row.get("fs_div") == "CFS"]
    is_consolidated = bool(consolidated)  # 연결재무제표 우선, 없으면 별도
    items = consolidated or [row for row in rows if row.get("fs_div") == "OFS"] or rows
    report_name, months, short_name, _ = DART_REPORTS[reprt_code]
    fin   = _parse_dart_amounts(items, cumulative=months < 12)
    income_row = next((row for row in items if row.get("sj_div") == "IS"), {})
    balance_row = next((row for row in items if row.get("sj_div") == "BS"), {})
    rcept_no = next((row.get("rcept_no") for row in items if row.get("rcept_no")), "")
    report = {
        "bsns_year": bsns_year, "reprt_code": reprt_code, "reprt_name": report_name,
        "label": f"{bsns_year}년 {short_name}", "months": months, "annualized": months < 12,
        "mode": "selected" if req.bsns_year and req.reprt_code else "latest",
        "period": income_row.get("thstrm_dt", ""), "prior_period": income_row.get("frmtrm_dt", ""),
        "balance_date": balance_row.get("thstrm_dt", ""),
        "current_label": f"{bsns_year}년 {short_name}" + (" 누적" if months < 12 else ""),
        "prior_label": f"{int(bsns_year) - 1}년 {short_name}" + (" 누적" if months < 12 else ""),
        "rcept_no": rcept_no,
        "dart_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else "",
        "checked": tried,
    }

    # ── 3. Ratios & scoring ──────────────────────────────────────────────────
    ratios       = _calc_dart_ratios(fin, annualize=12 / months)
    score, breakdown = _score_financial_health(ratios)
    grade        = (
        "A+" if score >= 90 else "A" if score >= 80 else "B+" if score >= 75
        else "B" if score >= 70 else "C" if score >= 60 else "D" if score >= 50 else "F"
    )
    verdict      = "매우 견실" if score >= 85 else "견실" if score >= 70 else "보통" if score >= 55 else "취약"

    # ── 4. Friendly financial snapshot (unit: 억원) ──────────────────────────
    B = 100_000_000  # 1억

    def to_eok(v: float) -> float | None:
        return round(v / B, 1) if v else None

    snap = {
        "revenue":              to_eok(fin["revenue"]["current"]),
        "prev_revenue":         to_eok(fin["revenue"]["prior"]),
        "op_income":            to_eok(fin["op_income"]["current"]),
        "prev_op_income":       to_eok(fin["op_income"]["prior"]),
        "net_income":           to_eok(fin["net_income"]["current"]),
        "prev_net_income":      to_eok(fin["net_income"]["prior"]),
        "total_assets":         to_eok(fin["total_assets"]["current"]),
        "total_liabilities":    to_eok(fin["total_liabilities"]["current"]),
        "total_equity":         to_eok(fin["total_equity"]["current"]),
        "current_assets":       to_eok(fin["current_assets"]["current"]),
        "current_liabilities":  to_eok(fin["current_liabilities"]["current"]),
        "retained_earnings":    to_eok(fin["retained_earnings"]["current"]),
        "is_consolidated":      is_consolidated,
        "unit":                 "억원",
    }

    analysis = _generate_dart_analysis(company, market, ratios, score, grade, report["label"])

    return {
        "company":  {
            "corp_code":  req.corp_code,
            "corp_name":  company.get("corp_name", ""),
            "ceo_nm":     company.get("ceo_nm", ""),
            "adres":      company.get("adres", ""),
            "est_dt":     company.get("est_dt", ""),
            "stock_code": company.get("stock_code", ""),
            "corp_cls":   corp_cls,
            "market":     market,
        },
        "financials": snap,
        "ratios":     ratios,
        "health":     {
            "score":     score,
            "grade":     grade,
            "verdict":   verdict,
            "breakdown": breakdown,
        },
        "analysis":       analysis,
        "bsns_year":  bsns_year,
        "report":     report,
    }


install_openapi(app)

# ─────────────────────────────────────────────────────────────────────────────

# 학습 문서에서 사용하는 Notebook 내보내기 이미지는 프런트엔드 정적 폴더 밖에
# 보관되어 있으므로, 루트 정적 파일보다 먼저 별도 경로로 제공합니다.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
