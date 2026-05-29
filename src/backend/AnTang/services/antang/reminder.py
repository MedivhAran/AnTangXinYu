"""心跳提醒系统：到点生成主动提醒消息并送达。

- reminder_heartbeat_loop：lifespan 后台循环，每 N 秒认领并触发到期提醒。
- ReminderService.fire：生成文案 → 落 history（进原会话）→ 写 notification → 顺延/结束。

文案生成走轻量独立 LLM（仿 light_analyzer），不实例化 AnTangAgent、不跑完整对话轮、
不调 finalize_turn，所以不会把这条"主动提醒"污染进长期记忆 / 画像。
"""

import asyncio

from loguru import logger

from AnTang.api.services.history import HistoryService
from AnTang.database.dao.notification import NotificationDao
from AnTang.database.dao.reminder import ReminderDao
from AnTang.database.models.reminder import ReminderTable
from AnTang.services.antang.profile import AnTangProfileService
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
        """复用轻量分析器那档快模型；未配置则回退主对话模型。"""
        if cls._model is not None:
            return cls._model

        from AnTang.core.models.manager import ModelManager

        multi = app_settings.multi_models
        analyzer_cfg = getattr(multi, "light_analyzer", None) if multi else None
        if analyzer_cfg and analyzer_cfg.model_name:
            cls._model = ModelManager.get_user_model(
                model=analyzer_cfg.model_name,
                base_url=analyzer_cfg.base_url,
                api_key=analyzer_cfg.api_key,
            )
        else:
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
                        "data": {
                            "event_type": "proactive_reminder",
                            "reminder_id": reminder.id,
                        },
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


async def reminder_heartbeat_loop() -> None:
    """心跳循环：每 check_interval_seconds 认领并触发到期提醒。

    单个 except 兜底确保循环永不因偶发异常退出。
    """
    interval = max(5, app_settings.reminder.check_interval_seconds)
    logger.info(f"[reminder] 心跳循环启动，间隔 {interval}s")
    while True:
        await asyncio.sleep(interval)  # 先睡 30 秒
        if not app_settings.reminder.enabled:
            continue
        try:
            due = await ReminderDao.claim_due()  # 认领到期提醒（更新状态为 processing，避免重复触发）
            for reminder in due:
                await ReminderService.fire(reminder)
        except Exception as err:
            logger.warning(f"[reminder] 心跳异常（已忽略，循环继续）: {err}")
