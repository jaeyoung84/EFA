"""주문 권한과 킬스위치의 중앙 안전 정책.

환경변수의 민감 값 자체는 이 모듈 밖으로 반환하지 않는다. 현재는 paper 주문만
지원하며 live 조건이 모두 충족돼도 어댑터가 disabled면 전송할 수 없다.
"""
from __future__ import annotations

import os

from .broker_adapter import get_broker_adapter
from .db import get_db


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def paper_order_promotion_approval_required() -> bool:
    """모의 주문 제안에 운영자 승인 전략만 허용할지 여부(기본은 기존 호환을 위해 해제)."""
    return _flag("PAPER_ORDER_REQUIRE_PROMOTION_APPROVAL", False)


def paper_order_promotion_alerts_block() -> bool:
    """승인 전략의 활성 재검토 경고가 있을 때 모의 주문을 차단할지 여부."""
    return _flag("PAPER_ORDER_BLOCK_PROMOTION_ALERTS", True)


def execution_environment_status() -> dict:
    adapter = get_broker_adapter()
    mode = os.getenv("EXECUTION_MODE", "paper").strip().lower()
    global_kill = _flag("EXECUTION_GLOBAL_KILL_SWITCH")
    live_enabled = _flag("LIVE_TRADING_ENABLED")
    live_kill = _flag("LIVE_TRADING_KILL_SWITCH", True)
    credentials_configured = bool(os.getenv("BROKER_API_KEY") and os.getenv("BROKER_API_SECRET") and os.getenv("BROKER_ACCOUNT_ID"))
    secret_source = os.getenv("BROKER_SECRET_SOURCE", "none").strip().lower()
    if secret_source not in {"none", "env", "external_reference"}:
        secret_source = "none"
    secret_references_configured = bool(os.getenv("BROKER_API_KEY_REF") and os.getenv("BROKER_API_SECRET_REF") and os.getenv("BROKER_ACCOUNT_ID_REF"))
    adapter_health = adapter.health()
    sandbox_ready = mode == "sandbox" and not global_kill and adapter_health["ready"] and adapter_health.get("live_capable") is False
    # 샌드박스 어댑터는 실제 주문 능력이 없으므로, live 가능 상태는 항상 별도 구현체의
    # 명시적 live_capable 표시까지 요구한다.
    live_permitted = mode == "live" and live_enabled and not live_kill and not global_kill and credentials_configured and adapter_health["ready"] and adapter_health.get("live_capable") is True
    return {
        "execution_mode": mode if mode in {"paper", "sandbox", "live"} else "paper",
        "global_kill_switch": global_kill,
        "paper_order_submission_available": not global_kill,
        "paper_order_requires_promotion_approval": paper_order_promotion_approval_required(),
        "paper_order_blocks_promotion_alerts": paper_order_promotion_alerts_block(),
        "live_trading_enabled": live_enabled,
        "live_trading_kill_switch": live_kill,
        "live_credentials_configured": credentials_configured,
        "broker_secret_source": secret_source,
        "broker_secret_references_configured": secret_references_configured,
        "live_adapter": adapter_health,
        "sandbox_order_submission_available": sandbox_ready,
        "live_order_submission_available": live_permitted,
        "secrets_exposed": False,
    }


async def user_execution_permission(user_id) -> dict:
    saved = await get_db().execution_permissions.find_one({"user_id": user_id}, {"_id": 0, "user_id": 0})
    return {"paper_trading_enabled": bool((saved or {}).get("paper_trading_enabled", True)), "live_trading_authorized": False}


async def paper_submission_allowed(user_id) -> tuple[bool, str | None]:
    environment = execution_environment_status()
    if environment["global_kill_switch"]:
        return False, "전역 킬스위치가 활성화되어 모든 주문 제출이 중지되었습니다."
    permission = await user_execution_permission(user_id)
    if not permission["paper_trading_enabled"]:
        return False, "이 계정의 모의 주문 권한이 비활성화되어 있습니다."
    return True, None
