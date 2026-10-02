"""Strategy board (EINT-017): the three items' picks side by side and sell signals for the user's own holdings.

Pure functions over an already validated publication and the user's FIFO lot book. Nothing here
places orders, records fills or imports the engine runtime; a sell signal is information for a
manual decision. The Pullback v4 exit check mirrors ``kqs_runtime.pullback_v4.holding_exit``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ITEMS = ("swing", "long_term", "pullback_v4")
ITEM_META = {
    "swing": {
        "label": "Bollinger-RSI", "title": "과매도 평균회귀",
        "summary": "볼린저 하단 이탈 + RSI(14) ≤ 30. 시총 상위·거래대금 50억 이상. 시장 조건 없음(시장 ETF의 200일선 대비 위치는 근거에 참고로 표시).",
        "exit": "종가가 볼린저 중간선 이상 또는 RSI ≥ 50",
        "order": "시가총액 순위 순",
    },
    "long_term": {
        "label": "RSI-OBV", "title": "RSI 회복·OBV 상승 장기",
        "summary": "RSI(14)가 30을 상향 돌파 + OBV가 9일 평균 위·당일 증가. 시장 조건 없음(시장 상태는 근거에 참고로 표시).",
        "exit": "RSI ≥ 50",
        "order": "시가총액 순위 순",
    },
    "pullback_v4": {
        "label": "Trend Pullback v4", "title": "상승추세 눌림목",
        "summary": "주간 상승추세·EMA5>EMA10에서 EMA10 눌림 후 회복, RSI 50~70·ADX>20·MACD≥0. 시장 ETF 50일선 위일 때만.",
        "exit": "손절 1.5×ATR · 15거래일 · 최고종가-2×ATR · 시장 50일선 이탈 시 다음 날 시가",
        "order": "사전등록 점수 순(EMA10 근접·ADX·RSI)",
    },
}
V4_ATR_MULTIPLE = 1.5
V4_TRAILING_ATR_MULTIPLE = 2.0
V4_HOLDING_LIMIT = 15
EXIT_REASON_LABELS = {
    "exit_signal": "청산 조건 충족", "stop": "손절선 이하", "holding_limit": "보유 15거래일 도달",
    "trailing": "트레일링 이탈", "market_regime": "시장 50일선 이탈",
}


def board_items(payload: dict) -> list[dict]:
    """Group selected entry signals by item, keeping the published per-item rank."""
    picks = {item: [] for item in ITEMS}
    for signal in payload.get("signals", []):
        if signal.get("action") != "entry" or signal.get("selection_outcome") != "selected":
            continue
        item = signal.get("signal_type")
        if item not in picks:
            continue
        picks[item].append({
            "rank": signal.get("selection_rank"), "signal_id": signal["signal_id"], "symbol": signal["symbol"],
            "name": signal.get("name") or signal["symbol"], "market": signal.get("market", ""),
            "reasons": [reason["evidence"] for reason in signal.get("reasons", [])],
            "reason_labels": [reason.get("label", "") for reason in signal.get("reasons", [])],
            "exit_reference": signal.get("exit_reference"), "selection_note": signal.get("selection_note"),
            "close": (signal.get("features") or {}).get("close"),
        })
    v2 = payload.get("schema_version") == "kqs-recommendations/v2"
    out = []
    for item in ITEMS:
        rows = sorted(picks[item], key=lambda row: (row["rank"] is None, row["rank"] or 0))
        out.append({"item": item, **ITEM_META[item], "picks": rows, "max_picks": 5,
                    "published": v2 or item != "pullback_v4"})
    return out


def v4_holding_exit(series: list[dict], regime_off: bool, entry_date: str, entry_price: float) -> dict:
    """Same rules and priority as the engine's holding_exit, on the published close/ATR series.

    holding_limit and market_regime are exact. stop/trailing use today's close because the next
    open that the backtest compares is unknown before the session.
    """
    if not series:
        return {"state": "unavailable", "reason": None}
    since = [point for point in series if point["date"] >= entry_date]
    holding_days = len(since) if series[0]["date"] < entry_date else None  # None: entry older than the series
    before = [point for point in series if point["date"] < entry_date]
    stop_price = entry_price - V4_ATR_MULTIPLE * before[-1]["atr"] if before else None
    highest_close = max([entry_price] + [point["close"] for point in since])
    last = series[-1]
    trailing_level = highest_close - V4_TRAILING_ATR_MULTIPLE * last["atr"]
    checks = [
        ("stop", stop_price is not None and last["close"] <= stop_price),
        ("holding_limit", holding_days is None or holding_days >= V4_HOLDING_LIMIT),
        ("trailing", last["close"] <= trailing_level),
        ("market_regime", bool(regime_off)),
    ]
    reason = next((name for name, hit in checks if hit), None)
    return {"state": "exit_signal" if reason else "hold", "reason": reason, "close": last["close"],
            "stop_price": round(stop_price, 2) if stop_price is not None else None,
            "trailing_level": round(trailing_level, 2), "holding_days": holding_days,
            "holding_limit": V4_HOLDING_LIMIT, "market_regime_off": bool(regime_off)}


def holding_signals(lot_book: dict, payload: dict | None, trade_day) -> list[dict]:
    """One row per open lot, judged by the item that recommended its buy."""
    monitor = {(row["symbol"], row["signal_type"]): row for row in (payload or {}).get("exit_monitor", [])}
    rows = []
    for symbol_key, lots in lot_book["lots"].items():
        for lot in lots:
            if lot["remaining"] <= 1e-9:
                continue
            buy = lot["buy"]
            snapshot = buy.get("recommendation_snapshot") or {}
            item = snapshot.get("signal_type")
            entry_date, entry_price = trade_day(buy), float(buy["fill_price"])
            row = {"symbol": buy["symbol"], "symbol_key": symbol_key, "name": buy.get("name", ""),
                   "item": item if item in ITEMS else None, "bought_on": entry_date, "fill_price": entry_price,
                   "remaining_quantity": round(lot["remaining"], 8), "buy_decision_id": str(buy["_id"])}
            if item not in ITEMS:
                row.update(state="no_item", detail="추천 아이템과 연결되지 않은 매수라 매도 신호를 계산하지 않습니다.")
            elif payload is None:
                row.update(state="unavailable", detail="검증된 엔진 게시본이 없어 매도 신호를 계산할 수 없습니다.")
            else:
                fact = monitor.get((symbol_key, item))
                if fact is None:
                    row.update(state="not_monitored",
                               detail="엔진 청산 모니터 대상이 아닙니다(추적 기간 밖이거나 v2 게시 전 추천).")
                elif fact["state"] == "no_current_bar":
                    row.update(state="unavailable", detail="기준일 시세가 없어 판단하지 않았습니다(거래정지 등).")
                elif item == "pullback_v4":
                    row.update(v4_holding_exit(fact["series"], fact["market_regime_off"], entry_date, entry_price))
                else:
                    row.update(state=fact["state"], reason="exit_signal" if fact["state"] == "exit_signal" else None,
                               close=fact.get("close"))
            if row.get("reason"):
                row["reason_label"] = EXIT_REASON_LABELS.get(row["reason"], row["reason"])
            rows.append(row)
    rows.sort(key=lambda row: (row["state"] != "exit_signal", row["symbol"], row["bought_on"]))
    return rows


# EINT-019: forward outcomes of the published picks, computed by the engine from KRX daily bars.
OUTCOME_SCHEMA = "kqs-recommendation-outcomes/v1"
OUTCOME_ROW_LIMIT = 300


def outcome_report_path() -> Path:
    configured = os.getenv("KQS_RECOMMENDATION_OUTCOMES", "").strip()
    if configured:
        return Path(configured)
    snapshot = os.getenv("KQS_SNAPSHOT_DB", "").strip()
    if snapshot:
        return Path(snapshot).parent / "recommendation-outcomes.json"
    return Path(__file__).resolve().parents[2] / "state/kqs/recommendation-outcomes.json"


def read_outcome_report(path: Path | None = None) -> dict:
    """The engine's outcome file for the website: per-item summaries and the newest rows.

    A missing or foreign file is reported as unavailable, never replaced by computed numbers.
    """
    path = path or outcome_report_path()
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"available": False, "detail": "추천 성과 파일이 아직 없습니다. 엔진의 다음 일일 실행 후 생성됩니다."}
    except (OSError, ValueError):
        return {"available": False, "detail": "추천 성과 파일을 읽을 수 없습니다."}
    if not isinstance(report, dict) or report.get("schema") != OUTCOME_SCHEMA:
        return {"available": False, "detail": "추천 성과 파일 형식이 다릅니다."}
    rows = sorted(report.get("rows", []), key=lambda row: (row["as_of"], -ITEMS.index(row["item"]), -row["rank"]),
                  reverse=True)
    return {
        "available": True, "through": report["through"], "horizons": report["horizons"],
        "min_sample": report["min_sample"], "source_count": report["source_count"],
        "rows_sha256": report["rows_sha256"],
        "items": [{"item": item, "label": ITEM_META[item]["label"], "title": ITEM_META[item]["title"],
                   **report["summary"].get(item, {"pick_count": 0, "session_count": 0, "horizons": {}})}
                  for item in ITEMS],
        "rows": rows[:OUTCOME_ROW_LIMIT], "row_count": len(rows),
        "disclaimer": ("추천 다음 거래일 시가에 샀다고 가정한 사후 관측치입니다. 수수료·세금·슬리피지와 각 아이템의 "
                       "청산 규칙은 반영하지 않았습니다. 아이템별 표본이 기준 미만이면 성과 판단을 보류합니다. "
                       "미래 수익을 보장하지 않으며 주문·자동 모의체결은 없습니다."),
    }
