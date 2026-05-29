from datetime import datetime
from typing import List, Optional

from sqlmodel import select

from AnTang.database.models.notification import NotificationTable
from AnTang.database.session import async_session_getter
from AnTang.utils.common import get_now_time


def _now() -> datetime:
    return get_now_time().replace(tzinfo=None)


class NotificationDao:

    @classmethod
    async def create(
        cls,
        *,
        user_id: str,
        dialog_id: str,
        content: str,
        reminder_id: Optional[str] = None,
    ) -> NotificationTable:
        notification = NotificationTable(
            user_id=user_id,
            dialog_id=dialog_id,
            content=content,
            reminder_id=reminder_id,
        )
        async with async_session_getter() as session:
            session.add(notification)
            await session.commit()
            await session.refresh(notification)
            return notification

    @classmethod
    async def list_since(cls, user_id: str, since: Optional[datetime]) -> List[NotificationTable]:
        """拉取用户在 since 之后产生的通知（前端轮询）。since 为空则只取未读。"""
        async with async_session_getter() as session:
            statement = select(NotificationTable).where(NotificationTable.user_id == user_id)
            if since is not None:
                statement = statement.where(NotificationTable.create_time > since)
            else:
                statement = statement.where(NotificationTable.read_at.is_(None))
            statement = statement.order_by(NotificationTable.create_time)
            result = await session.exec(statement)
            return list(result.all())

    @classmethod
    async def mark_read(cls, user_id: str, notification_ids: List[str]) -> int:
        """标记已读。notification_ids 为空则把该用户全部未读标记为已读。返回标记条数。"""
        now = _now()
        async with async_session_getter() as session:
            statement = (
                select(NotificationTable)
                .where(NotificationTable.user_id == user_id)
                .where(NotificationTable.read_at.is_(None))
            )
            if notification_ids:
                statement = statement.where(NotificationTable.id.in_(notification_ids))
            result = await session.exec(statement)
            rows = list(result.all())
            for row in rows:
                row.read_at = now
                session.add(row)
            await session.commit()
            return len(rows)
