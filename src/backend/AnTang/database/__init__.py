from loguru import logger
from sqlmodel import SQLModel, create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from AnTang.database.models.agent import AgentTable
from AnTang.database.models.antang_profile import AnTangProfileTable
from AnTang.database.models.history import HistoryTable
from AnTang.database.models.memory_history import MemoryHistoryTable
from AnTang.database.models.user import SystemUser
from AnTang.database.models.knowledge import KnowledgeTable
from AnTang.database.models.knowledge_file import KnowledgeFileTable
from AnTang.database.models.tool import ToolTable
from AnTang.database.models.dialog import DialogTable
from AnTang.database.models.mcp_server import MCPServerTable, MCPServerStdioTable
from AnTang.database.models.mcp_user_config import MCPUserConfigTable
from AnTang.database.models.user_role import UserRole
from AnTang.database.models.llm import LLMTable
from AnTang.database.models.message import MessageDownTable, MessageLikeTable
from AnTang.database.models.role import Role
from AnTang.database.models.usage_stats import UsageStats
from AnTang.database.models.agent_skill import AgentSkill
from AnTang.database.models.register_mcp import RegisterMcpServer
from AnTang.database.models.register_task import RegisterMcpTask
from AnTang.database.models.register_mcp_tool import RegisterMcpTool
from AnTang.settings import app_settings


# 创建数据库引擎
engine = create_engine(
    url=app_settings.mysql.get('endpoint'),
    pool_pre_ping=True, # 连接前检查其有效性
    pool_recycle=3600, # 每隔1小时进行重连一次
    connect_args={
        "charset": "utf8mb4",
        "use_unicode": True,
        "init_command": "SET SESSION time_zone = '+08:00'"
    }
)

async_engine = create_async_engine(
    url=app_settings.mysql.get('async_endpoint'),
    pool_pre_ping=True,  # 连接前检查其有效性
    pool_recycle=3600,  # 每隔1小时进行重连一次
    connect_args={
        "charset": "utf8mb4",
        "use_unicode": True,
        "init_command": "SET SESSION time_zone = '+08:00'"
    }
)


def ensure_mysql_database(endpoint: str=None) -> None:
    """
    Ensure MySQL database exists.
    This function is safe to call on every startup.
    """
    from urllib.parse import urlparse, urlunparse

    if not endpoint:
        endpoint = app_settings.mysql.get('endpoint')
    parsed = urlparse(endpoint)

    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError("MySQL endpoint must include database name")

    bootstrap_url = urlunparse((
        "mysql+pymysql",
        f"{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port or 3306}",
        "/",
        "",
        "",
        ""
    ))

    logger.info(f"Checking MySQL database `{database}`")

    engine = create_engine(
        bootstrap_url,
        isolation_level="AUTOCOMMIT",
        connect_args={
            "charset": "utf8mb4",
            "init_command": "SET SESSION time_zone = '+08:00'"
        }
    )

    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE DATABASE IF NOT EXISTS `{database}`
                    DEFAULT CHARACTER SET utf8mb4
                    COLLATE utf8mb4_unicode_ci
                    """
                )
            )
        logger.success(f"MySQL database `{database}` is ready")
    finally:
        engine.dispose()
