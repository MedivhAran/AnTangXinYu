from collections.abc import AsyncIterator
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
