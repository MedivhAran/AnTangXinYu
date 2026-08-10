from uuid import UUID

from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from antang_api.database import Base


class CompanionMemoryCursor(Base):
    """记录每位用户已经成功写入 Hindsight 的最后一条用户消息。"""

    __tablename__ = "companion_memory_cursors"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    through_message_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
