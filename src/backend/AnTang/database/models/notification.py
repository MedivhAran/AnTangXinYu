from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, String, Text, text
from sqlmodel import Field

from AnTang.database.models.base import SQLModelSerializable


class NotificationTable(SQLModelSerializable, table=True):
    """通知投递记录（一次触发一行，带已读态）。

    web 端轮询、将来手机端推送都读这张表，是所有送达通道的唯一来源。
    read_at 为 null 表示未读。content 直接存生成好的文案，toast/push 不必再查 history。
    """

    __tablename__ = "notification"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: str = Field(
        sa_column=Column(String(64), nullable=False, index=True),
        description="通知所属用户ID",
    )
    dialog_id: str = Field(
        sa_column=Column(String(64), nullable=False),
        description="关联会话ID（点开即跳转到该会话）",
    )
    reminder_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
        description="由哪条提醒产生；预留给非提醒类通知留空",
    )
    content: str = Field(
        sa_column=Column(Text, nullable=False),
        description="生成好的提醒文案",
    )
    read_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="已读时间；null 表示未读",
    )
    create_time: Optional[datetime] = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            index=True,
        ),
    )
