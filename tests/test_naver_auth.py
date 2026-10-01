"""네이버 OAuth 가입·로그인 흐름의 state·계정·세션 경계를 검증한다."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

from app.backend.db import get_db
from app.backend.routers import auth


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("NAVER_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("NAVER_REDIRECT_URI", "http://test/api/auth/naver/callback")


async def _start(client, monkeypatch) -> str:
    _configure(monkeypatch)
    response = await client.get("/api/auth/naver/start", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    parsed = urlsplit(location)
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == auth.NAVER_AUTHORIZE_URL
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["test-client-id"]
    assert query["redirect_uri"] == ["http://test/api/auth/naver/callback"]
    state = query["state"][0]
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    assert client.cookies.get(auth.NAVER_STATE_COOKIE) == state
    return state


def _local_session_token(html: str) -> str:
    match = re.search(r'localStorage\.setItem\([^,]+, ("[^"]+")\)', html)
    assert match, html
    return json.loads(match.group(1))


async def test_legacy_email_signup_is_disabled_by_default(client, monkeypatch):
    monkeypatch.setenv("LEGACY_EMAIL_SIGNUP_ENABLED", "false")
    response = await client.post("/api/auth/signup", json={
        "email": "new@example.com", "username": "new-user", "password": "password123",
    })
    assert response.status_code == 410
    assert "네이버" in response.json()["detail"]


async def test_naver_start_requires_server_configuration(client, monkeypatch):
    for key in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "NAVER_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)
    response = await client.get("/api/auth/naver/start", follow_redirects=False)
    assert response.status_code == 503
    assert "환경설정" in response.json()["detail"]


async def test_naver_callback_creates_user_and_one_time_local_session(client, monkeypatch):
    state = await _start(client, monkeypatch)

    async def exchange(code, received_state, client_id, client_secret, redirect_uri):
        assert (code, received_state) == ("authorization-code", state)
        assert (client_id, client_secret) == ("test-client-id", "test-client-secret")
        assert redirect_uri == "http://test/api/auth/naver/callback"
        return "provider-access-token"

    async def profile(access_token):
        assert access_token == "provider-access-token"
        return {"id": "naver-app-user-1", "name": "네이버 사용자", "email": "naver@example.com"}

    monkeypatch.setattr(auth, "_exchange_naver_code", exchange)
    monkeypatch.setattr(auth, "_fetch_naver_profile", profile)
    response = await client.get(f"/api/auth/naver/callback?code=authorization-code&state={state}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "provider-access-token" not in response.text
    token = _local_session_token(response.text)

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"] == {
        "id": me.json()["user"]["id"], "email": "naver@example.com",
        "username": "네이버 사용자", "auth_provider": "naver",
    }
    saved = await get_db().users.find_one({"auth_provider": "naver"})
    assert saved["auth_provider_id"] == "naver-app-user-1"
    assert "access_token" not in saved and "refresh_token" not in saved

    replay = await client.get(f"/api/auth/naver/callback?code=authorization-code&state={state}")
    assert replay.status_code == 400
    assert "이 브라우저" in replay.text


async def test_naver_cancel_consumes_state_and_escapes_error(client, monkeypatch):
    state = await _start(client, monkeypatch)
    response = await client.get(f"/api/auth/naver/callback?state={state}&error=access_denied&error_description=%3Cscript%3Ebad%3C/script%3E")
    assert response.status_code == 400
    assert "<script>bad</script>" not in response.text
    assert "&lt;script&gt;bad&lt;/script&gt;" in response.text
    assert await get_db().auth_oauth_states.count_documents({}) == 0


async def test_same_naver_id_reuses_user_even_if_profile_changes(client, monkeypatch):
    _configure(monkeypatch)
    profiles = [
        {"id": "stable-id", "nickname": "첫이름", "email": "first@example.com"},
        {"id": "stable-id", "nickname": "새이름", "email": "changed@example.com"},
    ]

    async def exchange(*_args): return "token"
    async def profile(_token): return profiles.pop(0)
    monkeypatch.setattr(auth, "_exchange_naver_code", exchange)
    monkeypatch.setattr(auth, "_fetch_naver_profile", profile)

    for _ in range(2):
        state = await _start(client, monkeypatch)
        response = await client.get(f"/api/auth/naver/callback?code=code&state={state}")
        assert response.status_code == 200
    assert await get_db().users.count_documents({"auth_provider": "naver"}) == 1
    saved = await get_db().users.find_one({"auth_provider": "naver"})
    assert saved["display_name"] == "새이름"
    assert saved["profile_email"] == "changed@example.com"
