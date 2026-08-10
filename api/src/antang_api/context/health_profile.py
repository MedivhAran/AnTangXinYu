import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from antang_api.health_profile.service import load_health_profile_snapshot
from antang_api.health_profile.types import HealthProfileSnapshot
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    Message,
    MessageRole,
    MessageStatus,
)


class ConversationTurn(BaseModel):
    """健康档案 Agent 可见的一轮已完成对话。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user: str
    assistant: str


class ProfileInput(BaseModel):
    """每次委派时重新组装的完整、隔离的子 Agent 输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    current_profile: HealthProfileSnapshot
    personal_field_revisions: dict[str, int] = Field(exclude=True)
    recent_conversation: list[ConversationTurn]
    current_user_message: str

    def to_model_text(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)


async def _load_user_message(
    session: AsyncSession,
    user_id: UUID,
    message_id: UUID,
) -> Message:
    message = await session.get(Message, message_id)
    if message is None:
        raise RuntimeError(f"找不到健康档案委派的触发消息：{message_id}")
    if message.user_id != user_id:
        raise RuntimeError("健康档案委派的触发消息不属于当前用户")
    if message.role != MessageRole.USER or message.status != MessageStatus.COMPLETED:
        raise RuntimeError("健康档案委派只能由已完成的用户消息触发")
    return message


async def _load_recent_turns(
    session: AsyncSession,
    user_id: UUID,
    before_message_id: UUID,
    *,
    limit: int,
) -> list[ConversationTurn]:
    if limit < 1:
        raise ValueError("最近对话轮数必须大于 0")

    trigger_message = aliased(Message)
    result_message = aliased(Message)
    rows = list(
        await session.execute(
            select(trigger_message.content, result_message.content)
            .join(AgentRun, AgentRun.trigger_message_id == trigger_message.id)
            .join(result_message, AgentRun.result_message_id == result_message.id)
            .where(
                AgentRun.user_id == user_id,
                AgentRun.parent_run_id.is_(None),
                AgentRun.agent_name == "core_agent",
                AgentRun.status == AgentRunStatus.COMPLETED,
                trigger_message.user_id == user_id,
                trigger_message.role == MessageRole.USER,
                trigger_message.status == MessageStatus.COMPLETED,
                trigger_message.id < before_message_id,
                result_message.user_id == user_id,
                result_message.role == MessageRole.ASSISTANT,
                result_message.status == MessageStatus.COMPLETED,
            )
            .order_by(trigger_message.id.desc())
            .limit(limit)
        )
    )

    # 数据库查询先取最新五轮，模型输入恢复为自然的时间顺序。
    return [
        ConversationTurn(user=user_text, assistant=assistant_text)
        for user_text, assistant_text in reversed(rows)
    ]


async def load_profile_input(
    session: AsyncSession,
    user_id: UUID,
    trigger_message_id: UUID,
    *,
    recent_turns: int = 5,
) -> ProfileInput:
    """读取当前档案、最近五轮完整对话和本次触发消息。"""

    current_message = await _load_user_message(
        session,
        user_id,
        trigger_message_id,
    )
    snapshot = await load_health_profile_snapshot(session, user_id)
    turns = await _load_recent_turns(
        session,
        user_id,
        trigger_message_id,
        limit=recent_turns,
    )
    return ProfileInput(
        current_profile=snapshot,
        personal_field_revisions=dict(snapshot.personal_profile.field_revisions),
        recent_conversation=turns,
        current_user_message=current_message.content,
    )


async def render_core_profile(
    session: AsyncSession,
    user_id: UUID,
) -> str:
    """为 Core Agent 输出当前档案；不包含设备原始时序样本。"""

    snapshot = await load_health_profile_snapshot(session, user_id)
    # Core 只需要稳定档案与卡片线索。分钟级心率、睡眠阶段等设备原始值
    # 按需通过当前用户只读工具查询，不能默认塞入每轮聊天。
    payload = {
        "personal_profile": snapshot.personal_profile.model_dump(mode="json"),
        "health_facts": [
            fact.model_dump(mode="json") for fact in snapshot.health_facts
        ],
        "pending_changes": [
            {
                "id": str(change.id),
                "mode": change.mode.value,
                "field_name": change.field_name,
                "operation": change.operation.value,
                "question": change.question,
            }
            for change in snapshot.pending_changes
        ],
    }
    return json.dumps(payload, ensure_ascii=False)
