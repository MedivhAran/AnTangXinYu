from uuid import UUID

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    get_buffer_string,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.context.builder import to_historical_model_message
from antang_api.models import (
    ConversationSummary,
    Message,
    MessageRole,
    MessageStatus,
)

SUMMARY_PROMPT_VERSION = "v1"

SUMMARY_SYSTEM_PROMPT = """
你负责为一段长期陪伴对话生成连续性摘要。

摘要需要保留：
- 双方主要讨论过的事情；
- 用户明确表达的感受、问题和需求；
- 助手的重要回复、行动和承诺；
- 尚未解决或仍在继续的话题；
- 后续交流需要使用的日期、数字和名称。

规则：
- 严格依据提供的摘要和原始对话；
- 保留事实、不确定性和用户原本表达的语气；
- 摘要只负责维持对话连续性；
- 健康档案、心理判断和陪伴记忆由其他系统处理；
- 不保留 [S1] 这类只对原回答有效的来源编号；
- 输出连续、简洁的摘要正文。
""".strip()


class InsufficientMessagesForCompactionError(Exception):
    """当前可以压缩的完整对话数量不足。"""


def to_summary_message(message: Message) -> BaseMessage:
    """移除只属于原回答的来源编号，再交给摘要模型。"""

    return to_historical_model_message(message)


def select_messages_to_compact(
    messages: list[Message],
    messages_to_keep: int,
) -> list[Message]:
    """选择较早的完整对话，并保留指定数量的近期原始消息。"""

    if messages_to_keep < 1:
        raise ValueError("messages_to_keep 必须大于 0")

    cutoff = len(messages) - messages_to_keep

    if cutoff <= 0:
        raise InsufficientMessagesForCompactionError()

    # 摘要边界落在助手消息之后，让近期原文从用户消息开始。
    while cutoff > 0 and messages[cutoff - 1].role != MessageRole.ASSISTANT:
        cutoff -= 1

    if cutoff == 0:
        raise InsufficientMessagesForCompactionError()

    return messages[:cutoff]


async def compact_conversation(
    session: AsyncSession,
    model: ChatAnthropic,
    user_id: UUID,
    through_message_id: UUID,
    messages_to_keep: int,
) -> ConversationSummary:
    """生成并保存一份新的对话摘要快照。

    读取摘要素材和写入新快照使用两个短事务。中间调用摘要模型时，
    当前会话不持有 PostgreSQL 事务和连接。
    """

    if session.new or session.dirty or session.deleted:
        raise RuntimeError("生成对话摘要前，数据库会话中不能有未提交的修改")

    current_summary = await session.scalar(
        select(ConversationSummary)
        .where(
            ConversationSummary.user_id == user_id,
            ConversationSummary.through_message_id <= through_message_id,
        )
        .order_by(ConversationSummary.id.desc())
        .limit(1)
    )

    message_query = select(Message).where(
        Message.user_id == user_id,
        Message.status == MessageStatus.COMPLETED,
        Message.id <= through_message_id,
    )

    if current_summary is not None:
        message_query = message_query.where(
            Message.id > current_summary.through_message_id,
        )

    saved_messages = list(
        await session.scalars(
            message_query.order_by(Message.id),
        )
    )

    messages_to_compact = select_messages_to_compact(
        saved_messages,
        messages_to_keep,
    )

    transcript = get_buffer_string(
        [to_summary_message(message) for message in messages_to_compact],
        human_prefix="用户",
        ai_prefix="助手",
    )

    input_parts: list[str] = []

    if current_summary is not None:
        input_parts.append(f"现有摘要：\n{current_summary.content}")

    input_parts.append(f"后续原始对话：\n{transcript}")
    compaction_input = "\n\n".join(input_parts)

    # rollback 会让 ORM 对象过期，所以先复制写入摘要所需的普通值。
    source_summary_id = current_summary.id if current_summary is not None else None
    compacted_through_message_id = messages_to_compact[-1].id

    # 摘要模型可能需要等待较长时间，调用前结束上面的只读事务。
    await session.rollback()

    response = await model.ainvoke(
        [
            SystemMessage(content=SUMMARY_SYSTEM_PROMPT),
            HumanMessage(content=compaction_input),
        ],
        config={
            "metadata": {
                "lc_source": "conversation_compaction",
            }
        },
    )

    summary_content = response.text.strip()

    if not summary_content:
        raise RuntimeError("模型返回了空的对话摘要")

    usage = response.usage_metadata

    if usage is None:
        raise RuntimeError("模型没有返回摘要调用的 token usage")

    summary = ConversationSummary(
        user_id=user_id,
        through_message_id=compacted_through_message_id,
        source_summary_id=source_summary_id,
        content=summary_content,
        model=model.model,
        prompt_version=SUMMARY_PROMPT_VERSION,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
    )

    session.add(summary)
    await session.commit()

    return summary
