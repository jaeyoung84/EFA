"""Alpaca Paper Trading API 연동 — 항상 페이퍼(모의) 엔드포인트만 호출한다.

이 모듈은 ``https://paper-api.alpaca.markets``만 호출하며, 실거래 엔드포인트로
바꾸는 설정값을 두지 않는다. Alpaca 자체의 모의 계좌·가짜 자금으로 체결되므로
실제 금전 거래가 아니다. API 키는 환경변수에서만 읽고 응답에 원문을 노출하지 않는다.

지정가를 먼저 제출하고 짧게 대기한 뒤 미체결이면 취소하고 시장가로 전환한다 —
자체 모의 체결 엔진과 달리 Alpaca는 실제 주문 매칭 엔진을 쓰므로 이 전환이
실제로 의미 있는 동작 차이를 만든다.
"""
from __future__ import annotations

import os
import time

import requests

ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
REQUEST_TIMEOUT_SECONDS = 10
LIMIT_WAIT_POLL_SECONDS = 2
LIMIT_WAIT_MAX_SECONDS = 10


class AlpacaPaperError(RuntimeError):
    pass


def alpaca_paper_configured() -> bool:
    return bool(os.getenv("ALPACA_PAPER_API_KEY") and os.getenv("ALPACA_PAPER_API_SECRET"))


def _headers() -> dict:
    return {
        "APCA-API-KEY-ID": os.getenv("ALPACA_PAPER_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.getenv("ALPACA_PAPER_API_SECRET", ""),
    }


def _request(method: str, path: str, *, json: dict | None = None) -> dict:
    if not alpaca_paper_configured():
        raise AlpacaPaperError("Alpaca 페이퍼 API 키가 설정되지 않았습니다.")
    response = requests.request(
        method, f"{ALPACA_PAPER_BASE_URL}{path}", headers=_headers(), json=json, timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise AlpacaPaperError(f"Alpaca 페이퍼 API 오류 {response.status_code}: {response.text[:300]}")
    return response.json() if response.text else {}


def submit_order(*, ticker: str, side: str, quantity: float, order_type: str, limit_price: float | None = None) -> dict:
    payload = {"symbol": ticker, "qty": str(round(quantity, 6)), "side": side, "type": order_type, "time_in_force": "day"}
    if order_type == "limit":
        payload["limit_price"] = str(round(limit_price, 2))
    return _request("POST", "/v2/orders", json=payload)


def get_order(order_id: str) -> dict:
    return _request("GET", f"/v2/orders/{order_id}")


def list_positions() -> list[dict]:
    """페이퍼 계좌의 현재 보유 포지션(읽기 전용)."""
    return _request("GET", "/v2/positions") or []


def cancel_order(order_id: str) -> None:
    _request("DELETE", f"/v2/orders/{order_id}")


def _order_to_fill(order: dict, *, order_type: str) -> dict:
    return {
        "status": "filled", "fill_price": float(order.get("filled_avg_price") or 0),
        "fill_quantity": float(order.get("filled_qty") or 0), "order_type": order_type,
        "alpaca_order_id": order.get("id"), "external_execution": True,
    }


def _wait_for_fill(order_id: str, *, order_type: str) -> dict | None:
    waited = 0.0
    while waited < LIMIT_WAIT_MAX_SECONDS:
        time.sleep(LIMIT_WAIT_POLL_SECONDS)
        waited += LIMIT_WAIT_POLL_SECONDS
        current = get_order(order_id)
        if current.get("status") == "filled":
            return _order_to_fill(current, order_type=order_type)
    return None


def execute_paper_order_sync(*, ticker: str, side: str, requested_quantity: float, limit_price: float) -> dict:
    """지정가 제출 → 대기 → 미체결 시 취소 후 시장가 전환(동기, 블로킹 — 스레드에서 호출)."""
    order = submit_order(ticker=ticker, side=side, quantity=requested_quantity, order_type="limit", limit_price=limit_price)
    order_id = order.get("id")
    if not order_id:
        raise AlpacaPaperError("Alpaca 주문 접수 응답에 주문 ID가 없습니다.")
    filled = _wait_for_fill(order_id, order_type="limit")
    if filled:
        return filled
    try:
        cancel_order(order_id)
    except AlpacaPaperError:
        pass  # 이미 체결·만료된 주문의 취소 실패는 무시하고 시장가로 넘어간다.
    market_order = submit_order(ticker=ticker, side=side, quantity=requested_quantity, order_type="market")
    market_order_id = market_order.get("id")
    if not market_order_id:
        raise AlpacaPaperError("Alpaca 시장가 전환 주문 접수 응답에 주문 ID가 없습니다.")
    filled = _wait_for_fill(market_order_id, order_type="market")
    if filled:
        return filled
    return {"status": "unfilled", "alpaca_order_id": market_order_id, "external_execution": True}
