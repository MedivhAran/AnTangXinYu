from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from antang_api.settings import settings


class Base(DeclarativeBase):
    """所有SQL表模型的基类，继承它可以让SQLAlchemy知道这是一个表模型类"""

    pass


engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def lock_user_conversation(
    session: AsyncSession,
    user_id: UUID,
    *,
    wait: bool = True,
) -> bool:
    """让聊天和主动关怀按同一个用户串行提交数据库变更。"""

    statement = text(
        "SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"
        if wait
        else "SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))"
    )
    acquired = await session.scalar(
        statement,
        {"lock_key": f"antang:chat-care:{user_id}"},
    )
    return True if wait else acquired is True
