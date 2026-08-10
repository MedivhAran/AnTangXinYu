import time
from datetime import datetime
from typing import Literal
from uuid import UUID

from hindsight_client import Hindsight
from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    CompanionMemoryCursor,
    Message,
    MessageRole,
    MessageStatus,
)


class CompanionMemory:
    """把完成的短对话写入 Hindsight，并为 Agent 召回陪伴记忆。"""

    def __init__(
        self,
        client: Hindsight,
        *,
        mode: Literal["disabled", "shadow", "online"],
        retain_user_turns: int,
        recall_budget: Literal["low", "mid", "high"],
        recall_max_tokens: int,
    ) -> None:
        self.client = client
        self.mode = mode
        self.retain_user_turns = retain_user_turns
        self.recall_budget = recall_budget
        self.recall_max_tokens = recall_max_tokens

    async def prepare_context(
        self,
        session: AsyncSession,
        user_id: UUID,
        current_message_id: UUID,
    ) -> str:
        """同步旧对话并返回与当前消息相关的记忆；shadow 模式只写不读。"""

        if self.mode == "disabled":
            return ""

        current_message = (
            await session.execute(
                select(Message.content, Message.created_at).where(
                    Message.id == current_message_id,
                    Message.user_id == user_id,
                    Message.role == MessageRole.USER,
                    Message.status == MessageStatus.COMPLETED,
                )
            )
        ).one_or_none()
        if current_message is None:
            raise RuntimeError("找不到当前已完成的用户消息")

        cursor = await session.get(CompanionMemoryCursor, user_id)
        cursor_message_id = cursor.through_message_id if cursor is not None else None

        # 成功的 Core 对话只决定每三个用户回合切一次段；段内正文仍按真实
        # 消息顺序读取，因此主动关怀消息和失败运行前已保存的用户消息不会丢失。
        user_message = aliased(Message, name="memory_user_message")
        assistant_message = aliased(Message, name="memory_assistant_message")
        completed_core_turns = (
            select(
                user_message.id.label("user_message_id"),
                user_message.created_at.label("user_created_at"),
            )
            .join(AgentRun, AgentRun.trigger_message_id == user_message.id)
            .join(assistant_message, AgentRun.result_message_id == assistant_message.id)
            .where(
                AgentRun.user_id == user_id,
                AgentRun.parent_run_id.is_(None),
                AgentRun.agent_name == "core_agent",
                AgentRun.status == AgentRunStatus.COMPLETED,
                user_message.user_id == user_id,
                user_message.role == MessageRole.USER,
                user_message.status == MessageStatus.COMPLETED,
                user_message.id < current_message_id,
                assistant_message.user_id == user_id,
                assistant_message.role == MessageRole.ASSISTANT,
                assistant_message.status == MessageStatus.COMPLETED,
            )
        )
        if cursor_message_id is not None:
            completed_core_turns = completed_core_turns.where(
                user_message.id > cursor_message_id
            )
        segment_turns = (
            await session.execute(
                completed_core_turns.order_by(user_message.id).limit(
                    self.retain_user_turns
                )
            )
        ).all()

        recent_messages = list(
            reversed(
                (
                    await session.execute(
                        select(Message.role, Message.content)
                        .where(
                            Message.user_id == user_id,
                            Message.status == MessageStatus.COMPLETED,
                            Message.id < current_message_id,
                        )
                        .order_by(Message.id.desc())
                        .limit(2)
                    )
                ).all()
            )
        )

        segment_messages: list[tuple[UUID, MessageRole, str]] = []
        if len(segment_turns) == self.retain_user_turns:
            segment_end_message_id = segment_turns[-1].user_message_id
            segment_query = select(Message.id, Message.role, Message.content).where(
                Message.user_id == user_id,
                Message.status == MessageStatus.COMPLETED,
                Message.id <= segment_end_message_id,
            )
            if cursor_message_id is not None:
                segment_query = segment_query.where(Message.id > cursor_message_id)
            segment_messages = list(
                (await session.execute(segment_query.order_by(Message.id)))
                .tuples()
                .all()
            )

        current_content = current_message.content
        current_created_at = current_message.created_at
        await session.rollback()

        has_memory = cursor_message_id is not None
        if segment_messages:
            segment_end_message_id = segment_turns[-1].user_message_id
            segment_lines = [
                f"{'用户' if role == MessageRole.USER else '助手'}：{content}"
                for _, role, content in segment_messages
            ]
            retain_started_at = time.perf_counter()
            try:
                retain_response = await self.client.aretain(
                    bank_id=f"antang-user-{user_id}",
                    content="\n".join(segment_lines),
                    timestamp=segment_turns[-1].user_created_at,
                    context=(
                        "安糖心语的陪伴对话。助手内容只用于理解用户话语，"
                        "不能当作用户事实。"
                    ),
                    document_id=f"chat-segment-{segment_end_message_id}",
                    metadata={
                        "source": "antang_chat",
                        "start_message_id": str(segment_messages[0][0]),
                        "through_message_id": str(segment_end_message_id),
                    },
                    update_mode="replace",
                    retain_async=False,
                )
                if (
                    not retain_response.success
                    or retain_response.var_async
                    or retain_response.items_count != 1
                ):
                    raise RuntimeError("Hindsight 没有同步写入完整对话段")
            except Exception as error:
                if self.mode != "shadow":
                    raise
                logger.bind(
                    user_id=str(user_id),
                    error_type=type(error).__name__,
                    latency_ms=round((time.perf_counter() - retain_started_at) * 1000),
                ).error("companion_memory_shadow_retain_failed")
                return ""

            logger.bind(
                user_id=str(user_id),
                mode=self.mode,
                latency_ms=round((time.perf_counter() - retain_started_at) * 1000),
            ).info("companion_memory_retained")
            await session.execute(
                insert(CompanionMemoryCursor)
                .values(
                    user_id=user_id,
                    through_message_id=segment_end_message_id,
                )
                .on_conflict_do_update(
                    index_elements=[CompanionMemoryCursor.user_id],
                    set_={"through_message_id": segment_end_message_id},
                )
            )
            await session.commit()
            has_memory = True

        if self.mode == "shadow" or not has_memory:
            return ""

        query_parts = [
            f"{'用户' if role == MessageRole.USER else '助手'}：{content}"
            for role, content in recent_messages
        ]
        query_parts.append(f"当前用户：{current_content}")
        return await self._recall(
            user_id,
            query="\n".join(query_parts),
            query_timestamp=current_created_at,
        )

    async def recall_for_proactive(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        query: str,
        query_timestamp: datetime,
    ) -> str:
        """从同一用户记忆库为主动关怀召回上下文。"""

        if self.mode != "online":
            return ""

        has_memory = await session.get(CompanionMemoryCursor, user_id) is not None
        await session.rollback()
        if not has_memory:
            return ""
        return await self._recall(
            user_id,
            query=query,
            query_timestamp=query_timestamp,
        )

    async def _recall(
        self,
        user_id: UUID,
        *,
        query: str,
        query_timestamp: datetime,
    ) -> str:
        recall_started_at = time.perf_counter()
        recall_response = await self.client.arecall(
            bank_id=f"antang-user-{user_id}",
            query=query,
            types=["world", "experience", "observation"],
            max_tokens=self.recall_max_tokens,
            budget=self.recall_budget,
            trace=False,
            query_timestamp=query_timestamp.isoformat(),
            include_entities=False,
            include_chunks=False,
            include_source_facts=False,
            prefer_observations=True,
        )
        memories = [
            result.text.strip()
            for result in recall_response.results
            if result.text.strip()
        ]
        logger.bind(
            user_id=str(user_id),
            result_count=len(memories),
            latency_ms=round((time.perf_counter() - recall_started_at) * 1000),
        ).info("companion_memory_recalled")
        return "\n".join(f"- {memory}" for memory in memories)
