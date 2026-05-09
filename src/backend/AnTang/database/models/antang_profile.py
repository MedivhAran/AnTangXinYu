from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, String, Text, text
from sqlmodel import Field

from AnTang.database.models.base import SQLModelSerializable


class AnTangProfileTable(SQLModelSerializable, table=True):
    __tablename__ = "antang_profile"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: str = Field(
        sa_column=Column(String(64), nullable=False, unique=True, index=True),
        description="画像所属用户ID",
    )
    profile_summary: str = Field(
        default="",
        sa_column=Column(Text, nullable=False),
        description="面向展示的显式画像摘要",
    )
    profile_data: Dict[str, Any] = Field(
        default={},
        sa_column=Column(JSON, nullable=False),
        description="结构化用户画像数据",
    )
    last_memory_excerpt: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="最近一次命中的长期记忆摘要",
    )
    update_time: Optional[datetime] = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        ),
        description="修改时间",
    )
    create_time: Optional[datetime] = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
        ),
        description="创建时间",
    )
