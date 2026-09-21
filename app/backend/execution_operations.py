"""실거래 전 운영 승인 기록. 이 모듈은 주문 권한을 부여하지 않는다."""
from __future__ import annotations

import os
import uuid

from pymongo import ReturnDocument

from .db import get_db


def configured_operator_emails() -> set[str]:
    return {email.strip().lower() for email in os.getenv("EXECUTION_OPERATOR_EMAILS", "").split(",") if email.strip()}


def is_execution_operator(user: dict) -> bool:
    return user.get("email", "").lower() in configured_operator_emails()


async def create_readiness_request(user: dict, purpose: str) -> dict:
    """두 명의 별도 운영자 검토가 필요한 준비 요청을 만든다.

    purpose는 검토 대상 표식일 뿐 실행 모드나 브로커 권한을 변경하지 않는다.
    """
    request = {
        "request_id": uuid.uuid4().hex, "purpose": purpose,
        "created_by": user["_id"], "created_by_email": user["email"],
        "approvals": [], "required_approvals": 2, "status": "pending",
    }
    await get_db().execution_readiness_requests.insert_one(request)
    return request


async def approve_readiness_request(request_id: str, user: dict) -> dict | None:
    """요청 작성자를 제외한 서로 다른 운영자만 승인할 수 있다."""
    request = await get_db().execution_readiness_requests.find_one({"request_id": request_id})
    if not request or request["status"] != "pending" or request["created_by"] == user["_id"]:
        return None
    identity = str(user["_id"])
    if identity in request.get("approvals", []):
        return None
    approvals = [*request.get("approvals", []), identity]
    status = "approved_for_review" if len(approvals) >= request["required_approvals"] else "pending"
    result = await get_db().execution_readiness_requests.find_one_and_update(
        {"_id": request["_id"], "status": "pending", "approvals": request.get("approvals", [])},
        {"$set": {"approvals": approvals, "status": status}},
        return_document=ReturnDocument.AFTER,
    )
    return result


def serialize_readiness_request(request: dict) -> dict:
    return {
        "request_id": request["request_id"], "purpose": request["purpose"],
        "approval_count": len(request.get("approvals", [])), "required_approvals": request["required_approvals"],
        "status": request["status"],
        "creates_live_authority": False,
    }
