from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    LoginSession,
    Message,
    MessageRole,
    MessageStatus,
    ProactiveCareSettings,
    PushDelivery,
    PushDeliveryStatus,
    PushInstallation,
    PushPermissionState,
    PushPlatform,
    User,
)
from antang_api.security import (
    ALGORITHM,
    AUDIENCE,
    ISSUER,
    create_access_token,
    hash_refresh_token,
)
from antang_api.settings import settings


def unique_username(prefix: str) -> str:
    """每个测试生成独立用户名，避免测试之间相互影响。"""

    return f"{prefix}_{uuid4().hex[:12]}"


async def test_register_login_and_duplicate_username(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """注册后可以登录，用户名的大小写形式不能重复注册。"""

    username = unique_username("auth")
    password = "correct-password"

    register_response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )

    assert register_response.status_code == 201
    assert register_response.json()["user"]["username"] == username

    registered_user = await db_session.scalar(
        select(User).where(User.username_normalized == username.casefold())
    )
    assert registered_user is not None
    assert await db_session.get(ProactiveCareSettings, registered_user.id) is not None

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


async def test_access_refresh_and_logout_lifecycle(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """访问凭证可读取用户；续期和退出会让旧长期凭证失效。"""

    username = unique_username("session")
    register_response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "correct-password"},
    )
    auth = register_response.json()

    original_session = await db_session.scalar(
        select(LoginSession).where(
            LoginSession.refresh_token_hash
            == hash_refresh_token(auth["refresh_token"])
        )
    )
    assert original_session is not None
    installation = PushInstallation(
        id=uuid4(),
        login_session_id=original_session.id,
        expo_push_token="ExponentPushToken[test-auth-session]",
        permission=PushPermissionState.GRANTED,
        platform=PushPlatform.ANDROID,
        app_version="1.0.0",
        last_seen_at=original_session.created_at,
    )
    db_session.add(installation)
    await db_session.flush()

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

    refreshed_session = await db_session.scalar(
        select(LoginSession).where(
            LoginSession.refresh_token_hash
            == hash_refresh_token(refreshed["refresh_token"])
        )
    )
    assert refreshed_session is not None
    await db_session.refresh(original_session)
    assert original_session.replaced_by_session_id == refreshed_session.id
    await db_session.refresh(installation)
    assert installation.login_session_id == refreshed_session.id
    assert installation.disabled_at is None

    old_access_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {auth['access_token']}"},
    )
    assert old_access_response.status_code == 401

    refreshed_access_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refreshed['access_token']}"},
    )
    assert refreshed_access_response.status_code == 200

    reused_token_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": auth["refresh_token"]},
    )

    assert reused_token_response.status_code == 401

    now = datetime.now(timezone.utc)
    message = Message(
        user_id=UUID(auth["user"]["id"]),
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="退出后不应发送",
        completed_at=now,
    )
    db_session.add(message)
    await db_session.flush()
    delivery = PushDelivery(
        message_id=message.id,
        installation_id=installation.id,
        installation_revision=installation.registration_revision,
        show_message_preview=True,
        status=PushDeliveryStatus.PENDING,
        next_attempt_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db_session.add(delivery)
    await db_session.commit()

    logout_response = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": auth["refresh_token"]},
    )

    assert logout_response.status_code == 204

    await db_session.refresh(installation)
    assert installation.disabled_at is not None
    assert installation.disabled_reason == "logout"
    assert installation.registration_revision == 2
    await db_session.refresh(delivery)
    assert delivery.status == PushDeliveryStatus.FAILED
    assert delivery.provider_error_code == "installation_changed"

    logged_out_access_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refreshed['access_token']}"},
    )
    assert logged_out_access_response.status_code == 401

    revoked_token_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refreshed["refresh_token"]},
    )

    assert revoked_token_response.status_code == 401


async def test_legacy_access_token_refreshes_without_forcing_a_new_login(
    client: AsyncClient,
) -> None:
    """升级前缺少 sid 的 access 失效，但原 refresh 仍可换取新凭证。"""

    response = await client.post(
        "/api/v1/auth/register",
        json={
            "username": unique_username("legacy_access"),
            "password": "correct-password",
        },
    )
    auth = response.json()
    now = datetime.now(timezone.utc)
    legacy_access = jwt.encode(
        {
            "sub": auth["user"]["id"],
            "type": "access",
            "jti": str(uuid4()),
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=15),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm=ALGORITHM,
    )

    me_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {legacy_access}"},
    )
    refresh_response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": auth["refresh_token"]},
    )

    assert me_response.status_code == 401
    assert refresh_response.status_code == 200
    refreshed_access = refresh_response.json()["access_token"]
    assert (
        await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {refreshed_access}"},
        )
    ).status_code == 200


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


async def test_access_token_session_must_belong_to_the_same_user(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """不能把一个用户 ID 和另一台登录会话拼成有效凭证。"""

    first = await client.post(
        "/api/v1/auth/register",
        json={
            "username": unique_username("token_owner_a"),
            "password": "correct-password",
        },
    )
    second = await client.post(
        "/api/v1/auth/register",
        json={
            "username": unique_username("token_owner_b"),
            "password": "correct-password",
        },
    )
    first_user_id = UUID(first.json()["user"]["id"])
    second_session = await db_session.scalar(
        select(LoginSession).where(
            LoginSession.refresh_token_hash
            == hash_refresh_token(second.json()["refresh_token"])
        )
    )
    assert second_session is not None

    forged = create_access_token(first_user_id, second_session.id)
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert response.status_code == 401
