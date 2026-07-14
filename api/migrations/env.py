import asyncio
from logging.config import fileConfig

from alembic import context
from alembic.runtime.environment import NameFilterParentNames, NameFilterType
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from antang_api.database import Base
from antang_api.settings import settings

# 导入全部业务模型，让 Base.metadata 包含这些表。
from antang_api import models as _models  # noqa: F401

config = context.config
config.set_main_option(
    "sqlalchemy.url",
    settings.database_url.replace("%", "%%"),
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
# 这些表由 LangGraph 的 checkpointer.setup() 独立管理。
LANGGRAPH_TABLES = {
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
}


def include_name(
    name: str | None,
    type_: NameFilterType,
    parent_names: NameFilterParentNames,
) -> bool:
    """让 Alembic 忽略由 LangGraph 管理的数据库表。"""

    if type_ == "table" and name in LANGGRAPH_TABLES:
        return False

    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_sync_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(run_sync_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
