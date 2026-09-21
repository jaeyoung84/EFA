"""KRX 상장법인 목록을 이용한 국내 종목 검색.

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
from typing import Any

KIND_COMPANY_LIST_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download"
CATALOG_TTL_SECONDS = 24 * 60 * 60
FAILED_REFRESH_RETRY_SECONDS = 5 * 60

_MARKET_META = {
    "유가": ("KS", "KOSPI"),
    "코스피": ("KS", "KOSPI"),
    "코스닥": ("KQ", "KOSDAQ"),
}
_catalog_lock = threading.Lock()
_catalog: list[dict[str, str]] = []
_catalog_loaded_at = 0.0
_last_refresh_failed_at = 0.0


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
    """캐시된 KOSPI/KOSDAQ 상장법인 목록을 반환한다."""
    global _catalog, _catalog_loaded_at, _last_refresh_failed_at
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
    """회사명 또는 6자리 종목코드로 국내 상장사를 검색한다."""
    key = _search_key(query)
    if not key:
        return []
    ranked: list[tuple[tuple[int, int, str], dict[str, str]]] = []
    for item in get_krx_catalog():
        name_key = _search_key(item["name"])
        code_key = item["code"].casefold()
        if key not in name_key and key not in code_key:
            continue
        rank = (
            0 if key in {name_key, code_key} else 1 if name_key.startswith(key) or code_key.startswith(key) else 2,
            len(name_key),
            name_key,
        )
        ranked.append((rank, item))
    ranked.sort(key=lambda pair: pair[0])
    return [dict(item) for _, item in ranked[: max(1, min(limit, 30))]]


def _reset_catalog_cache_for_tests() -> None:
    global _catalog, _catalog_loaded_at, _last_refresh_failed_at
    with _catalog_lock:
        _catalog = []
        _catalog_loaded_at = 0.0
        _last_refresh_failed_at = 0.0
