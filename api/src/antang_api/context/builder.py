from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolCall,
    ToolMessage,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
    ConversationSummary,
    Message,
    MessageRole,
    MessageStatus,
)

SUMMARY_PREFIX = """ [较早对话摘要]
以下内容由系统根据更早的用户和你的聊天消息生成：
"""


@dataclass(frozen=True)
class ChatContext:
    """一次Core Agent 调用所需要的所有对话上下文"""

    messages: tuple[BaseMessage, ...]
    summary_id: UUID | None
    summary_through_message_id: UUID | None


def to_model_message(message: Message) -> BaseMessage:
    """把数据库中的原始消息转换成 LangChain 消息。"""

    match message.role:
        case MessageRole.USER:
            return HumanMessage(content=message.content)

        case MessageRole.ASSISTANT:
            return AIMessage(content=message.content)

    raise ValueError(f"无法转换消息角色：{message.role}")


async def _load_completed_tool_messages(
    session: AsyncSession,
    user_id: UUID,
    result_message_ids: list[UUID],
    after_message_id: UUID | None,
) -> dict[UUID, tuple[BaseMessage, ...]]:
    """按模型轮次重建已完成运行中的工具调用和结果。"""

    if not result_message_ids:
        return {}

    tool_call_query = (
        select(AgentToolCall, AgentRun.result_message_id)
        .join(AgentRun, AgentRun.id == AgentToolCall.agent_run_id)
        .where(
            AgentRun.user_id == user_id,
            AgentRun.status == AgentRunStatus.COMPLETED,
            AgentRun.result_message_id.in_(result_message_ids),
            AgentToolCall.status == AgentToolCallStatus.COMPLETED,
        )
        .order_by(
            AgentRun.result_message_id,
            AgentToolCall.model_turn_index,
            AgentToolCall.tool_call_index,
        )
    )

    if after_message_id is not None:
        tool_call_query = tool_call_query.where(
            AgentRun.trigger_message_id > after_message_id,
        )

    rows = await session.execute(tool_call_query)

    calls_by_message_and_turn: dict[UUID, dict[int, list[AgentToolCall]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for tool_call, result_message_id in rows:
        if result_message_id is None:
            raise RuntimeError("已完成的工具调用缺少 AgentRun 结果消息")
        calls_by_message_and_turn[result_message_id][tool_call.model_turn_index].append(
            tool_call
        )

    tool_messages_by_result: dict[UUID, tuple[BaseMessage, ...]] = {}

    for result_message_id, calls_by_turn in calls_by_message_and_turn.items():
        rebuilt_messages: list[BaseMessage] = []

        for calls in calls_by_turn.values():
            rebuilt_messages.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name=call.tool_name,
                            args=call.arguments,
                            id=call.tool_call_id,
                            type="tool_call",
                        )
                        for call in calls
                    ],
                )
            )

            for call in calls:
                if call.result is None:
                    raise RuntimeError(f"已完成的工具调用缺少结果：{call.tool_call_id}")
                rebuilt_messages.append(
                    ToolMessage(
                        content=call.result,
                        tool_call_id=call.tool_call_id,
                        name=call.tool_name,
                    )
                )

        tool_messages_by_result[result_message_id] = tuple(rebuilt_messages)

    return tool_messages_by_result


async def build_chat_context(
    session: AsyncSession, user_id: UUID, through_message_id: UUID
) -> ChatContext:
    """读取最新摘要，以及摘要之后、当前消息之前的完整对话。"""

    # 取出最新的摘要
    summary = await session.scalar(
        select(ConversationSummary)
        .where(
            ConversationSummary.user_id == user_id,
            ConversationSummary.through_message_id <= through_message_id,
        )
        .order_by(ConversationSummary.id.desc())
        .limit(1)
    )

    # 该用户所有「已完成、且不晚于当前消息的消息」 的查询语句
    message_query = select(Message).where(
        Message.user_id == user_id,
        Message.status == MessageStatus.COMPLETED,
        Message.id <= through_message_id,
    )

    # 只要摘要覆盖之后的消息
    if summary is not None:
        message_query = message_query.where(Message.id > summary.through_message_id)

    # 执行查询，得到最新摘要之后所有的消息
    saved_messages = list(
        await session.scalars(
            message_query.order_by(Message.id),
        )
    )

    result_message_ids = [
        message.id
        for message in saved_messages
        if message.role == MessageRole.ASSISTANT
    ]
    tool_messages_by_result = await _load_completed_tool_messages(
        session,
        user_id,
        result_message_ids,
        summary.through_message_id if summary is not None else None,
    )

    model_messages: list[BaseMessage] = []

    if summary:
        model_messages.append(
            HumanMessage(
                f"{SUMMARY_PREFIX}{summary.content}",
                None,
                additional_kwargs={"lc_source": "summarization"},
            )
        )

    for message in saved_messages:
        # 工具调用属于生成这条最终助手消息的 AgentRun，必须紧挨着放在它前面。
        model_messages.extend(tool_messages_by_result.get(message.id, ()))
        model_messages.append(to_model_message(message))

    return ChatContext(
        messages=tuple(model_messages),
        summary_id=summary.id if summary else None,
        summary_through_message_id=(summary.through_message_id if summary else None),
    )
