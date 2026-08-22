from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.context.compaction import (
    InsufficientMessagesForCompactionError,
    SUMMARY_PROMPT_VERSION,
    compact_conversation,
    select_messages_to_compact,
)
from antang_api.models import (
    Message,
    MessageRole,
    MessageStatus,
    User,
)


def make_message(role: MessageRole, content: str) -> Message:
    """创建供压缩选择测试使用的消息。"""

    return Message(
        user_id=uuid4(),
        role=role,
        status=MessageStatus.COMPLETED,
        content=content,
    )


def test_select_messages_to_compact_ends_after_assistant() -> None:
    """摘要边界会回退到助手消息之后，近期原文从用户消息开始。"""

    messages = [
        make_message(MessageRole.USER, "用户 1"),
        make_message(MessageRole.ASSISTANT, "助手 1"),
        make_message(MessageRole.USER, "用户 2"),
        make_message(MessageRole.ASSISTANT, "助手 2"),
        make_message(MessageRole.USER, "用户 3"),
        make_message(MessageRole.ASSISTANT, "助手 3"),
        make_message(MessageRole.USER, "用户 4"),
    ]

    selected = select_messages_to_compact(
        messages,
        messages_to_keep=2,
    )

    assert selected == messages[:4]
    assert selected[-1].role == MessageRole.ASSISTANT


def test_select_messages_to_compact_requires_older_complete_turns() -> None:
    """近期消息已经占满保留数量时会明确报告无法压缩。"""

    messages = [
        make_message(MessageRole.USER, "用户消息"),
        make_message(MessageRole.ASSISTANT, "助手消息"),
    ]

    with pytest.raises(InsufficientMessagesForCompactionError):
        select_messages_to_compact(
            messages,
            messages_to_keep=2,
        )


async def test_compaction_snapshots_form_a_chain(
    db_session: AsyncSession,
) -> None:
    """连续压缩会保存边界、token usage，并关联上一份摘要。"""

    username = f"compaction_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    user_id = user.id

    messages: list[Message] = []
    for number, role in enumerate(
        [
            MessageRole.USER,
            MessageRole.ASSISTANT,
            MessageRole.USER,
            MessageRole.ASSISTANT,
            MessageRole.USER,
            MessageRole.ASSISTANT,
            MessageRole.USER,
        ],
        start=1,
    ):
        message = Message(
            user_id=user_id,
            role=role,
            status=MessageStatus.COMPLETED,
            content=(
                "第一阶段消息 2。[S1]" if number == 2 else f"第一阶段消息 {number}"
            ),
            sources=(
                [
                    {
                        "source_id": "S1",
                        "title": "测试来源",
                        "url": "https://example.com/source",
                    }
                ]
                if number == 2
                else []
            ),
        )
        db_session.add(message)
        await db_session.flush()
        messages.append(message)

    await db_session.commit()
    message_ids = [message.id for message in messages]

    first_response = AIMessage(
        content="第一份连续性摘要",
        usage_metadata={
            "input_tokens": 120,
            "output_tokens": 24,
            "total_tokens": 144,
        },
    )
    second_response = AIMessage(
        content="第二份连续性摘要",
        usage_metadata={
            "input_tokens": 160,
            "output_tokens": 30,
            "total_tokens": 190,
        },
    )
    responses = [first_response, second_response]
    compaction_inputs: list[str] = []

    async def invoke_without_open_transaction(
        *_args: object,
        **_kwargs: object,
    ) -> AIMessage:
        """摘要模型执行期间，业务数据库事务必须已经结束。"""

        assert db_session.in_transaction() is False
        messages = cast("list[object]", _args[0])
        assert isinstance(messages[-1], HumanMessage)
        compaction_inputs.append(messages[-1].text)
        return responses.pop(0)

    fake_model = cast(
        "BaseChatModel",
        SimpleNamespace(
            model="deepseek-v4-pro",
            ainvoke=AsyncMock(side_effect=invoke_without_open_transaction),
        ),
    )

    first_summary = await compact_conversation(
        db_session,
        fake_model,
        user_id,
        message_ids[-1],
        messages_to_keep=2,
    )

    assert first_summary.source_summary_id is None
    assert first_summary.through_message_id == message_ids[3]
    assert first_summary.content == "第一份连续性摘要"
    assert first_summary.input_tokens == 120
    assert first_summary.output_tokens == 24
    assert "第一阶段消息 2。" in compaction_inputs[0]
    assert "[S1]" not in compaction_inputs[0]
    first_summary_id = first_summary.id

    later_messages: list[Message] = []
    for number, role in enumerate(
        [
            MessageRole.ASSISTANT,
            MessageRole.USER,
            MessageRole.ASSISTANT,
            MessageRole.USER,
        ],
        start=1,
    ):
        message = Message(
            user_id=user_id,
            role=role,
            status=MessageStatus.COMPLETED,
            content=f"第二阶段消息 {number}",
        )
        db_session.add(message)
        await db_session.flush()
        later_messages.append(message)

    await db_session.commit()
    later_message_ids = [message.id for message in later_messages]

    second_summary = await compact_conversation(
        db_session,
        fake_model,
        user_id,
        later_message_ids[-1],
        messages_to_keep=2,
    )

    assert second_summary.source_summary_id == first_summary_id
    assert second_summary.through_message_id == later_message_ids[0]
    assert second_summary.content == "第二份连续性摘要"
    assert second_summary.input_tokens == 160
    assert second_summary.output_tokens == 30


async def test_compaction_preserves_proactive_question_and_short_reply(
    db_session: AsyncSession,
) -> None:
    """摘要模型能看到主动开场、连续助手消息和简短回复的关系。"""

    username = f"proactive_compact_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    messages = [
        Message(
            user_id=user.id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="你昨天说晚上容易害怕低血糖，今天还会吗？",
        ),
        Message(
            user_id=user.id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="不用详细说，告诉我大概感受就好。",
        ),
        Message(
            user_id=user.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="对。",
        ),
        Message(
            user_id=user.id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="明白了，你今天还在担心。",
        ),
        Message(
            user_id=user.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="我再说说昨晚的情况。",
        ),
    ]
    db_session.add_all(messages)
    await db_session.commit()
    through_message_id = messages[-1].id
    expected_summary_boundary = messages[3].id

    async def inspect_prompt(
        model_messages: list[object],
        **_kwargs: object,
    ) -> AIMessage:
        system_message, input_message = model_messages
        assert isinstance(system_message, SystemMessage)
        assert "主动发起" in system_message.text
        assert "简短回复" in system_message.text
        assert isinstance(input_message, HumanMessage)
        assert input_message.text.index("助手: 你昨天") < input_message.text.index(
            "助手: 不用详细说"
        )
        assert input_message.text.index("助手: 不用详细说") < input_message.text.index(
            "用户: 对。"
        )
        return AIMessage(
            content="助手主动询问昨晚的低血糖担心，用户简短确认今天仍在担心。",
            usage_metadata={
                "input_tokens": 80,
                "output_tokens": 20,
                "total_tokens": 100,
            },
        )

    fake_model = cast(
        "BaseChatModel",
        SimpleNamespace(
            model="deepseek-v4-pro",
            ainvoke=AsyncMock(side_effect=inspect_prompt),
        ),
    )

    summary = await compact_conversation(
        db_session,
        fake_model,
        user.id,
        through_message_id,
        messages_to_keep=1,
    )

    assert SUMMARY_PROMPT_VERSION == "v2"
    assert summary.prompt_version == "v2"
    assert summary.through_message_id == expected_summary_boundary
