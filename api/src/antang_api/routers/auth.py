from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from antang_api.database import get_session
from antang_api.models import LoginSession, User
from antang_api.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
)
from antang_api.security import (
    access_token_expires_in,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from antang_api.settings import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )


def new_login_session(
    user_id: UUID,
    now: datetime,
) -> tuple[str, LoginSession]:
    refresh_token = create_refresh_token()

    login_session = LoginSession(
        user_id=user_id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        expires_at=now + timedelta(days=settings.refresh_token_days),
    )

    return refresh_token, login_session


def token_pair(user_id: UUID, refresh_token: str) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user_id),
        refresh_token=refresh_token,
        expires_in=access_token_expires_in(),
    )


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    if credentials is None:
        raise unauthorized()

    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidTokenError:
        raise unauthorized()

    user = await session.get(User, user_id)

    if user is None or not user.is_active:
        raise unauthorized()

    return user


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    request: RegisterRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthResponse:
    """注册用户，并为新用户创建第一次登录所需的凭证。"""

    now = datetime.now(timezone.utc)

    user = User(
        username=request.username,
        username_normalized=request.username.casefold(),
        password_hash=await run_in_threadpool(
            hash_password,
            request.password,
        ),
    )

    session.add(user)

    try:
        # 执行用户 INSERT，让 SQLAlchemy 生成 user.id。
        # 当前事务仍未提交，后面的登录记录会和用户一起提交。
        await session.flush()

        refresh_token, login_session = new_login_session(user.id, now)
        session.add(login_session)

        # 提交事务
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="注册信息与已有记录冲突",
        ) from error

    tokens = token_pair(user.id, refresh_token)

    return AuthResponse(
        user=UserResponse.model_validate(user),
        **tokens.model_dump(),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthResponse:
    user = await session.scalar(select(User).where(User.username_normalized == request.username.casefold()))

    if user is None or not user.is_active:
        raise unauthorized()

    password_matches = await run_in_threadpool(
        verify_password,
        request.password,
        user.password_hash,
    )

    if not password_matches:
        raise unauthorized()

    now = datetime.now(timezone.utc)
    refresh_token, login_session = new_login_session(user.id, now)

    session.add(login_session)
    await session.commit()

    tokens = token_pair(user.id, refresh_token)

    return AuthResponse(
        user=UserResponse.model_validate(user),
        **tokens.model_dump(),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    request: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenPair:
    """用refresh token换取新的access token和refresh token。旧的refresh token会被废弃。"""

    now = datetime.now(timezone.utc)
    token_hash = hash_refresh_token(request.refresh_token)

    old_session = await session.scalar(
        select(LoginSession).where(LoginSession.refresh_token_hash == token_hash).with_for_update()
    )

    if old_session is None or old_session.revoked_at is not None or old_session.expires_at <= now:
        raise unauthorized()

    user = await session.get(User, old_session.user_id)

    if user is None or not user.is_active:
        raise unauthorized()

    old_session.revoked_at = now
    refresh_token, login_session = new_login_session(user.id, now)

    session.add(login_session)
    await session.commit()

    return token_pair(user.id, refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: LogoutRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    token_hash = hash_refresh_token(request.refresh_token)

    login_session = await session.scalar(
        select(LoginSession).where(LoginSession.refresh_token_hash == token_hash).with_for_update()
    )

    if login_session is None or login_session.revoked_at is not None:
        raise unauthorized()

    login_session.revoked_at = datetime.now(timezone.utc)
    await session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(
    user: Annotated[User, Depends(get_current_user)],
) -> UserResponse:
    return UserResponse.model_validate(user)
