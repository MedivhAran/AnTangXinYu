from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from antang_api.database import Base
from antang_api.models.chat import enum_values


class AgentToolCallStatus(str, Enum):
    """一次工具调用从开始到结束的状态。"""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentToolCall(Base):
    """保存 AgentRun 中实际执行过的一次工具调用。"""

    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        UniqueConstraint(
            "agent_run_id",
            "tool_call_id",
            name="uq_agent_tool_calls_run_tool_call_id",
        ),
        Index(
            "uq_agent_tool_calls_run_turn_call",
            "agent_run_id",
            "model_turn_index",
            "tool_call_index",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    agent_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        index=True,
    )

    # tool_call_id 来自模型；turn/call 记录并行调用在原始消息中的稳定顺序。
    tool_call_id: Mapped[str] = mapped_column(String(255))
    tool_name: Mapped[str] = mapped_column(String(128))
    model_turn_index: Mapped[int] = mapped_column(Integer)
    tool_call_index: Mapped[int] = mapped_column(Integer)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[str | list[str | dict[str, Any]] | None] = mapped_column(JSONB)

    status: Mapped[AgentToolCallStatus] = mapped_column(
        SqlEnum(
            AgentToolCallStatus,
            name="agent_tool_call_status",
            values_callable=enum_values,
        )
    )
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
