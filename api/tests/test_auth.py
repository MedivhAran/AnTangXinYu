from uuid import uuid4

from httpx import AsyncClient


def unique_username(prefix: str) -> str:
    """每个测试生成独立用户名，避免测试之间相互影响。"""

    return f"{prefix}_{uuid4().hex[:12]}"


async def test_register_login_and_duplicate_username(client: AsyncClient) -> None:
    """注册后可以登录，用户名的大小写形式不能重复注册。"""

    username = unique_username("auth")
    password = "correct-password"

    register_response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )

    assert register_response.status_code == 201
    assert register_response.json()["user"]["username"] == username

    duplicate_response = await client.post(
        "/api/v1/auth/register",
        json={"username": username.upper(), "password": password},
    )

    assert duplicate_response.status_code == 409

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"username": username.upper(), "password": password},
    )

    assert login_response.status_code == 200
    assert login_response.json()["user"]["username"] == username

    wrong_password_response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "wrong-password"},
    )

    assert wrong_password_response.status_code == 401


async def test_access_refresh_and_logout_lifecycle(client: AsyncClient) -> None:
    """访问凭证可读取用户；续期和退出会让旧长期凭证失效。"""

    username = unique_username("session")
    register_response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "correct-password"},
    )
    auth = register_response.json()

    me_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {auth['access_token']}"},
    )

    assert me_response.status_code == 200
    assert me_response.json()["username"] == username

    refresh_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": auth["refresh_token"]},
    )

    assert refresh_response.status_code == 200
    refreshed = refresh_response.json()
    assert refreshed["refresh_token"] != auth["refresh_token"]

    reused_token_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": auth["refresh_token"]},
    )

    assert reused_token_response.status_code == 401

    logout_response = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refreshed["refresh_token"]},
    )

    assert logout_response.status_code == 204

    revoked_token_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refreshed["refresh_token"]},
    )

    assert revoked_token_response.status_code == 401


async def test_registration_rejects_password_shorter_than_eight_characters(
    client: AsyncClient,
) -> None:
    """密码规则固定为最少八个字符。"""

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "username": unique_username("password"),
            "password": "1234567",
        },
    )

    assert response.status_code == 422
