from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, Integer, String, Text, text
from sqlmodel import Field

from AnTang.database.models.base import SQLModelSerializable


class ReminderTable(SQLModelSerializable, table=True):
    """用户定时提醒（心跳系统的调度记录）。

    一条提醒一行。心跳循环到点后生成主动提醒消息，落进 dialog_id 指向的会话，
    并写一条 notification 投递记录。recurring 提醒触发后顺延 next_fire_at，
    once 提醒触发后置 done。
    """

    __tablename__ = "reminder"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: str = Field(
        sa_column=Column(String(64), nullable=False, index=True),
        description="提醒所属用户ID",
    )
    dialog_id: str = Field(
        sa_column=Column(String(64), nullable=False),
        description="到点后主动消息落进哪个会话",
    )
    content: str = Field(
        sa_column=Column(Text, nullable=False),
        description="用户设定的提醒事项，如'测空腹血糖'",
    )
    recurrence: str = Field(
        default="once",
        sa_column=Column(String(16), nullable=False),
        description="重复方式 once/hourly/daily/weekly",
    )
    repeat_count: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
        description="剩余触发次数；null 表示无限重复，仅对 recurring 生效",
    )
    next_fire_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False, index=True),
        description="下次触发时间（Asia/Shanghai 墙钟，naive）",
    )
    status: str = Field(
        default="pending",
        sa_column=Column(String(16), nullable=False),
        description="pending/firing/done/cancelled",
    )
    last_fired_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="最近一次触发时间",
    )
    create_time: Optional[datetime] = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
        ),
    )
    update_time: Optional[datetime] = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        ),
    )
