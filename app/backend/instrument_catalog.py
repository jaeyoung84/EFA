"""KRX 상장법인 목록을 이용한 국내 종목 검색.

EINT-018: 1순위 출처는 KQS 엔진이 매 거래일 게시하는 ``instrument-catalog.json``(KRX 공식 일봉 수집본의
KOSPI/KOSDAQ 전 종목, 현재 상장 종목만)이다. AWS에서는 KIND 접근이 403으로 막히므로 KIND는 보조 출처다.

포트폴리오 입력에서 한글 회사명을 Yahoo 자동완성에만 의존하지 않도록 KIND가
제공하는 상장법인 목록을 짧게 내려받아 메모리에 캐시한다. 운영 환경에서 KIND
접근이 제한되는 경우에는 ``KRX_COMPANY_LIST_URL``에 같은 형식의 JSON 미러를
지정할 수 있다. 외부 소스가 실패하면 호출자는 기존 정적 별칭/Yahoo 검색으로
폴백할 수 있도록 빈 목록을 반환한다.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

KIND_COMPANY_LIST_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download"
CATALOG_TTL_SECONDS = 24 * 60 * 60
FAILED_REFRESH_RETRY_SECONDS = 5 * 60

_MARKET_META = {
    "유가": ("KS", "KOSPI"),
    "코스피": ("KS", "KOSPI"),
    "KOSPI": ("KS", "KOSPI"),
    "코스닥": ("KQ", "KOSDAQ"),
    "KOSDAQ": ("KQ", "KOSDAQ"),
}
# KRX 약식 종목명과 흔히 쓰는 이름이 다른 경우만 둔다(검색어 → KRX 종목명).
SEARCH_ALIASES = {
    "네이버": "NAVER", "현대자동차": "현대차", "기아자동차": "기아", "포스코": "POSCO홀딩스",
    "엘지전자": "LG전자", "엘지화학": "LG화학", "에스케이하이닉스": "SK하이닉스", "하이닉스": "SK하이닉스",
    "삼전": "삼성전자", "엔씨소프트": "엔씨소프트", "케이티": "KT", "케이티앤지": "KT&G",
}
_catalog_lock = threading.Lock()
_catalog: list[dict[str, str]] = []
_catalog_loaded_at = 0.0
_last_refresh_failed_at = 0.0
_kqs_catalog: list[dict[str, str]] = []
_kqs_catalog_key: tuple[str, float] | None = None
_kqs_lock = threading.Lock()


def kqs_catalog_path() -> Path:
    configured = os.getenv("KQS_INSTRUMENT_CATALOG", "").strip()
    if configured:
        return Path(configured)
    snapshot = os.getenv("KQS_SNAPSHOT_DB", "").strip()
    if snapshot:
        return Path(snapshot).parent / "instrument-catalog.json"
    return Path(__file__).resolve().parents[2] / "state/kqs/instrument-catalog.json"


def get_kqs_catalog() -> list[dict[str, str]]:
    """엔진 게시 종목 목록. 파일이 바뀌면(mtime) 다시 읽고, 없거나 손상되면 빈 목록이다."""
    global _kqs_catalog, _kqs_catalog_key
    path = kqs_catalog_path()
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return []
    if key == _kqs_catalog_key:
        return _kqs_catalog
    with _kqs_lock:
        if key == _kqs_catalog_key:
            return _kqs_catalog
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema") != "kqs-instrument-catalog/v1":
                return _kqs_catalog
            items = []
            for row in payload.get("items", []):
                item = _normalize_entry({"name": row.get("name"), "code": row.get("symbol"), "market": row.get("market")})
                if item:
                    item.update(source="KQS_KRX_DAILY", market=item["exchange"], as_of=payload.get("as_of"),
                                market_cap=int(row.get("market_cap") or 0))
                    items.append(item)
        except (OSError, ValueError, TypeError, AttributeError):
            return _kqs_catalog
        if items:
            _kqs_catalog, _kqs_catalog_key = items, key
        return _kqs_catalog


class _KindTableParser(HTMLParser):
    """KIND가 XLS 확장자로 반환하는 HTML 테이블의 셀을 읽는다."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._row is not None and self._cell_parts is not None:
            self._row.append(" ".join("".join(self._cell_parts).split()))
            self._cell_parts = None
        elif lowered == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell_parts = None


def _timeout_seconds() -> float:
    try:
        return min(15.0, max(1.0, float(os.getenv("KRX_COMPANY_LIST_TIMEOUT_SECONDS", "4"))))
    except ValueError:
        return 4.0


def _request_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; JY-Investing/2.0)"},
    )
    with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
        return response.read()


def _normalize_entry(raw: dict[str, Any]) -> dict[str, str] | None:
    name = str(raw.get("name") or raw.get("회사명") or "").strip()
    code = str(raw.get("code") or raw.get("종목코드") or "").strip().upper()
    market = str(raw.get("market") or raw.get("시장구분") or "").strip()
    market_meta = _MARKET_META.get(market)
    if not name or not re.fullmatch(r"[0-9A-Z]{6}", code) or not market_meta:
        return None
    suffix, exchange = market_meta
    return {
        "ticker": f"{code}.{suffix}",
        "code": code,
        "name": name,
        "exchange": exchange,
        "type": "EQUITY",
        "source": "KRX_KIND",
    }


def _parse_kind_html(payload: bytes) -> list[dict[str, str]]:
    parser = _KindTableParser()
    parser.feed(payload.decode("euc-kr", errors="replace"))
    companies: list[dict[str, str]] = []
    for row in parser.rows:
        if len(row) < 3 or row[0] == "회사명":
            continue
        item = _normalize_entry({"name": row[0], "market": row[1], "code": row[2]})
        if item:
            companies.append(item)
    return companies


def _load_json_mirror(url: str) -> list[dict[str, str]]:
    payload = json.loads(_request_bytes(url).decode("utf-8"))
    rows = payload.get("items", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows:
        if isinstance(row, dict) and (item := _normalize_entry(row)):
            result.append(item)
    return result


def _download_catalog() -> list[dict[str, str]]:
    mirror_url = os.getenv("KRX_COMPANY_LIST_URL", "").strip()
    if mirror_url:
        mirrored = _load_json_mirror(mirror_url)
        if mirrored:
            return mirrored
    return _parse_kind_html(_request_bytes(KIND_COMPANY_LIST_URL))


def get_krx_catalog() -> list[dict[str, str]]:
    """KOSPI/KOSDAQ 상장법인 목록. 엔진 게시 목록이 있으면 그것을, 없으면 캐시된 KIND 목록을 쓴다."""
    global _catalog, _catalog_loaded_at, _last_refresh_failed_at
    kqs = get_kqs_catalog()
    if kqs:
        return kqs
    now = time.monotonic()
    if _catalog and now - _catalog_loaded_at < CATALOG_TTL_SECONDS:
        return _catalog
    if _last_refresh_failed_at and now - _last_refresh_failed_at < FAILED_REFRESH_RETRY_SECONDS:
        return _catalog

    with _catalog_lock:
        now = time.monotonic()
        if _catalog and now - _catalog_loaded_at < CATALOG_TTL_SECONDS:
            return _catalog
        if _last_refresh_failed_at and now - _last_refresh_failed_at < FAILED_REFRESH_RETRY_SECONDS:
            return _catalog
        try:
            downloaded = _download_catalog()
        except Exception:
            _last_refresh_failed_at = now
            return _catalog
        if downloaded:
            _catalog = downloaded
            _catalog_loaded_at = now
            _last_refresh_failed_at = 0.0
        return _catalog


def _search_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def search_krx_instruments(query: str, limit: int = 12) -> list[dict[str, str]]:
    """회사명(별칭 포함) 또는 6자리 종목코드로 국내 상장사를 검색한다.

    정확히 일치 → 앞부분 일치 → 포함 순, 같은 순위 안에서는 시가총액이 큰 종목이 먼저다.
    """
    key = _search_key(query)
    if not key:
        return []
    keys = {key}
    for alias, target in SEARCH_ALIASES.items():
        alias_key = _search_key(alias)
        if alias_key == key or (len(key) >= 2 and alias_key.startswith(key)):
            keys.add(_search_key(target))
    ranked: list[tuple[tuple[int, int, int, str], dict[str, str]]] = []
    for item in get_krx_catalog():
        name_key = _search_key(item["name"])
        code_key = item["code"].casefold()
        best = None
        for candidate in keys:
            if candidate in {name_key, code_key}:
                level = 0
            elif name_key.startswith(candidate) or code_key.startswith(candidate):
                level = 1
            elif candidate in name_key or candidate in code_key:
                level = 2
            else:
                continue
            best = level if best is None else min(best, level)
        if best is None:
            continue
        ranked.append(((best, -int(item.get("market_cap") or 0), len(name_key), name_key), item))
    ranked.sort(key=lambda pair: pair[0])
    return [dict(item) for _, item in ranked[: max(1, min(limit, 30))]]


def resolve_krx_name(query: str) -> dict[str, str] | None:
    """종목명·별칭·코드 하나를 국내 종목 하나로 확정한다(정확히 일치하거나 결과가 하나일 때만)."""
    results = search_krx_instruments(query, limit=5)
    if not results:
        return None
    key = _search_key(query)
    targets = {key} | {_search_key(t) for a, t in SEARCH_ALIASES.items() if _search_key(a) == key}
    exact = [item for item in results if _search_key(item["name"]) in targets or item["code"].casefold() in targets]
    if exact:
        return exact[0]
    return results[0] if len(results) == 1 else None


def _reset_catalog_cache_for_tests() -> None:
    global _catalog, _catalog_loaded_at, _last_refresh_failed_at, _kqs_catalog, _kqs_catalog_key
    _kqs_catalog, _kqs_catalog_key = [], None
    with _catalog_lock:
        _catalog = []
        _catalog_loaded_at = 0.0
        _last_refresh_failed_at = 0.0
