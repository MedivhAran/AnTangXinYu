from typing import List
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage

from AnTang.database.dao.dialog import DialogDao
from AnTang.database.dao.history import HistoryDao
from AnTang.utils.file_utils import normalize_storage_public_url

Assistant_Role = "assistant"
User_Role = "user"


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
    async def save_chat_history(
        cls, role, content, events, dialog_id, token_usage: int = 0, memory_enable: bool = False
    ):
        await cls.create_history(
            role=role, content=content, events=events, dialog_id=dialog_id, token_usage=token_usage
        )
        await DialogDao.touch_dialog_last_active(dialog_id=dialog_id)

    @classmethod
    async def get_short_term_messages(cls, dialog_id, user_id: str):
        """通过上次总结的时间来获取短期记忆, summary_last_time"""
        db_dialog = await DialogDao.select_dialog_by_id(dialog_id)
        if db_dialog.user_id != user_id:
            raise ValueError(f"没有权限获取 {dialog_id} 的对话信息")

        short_term_messages = await HistoryDao.get_short_term_messages(dialog_id, db_dialog.summary_last_time)
        messages: List[BaseMessage] = []
        for msg in short_term_messages:
            if msg.role == Assistant_Role:
                messages.append(AIMessage(content=msg.content))
            elif msg.role == User_Role:
                messages.append(HumanMessage(content=msg.content))
        return messages
