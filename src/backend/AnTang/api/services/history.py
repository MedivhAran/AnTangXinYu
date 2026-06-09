from typing import List
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage

from AnTang.database.dao.dialog import DialogDao
from AnTang.database.dao.history import HistoryDao
from AnTang.services.redis import redis_client
from AnTang.utils.file_utils import normalize_storage_public_url

Assistant_Role = "assistant"
User_Role = "user"

# 对话历史 Redis 缓存
_HIST_KEY_PREFIX = "antang:hist:"
_HIST_TTL = 2 * 3600  # 2h
_HIST_PROACTIVE = "antang:proactive:"  # AI 主动提醒缓存（Part 6 预留）


class HistoryService:

    @classmethod
    async def create_history(cls, role: str, content: str, events: List[dict], dialog_id: str, token_usage: int = 0):
        try:
            await HistoryDao.create_history(
                role=role, content=content, events=events, dialog_id=dialog_id, token_usage=token_usage
            )
        except Exception as err:
            raise ValueError(f"Add history data appear error: {err}")

    @classmethod
    async def select_history(cls, dialog_id: str, top_k: int = 4) -> List[BaseMessage] | None:
        try:
            result = await HistoryDao.select_history_from_time(dialog_id, top_k)
            messages: List[BaseMessage] = []
            for data in result:
                if data.role == Assistant_Role:
                    messages.append(AIMessage(content=data.content))
                elif data.role == User_Role:
                    messages.append(HumanMessage(content=data.content))
            return messages
        except Exception as err:
            raise ValueError(f"Select history is appear error: {err}")

    @classmethod
    async def select_original_history_messages(cls, dialog_id: str, top_k: int = 10000):
        """直接选取全部的历史消息"""
        result = await HistoryDao.select_history_from_time(dialog_id, top_k)
        return result

    @classmethod
    async def get_dialog_history(cls, dialog_id: str):
        try:
            results = await HistoryDao.get_dialog_history(dialog_id)
            history_items = [res.to_dict() for res in results]
            for item in history_items:
                events = item.get("events") or []
                visible_events = []
                for event in events:
                    event_data = event.get("data") or {}
                    # 标记为 hidden 的事件（如 antang_dialog_state）只用于跨轮持久化，不暴露给前端
                    if event_data.get("hidden"):
                        continue
                    if event.get("type") == "attachment":
                        file_url = event_data.get("file_url")
                        if file_url:
                            event_data["file_url"] = normalize_storage_public_url(file_url)
                    visible_events.append(event)
                item["events"] = visible_events
            return history_items
        except Exception as err:
            raise ValueError(f"Get dialog history is appear error: {err}")

    @classmethod
    def _hist_key(cls, dialog_id: str) -> str:
        return _HIST_KEY_PREFIX + dialog_id

    @classmethod
    def _cache_messages(cls, dialog_id: str, messages: List[BaseMessage]) -> None:
        try:
            data = [{"role": m.__class__.__name__, "content": m.content} for m in messages]
            redis_client.set(cls._hist_key(dialog_id), data, _HIST_TTL)
        except Exception:
            pass  # 缓存写失败不阻塞主流程

    @classmethod
    def _read_cached_messages(cls, dialog_id: str) -> List[BaseMessage] | None:
        try:
            raw = redis_client.get(cls._hist_key(dialog_id))
            if not raw:
                return None
            messages: List[BaseMessage] = []
            for item in raw:
                if item.get("role") == "AIMessage":
                    messages.append(AIMessage(content=item["content"]))
                elif item.get("role") == "HumanMessage":
                    messages.append(HumanMessage(content=item["content"]))
            return messages if messages else None
        except Exception:
            return None

    @classmethod
    async def save_chat_history(
        cls, role, content, events, dialog_id, token_usage: int = 0, memory_enable: bool = False
    ):
        await cls.create_history(
            role=role, content=content, events=events, dialog_id=dialog_id, token_usage=token_usage
        )
        await DialogDao.touch_dialog_last_active(dialog_id=dialog_id)
        # 写入 Redis 缓存（write-through）：先从缓存读已有轮次，追新轮，再回写
        try:
            cached = cls._read_cached_messages(dialog_id) or []
            new_msg = AIMessage(content=content) if role == Assistant_Role else HumanMessage(content=content)
            cached.append(new_msg)
            if len(cached) > 40:  # 只缓存最近 40 条（~20 轮）
                cached = cached[-40:]
            cls._cache_messages(dialog_id, cached)
        except Exception:
            pass

    @classmethod
    async def get_short_term_messages(cls, dialog_id, user_id: str):
        """获取短期消息：优先 Redis 缓存，未命中再查 MySQL 并回填。"""
        # 校验权限
        db_dialog = await DialogDao.select_dialog_by_id(dialog_id)
        if db_dialog.user_id != user_id:
            raise ValueError(f"没有权限获取 {dialog_id} 的对话信息")

        # 先查 Redis 缓存
        cached = cls._read_cached_messages(dialog_id)
        if cached is not None:
            return cached

        # 缓存未命中 → MySQL 并回填
        short_term_messages = await HistoryDao.get_short_term_messages(dialog_id, db_dialog.summary_last_time)
        messages: List[BaseMessage] = []
        for msg in short_term_messages:
            if msg.role == Assistant_Role:
                messages.append(AIMessage(content=msg.content))
            elif msg.role == User_Role:
                messages.append(HumanMessage(content=msg.content))
        cls._cache_messages(dialog_id, messages)
        return messages
