import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

# Settings 在模块导入时读取环境变量。测试只验证本地封装，不会向 Tavily 发请求。
os.environ.setdefault("TAVILY_API_KEY", "test-only-tavily-key")

from antang_api.database import engine, get_session, session_factory  # noqa: E402
from antang_api.main import app  # noqa: E402
from antang_api.models import (  # noqa: E402
    PersonalProfile,
    ProactiveCareSettings,
    User,
)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """提供隔离的数据库会话，并在测试结束后回滚全部改动。"""

    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """让 API 测试使用隔离的数据库会话。"""

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def committed_user_id() -> AsyncIterator[UUID]:
    """为真实多连接并发测试创建一位其他连接可见的用户。"""

    suffix = uuid4().hex[:16]
    async with session_factory() as session:
        user = User(
            username=f"concurrent_{suffix}",
            username_normalized=f"concurrent_{suffix}",
            password_hash="test-only-password-hash",
        )
        session.add(user)
        await session.flush()
        session.add_all(
            [
                PersonalProfile(user_id=user.id),
                ProactiveCareSettings(user_id=user.id),
            ]
        )
        await session.commit()
        user_id = user.id

    try:
        yield user_id
    finally:
        async with session_factory() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
