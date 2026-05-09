import orjson
from typing import List, Optional
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, text
from sqlmodel import Field, SQLModel

from AnTang.database.models.base import SQLModelSerializable

# 系统用户
SystemUser = "0"
# 管理员
AdminUser = "1"


class UserTable(SQLModelSerializable, table=True):
    __tablename__ = "user"

    user_id: str = Field(primary_key=True)
    user_name: str = Field(index=True, unique=True)
    user_email: str = Field(default=None)
    user_avatar: str = Field(description="用户头像")
    user_description: str = Field(default="该用户很懒，没有留下一片云彩")
    user_password: str = Field(description="经过加密后的用户密码")
    delete: bool = Field(default=False, description="该用户是否删除")
    create_time: datetime | None = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            index=True,
            server_default=text("CURRENT_TIMESTAMP"),
        )
    )
    update_time: datetime | None = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        )
    )
