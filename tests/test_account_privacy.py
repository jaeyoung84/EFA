"""회원 탈퇴·개인정보처리방침·네이버 공식 로그인 버튼(네이버 로그인 검수 대비)."""
from pathlib import Path

import pytest

from app.backend.db import get_db

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_withdrawal_deletes_account_sessions_and_activity_only(client, make_user):
    headers = await make_user("leaving@example.com")
    other = await make_user("staying@example.com")
    assert (await client.post("/api/auth/activity", headers=headers, json={"action": "view", "view": "x", "detail": ""})).status_code == 201
    assert (await client.post("/api/auth/activity", headers=other, json={"action": "view", "view": "y", "detail": ""})).status_code == 201
    user = await get_db().users.find_one({"email": "leaving@example.com"})

    wrong = await client.request("DELETE", "/api/auth/me", headers=headers, json={"confirm": "yes"})
    assert wrong.status_code == 422 and await get_db().users.find_one({"_id": user["_id"]})
    deleted = await client.request("DELETE", "/api/auth/me", headers=headers, json={"confirm": "탈퇴"})
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True

    assert await get_db().users.find_one({"_id": user["_id"]}) is None
    assert await get_db().auth_sessions.count_documents({"user_id": user["_id"]}) == 0
    assert await get_db().user_activity.count_documents({"user_id": user["_id"]}) == 0
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    assert (await client.get("/api/auth/me", headers=other)).status_code == 200  # 다른 회원은 그대로
    assert (await client.get("/api/auth/activity", headers=other)).json()["items"]
    assert (await client.request("DELETE", "/api/auth/me", json={"confirm": "탈퇴"})).status_code == 401


@pytest.mark.asyncio
async def test_privacy_page_is_public_and_covers_required_sections(client):
    response = await client.get("/privacy.html")
    assert response.status_code == 200
    page = response.text
    for phrase in ("개인정보처리방침", "네이버 회원 고유 식별자", "이용 목적", "보관 기간", "제3자 제공",
                   "처리 위탁", "Cloudflare", "회원 탈퇴", "연결된 서비스 관리", "시행일"):
        assert phrase in page, phrase


def test_login_uses_the_official_naver_button_and_links_the_policy():
    auth_js = (ROOT / "app/frontend/js/auth.js").read_text()
    asset = ROOT / "app/frontend/assets/naver/NAVER_login_Dark_KR_green_wide_H56.png"
    assert asset.is_file() and asset.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert 'src="/assets/naver/NAVER_login_Dark_KR_green_wide_H56.png" alt="네이버 로그인"' in auth_js
    assert 'href="/privacy.html"' in auth_js and "auth-naver-mark" not in auth_js
    assert 'href="/privacy.html"' in (ROOT / "app/frontend/index.html").read_text()


@pytest.mark.asyncio
async def test_email_login_and_signup_are_closed_by_default(client, make_user, monkeypatch):
    await make_user("closed-login@example.com")  # 테스트 환경에서만 켠 가입으로 계정을 만든다
    monkeypatch.setenv("LEGACY_EMAIL_SIGNUP_ENABLED", "false")
    login = await client.post("/api/auth/login", json={"email": "closed-login@example.com", "password": "testpassword123"})
    assert login.status_code == 410 and "네이버" in login.json()["detail"]
    signup = await client.post("/api/auth/signup", json={"email": "new@example.com", "username": "new1", "password": "testpassword123"})
    assert signup.status_code == 410


def test_login_window_has_no_email_password_form():
    auth_js = (ROOT / "app/frontend/js/auth.js").read_text()
    assert "auth-password" not in auth_js and "/api/auth/login" not in auth_js


def test_logout_and_withdrawal_redraw_the_page():
    auth_js = (ROOT / "app/frontend/js/auth.js").read_text()
    logout = auth_js[auth_js.index("#auth-logout').addEventListener"):]
    assert "window.location.reload()" in logout[:400]  # 이름·개인 데이터가 화면에 남지 않게
    withdraw = auth_js[auth_js.index("탈퇴가 완료됐습니다") - 200:auth_js.index("탈퇴가 완료됐습니다")]
    assert "window.location.reload()" in withdraw
