from datetime import datetime, timedelta
from typing import List, Optional

from sqlmodel import select

from AnTang.database.models.reminder import ReminderTable
from AnTang.database.session import async_session_getter
from AnTang.utils.common import get_now_time


def _now() -> datetime:
    """Asia/Shanghai 墙钟（naive），与 dialog 表 / 数据库时区保持一致。"""
    return get_now_time().replace(tzinfo=None)


class ReminderDao:

    @classmethod
    async def create(cls, reminder: ReminderTable) -> ReminderTable:
        async with async_session_getter() as session:
            session.add(reminder)
            await session.commit()
            await session.refresh(reminder)
            return reminder

    @classmethod
    async def list_active_by_user(cls, user_id: str) -> List[ReminderTable]:
        """列出用户未结束的提醒（pending/firing），按下次触发时间升序。"""
        async with async_session_getter() as session:
            statement = (
                select(ReminderTable)
                .where(ReminderTable.user_id == user_id)
                .where(ReminderTable.status.in_(["pending", "firing"]))
                .order_by(ReminderTable.next_fire_at)
            )
            result = await session.exec(statement)
            return list(result.all())

    @classmethod
    async def cancel(cls, reminder_id: str, user_id: str) -> bool:
        """取消提醒（软删，status=cancelled）。双重校验 user_id 防越权。"""
        async with async_session_getter() as session:
            statement = (
                select(ReminderTable).where(ReminderTable.id == reminder_id).where(ReminderTable.user_id == user_id)
            )
            result = await session.exec(statement)
            reminder = result.first()
            if not reminder or reminder.status in ("done", "cancelled"):
                return False
            reminder.status = "cancelled"
            session.add(reminder)
            await session.commit()
            return True

    @classmethod
    async def claim_due(cls) -> List[ReminderTable]:
        """取出到期的 pending 提醒并原子置为 firing，返回认领到的列表。

        单进程顺序执行天然不会重复认领。将来多副本时，应改为带 claim_token 的
        UPDATE ... WHERE status='pending'（看 rowcount）以保证跨进程只认领一次。
        firing 状态同时充当"处理中"标记，避免下一跳重复触发。
        """
        now = _now()
        async with async_session_getter() as session:
            statement = (
                select(ReminderTable).where(ReminderTable.status == "pending").where(ReminderTable.next_fire_at <= now)
            )
            result = await session.exec(statement)
            due = list(result.all())
            for reminder in due:
                reminder.status = "firing"
                session.add(reminder)
            await session.commit()
            for reminder in due:
                await session.refresh(reminder)
            return due

    @classmethod
    async def advance_or_finish(cls, reminder_id: str) -> None:
        """触发成功后顺延（recurring）或结束（once / 次数耗尽）。"""
        async with async_session_getter() as session:
            reminder = await session.get(ReminderTable, reminder_id)
            if not reminder:
                return
            now = _now()
            reminder.last_fired_at = now
            next_at = cls._compute_next_fire(reminder, now)
            if next_at is None:
                reminder.status = "done"
            else:
                reminder.next_fire_at = next_at
                reminder.status = "pending"
            session.add(reminder)
            await session.commit()

    @classmethod
    async def requeue(cls, reminder_id: str) -> None:
        """触发失败时把 firing 复位为 pending，下个心跳重试，不丢提醒。"""
        async with async_session_getter() as session:
            reminder = await session.get(ReminderTable, reminder_id)
            if not reminder or reminder.status != "firing":
                return
            reminder.status = "pending"
            session.add(reminder)
            await session.commit()

    @classmethod
    async def reset_stale_firing(cls) -> int:
        """启动时把残留的 firing 复位为 pending（崩溃恢复）。返回复位条数。"""
        async with async_session_getter() as session:
            statement = select(ReminderTable).where(ReminderTable.status == "firing")
            result = await session.exec(statement)
            stale = list(result.all())
            for reminder in stale:
                reminder.status = "pending"
                session.add(reminder)
            await session.commit()
            return len(stale)

    @staticmethod
    def _compute_next_fire(reminder: ReminderTable, now: datetime) -> Optional[datetime]:
        """算下一次触发时间；一次性或重复次数耗尽返回 None。

        过期任务一次性追平到下一个未来时刻（跳过堆积的间隔），避免补发雪崩。
        """
        if reminder.recurrence == "once":
            return None

        # 有限次重复：本次已触发，剩余次数减 1；减到 0 即结束。
        if reminder.repeat_count is not None:
            reminder.repeat_count -= 1
            if reminder.repeat_count <= 0:
                return None

        step = {
            "hourly": timedelta(hours=1),
            "daily": timedelta(days=1),
            "weekly": timedelta(weeks=1),
        }.get(reminder.recurrence)
        if step is None:
            return None  # 未知重复方式，按一次性处理

        next_at = reminder.next_fire_at + step
        while next_at <= now:
            next_at += step
        return next_at
