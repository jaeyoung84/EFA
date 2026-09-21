"""실거래 연동을 위한 안전한 어댑터 경계.

현재 배포본에는 주문을 전송하는 구현체가 없다. 실거래 지원은 증권사별 어댑터가
추가되고 별도 보안 검토를 통과하기 전까지 항상 차단된다.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Protocol
import uuid


@dataclass(frozen=True)
class BrokerOrderRequest:
    ticker: str
    side: str
    quantity: float
    order_type: str = "market"


@dataclass(frozen=True)
class BrokerOrderResult:
    accepted: bool
    broker_order_id: str | None
    message: str


class BrokerAdapter(Protocol):
    name: str

    def health(self) -> dict: ...

    def submit_order(self, order: BrokerOrderRequest) -> BrokerOrderResult: ...


class DisabledBrokerAdapter:
    """명시적으로 주문을 거부하는 기본 어댑터. 자격증명도 읽거나 노출하지 않는다."""
    name = "disabled"

    def health(self) -> dict:
        return {"adapter": self.name, "ready": False, "message": "실거래 어댑터가 설치되지 않았습니다."}

    def submit_order(self, order: BrokerOrderRequest) -> BrokerOrderResult:
        return BrokerOrderResult(False, None, "실거래 어댑터가 설치되지 않아 주문을 전송하지 않았습니다.")


class SandboxBrokerAdapter:
    """외부 네트워크 없이 계약 검증만 하는 샌드박스 어댑터.

    이 구현은 브로커 API나 계좌에 연결하지 않는다. 주문 형식, 호출 순서, 장애 처리
    테스트를 위한 응답만 만들며 ``live_capable``은 의도적으로 False다.
    """
    name = "local-sandbox"

    def health(self) -> dict:
        return {
            "adapter": self.name,
            "ready": True,
            "live_capable": False,
            "message": "로컬 샌드박스 어댑터입니다. 외부 주문은 전송하지 않습니다.",
        }

    def submit_order(self, order: BrokerOrderRequest) -> BrokerOrderResult:
        if order.side not in {"buy", "sell"} or order.quantity <= 0:
            return BrokerOrderResult(False, None, "샌드박스 주문 형식이 올바르지 않습니다.")
        return BrokerOrderResult(True, f"sandbox-{uuid.uuid4().hex}", "로컬 샌드박스에서만 주문을 수락했습니다.")


def run_local_sandbox_contract_check() -> dict:
    """Exercise only an in-process fake adapter; it cannot contact a broker or account.

    The suite intentionally creates its own SandboxBrokerAdapter instead of reading the
    deployment's selected adapter.  This keeps the check useful while still proving no
    environment combination can transform it into a live-order submission.
    """
    adapter = SandboxBrokerAdapter()
    health = adapter.health()
    accepted = adapter.submit_order(BrokerOrderRequest(ticker="005930.KS", side="buy", quantity=1))
    rejected = adapter.submit_order(BrokerOrderRequest(ticker="005930.KS", side="invalid", quantity=1))
    checks = [
        {"name": "로컬 어댑터 준비", "passed": health.get("ready") is True},
        {"name": "실거래 기능 미탑재", "passed": health.get("live_capable") is False},
        {"name": "유효 주문 형식 수락", "passed": accepted.accepted and str(accepted.broker_order_id or "").startswith("sandbox-")},
        {"name": "잘못된 주문 형식 거절", "passed": not rejected.accepted and rejected.broker_order_id is None},
    ]
    return {
        "suite_version": "local-sandbox-contract-v1", "adapter": adapter.name, "checks": checks,
        "passed": all(item["passed"] for item in checks), "external_network_used": False,
        "live_order_submission_available": False,
        "disclaimer": "이 검증은 프로세스 내부 가짜 어댑터만 호출합니다. 증권사 API·계좌·자격증명·실거래 주문을 사용하거나 만들지 않습니다.",
    }


def get_broker_adapter() -> BrokerAdapter:
    """명시적으로 켠 경우에만 비연결 샌드박스를 제공한다.

    어떤 환경 변수 조합도 실브로커 구현을 선택할 수 없게 두어, 새 어댑터 도입 시
    이 함수를 별도 보안 검토 대상으로 유지한다.
    """
    if os.getenv("BROKER_ADAPTER", "").strip().lower() == "sandbox" and os.getenv("BROKER_SANDBOX_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        return SandboxBrokerAdapter()
    return DisabledBrokerAdapter()
