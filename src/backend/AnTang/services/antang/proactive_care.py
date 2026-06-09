"""主动关怀：根据用户近期的血糖、情绪状态，在合适的时机主动发一条关怀消息。

借鉴 jiwen 的思路（用随时间涨落、被事件推动的连续数值来决定何时开口），但改造成
以"用户需要"为中心的关怀，而不是 AI 角色自己的情绪。

三个关怀理由（计量值 0~1）：
  · glucose_concern  血糖担忧：近期低血糖/异常事件推高，平稳则慢慢回落
  · mood_fear        情绪/低血糖恐惧：对话出现焦虑恐惧等负面情绪、或踩到诱因时推高
  · disconnect       太久没联系：不存值，直接由"距上次联系多久"实时算

状态存在 Redis 哈希 antang:care_state:{user_id}，频繁更新、丢了能重攒。
事件（每轮对话收尾、血糖入库）只负责更新计量值；要不要真的发，由心跳定时扫描统一把关。
"""

from __future__ import annotations

import time
from datetime import datetime

import pytz
from loguru import logger

from AnTang.services.redis import redis_client
from AnTang.settings import app_settings

_SH = pytz.timezone("Asia/Shanghai")

# ---- 可调参数（计量与衰减）----
THRESHOLD = 0.5  # 三个理由共用的触发阈值
GLUCOSE_BUMP = {"severe_low": 0.6, "low": 0.4, "low_warning": 0.2}  # 按血糖分层加分
GLUCOSE_SEVERE_FLOOR = 0.85  # 严重低血糖至少把担忧顶到这个值
GLUCOSE_NORMAL_RELIEF = 0.7  # 报告正常血糖时，担忧乘这个值缓解
MOOD_BUMP = 0.15  # 单次负面情绪加分
MOOD_DIMINISH = 0.5  # 同方向连续出现的递减系数
HYPO_TO_FEAR = 0.15  # 低血糖事件额外喂给"恐惧"的分
GLUCOSE_DECAY_PER_DAY = 0.1  # 血糖担忧每天回落
MOOD_DECAY_PER_DAY = 0.15  # 情绪每天回落
RELIEF_AFTER_FIRE = 0.3  # 关怀过后，把触发的那个值乘这个压下去

# ---- 可调参数（防打扰，已按需求定）----
IDLE_SECONDS = 60 * 60  # 空闲 1 小时才算"没在聊"
QUIET_START_HOUR, QUIET_END_HOUR = 2, 6  # 0~6 点静默不发
COOLDOWN_SECONDS = 6 * 60 * 60  # 两条主动消息至少隔 6 小时
DAILY_CAP = 3  # 每人每天最多 3 条
DISCONNECT_FULL_SECONDS = 6 * 86400  # 疏离到 1.0 的时长；3 天正好到阈值 0.5
MAX_IDLE_SECONDS = 14 * 86400  # 超过 14 天彻底沉默就放弃，不再打扰
STATE_TTL_SECONDS = 30 * 86400  # Redis 状态保活时长

# DialogState.dominant_emotions 存的是英文标签（见 antang_agent._update_dialog_state，
# 它把中文情绪词映射成 fear/anxiety/sadness/frustration/relief），所以这里必须用英文标签匹配。
# relief 是正面情绪，不算负面。
_NEGATIVE_EMOTIONS = {"fear", "anxiety", "sadness", "frustration"}

ACTIVE_SET = "antang:care:active_users"


def _key(user_id: str) -> str:
    return f"antang:care_state:{user_id}"


def _now() -> float:
    return time.time()


def _today() -> str:
    return datetime.now(_SH).strftime("%Y-%m-%d")


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


# ---------- 状态读写（哈希 + 懒衰减）----------
def _read_state(user_id: str) -> dict | None:
    """读出状态并按距上次更新的时间做懒衰减。新用户/已过期返回 None。"""
    raw = redis_client.hgetall(_key(user_id))
    if not raw:
        return None
    g = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in raw.items()
    }
    now = _now()
    last_update = float(g.get("last_update_at", now))
    elapsed_days = max(0.0, (now - last_update) / 86400)
    return {
        "glucose_concern": _clamp(float(g.get("glucose_concern", 0)) - GLUCOSE_DECAY_PER_DAY * elapsed_days),
        "mood_fear": _clamp(float(g.get("mood_fear", 0)) - MOOD_DECAY_PER_DAY * elapsed_days),
        "mood_streak": int(g.get("mood_streak", 0)),
        "last_interaction_at": float(g.get("last_interaction_at", now)),
        "last_proactive_at": float(g.get("last_proactive_at", 0)),
        "proactive_count": int(g.get("proactive_count", 0)),
        "count_date": g.get("count_date", ""),
    }


def _write_state(user_id: str, st: dict) -> None:
    mapping = {
        "glucose_concern": f"{st['glucose_concern']:.4f}",
        "mood_fear": f"{st['mood_fear']:.4f}",
        "mood_streak": str(st["mood_streak"]),
        "last_interaction_at": f"{st['last_interaction_at']:.0f}",
        "last_proactive_at": f"{st['last_proactive_at']:.0f}",
        "proactive_count": str(st["proactive_count"]),
        "count_date": st["count_date"],
        "last_update_at": f"{_now():.0f}",
    }
    redis_client.hset(_key(user_id), mapping=mapping, expiration=STATE_TTL_SECONDS)


class ProactiveCareService:

    # ---------- 事件更新：每轮对话收尾 / 血糖入库时调用 ----------
    @classmethod
    def on_interaction(cls, user_id: str, dominant_emotions: list[str] | None, glucose_zone: str) -> None:
        """把这轮的情绪和血糖情况记进计量值；并把"太久没联系"清零（标记刚联系过）。

        只更新数值，不发消息。任何异常都吞掉，绝不影响主对话。
        """
        if not app_settings.proactive_care.enabled:
            return  # 功能没开就不积累状态，省掉每轮的 Redis 写
        try:
            now = _now()
            st = _read_state(user_id) or {
                "glucose_concern": 0.0,
                "mood_fear": 0.0,
                "mood_streak": 0,
                "last_interaction_at": now,
                "last_proactive_at": 0.0,
                "proactive_count": 0,
                "count_date": "",
            }

            # 血糖：按分层加分；报告正常则缓解担忧
            bump = GLUCOSE_BUMP.get(glucose_zone)
            if bump:
                st["glucose_concern"] = _clamp(st["glucose_concern"] + bump)
                if glucose_zone == "severe_low":
                    st["glucose_concern"] = max(st["glucose_concern"], GLUCOSE_SEVERE_FLOOR)
                if glucose_zone in ("severe_low", "low"):
                    st["mood_fear"] = _clamp(st["mood_fear"] + HYPO_TO_FEAR)  # 低血糖也喂恐惧
            elif glucose_zone == "normal":
                st["glucose_concern"] = _clamp(st["glucose_concern"] * GLUCOSE_NORMAL_RELIEF)

            # 情绪：出现负面情绪则加分（同方向连续递减），否则连续计数清零
            has_negative = bool(set(dominant_emotions or []) & _NEGATIVE_EMOTIONS)
            if has_negative:
                effective = MOOD_BUMP / (1 + st["mood_streak"] * MOOD_DIMINISH)
                st["mood_fear"] = _clamp(st["mood_fear"] + effective)
                st["mood_streak"] += 1
            else:
                st["mood_streak"] = 0

            # 刚联系过：疏离时钟清零（用 last_interaction_at 体现）
            st["last_interaction_at"] = now

            _write_state(user_id, st)
            redis_client.sadd(ACTIVE_SET, user_id, expiration=STATE_TTL_SECONDS)
        except Exception as err:
            logger.warning(f"[proactive-care] on_interaction 失败（已忽略）: {err}")

    # ---------- 判断：该不该发、发哪条 ----------
    @classmethod
    def _decide(cls, st: dict, now: float) -> str | None:
        """过四道防打扰关卡 + 按优先级挑一个超阈值的理由。返回理由名或 None。"""
        # 1. 空闲：还在聊或刚聊完，不打扰
        if now - st["last_interaction_at"] < IDLE_SECONDS:
            return None
        # 2. 静默时段
        if QUIET_START_HOUR <= datetime.now(_SH).hour < QUIET_END_HOUR:
            return None
        # 3. 冷却
        if st["last_proactive_at"] and now - st["last_proactive_at"] < COOLDOWN_SECONDS:
            return None
        # 4. 每日上限
        if st["count_date"] == _today() and st["proactive_count"] >= DAILY_CAP:
            return None

        # 疏离 = 距"上次联系或上次主动消息"多久（发过主动消息也算联系过，避免反复问候）
        idle = now - max(st["last_interaction_at"], st["last_proactive_at"])
        disconnect = min(1.0, idle / DISCONNECT_FULL_SECONDS)

        # 优先级：血糖 > 情绪 > 问候
        if st["glucose_concern"] >= THRESHOLD:
            return "glucose"
        if st["mood_fear"] >= THRESHOLD:
            return "mood"
        if disconnect >= THRESHOLD:
            return "disconnect"
        return None

    # ---------- 心跳定时调用：扫活跃用户，该发的发 ----------
    @classmethod
    async def scan_and_fire(cls, now: float | None = None) -> None:
        now = now or _now()
        try:
            members = redis_client.smembers(ACTIVE_SET)
        except Exception as err:
            logger.warning(f"[proactive-care] 读活跃用户失败: {err}")
            return

        for raw_uid in members:
            user_id = raw_uid.decode() if isinstance(raw_uid, bytes) else raw_uid
            try:
                st = _read_state(user_id)
                if st is None:
                    redis_client.srem(ACTIVE_SET, user_id)
                    continue
                # 太久彻底沉默，放弃打扰
                if now - max(st["last_interaction_at"], st["last_proactive_at"]) > MAX_IDLE_SECONDS:
                    redis_client.srem(ACTIVE_SET, user_id)
                    continue
                reason = cls._decide(st, now)
                if reason:
                    await cls._fire(user_id, reason, st, now)
            except Exception as err:
                logger.warning(f"[proactive-care] 处理用户 {user_id} 失败: {err}")

    @classmethod
    async def _fire(cls, user_id: str, reason: str, st: dict, now: float) -> None:
        delivered = await cls._generate_and_deliver(user_id, reason, st)
        if not delivered:
            return
        # 记录冷却 + 当日计数；把触发的那个值压下去
        st["last_proactive_at"] = now
        if st["count_date"] != _today():
            st["count_date"] = _today()
            st["proactive_count"] = 0
        st["proactive_count"] += 1
        if reason == "glucose":
            st["glucose_concern"] *= RELIEF_AFTER_FIRE
        elif reason == "mood":
            st["mood_fear"] *= RELIEF_AFTER_FIRE
            st["mood_streak"] = 0
        _write_state(user_id, st)
        logger.info(f"[proactive-care] 已向 {user_id} 发出 {reason} 关怀")

    # ---------- 生成话术 + 投递 ----------
    @classmethod
    def _build_prompt(cls, reason: str, profile_summary: str) -> tuple[str, str]:
        """返回 (系统提示, 用户侧上下文)。口气随理由变。"""
        tone = {
            "glucose": "口气关切但平实，可以顺带提一句实用建议，别说教。",
            "mood": "口气温暖、平稳、给安全感，可以引导用户转移注意力，引导用户聊聊自己的感受。",
            "disconnect": "口气轻松活泼，像朋友顺口问一句近况，别太正式。",
        }[reason]
        focus = {
            "glucose": "你注意到 TA 最近血糖有过波动或低血糖，想主动关心一下近况。",
            "mood": "你感觉到 TA 最近情绪有点低落、或对低血糖比较担心，想主动安抚陪伴一下。",
            "disconnect": "TA 有一阵子没来聊了，你想主动问候一声。",
        }[reason]
        system = (
            "你是安糖心语，一个温暖的糖尿病陪伴助手。现在是你主动找用户说话的时刻。"
            f"{focus} 请生成一句自然的主动消息。"
            f"要求：{tone} 不要寒暄客套、不要列清单、不要解释你是 AI。"
        )
        user_msg = f"【用户画像】\n{profile_summary or '暂无'}"
        return system, user_msg

    @classmethod
    async def _generate_and_deliver(cls, user_id: str, reason: str, st: dict) -> bool:
        """生成关怀文案并投递到用户最近一条对话。成功返回 True。"""
        import asyncio

        from AnTang.api.services.history import HistoryService
        from AnTang.core.models.manager import ModelManager
        from AnTang.database.dao.dialog import DialogDao
        from AnTang.database.dao.notification import NotificationDao
        from AnTang.services.antang.profile import AnTangProfileService
        from AnTang.settings import app_settings
        from AnTang.utils.common import count_tokens_usage

        # 找用户最近一条对话作为落点；没有就没法发
        dialogs = await DialogDao.get_dialog_by_user(user_id)
        if not dialogs:
            return False
        dialog_id = dialogs[0].dialog_id  # DialogTable 主键是 dialog_id

        try:
            profile = await AnTangProfileService.get_profile(user_id)
            profile_summary = (profile.summary or "").strip() if profile else ""
        except Exception:
            profile_summary = ""

        system, user_msg = cls._build_prompt(reason, profile_summary)
        cfg = app_settings.reminder
        fallback = {
            "glucose": "最近血糖还稳吗？有不舒服随时跟我说。",
            "mood": "这两天还好吗？我一直都在，想聊随时找我。",
            "disconnect": "有阵子没见你啦，最近怎么样？",
        }[reason]
        try:
            model = ModelManager.get_conversation_model()
            resp = await asyncio.wait_for(
                model.bind(max_tokens=cfg.max_output_tokens).ainvoke(
                    [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]
                ),
                timeout=cfg.generate_timeout_ms / 1000.0,
            )
            message = (resp.content or "").strip() or fallback
        except Exception as err:
            logger.warning(f"[proactive-care] 生成文案失败，用兜底文本: {err}")
            message = fallback

        # 投递：写进对话历史（可见）+ 写通知（提醒用户）
        await HistoryService.save_chat_history(
            role="assistant",
            content=message,
            events=[{"type": "event", "data": {"hidden": True, "event_type": "proactive_care", "reason": reason}}],
            dialog_id=dialog_id,
            token_usage=count_tokens_usage(message),
        )
        await NotificationDao.create(user_id=user_id, dialog_id=dialog_id, content=message, reminder_id=None)
        return True
