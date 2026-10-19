"""슈퍼트렌드 독립 차트 계산 (설계: docs/handoff/codex/2026-10-19-supertrend-chart-design.md).

차트 화면의 판단 보조 수치만 계산한다. 엔진 추천·청산·자동매매·원장에 연결하지 않으며,
신호는 확정 일봉에서만 만들고 BUY·SELL은 체결이 아니라 추세 전환 사실을 뜻한다.

- 슈퍼트렌드: TradingView ``ta.supertrend``와 같은 hl2·TR·RMA ATR·밴드 재귀식·초기 방향
  (방향 -1=상승, 1=하락). ATR을 rolling SMA로 다시 계산하지 않는다.
- 주봉: 일봉 원 OHLC를 거래소 현지 ISO 주 단위로 묶어 독립 계산하고, 각 일봉에는 그 주가
  시작되기 전에 끝난 직전 주의 방향만 붙인다(Pine ``[1]`` + ``lookahead_on``과 같은 의미).
- 모든 지표는 봉 t까지의 자료로만 계산하므로, 뒤에 봉을 추가하거나 표시 구간을 바꿔도
  기존 봉의 값은 바뀌지 않는다.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_ATR_LENGTH = 10
DEFAULT_FACTOR = 3.0
ADX_LENGTH = 14
VOLUME_LOOKBACK = 20
FLIP_LOOKBACK = 20
LEVEL_LOOKBACKS = (20, 60)
SMA_LENGTHS = (20, 60, 200)
# 정규장 마감 뒤 공급자 일봉 수집이 끝날 때까지 기다리는 여유. 그 전의 당일 봉은 미확정이다.
CONFIRM_BUFFER = timedelta(minutes=30)
KR_SESSION = (ZoneInfo("Asia/Seoul"), time(15, 30))
US_SESSION = (ZoneInfo("America/New_York"), time(16, 0))


# ── 기본 시계열 ─────────────────────────────────────────────────────────────

def true_range(highs: list[float], lows: list[float], closes: list[float], *, first_is_range: bool) -> list[float | None]:
    """TR. 첫 봉은 ``ta.atr``처럼 고가-저가(first_is_range) 또는 ``ta.tr``처럼 결측이다."""
    out: list[float | None] = []
    for i, (high, low) in enumerate(zip(highs, lows)):
        if i == 0:
            out.append(high - low if first_is_range else None)
        else:
            prev = closes[i - 1]
            out.append(max(high - low, abs(high - prev), abs(low - prev)))
    return out


def rma(values: list[float | None], length: int) -> list[float | None]:
    """Pine ``ta.rma``: 직전 length개가 모두 있는 첫 봉에서 SMA로 시작해 Wilder 평활한다."""
    out: list[float | None] = [None] * len(values)
    alpha = 1.0 / length
    prev: float | None = None
    run = 0
    for i, value in enumerate(values):
        if prev is None:
            run = run + 1 if value is not None else 0
            if run >= length:
                prev = sum(values[i - length + 1:i + 1]) / length  # type: ignore[arg-type]
                out[i] = prev
        elif value is not None:
            prev = alpha * value + (1 - alpha) * prev
            out[i] = prev
        else:
            out[i] = prev
    return out


def sma(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= length:
            total -= values[i - length]
        if i >= length - 1:
            out[i] = total / length
    return out


def supertrend(highs: list[float], lows: list[float], closes: list[float], *, atr_length: int = DEFAULT_ATR_LENGTH,
               factor: float = DEFAULT_FACTOR) -> dict[str, list]:
    """TradingView ``ta.supertrend`` 재현. 반환: line, direction(-1 상승·1 하락·None 준비 중), atr, upper, lower."""
    n = len(closes)
    atr = rma(true_range(highs, lows, closes, first_is_range=True), atr_length)
    line: list[float | None] = [None] * n
    direction: list[int | None] = [None] * n
    upper_out: list[float | None] = [None] * n
    lower_out: list[float | None] = [None] * n
    # Pine은 nz(band[1])로 결측 밴드를 0으로 본다. 준비 기간의 밴드도 그 규칙을 따른다.
    prev_upper: float | None = None
    prev_lower: float | None = None
    prev_line: float | None = None
    for i in range(n):
        p_upper = prev_upper if prev_upper is not None else 0.0
        p_lower = prev_lower if prev_lower is not None else 0.0
        close_prev = closes[i - 1] if i else None
        if atr[i] is None:
            upper = lower = None
        else:
            src = (highs[i] + lows[i]) / 2
            upper = src + factor * atr[i]
            lower = src - factor * atr[i]
        # lowerBand := lowerBand > prevLower or close[1] < prevLower ? lowerBand : prevLower (na 비교는 거짓)
        lower = lower if (lower is not None and lower > p_lower) or (close_prev is not None and close_prev < p_lower) else p_lower
        upper = upper if (upper is not None and upper < p_upper) or (close_prev is not None and close_prev > p_upper) else p_upper
        if i == 0 or atr[i - 1] is None:
            dir_i = 1
        elif prev_line == p_upper:
            dir_i = -1 if closes[i] > upper else 1
        else:
            dir_i = 1 if closes[i] < lower else -1
        value = lower if dir_i == -1 else upper
        if atr[i] is not None and value is not None:
            line[i] = value
            direction[i] = dir_i
            upper_out[i] = upper
            lower_out[i] = lower
        prev_upper, prev_lower, prev_line = upper, lower, value
    return {"line": line, "direction": direction, "atr": atr, "upper": upper_out, "lower": lower_out}


def dmi_adx(highs: list[float], lows: list[float], closes: list[float], length: int = ADX_LENGTH) -> dict[str, list]:
    """Pine ``ta.dmi(length, length)``: Wilder +DI·-DI·ADX."""
    n = len(closes)
    plus_dm: list[float | None] = [None] * n
    minus_dm: list[float | None] = [None] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    tr_rma = rma(true_range(highs, lows, closes, first_is_range=False), length)
    plus_rma = rma(plus_dm, length)
    minus_rma = rma(minus_dm, length)
    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    dx: list[float | None] = [None] * n
    for i in range(n):
        if tr_rma[i] and plus_rma[i] is not None and minus_rma[i] is not None:
            plus_di[i] = 100 * plus_rma[i] / tr_rma[i]
            minus_di[i] = 100 * minus_rma[i] / tr_rma[i]
            total = plus_di[i] + minus_di[i]
            dx[i] = abs(plus_di[i] - minus_di[i]) / (total if total else 1)
    adx = [None if value is None else 100 * value for value in rma(dx, length)]
    return {"plus_di": plus_di, "minus_di": minus_di, "adx": adx}


# ── 주봉·확정 여부 ──────────────────────────────────────────────────────────

def week_key(day: str) -> tuple[int, int]:
    iso = date.fromisoformat(day).isocalendar()
    return iso[0], iso[1]


def weekly_bars(days: list[str], opens: list[float], highs: list[float], lows: list[float], closes: list[float]) -> list[dict]:
    """일봉을 ISO 주(월~일) 단위로 묶는다. 휴장일은 봉이 없으므로 실제 거래일만 모인다."""
    weeks: list[dict] = []
    for i, day in enumerate(days):
        key = week_key(day)
        if not weeks or weeks[-1]["key"] != key:
            weeks.append({"key": key, "start": day, "end": day, "open": opens[i], "high": highs[i], "low": lows[i], "close": closes[i]})
        else:
            week = weeks[-1]
            week.update(end=day, high=max(week["high"], highs[i]), low=min(week["low"], lows[i]), close=closes[i])
    return weeks


def session_for(ticker: str) -> tuple[ZoneInfo, time]:
    upper = ticker.upper()
    if upper.endswith((".KS", ".KQ")) or upper in {"^KS11", "^KQ11", "^KS200"}:
        return KR_SESSION
    return US_SESSION


def last_bar_confirmed(ticker: str, last_day: str, now: datetime | None = None) -> bool:
    """마지막 일봉이 정규장 마감·수집 여유 이후인지. 날짜가 바뀌었다는 것만으로 확정하지 않는다."""
    zone, close_time = session_for(ticker)
    now_local = (now or datetime.now(zone)).astimezone(zone)
    bar_day = date.fromisoformat(last_day)
    if bar_day < now_local.date():
        return True
    if bar_day > now_local.date():
        return False
    return now_local >= datetime.combine(bar_day, close_time, zone) + CONFIRM_BUFFER


# ── 화면용 계산 ─────────────────────────────────────────────────────────────

def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _pct(numerator: float, denominator: float) -> float | None:
    return None if not denominator else numerator / denominator * 100


def compute_chart(bars: list[dict], *, ticker: str, atr_length: int = DEFAULT_ATR_LENGTH, factor: float = DEFAULT_FACTOR,
                  now: datetime | None = None, benchmark_bars: list[dict] | None = None) -> dict:
    """일봉 목록(timestamp·open·high·low·close·volume)으로 화면에 필요한 봉별 값·전환 목록을 만든다."""
    ordered = sorted(bars, key=lambda bar: str(bar["timestamp"])[:10])
    days = [str(bar["timestamp"])[:10] for bar in ordered]
    opens = [float(bar["open"]) for bar in ordered]
    highs = [float(bar["high"]) for bar in ordered]
    lows = [float(bar["low"]) for bar in ordered]
    closes = [float(bar["close"]) for bar in ordered]
    volumes = [None if bar.get("volume") is None else float(bar["volume"]) for bar in ordered]
    n = len(days)
    if not n:
        return {"bars": [], "flips": [], "summary": None}

    confirmed_last = last_bar_confirmed(ticker, days[-1], now)
    st = supertrend(highs, lows, closes, atr_length=atr_length, factor=factor)
    dmi = dmi_adx(highs, lows, closes)
    smas = {length: sma(closes, length) for length in SMA_LENGTHS}

    # 주봉: 진행 중인 마지막 주도 계산되지만, 일봉에는 직전에 끝난 주의 값만 붙인다.
    weeks = weekly_bars(days, opens, highs, lows, closes)
    week_st = supertrend([w["high"] for w in weeks], [w["low"] for w in weeks], [w["close"] for w in weeks],
                         atr_length=atr_length, factor=factor)
    week_index = {week["key"]: i for i, week in enumerate(weeks)}

    bench_dir: dict[str, int | None] = {}
    if benchmark_bars:
        b_sorted = sorted(benchmark_bars, key=lambda bar: str(bar["timestamp"])[:10])
        b_st = supertrend([float(b["high"]) for b in b_sorted], [float(b["low"]) for b in b_sorted],
                          [float(b["close"]) for b in b_sorted], atr_length=atr_length, factor=factor)
        bench_dir = {str(b["timestamp"])[:10]: d for b, d in zip(b_sorted, b_st["direction"])}

    rows: list[dict] = []
    flips: list[dict] = []
    last_flip: int | None = None
    flip_flags: list[bool] = [False] * n
    for i in range(n):
        confirmed = i < n - 1 or confirmed_last
        direction = st["direction"][i]
        prev_direction = st["direction"][i - 1] if i else None
        flip = None
        if confirmed and direction is not None and prev_direction is not None and direction != prev_direction:
            flip = "up" if direction == -1 else "down"
            flip_flags[i] = True
            last_flip = i
        w = week_index[week_key(days[i])]
        weekly_direction = week_st["direction"][w - 1] if w >= 1 else None
        weekly_ref = weeks[w - 1] if w >= 1 else None

        line = st["line"][i]
        atr = st["atr"][i]
        close = closes[i]
        distance_pct = atr_distance = None
        if line is not None:
            distance_pct = _pct(close - line, close) if direction == -1 else _pct(line - close, close)
            atr_distance = abs(close - line) / atr if atr else None

        volume_ratio = None
        # 장중 누적 거래량은 확정 일봉 평균과 같은 조건이 아니므로 비교하지 않는다.
        if confirmed and i >= VOLUME_LOOKBACK and volumes[i] is not None:
            window = volumes[i - VOLUME_LOOKBACK:i]
            if all(v is not None for v in window):
                mean = sum(window) / VOLUME_LOOKBACK  # type: ignore[arg-type]
                volume_ratio = volumes[i] / mean if mean > 0 else None

        flip_count = None
        if i >= FLIP_LOOKBACK and st["direction"][i - FLIP_LOOKBACK] is not None:
            flip_count = sum(flip_flags[i - FLIP_LOOKBACK + 1:i + 1])

        levels = {}
        for lookback in LEVEL_LOOKBACKS:
            if i >= lookback:
                levels[f"high{lookback}"] = max(highs[i - lookback:i])
                levels[f"low{lookback}"] = min(lows[i - lookback:i])
            else:
                levels[f"high{lookback}"] = levels[f"low{lookback}"] = None

        gap_pct = gap_atr = None
        if i:
            gap_pct = _pct(opens[i] - closes[i - 1], closes[i - 1])
            prev_atr = st["atr"][i - 1]
            gap_atr = abs(opens[i] - closes[i - 1]) / prev_atr if prev_atr else None

        # 확정 상승 추세에서 미확정 당일 종가가 전일 확정 상승선 아래면 '장중 이탈'로만 따로 표시한다.
        intraday_break = (not confirmed and i and st["direction"][i - 1] == -1 and st["line"][i - 1] is not None
                          and close < st["line"][i - 1])

        row = {
            "date": days[i], "o": opens[i], "h": highs[i], "l": lows[i], "c": close, "v": volumes[i],
            "confirmed": confirmed, "st": _round(line), "dir": direction, "flip": flip,
            "prev_st": _round(st["line"][i - 1]) if i else None, "prev_dir": prev_direction,
            "atr": _round(atr), "atr_pct": _round(_pct(atr, close)) if atr else None,
            "distance_pct": _round(distance_pct), "atr_distance": _round(atr_distance),
            "weekly_dir": weekly_direction, "weekly_ref_start": weekly_ref["start"] if weekly_ref else None,
            "weekly_ref_end": weekly_ref["end"] if weekly_ref else None,
            "adx": _round(dmi["adx"][i]), "plus_di": _round(dmi["plus_di"][i]), "minus_di": _round(dmi["minus_di"][i]),
            "volume_ratio": _round(volume_ratio), "flip_count": flip_count,
            "gap_pct": _round(gap_pct), "gap_atr": _round(gap_atr),
            "last_flip_date": days[last_flip] if last_flip is not None else None,
            "last_flip_close": closes[last_flip] if last_flip is not None else None,
            "last_flip_type": rows[last_flip]["flip"] if last_flip is not None and last_flip < i else flip,
            "days_since_flip": i - last_flip if last_flip is not None else None,
            "change_since_flip_pct": _round(_pct(close - closes[last_flip], closes[last_flip])) if last_flip is not None else None,
            "intraday_break": bool(intraday_break),
            "benchmark_dir": bench_dir.get(days[i]),
            **{key: _round(value) for key, value in levels.items()},
            **{f"sma{length}": _round(values[i]) for length, values in smas.items()},
        }
        rows.append(row)
        if flip:
            flips.append({"date": days[i], "type": flip, "close": close, "st": _round(line), "prev_st": row["prev_st"],
                          "weekly_dir": weekly_direction,
                          "weekly_aligned": None if weekly_direction is None else weekly_direction == direction})
    return {"bars": rows, "flips": flips, "last_confirmed": confirmed_last,
            "weekly_last_ref": {"start": weeks[-2]["start"], "end": weeks[-2]["end"]} if len(weeks) > 1 else None}


def benchmark_ticker(ticker: str) -> str:
    upper = ticker.upper()
    if upper.endswith(".KQ"):
        return "^KQ11"
    if upper.endswith(".KS"):
        return "^KS11"
    return "^GSPC"
