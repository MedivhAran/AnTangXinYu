import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    ToolMessage,
)
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api import chat
from antang_api.agents.core import CoreAgentGraph
from antang_api.agents.runtime import CoreAgentContext
from antang_api.chat import (
    ActiveAgentRunError,
    DuplicateClientMessageError,
    extract_agent_result,
    prepare_chat_run,
    stream_chat_run,
)
from antang_api.context.builder import ChatContext
from antang_api.context.preparation import PreparedChatContext
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
    Message,
    MessageStatus,
    User,
)
from antang_api.schemas.chat import (
    AgentActivityEvent,
    MessageCompletedEvent,
    TextDeltaEvent,
)


class FakeAgent:
    """按顺序返回指定 LangGraph v2 流事件。"""

    def __init__(
        self,
        events: list[dict[str, Any]],
        error: BaseException | None = None,
    ) -> None:
        self.events = events
        self.error = error
        self.config: dict[str, Any] | None = None
        self.context: CoreAgentContext | None = None

    async def astream(
        self,
        _input: dict[str, Any],
        *,
        config: dict[str, Any],
        context: CoreAgentContext,
        stream_mode: list[str],
        version: str,
    ) -> AsyncIterator[dict[str, Any]]:
        assert stream_mode == ["messages", "custom", "values"]
        assert version == "v2"
        self.config = config
        self.context = context

        for event in self.events:
            yield event

        if self.error is not None:
            raise self.error


async def create_user(db_session: AsyncSession, prefix: str) -> User:
    username = f"{prefix}_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    return user


def prepared_context(message: str = "你好") -> PreparedChatContext:
    return PreparedChatContext(
        context=ChatContext(
            messages=(HumanMessage(content=message),),
            summary_id=None,
            summary_through_message_id=None,
        ),
        input_tokens=12,
        was_compacted=False,
        cleared_tool_result_count=0,
    )


def message_event(text: str, step: int = 0) -> dict[str, Any]:
    return {
        "type": "messages",
        "data": (
            AIMessageChunk(content=text),
            {"langgraph_node": "model", "langgraph_step": step},
        ),
    }


def tool_call_event(text: str = "") -> dict[str, Any]:
    return {
        "type": "messages",
        "data": (
            AIMessageChunk(
                content=text,
                tool_call_chunks=[
                    {
                        "name": "web_search",
                        "args": '{"query":"低血糖"}',
                        "id": "call-1",
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                ],
            ),
            {"langgraph_node": "model", "langgraph_step": 0},
        ),
    }


def tool_message_event() -> dict[str, Any]:
    return {
        "type": "messages",
        "data": (
            ToolMessage(
                content='{"results":[]}',
                tool_call_id="call-1",
                name="web_search",
            ),
            {"langgraph_node": "tools", "langgraph_step": 1},
        ),
    }


def tool_activity_event(
    status: str,
    *,
    tool_name: str = "web_search",
    tool_call_id: str = "call-1",
) -> dict[str, Any]:
    return {
        "type": "custom",
        "data": {
            "event": "tool_activity",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": status,
        },
    }


def thinking_event(secret: str, step: int = 0) -> dict[str, Any]:
    return {
        "type": "messages",
        "data": (
            AIMessageChunk(
                content=[{"type": "thinking", "thinking": secret, "index": 0}]
            ),
            {"langgraph_node": "model", "langgraph_step": step},
        ),
    }


def values_event(content: str) -> dict[str, Any]:
    final_message = AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": 21,
            "output_tokens": 3,
            "total_tokens": 24,
        },
    )
    return {
        "type": "values",
        "data": {
            "messages": [HumanMessage(content="你好"), final_message],
        },
    }


def tool_values_event(content: str, preamble: str = "") -> dict[str, Any]:
    tool_call_message = AIMessage(
        content=preamble,
        tool_calls=[
            {
                "name": "web_search",
                "args": {"query": "低血糖"},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
        usage_metadata={
            "input_tokens": 20,
            "output_tokens": 4,
            "total_tokens": 24,
        },
    )
    tool_result = ToolMessage(
        content='{"results":[]}',
        tool_call_id="call-1",
        name="web_search",
    )
    final_message = AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": 30,
            "output_tokens": 6,
            "total_tokens": 36,
        },
    )
    return {
        "type": "values",
        "data": {
            "messages": [
                HumanMessage(content="你好"),
                tool_call_message,
                tool_result,
                final_message,
            ],
        },
    }


def fetch_values_event(content: str) -> dict[str, Any]:
    tool_call_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "web_fetch",
                "args": {"url": "https://example.com/source", "query": "结论"},
                "id": "fetch-call",
                "type": "tool_call",
            }
        ],
        usage_metadata={
            "input_tokens": 20,
            "output_tokens": 4,
            "total_tokens": 24,
        },
    )
    tool_result = ToolMessage(
        content=json.dumps(
            {
                "source_id": "S1",
                "title": "资料页",
                "url": "https://example.com/source",
                "content": "资料正文",
            },
            ensure_ascii=False,
        ),
        tool_call_id="fetch-call",
        name="web_fetch",
    )
    final_message = AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": 30,
            "output_tokens": 6,
            "total_tokens": 36,
        },
    )
    return {
        "type": "values",
        "data": {
            "messages": [
                HumanMessage(content="你好"),
                tool_call_message,
                tool_result,
                final_message,
            ],
        },
    }


async def test_prepare_chat_run_rejects_duplicate_and_active_run(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "prepare")
    user_id = user.id
    client_message_id = uuid4()

    await prepare_chat_run(
        db_session,
        user_id,
        client_message_id,
        "第一条消息",
    )

    with pytest.raises(DuplicateClientMessageError):
        await prepare_chat_run(
            db_session,
            user_id,
            client_message_id,
            "重复消息",
        )

    with pytest.raises(ActiveAgentRunError):
        await prepare_chat_run(
            db_session,
            user_id,
            uuid4(),
            "并发消息",
        )


def test_extract_agent_result_requires_complete_usage() -> None:
    first = AIMessage(
        content="中间消息",
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
        },
    )
    final = AIMessage(
        content="最终回答",
        usage_metadata={
            "input_tokens": 12,
            "output_tokens": 4,
            "total_tokens": 16,
        },
    )

    result = extract_agent_result(
        {"messages": [HumanMessage(content="问题"), first, final]},
        input_message_count=1,
    )

    assert result[0] == "中间消息\n\n最终回答"
    assert [turn.content for turn in result[1]] == ["中间消息", "最终回答"]
    assert result[2:] == (22, 6)

    with pytest.raises(RuntimeError, match="usage_metadata"):
        extract_agent_result(
            {
                "messages": [
                    HumanMessage(content="问题"),
                    AIMessage(content="缺少用量"),
                ]
            },
            input_message_count=1,
        )

    with pytest.raises(RuntimeError, match="空回答"):
        extract_agent_result(
            {
                "messages": [
                    HumanMessage(content="问题"),
                    AIMessage(
                        content="   ",
                        usage_metadata={
                            "input_tokens": 10,
                            "output_tokens": 1,
                            "total_tokens": 11,
                        },
                    ),
                ]
            },
            input_message_count=1,
        )


async def test_stream_chat_run_completes_and_persists_result(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "stream_success")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "你好",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent(
        [
            message_event("收到"),
            message_event("。"),
            values_event("收到。"),
        ]
    )

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("ChatAnthropic", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert [event.type for event in events] == [
        "message_started",
        "agent_activity",
        "text_delta",
        "text_delta",
        "message_completed",
    ]
    assert fake_agent.config is not None
    assert fake_agent.config["configurable"]["thread_id"] == str(prepared_run.run_id)
    assert fake_agent.config["metadata"] == {
        "run_id": str(prepared_run.run_id),
        "user_id": str(user.id),
    }
    assert fake_agent.context == CoreAgentContext(
        user_id=user.id,
        run_id=prepared_run.run_id,
        input_message_count=1,
    )

    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.status == MessageStatus.COMPLETED
    assert message.content == "收到。"
    assert run is not None
    assert run.status == AgentRunStatus.COMPLETED
    assert run.input_tokens == 21
    assert run.output_tokens == 3


async def test_stream_chat_run_validates_and_persists_cited_sources(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "stream_sources")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "帮我查资料",
    )
    db_session.add_all(
        [
            AgentToolCall(
                agent_run_id=prepared_run.run_id,
                tool_call_id="search-call",
                tool_name="web_search",
                model_turn_index=1,
                tool_call_index=1,
                arguments={"query": "测试资料"},
                result=json.dumps(
                    {
                        "results": [
                            {
                                "title": "资料页",
                                "url": "https://example.com/source",
                                "snippet": "摘要",
                                "score": 0.95,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                status=AgentToolCallStatus.COMPLETED,
            ),
            AgentToolCall(
                agent_run_id=prepared_run.run_id,
                tool_call_id="fetch-call",
                tool_name="web_fetch",
                model_turn_index=2,
                tool_call_index=1,
                arguments={"url": "https://example.com/source", "query": "结论"},
                result=json.dumps(
                    {
                        "source_id": "S1",
                        "title": "资料页",
                        "url": "https://example.com/source",
                        "content": "资料正文",
                    },
                    ensure_ascii=False,
                ),
                status=AgentToolCallStatus.COMPLETED,
            ),
        ]
    )
    await db_session.commit()
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context("帮我查资料")),
    )
    answer = "这个结论来自刚才读取的网页。[S1]"
    fake_agent = FakeAgent([message_event(answer), fetch_values_event(answer)])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("ChatAnthropic", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    completed = cast("MessageCompletedEvent", events[-1])
    assert [source.model_dump() for source in completed.sources] == [
        {
            "source_id": "S1",
            "title": "资料页",
            "url": "https://example.com/source",
        }
    ]
    db_session.expire_all()
    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.sources == [
        {
            "source_id": "S1",
            "title": "资料页",
            "url": "https://example.com/source",
        }
    ]
    assert message.status == MessageStatus.COMPLETED
    assert run is not None
    assert run.status == AgentRunStatus.COMPLETED


async def test_stream_chat_run_rejects_uncited_fetch_and_keeps_sources_empty(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "stream_uncited")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "读取网页",
    )
    db_session.add(
        AgentToolCall(
            agent_run_id=prepared_run.run_id,
            tool_call_id="fetch-call",
            tool_name="web_fetch",
            model_turn_index=1,
            tool_call_index=1,
            arguments={"url": "https://example.com/source", "query": "结论"},
            result=json.dumps(
                {
                    "source_id": "S1",
                    "title": "资料页",
                    "url": "https://example.com/source",
                    "content": "资料正文",
                },
                ensure_ascii=False,
            ),
            status=AgentToolCallStatus.COMPLETED,
        )
    )
    await db_session.commit()
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context("读取网页")),
    )
    answer = "这段回答忘记引用来源。"
    fake_agent = FakeAgent([message_event(answer), fetch_values_event(answer)])

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("ChatAnthropic", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert events[-1].type == "message_failed"
    db_session.expire_all()
    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.status == MessageStatus.FAILED
    assert message.sources == []
    assert run is not None
    assert run.status == AgentRunStatus.FAILED
    assert run.error_type == "CitationValidationError"


async def test_stream_chat_run_streams_tool_preamble_and_activity(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工具前说明和最终回答都可见，工具细节只转成活动阶段。"""

    user = await create_user(db_session, "stream_tool_loop")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "帮我查一下",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent(
        [
            tool_call_event("我先帮你查一下。"),
            tool_activity_event("started"),
            tool_message_event(),
            tool_activity_event("completed"),
            thinking_event("不应暴露的内部推理", step=2),
            message_event("查到了。", step=2),
            tool_values_event("查到了。", preamble="我先帮你查一下。"),
        ]
    )

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("ChatAnthropic", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert [event.type for event in events] == [
        "message_started",
        "agent_activity",
        "text_delta",
        "agent_activity",
        "agent_activity",
        "text_delta",
        "text_delta",
        "message_completed",
    ]
    activities = [
        cast("AgentActivityEvent", event).phase
        for event in events
        if event.type == "agent_activity"
    ]
    assert activities == ["thinking", "searching", "organizing"]
    deltas = [
        cast("TextDeltaEvent", event).delta
        for event in events
        if event.type == "text_delta"
    ]
    assert deltas == ["我先帮你查一下。", "\n\n", "查到了。"]
    assert "不应暴露的内部推理" not in str(events)
    completed = cast("MessageCompletedEvent", events[-1])
    assert completed.input_tokens == 50
    assert completed.output_tokens == 10

    message = await db_session.get(Message, prepared_run.assistant_message_id)
    assert message is not None
    assert message.content == "我先帮你查一下。\n\n查到了。"


async def test_stream_chat_run_accepts_text_and_tool_call_in_same_chunk(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic 协议允许一个片段同时包含普通文字和 tool_use。"""

    user = await create_user(db_session, "stream_mixed_tool")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "帮我查一下",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent(
        [
            tool_call_event("我先查一下。"),
            tool_activity_event("started"),
            tool_activity_event("completed"),
            message_event("结果在这里。", step=2),
            tool_values_event("结果在这里。", preamble="我先查一下。"),
        ]
    )

    events = [
        event
        async for event in stream_chat_run(
            session=db_session,
            model=cast("ChatAnthropic", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            user_id=user.id,
            prepared_run=prepared_run,
        )
    ]

    assert [event.type for event in events] == [
        "message_started",
        "agent_activity",
        "text_delta",
        "agent_activity",
        "agent_activity",
        "text_delta",
        "text_delta",
        "message_completed",
    ]
    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.content == "我先查一下。\n\n结果在这里。"
    assert message.status == MessageStatus.COMPLETED
    assert run is not None
    assert run.status == AgentRunStatus.COMPLETED


async def test_stream_chat_run_marks_mismatched_output_failed_without_logging_error_text(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "stream_failed")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "包含敏感正文的用户消息",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("部分文字"), values_event("不同的最终文字")])
    running_tool = AgentToolCall(
        agent_run_id=prepared_run.run_id,
        tool_call_id="running-before-failure",
        tool_name="web_search",
        model_turn_index=1,
        tool_call_index=1,
        arguments={"query": "测试"},
        status=AgentToolCallStatus.RUNNING,
    )
    db_session.add(running_tool)
    await db_session.commit()
    captured_logs: list[str] = []
    sink_id = logger.add(
        captured_logs.append,
        format="{message}|{extra}",
        diagnose=False,
        catch=False,
    )

    try:
        events = [
            event
            async for event in stream_chat_run(
                session=db_session,
                model=cast("ChatAnthropic", object()),
                agent=cast("CoreAgentGraph", fake_agent),
                tools=(),
                user_id=user.id,
                prepared_run=prepared_run,
            )
        ]
    finally:
        logger.remove(sink_id)

    assert events[-1].type == "message_failed"
    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.status == MessageStatus.FAILED
    assert message.content == "部分文字"
    assert run is not None
    assert run.status == AgentRunStatus.FAILED
    assert run.error_type == "RuntimeError"
    await db_session.refresh(running_tool)
    assert running_tool.status == AgentToolCallStatus.FAILED
    assert running_tool.error_type == "RuntimeError"
    assert running_tool.finished_at is not None

    logs = "".join(captured_logs)
    assert "包含敏感正文的用户消息" not in logs
    assert "部分文字" not in logs
    assert "不同的最终文字" not in logs


async def test_stream_chat_run_marks_client_cancellation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(db_session, "stream_cancelled")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "测试取消",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent(
        [message_event("已经生成")],
        error=asyncio.CancelledError(),
    )
    running_tool = AgentToolCall(
        agent_run_id=prepared_run.run_id,
        tool_call_id="running-before-cancel",
        tool_name="web_search",
        model_turn_index=1,
        tool_call_index=1,
        arguments={"query": "测试"},
        status=AgentToolCallStatus.RUNNING,
    )
    db_session.add(running_tool)
    await db_session.commit()

    with pytest.raises(asyncio.CancelledError):
        _ = [
            event
            async for event in stream_chat_run(
                session=db_session,
                model=cast("ChatAnthropic", object()),
                agent=cast("CoreAgentGraph", fake_agent),
                tools=(),
                user_id=user.id,
                prepared_run=prepared_run,
            )
        ]

    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.status == MessageStatus.CANCELLED
    assert message.content == "已经生成"
    assert run is not None
    assert run.status == AgentRunStatus.CANCELLED
    await db_session.refresh(running_tool)
    assert running_tool.status == AgentToolCallStatus.CANCELLED
    assert running_tool.error_type == "CancelledError"
    assert running_tool.finished_at is not None


async def test_stream_chat_run_marks_closed_generator_cancelled(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """StreamingResponse 主动关闭生成器时也要保存取消状态。"""

    user = await create_user(db_session, "stream_closed")
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "测试关闭响应流",
    )
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(return_value=prepared_context()),
    )
    fake_agent = FakeAgent([message_event("已经发送的部分")])
    stream = cast(
        "AsyncGenerator[Any, None]",
        stream_chat_run(
            session=db_session,
            model=cast("ChatAnthropic", object()),
            agent=cast("CoreAgentGraph", fake_agent),
            tools=(),
            user_id=user.id,
            prepared_run=prepared_run,
        ),
    )

    assert (await anext(stream)).type == "message_started"
    assert (await anext(stream)).type == "agent_activity"
    assert (await anext(stream)).type == "text_delta"
    await stream.aclose()

    message = await db_session.get(Message, prepared_run.assistant_message_id)
    run = await db_session.get(AgentRun, prepared_run.run_id)
    assert message is not None
    assert message.status == MessageStatus.CANCELLED
    assert message.content == "已经发送的部分"
    assert run is not None
    assert run.status == AgentRunStatus.CANCELLED
