"""회원 인증과 개인별 사용 이력 API."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import hmac
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

try:
    from ..db import get_db
except ImportError:
    from db import get_db

router = APIRouter(prefix="/api/auth", tags=["인증"])
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9가-힣_-]{2,30}$")
PASSWORD_ITERATIONS = 310_000
NAVER_AUTHORIZE_URL = "https://nid.naver.com/oauth2.0/authorize"
NAVER_TOKEN_URL = "https://nid.naver.com/oauth2.0/token"
NAVER_PROFILE_URL = "https://openapi.naver.com/v1/nid/me"
NAVER_OAUTH_STATE_TTL_MINUTES = 10
NAVER_HTTP_TIMEOUT_SECONDS = 8
NAVER_STATE_COOKIE = "naver_oauth_state"
NAVER_STATE_COOKIE_PATH = "/api/auth/naver/callback"
AUTH_TOKEN_STORAGE_KEY = "investment_analysis_auth_token"


class SignupRequest(BaseModel):
    email: str = Field(max_length=120)
    username: str = Field(max_length=30)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(max_length=120)
    password: str = Field(min_length=1, max_length=128)


class ActivityRequest(BaseModel):
    action: str = Field(default="view", pattern=r"^[a-z0-9_-]{1,40}$")
    view: str = Field(default="", max_length=80)
    detail: str = Field(default="", max_length=160)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, encoded_salt, encoded_digest = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(encoded_salt)
        expected = base64.b64decode(encoded_digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _public_user(user: dict) -> dict[str, str]:
    return {
        "id": str(user["_id"]),
        "email": user.get("profile_email") or user.get("email", ""),
        "username": user.get("display_name") or user["username"],
        "auth_provider": user.get("auth_provider", "password"),
    }


async def _ensure_indexes() -> None:
    db = get_db()
    await db.users.create_index("email", unique=True)
    await db.users.create_index("username", unique=True)
    await db.auth_sessions.create_index("token_hash", unique=True)
    await db.auth_sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.user_activity.create_index([("user_id", 1), ("created_at", -1)])
    await db.auth_oauth_states.create_index("state_hash", unique=True)
    await db.auth_oauth_states.create_index("expires_at", expireAfterSeconds=0)
    await db.users.create_index(
        [("auth_provider", 1), ("auth_provider_id", 1)],
        unique=True,
        partialFilterExpression={"auth_provider": {"$type": "string"}, "auth_provider_id": {"$type": "string"}},
    )


async def _issue_session(user_id) -> str:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await get_db().auth_sessions.insert_one({"user_id": user_id, "token_hash": token_hash, "created_at": _now(), "expires_at": datetime.fromtimestamp(_now().timestamp() + 60 * 60 * 24 * 14, timezone.utc)})
    return raw_token


def _naver_config() -> tuple[str, str, str]:
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("NAVER_REDIRECT_URI", "").strip()
    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(status_code=503, detail="네이버 로그인 환경설정이 완료되지 않았습니다.")
    parsed = urllib.parse.urlsplit(redirect_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise HTTPException(status_code=503, detail="NAVER_REDIRECT_URI 설정이 올바르지 않습니다.")
    return client_id, client_secret, redirect_uri


def _request_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=NAVER_HTTP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=502, detail="네이버 인증 서버 응답을 확인하지 못했습니다.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="네이버 인증 서버 응답 형식이 올바르지 않습니다.")
    return payload


async def _exchange_naver_code(code: str, state: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
        "state": state,
    }).encode()
    request = urllib.request.Request(
        NAVER_TOKEN_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    payload = await asyncio.to_thread(_request_json, request)
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(status_code=502, detail="네이버 접근 토큰을 발급받지 못했습니다.")
    return access_token


async def _fetch_naver_profile(access_token: str) -> dict:
    request = urllib.request.Request(NAVER_PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"})
    payload = await asyncio.to_thread(_request_json, request)
    profile = payload.get("response")
    if payload.get("resultcode") != "00" or not isinstance(profile, dict) or not str(profile.get("id", "")).strip():
        raise HTTPException(status_code=502, detail="네이버 회원 프로필을 확인하지 못했습니다.")
    return profile


def _oauth_result_page(*, token: str | None = None, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    if token:
        nonce = secrets.token_urlsafe(18)
        script = (
            f"localStorage.setItem({json.dumps(AUTH_TOKEN_STORAGE_KEY)}, {json.dumps(token)});"
            "window.location.replace('/');"
        )
        content = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>네이버 로그인 완료</title></head><body><p>로그인을 완료했습니다. 잠시만 기다려 주세요.</p><script nonce="{nonce}">{script}</script></body></html>"""
        headers = {"Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'none'; base-uri 'none'; frame-ancestors 'none'"}
    else:
        message = html.escape(error or "네이버 로그인을 완료하지 못했습니다.")
        content = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>네이버 로그인 실패</title></head><body><main><h1>로그인 실패</h1><p>{message}</p><a href="/">홈으로 돌아가기</a></main></body></html>"""
        headers = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"}
    headers.update({"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})
    return HTMLResponse(content=content, status_code=status_code, headers=headers)


def _clear_naver_state_cookie(response: HTMLResponse, *, secure: bool) -> HTMLResponse:
    response.delete_cookie(
        NAVER_STATE_COOKIE, path=NAVER_STATE_COOKIE_PATH,
        secure=secure, httponly=True, samesite="lax",
    )
    return response


async def _naver_user(profile: dict) -> dict:
    provider_id = str(profile["id"]).strip()
    existing = await get_db().users.find_one({"auth_provider": "naver", "auth_provider_id": provider_id})
    display_name = str(profile.get("name") or profile.get("nickname") or "네이버 사용자").strip()[:30]
    profile_email = str(profile.get("email") or "").strip().lower()
    if profile_email and not EMAIL_PATTERN.fullmatch(profile_email):
        profile_email = ""
    if existing:
        await get_db().users.update_one(
            {"_id": existing["_id"]},
            {"$set": {"display_name": display_name, "profile_email": profile_email, "last_login_at": _now()}},
        )
        return {**existing, "display_name": display_name, "profile_email": profile_email}

    digest = hashlib.sha256(provider_id.encode()).hexdigest()
    # 네이버 프로필 이메일은 표시용이며, 공급자 고유 ID만 인증 식별자로 사용한다.
    # 내부 고유 이메일은 별도 namespace로 두어 기존 이메일 계정과 자동 병합하지 않는다.
    internal_email = f"naver_{digest[:20]}@users.invalid"
    document = {
        "email": internal_email,
        "profile_email": profile_email,
        "username": f"naver_{digest[:20]}",
        "display_name": display_name,
        "auth_provider": "naver",
        "auth_provider_id": provider_id,
        "created_at": _now(),
        "last_login_at": _now(),
    }
    try:
        result = await get_db().users.insert_one(document)
    except DuplicateKeyError:
        user = await get_db().users.find_one({"auth_provider": "naver", "auth_provider_id": provider_id})
        if not user:
            raise HTTPException(status_code=409, detail="네이버 계정을 등록하지 못했습니다. 다시 시도해 주세요.")
        return user
    return {**document, "_id": result.inserted_id}


async def current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    token_hash = hashlib.sha256(authorization.removeprefix("Bearer ").strip().encode()).hexdigest()
    session = await get_db().auth_sessions.find_one({"token_hash": token_hash, "expires_at": {"$gt": _now()}})
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인 세션이 만료되었습니다.")
    user = await get_db().users.find_one({"_id": session["user_id"]})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자를 찾을 수 없습니다.")
    return user


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(request: SignupRequest) -> dict:
    if os.getenv("LEGACY_EMAIL_SIGNUP_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=410, detail="신규 회원가입은 네이버 로그인을 이용해 주세요.")
    email = request.email.strip().lower()
    username = request.username.strip()
    if not EMAIL_PATTERN.fullmatch(email):
        raise HTTPException(status_code=422, detail="올바른 이메일 주소를 입력하세요.")
    if not USERNAME_PATTERN.fullmatch(username):
        raise HTTPException(status_code=422, detail="이름은 2~30자의 한글·영문·숫자·_-만 사용할 수 있습니다.")
    await _ensure_indexes()
    try:
        result = await get_db().users.insert_one({"email": email, "username": username, "password_hash": _password_hash(request.password), "created_at": _now()})
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="이미 사용 중인 이메일 또는 이름입니다.")
    user = {"_id": result.inserted_id, "email": email, "username": username}
    token = await _issue_session(user["_id"])
    return {"token": token, "user": _public_user(user)}


@router.post("/login")
async def login(request: LoginRequest) -> dict:
    email = request.email.strip().lower()
    user = await get_db().users.find_one({"email": email})
    if not user or not _password_matches(request.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    token = await _issue_session(user["_id"])
    return {"token": token, "user": _public_user(user)}


@router.get("/naver/start")
async def naver_login_start() -> RedirectResponse:
    client_id, _client_secret, redirect_uri = _naver_config()
    await _ensure_indexes()
    state = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    now = _now()
    await get_db().auth_oauth_states.insert_one({
        "provider": "naver",
        "state_hash": state_hash,
        "created_at": now,
        "expires_at": now + timedelta(minutes=NAVER_OAUTH_STATE_TTL_MINUTES),
    })
    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state,
    })
    response = RedirectResponse(
        f"{NAVER_AUTHORIZE_URL}?{query}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    response.set_cookie(
        NAVER_STATE_COOKIE, state, max_age=NAVER_OAUTH_STATE_TTL_MINUTES * 60,
        path=NAVER_STATE_COOKIE_PATH, secure=urllib.parse.urlsplit(redirect_uri).scheme == "https",
        httponly=True, samesite="lax",
    )
    return response


@router.get("/naver/callback")
async def naver_login_callback(
    request: Request,
    state: str = Query(default="", max_length=200),
    code: str = Query(default="", max_length=1000),
    error: str = Query(default="", max_length=120),
    error_description: str = Query(default="", max_length=300),
) -> HTMLResponse:
    client_id, client_secret, redirect_uri = _naver_config()
    secure_cookie = urllib.parse.urlsplit(redirect_uri).scheme == "https"

    def result(**kwargs) -> HTMLResponse:
        return _clear_naver_state_cookie(_oauth_result_page(**kwargs), secure=secure_cookie)

    browser_state = request.cookies.get(NAVER_STATE_COOKIE, "")
    if not state or not browser_state or not hmac.compare_digest(state, browser_state):
        return result(error="로그인 요청이 이 브라우저에서 시작되지 않았습니다.", status_code=400)
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    oauth_state = await get_db().auth_oauth_states.find_one_and_delete({
        "provider": "naver", "state_hash": state_hash, "expires_at": {"$gt": _now()},
    })
    if not oauth_state:
        return result(error="로그인 요청이 만료되었거나 이미 사용되었습니다.", status_code=400)
    if error:
        return result(error=error_description or "네이버 로그인이 취소되었습니다.", status_code=400)
    if not code:
        return result(error="네이버 인증 코드가 없습니다.", status_code=400)
    try:
        access_token = await _exchange_naver_code(code, state, client_id, client_secret, redirect_uri)
        profile = await _fetch_naver_profile(access_token)
        user = await _naver_user(profile)
        token = await _issue_session(user["_id"])
    except HTTPException as exc:
        return result(error=str(exc.detail), status_code=exc.status_code)
    return result(token=token)


@router.get("/me")
async def me(user: dict = Depends(current_user)) -> dict:
    return {"user": _public_user(user)}


class DeleteAccountRequest(BaseModel):
    confirm: str = Field(max_length=10)


@router.delete("/me")
async def delete_account(request: DeleteAccountRequest, user: dict = Depends(current_user)) -> dict:
    """회원 탈퇴: 계정(네이버 식별자·이름·이메일)·로그인 세션·이용 내역·관심종목·리서치 노트를 즉시 삭제한다.

    개인정보처리방침(/privacy.html)과 같은 범위다. 판단 기록·학습 데이터셋·레버리지 DCA 등은
    운영 지침상 자동 삭제하지 않으며, 계정이 지워지면 더 이상 회원과 연결되지 않는다.
    """
    if request.confirm != "탈퇴":
        raise HTTPException(status_code=422, detail="확인 문구로 '탈퇴'를 입력하세요.")
    db = get_db()
    sessions = await db.auth_sessions.delete_many({"user_id": user["_id"]})
    activity = await db.user_activity.delete_many({"user_id": user["_id"]})
    # 관심종목·리서치 노트는 회원이 쓴 개인 메모라 계정과 함께 지운다.
    await db.user_watchlist.delete_many({"user_id": user["_id"]})
    await db.research_notes.delete_many({"user_id": user["_id"]})
    await db.users.delete_one({"_id": user["_id"]})
    return {"deleted": True, "sessions_deleted": sessions.deleted_count, "activity_deleted": activity.deleted_count}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(authorization: str | None = Header(default=None)) -> None:
    if authorization and authorization.startswith("Bearer "):
        token_hash = hashlib.sha256(authorization.removeprefix("Bearer ").strip().encode()).hexdigest()
        await get_db().auth_sessions.delete_one({"token_hash": token_hash})


@router.post("/activity", status_code=status.HTTP_201_CREATED)
async def record_activity(request: ActivityRequest, user: dict = Depends(current_user)) -> dict:
    await get_db().user_activity.insert_one({"user_id": user["_id"], "action": request.action, "view": request.view, "detail": request.detail, "created_at": _now()})
    return {"ok": True}


@router.get("/activity")
async def activity(limit: int = 30, user: dict = Depends(current_user)) -> dict:
    limit = max(1, min(limit, 100))
    rows = await get_db().user_activity.find({"user_id": user["_id"]}, {"_id": 0, "user_id": 0}).sort("created_at", -1).to_list(limit)
    return {"items": [{**row, "created_at": row["created_at"].isoformat()} for row in rows]}
