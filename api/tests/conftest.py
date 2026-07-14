import os
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# Settings 在模块导入时读取环境变量。测试只验证本地封装，不会向 Tavily 发请求。
os.environ.setdefault("TAVILY_API_KEY", "test-only-tavily-key")

from antang_api.database import engine, get_session  # noqa: E402
from antang_api.main import app  # noqa: E402


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
