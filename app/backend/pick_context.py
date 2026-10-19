"""추천 카드의 판단 근거 표시(2026-10-06, 클로드코드·코덱스 합의안 A).

추천을 막거나 순서를 바꾸지 않는 참고 정보만 만든다. 엔진 규칙·게시본·전향 관측과 무관하다.
- 기준일 갭: 추천 기준일(as_of) 시가가 전 거래일 종가보다 5% 이상 낮게 시작했는지. 이미 관측된 값만 쓰고,
  다음 거래일 진입 시가의 갭은 예측하지 않는다.
- 업종 집중: 같은 날 추천(아이템 무관, 종목 중복 제거) 중 같은 업종이 2종목 이상인지.
- 최근 지분·자사주 공시: 최근 7일 수집분에 있는지. 좋다·나쁘다를 판정하지 않는 중립 표시다.
"""
from __future__ import annotations

import re

GAP_WARNING_PCT = -5.0
KIND_LABELS = {"treasury": "자사주", "insider": "임원·주요주주", "major": "5% 이상 보유"}


def as_of_gap(bars: list[dict], as_of: str) -> dict:
    """기준일 시가 / 전 거래일 종가 − 1. 기준일 봉이나 전일 봉이 없으면 확인 불가로 둔다."""
    ordered = sorted(bars, key=lambda bar: str(bar["timestamp"])[:10])
    index = next((i for i, bar in enumerate(ordered) if str(bar["timestamp"])[:10] == as_of), None)
    if index is None or index == 0:
        return {"status": "unavailable", "as_of": as_of}
    previous_close, open_price = float(ordered[index - 1]["close"]), float(ordered[index]["open"])
    if previous_close <= 0 or open_price <= 0:
        return {"status": "unavailable", "as_of": as_of}
    pct = round((open_price / previous_close - 1) * 100, 2)
    return {"status": "warning" if pct <= GAP_WARNING_PCT else "ok", "as_of": as_of, "gap_pct": pct,
            "open": open_price, "previous_close": previous_close,
            "previous_date": str(ordered[index - 1]["timestamp"])[:10]}


def sector_concentration(sectors: dict[str, str | None]) -> dict[str, list[str]]:
    """업종 → 같은 날 추천된 종목들(2종목 이상인 업종만). 업종을 모르는 종목은 세지 않는다."""
    groups: dict[str, list[str]] = {}
    for symbol, sector in sectors.items():
        if sector:
            groups.setdefault(sector, []).append(symbol)
    return {sector: sorted(symbols) for sector, symbols in groups.items() if len(symbols) >= 2}


def recent_activity(items: list[dict], symbol: str) -> dict | None:
    """최근 7일 지분·자사주 공시 요약. 없으면 None."""
    mine = [item for item in items if str(item.get("stock_code") or "") == symbol]
    if not mine:
        return None
    kinds: dict[str, int] = {}
    for item in mine:
        group = next((key for key in KIND_LABELS if str(item.get("kind", "")).startswith(key)), None)
        if group:
            kinds[KIND_LABELS[group]] = kinds.get(KIND_LABELS[group], 0) + 1
    latest = max(str(item.get("rcept_date") or "") for item in mine)
    return {"count": len(mine), "kinds": kinds, "latest_date": f"{latest[:4]}-{latest[4:6]}-{latest[6:8]}" if len(latest) == 8 else None}


# ── EINT-058 하락 구간의 공시·뉴스 동반 여부 ─────────────────────────────
# 뉴스·공시를 동반한 급락은 이어지고 동반하지 않은 급락은 되돌아오는 경향이 보고됐다(Chan 2003 JFE,
# Savor 2012 JFE). 이 시스템에서 검증한 규칙이 아니므로 표시만 하고 추천·순서에는 쓰지 않는다.
DECLINE_WINDOW_DAYS = 5
EARNINGS_TITLE = re.compile(r"영업\(잠정\)실적|잠정실적|매출액또는손익구조|결산실적|영업실적")


def decline_window(bars: list[dict], as_of: str, days: int = DECLINE_WINDOW_DAYS) -> dict | None:
    """기준일까지 최근 days거래일(기준일 포함)의 구간 수익률과 가장 크게 떨어진 날. 봉이 모자라면 None."""
    ordered = sorted(bars, key=lambda bar: str(bar["timestamp"])[:10])
    index = next((i for i, bar in enumerate(ordered) if str(bar["timestamp"])[:10] == as_of), None)
    if index is None or index < days:
        return None
    window = ordered[index - days + 1:index + 1]
    base = float(ordered[index - days]["close"])
    changes = []
    for i, bar in enumerate(window):
        prev = float(ordered[index - days + i]["close"])
        changes.append((str(bar["timestamp"])[:10], (float(bar["close"]) / prev - 1) * 100 if prev > 0 else 0.0))
    worst = min(changes, key=lambda item: item[1])
    return {"start": str(window[0]["timestamp"])[:10], "end": as_of,
            "return_pct": round((float(window[-1]["close"]) / base - 1) * 100, 2) if base > 0 else None,
            "worst_day": {"date": worst[0], "pct": round(worst[1], 2)}}


def _day(value: str | None) -> str:
    text = re.sub(r"\D", "", str(value or ""))[:8]
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 else ""


def decline_context(window: dict | None, *, disclosures: list[dict] | None, news: list[dict] | None,
                    news_partial: bool = False, disclosure_error: str | None = None, news_error: str | None = None,
                    classify=None) -> dict:
    """구간 안의 공시(DART list 행)·뉴스(relevant_news 행)를 날짜로 걸러 요약한다.

    조회 실패는 '없음'과 구별한다. 뉴스가 최근 N건 제한으로 구간 시작까지 닿지 않으면 partial로 둔다.
    """
    if not window:
        return {"status": "unavailable", "reason": "기준일 주변 일봉 부족"}
    start, end = window["start"], window["end"]
    in_window = lambda day: bool(day) and start <= day <= end  # noqa: E731
    result = {"status": "ok", **window, "disclosures": None, "news": None, "news_partial": bool(news_partial),
              "disclosure_error": disclosure_error, "news_error": news_error}
    if disclosures is not None:
        rows = []
        for row in disclosures:
            day = _day(row.get("rcept_dt"))
            if not in_window(day):
                continue
            title = str(row.get("report_nm") or "").strip()
            risk = classify(title) if classify else None
            tag = risk[1] if risk else ("실적" if EARNINGS_TITLE.search(re.sub(r"\s+", "", title)) else None)
            rows.append({"date": day, "title": title, "tag": tag, "severity": risk[0] if risk else None})
        result["disclosures"] = sorted(rows, key=lambda row: row["date"], reverse=True)
    if news is not None:
        rows = [{"date": (item.get("published_at") or "")[:10], "title": item.get("title"), "source": item.get("source")}
                for item in news if in_window((item.get("published_at") or "")[:10])]
        result["news"] = sorted(rows, key=lambda row: row["date"], reverse=True)
    if result["disclosures"] is None and result["news"] is None:
        result["status"] = "unavailable"
        result["reason"] = "공시·뉴스 조회 실패"
    return result
