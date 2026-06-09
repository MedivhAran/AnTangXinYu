"""心跳提醒系统：到点生成主动提醒消息并送达。

- reminder_heartbeat_loop：lifespan 后台循环，每 N 秒认领并触发到期提醒。
- ReminderService.fire：生成文案 → 落 history（进原会话）→ 写 notification → 顺延/结束。

文案生成直接用主对话模型（deepseek-v4-flash）独立调用，不实例化 AnTangAgent、
不跑完整对话轮、不调 finalize_turn，所以不会把这条"主动提醒"污染进长期记忆 / 画像。
"""

import asyncio
import os
import time

from loguru import logger

from AnTang.api.services.history import HistoryService
from AnTang.database.dao.notification import NotificationDao
from AnTang.database.dao.reminder import ReminderDao
from AnTang.database.models.reminder import ReminderTable
from AnTang.services.antang.profile import AnTangProfileService
from AnTang.services.redis import redis_client
from AnTang.settings import app_settings
from AnTang.utils.common import count_tokens_usage

_REMINDER_SYSTEM_PROMPT = (
    "你是安糖心语，一个温暖的糖尿病陪伴助手。现在正是你之前答应过用户、要提醒 TA 的时间点。"
    "请根据【提醒事项】和【用户画像】，生成一句简短、自然、有温度的主动提醒。"
    "要求：50 字以内；像朋友顺口提一句，不要寒暄客套，不要列清单，不要解释你是 AI。"
)


class ReminderService:

    _model = None

    @classmethod
    async def _get_model(cls):
        """直接用主对话模型（deepseek-v4-flash），生成提醒/关怀文案足够快。"""
        if cls._model is None:
            from AnTang.core.models.manager import ModelManager

            cls._model = ModelManager.get_conversation_model()
        return cls._model

    @classmethod
    async def generate_message(cls, reminder: ReminderTable) -> str:
        """生成主动提醒文案；任何异常都回退成静态文本，绝不让触发失败。"""
        cfg = app_settings.reminder
        fallback = f"到点啦，记得{reminder.content}哦～"
        try:
            profile = await AnTangProfileService.get_profile(reminder.user_id)
            profile_summary = (profile.summary or "").strip() if profile else ""
            user_message = f"【提醒事项】\n{reminder.content}\n\n" f"【用户画像】\n{profile_summary or '暂无'}"
            model = await cls._get_model()
            response = await asyncio.wait_for(
                model.bind(max_tokens=cfg.max_output_tokens).ainvoke(
                    [
                        {"role": "system", "content": _REMINDER_SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ]
                ),
                timeout=cfg.generate_timeout_ms / 1000.0,
            )
            text = (response.content or "").strip()
            return text or fallback
        except Exception as err:
            logger.warning(f"[reminder] 生成提醒文案失败，回退静态文本: {err}")
            return fallback

    @classmethod
    async def fire(cls, reminder: ReminderTable) -> None:
        """触发一条提醒：生成 → 落库（history + notification）→ 顺延/结束。"""
        try:
            message = await cls.generate_message(reminder)
            await HistoryService.save_chat_history(
                role="assistant",
                content=message,
                events=[
                    {
                        "type": "event",
                        "data": {"event_type": "proactive_reminder", "reminder_id": reminder.id, "hidden": True},
                    }
                ],
                dialog_id=reminder.dialog_id,
                token_usage=count_tokens_usage(message),
            )
            await NotificationDao.create(
                user_id=reminder.user_id,
                dialog_id=reminder.dialog_id,
                content=message,
                reminder_id=reminder.id,
            )
            await ReminderDao.advance_or_finish(reminder.id)
            logger.info(f"[reminder] 已触发提醒 {reminder.id} → dialog {reminder.dialog_id}")
        except Exception as err:
            # 触发失败：复位为 pending，下个心跳重试，不丢提醒。
            logger.warning(f"[reminder] 触发提醒 {reminder.id} 失败，将重试: {err}")
            await ReminderDao.requeue(reminder.id)


_LEADER_KEY = "antang:reminder:leader"
_LEADER_TTL = max(10, app_settings.reminder.check_interval_seconds * 3)


def _worker_id() -> str:
    return os.environ.get("HOSTNAME", str(os.getpid()))


async def reminder_heartbeat_loop() -> None:
    """心跳循环：多 worker 下由 Leader 独跑，Redis SETNX 选举 + TTL 故障转移。

    每个 tick：先 GET 确认自己还持有锁 → 是则续租并跑，否则抢锁。
    """
    from AnTang.services.antang.proactive_care import ProactiveCareService

    interval = max(5, app_settings.reminder.check_interval_seconds)
    wid = _worker_id()
    leader = False
    last_care_scan = 0.0  # 主动关怀扫描降频用：记上次扫描时间
    logger.info(f"[reminder] 心跳循环启动 worker={wid} interval={interval}s")

    while True:
        await asyncio.sleep(interval)  # 先睡 30 秒
        if not app_settings.reminder.enabled:
            continue
        try:
            if leader:  # 已 Leader → 确认还持有
                current = redis_client.get(_LEADER_KEY)
                if isinstance(current, bytes):
                    current = current.decode()
                if current == wid:
                    redis_client.connection.expire(_LEADER_KEY, _LEADER_TTL)  # 续租
                else:
                    leader = False  # 被抢走了（可能锁到期 / 其他原因）
                    continue
            if not leader:  # 抢锁
                leader = redis_client.setNx(_LEADER_KEY, wid, _LEADER_TTL)
                if not leader:
                    continue  # 其他 worker 持有，跳过
                logger.info(f"[reminder] Leader 当选 worker={wid}")

            due = await ReminderDao.claim_due()  # 认领到期提醒，更新状态避免重复触发
            for reminder in due:
                await ReminderService.fire(reminder)

            # 主动关怀：心跳每 30s 一跳，但关怀状态按小时/天变化，没必要每跳都扫，
            # 降频到约 scan_interval 秒一次；同样只在 Leader 上跑。
            care = getattr(app_settings, "proactive_care", None)
            if care and care.enabled:
                now = time.time()
                if now - last_care_scan >= care.scan_interval_seconds:
                    last_care_scan = now
                    await ProactiveCareService.scan_and_fire(now)
        except Exception as err:
            logger.warning(f"[reminder] 心跳异常（已忽略，循环继续）: {err}")
