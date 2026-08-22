from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.context.builder import ChatContext
from antang_api.context.preparation import (
    ContextBudgetExceededError,
    prepare_chat_context,
)
from antang_api.context.tool_results import CLEARED_TOOL_RESULT_CONTENT
from antang_api.context import preparation


def make_context(content: str) -> ChatContext:
    """创建供自动压缩编排测试使用的上下文。"""

    return ChatContext(
        messages=(HumanMessage(content=content),),
        summary_id=None,
        summary_through_message_id=None,
    )


async def test_prepare_chat_context_releases_read_transaction_before_counting(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外部 token 计数执行期间不能占用 PostgreSQL 事务。"""

    context = make_context("当前消息")

    async def build_context_with_database_read(*_args: object) -> ChatContext:
        await db_session.execute(select(1))
        assert db_session.in_transaction() is True
        return context

    async def count_without_open_transaction(*_args: object) -> int:
        assert db_session.in_transaction() is False
        return 100

    monkeypatch.setattr(
        preparation,
        "build_chat_context",
        build_context_with_database_read,
    )
    monkeypatch.setattr(
        preparation,
        "count_input_tokens",
        count_without_open_transaction,
    )
    monkeypatch.setattr(preparation, "compact_conversation", AsyncMock())

    result = await prepare_chat_context(
        db_session,
        cast("BaseChatModel", object()),
        "系统提示词",
        uuid4(),
        uuid4(),
        compaction_trigger_tokens=150,
        recent_messages_to_keep=20,
        tool_result_cleanup_trigger_tokens=120,
        recent_tool_results_to_keep=3,
    )

    assert result.was_compacted is False
    assert db_session.in_transaction() is False


async def test_prepare_chat_context_returns_without_compaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """输入低于阈值时直接返回当前上下文。"""

    context = make_context("当前消息")
    build_context = AsyncMock(return_value=context)
    count_tokens = AsyncMock(return_value=100)
    compact = AsyncMock()
    session = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(preparation, "build_chat_context", build_context)
    monkeypatch.setattr(preparation, "count_input_tokens", count_tokens)
    monkeypatch.setattr(preparation, "compact_conversation", compact)

    result = await prepare_chat_context(
        session,
        cast("BaseChatModel", object()),
        "系统提示词",
        uuid4(),
        uuid4(),
        compaction_trigger_tokens=150,
        recent_messages_to_keep=20,
        tool_result_cleanup_trigger_tokens=120,
        recent_tool_results_to_keep=3,
    )

    assert result.context is context
    assert result.input_tokens == 100
    assert result.was_compacted is False
    session.rollback.assert_awaited_once()
    compact.assert_not_awaited()


async def test_prepare_chat_context_compacts_and_rebuilds_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """输入达到阈值时压缩一次，并返回重新组装的上下文。"""

    original_context = make_context("压缩前")
    rebuilt_context = make_context("压缩后")
    build_context = AsyncMock(side_effect=[original_context, rebuilt_context])
    count_tokens = AsyncMock(side_effect=[150, 150, 80])
    compact = AsyncMock()
    session = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(preparation, "build_chat_context", build_context)
    monkeypatch.setattr(preparation, "count_input_tokens", count_tokens)
    monkeypatch.setattr(preparation, "compact_conversation", compact)

    result = await prepare_chat_context(
        session,
        cast("BaseChatModel", object()),
        "系统提示词",
        uuid4(),
        uuid4(),
        compaction_trigger_tokens=150,
        recent_messages_to_keep=20,
        tool_result_cleanup_trigger_tokens=120,
        recent_tool_results_to_keep=3,
    )

    assert result.context is rebuilt_context
    assert result.input_tokens == 80
    assert result.was_compacted is True
    assert session.rollback.await_count == 2
    compact.assert_awaited_once()
    assert build_context.await_count == 2
    assert count_tokens.await_count == 3


async def test_prepare_chat_context_raises_when_rebuilt_context_is_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重建后的上下文达到阈值时暴露 token 预算错误。"""

    build_context = AsyncMock(
        side_effect=[make_context("压缩前"), make_context("压缩后")]
    )
    count_tokens = AsyncMock(side_effect=[150, 150, 160, 160])
    compact = AsyncMock()
    session = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(preparation, "build_chat_context", build_context)
    monkeypatch.setattr(preparation, "count_input_tokens", count_tokens)
    monkeypatch.setattr(preparation, "compact_conversation", compact)

    with pytest.raises(ContextBudgetExceededError) as error_info:
        await prepare_chat_context(
            session,
            cast("BaseChatModel", object()),
            "系统提示词",
            uuid4(),
            uuid4(),
            compaction_trigger_tokens=150,
            recent_messages_to_keep=20,
            tool_result_cleanup_trigger_tokens=120,
            recent_tool_results_to_keep=3,
        )

    assert error_info.value.input_tokens == 160
    assert error_info.value.token_limit == 150
    assert session.rollback.await_count == 2
    compact.assert_awaited_once()


def make_tool_context(result_count: int) -> ChatContext:
    """创建调用参数和工具结果成对存在的模型上下文。"""

    messages = []
    for index in range(result_count):
        tool_call_id = f"call-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "web_search",
                            "args": {"query": f"问题 {index}"},
                            "id": tool_call_id,
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content=f"结果 {index}",
                    tool_call_id=tool_call_id,
                    name="web_search",
                ),
            ]
        )

    messages.append(HumanMessage(content="当前消息"))
    return ChatContext(
        messages=tuple(messages),
        summary_id=None,
        summary_through_message_id=None,
    )


async def test_prepare_chat_context_clears_old_tool_results_before_compaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """达到工具阈值时只保留最近三个真实结果，清理后不足摘要阈值便返回。"""

    original_context = make_tool_context(5)
    build_context = AsyncMock(return_value=original_context)
    count_tokens = AsyncMock(side_effect=[110, 90])
    compact = AsyncMock()
    session = AsyncMock(spec=AsyncSession)
    tools = [cast("BaseTool", object())]
    monkeypatch.setattr(preparation, "build_chat_context", build_context)
    monkeypatch.setattr(preparation, "count_input_tokens", count_tokens)
    monkeypatch.setattr(preparation, "compact_conversation", compact)

    result = await prepare_chat_context(
        session,
        cast("BaseChatModel", object()),
        "系统提示词",
        uuid4(),
        uuid4(),
        compaction_trigger_tokens=150,
        recent_messages_to_keep=20,
        tool_result_cleanup_trigger_tokens=100,
        recent_tool_results_to_keep=3,
        tools=tools,
    )

    tool_results = [
        message
        for message in result.context.messages
        if isinstance(message, ToolMessage)
    ]
    assert [message.content for message in tool_results] == [
        CLEARED_TOOL_RESULT_CONTENT,
        CLEARED_TOOL_RESULT_CONTENT,
        "结果 2",
        "结果 3",
        "结果 4",
    ]
    first_call = cast(AIMessage, result.context.messages[0]).tool_calls[0]
    assert first_call["args"] == {"query": "问题 0"}
    original_first_result = cast(ToolMessage, original_context.messages[1])
    assert original_first_result.content == "结果 0"
    assert result.input_tokens == 90
    assert result.was_compacted is False
    assert result.cleared_tool_result_count == 2
    assert count_tokens.await_count == 2
    assert all(call.args[3] is tools for call in count_tokens.await_args_list)
    compact.assert_not_awaited()


async def test_prepare_chat_context_clears_rebuilt_context_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """摘要重建后重新判断工具阈值，最终统计只描述实际返回的上下文。"""

    original_context = make_tool_context(6)
    rebuilt_context = make_tool_context(4)
    build_context = AsyncMock(side_effect=[original_context, rebuilt_context])
    count_tokens = AsyncMock(side_effect=[160, 155, 120, 95])
    compact = AsyncMock()
    session = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(preparation, "build_chat_context", build_context)
    monkeypatch.setattr(preparation, "count_input_tokens", count_tokens)
    monkeypatch.setattr(preparation, "compact_conversation", compact)

    result = await prepare_chat_context(
        session,
        cast("BaseChatModel", object()),
        "系统提示词",
        uuid4(),
        uuid4(),
        compaction_trigger_tokens=150,
        recent_messages_to_keep=20,
        tool_result_cleanup_trigger_tokens=100,
        recent_tool_results_to_keep=3,
    )

    assert result.was_compacted is True
    assert result.input_tokens == 95
    assert result.cleared_tool_result_count == 1
    assert count_tokens.await_count == 4
    compact.assert_awaited_once()
