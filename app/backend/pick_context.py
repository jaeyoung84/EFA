"""추천 카드의 판단 근거 표시(2026-10-06, 클로드코드·코덱스 합의안 A).

추천을 막거나 순서를 바꾸지 않는 참고 정보만 만든다. 엔진 규칙·게시본·전향 관측과 무관하다.
- 기준일 갭: 추천 기준일(as_of) 시가가 전 거래일 종가보다 5% 이상 낮게 시작했는지. 이미 관측된 값만 쓰고,
  다음 거래일 진입 시가의 갭은 예측하지 않는다.
- 업종 집중: 같은 날 추천(아이템 무관, 종목 중복 제거) 중 같은 업종이 2종목 이상인지.
- 최근 지분·자사주 공시: 최근 7일 수집분에 있는지. 좋다·나쁘다를 판정하지 않는 중립 표시다.
"""
from __future__ import annotations

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
