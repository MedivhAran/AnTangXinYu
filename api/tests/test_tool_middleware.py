import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Self, cast
from uuid import UUID, uuid4

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolCall
from langchain_core.messages.tool import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.types import Command
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
)

from antang_api.agents.core import build_core_agent
from antang_api.agents.runtime import CoreAgentContext
from antang_api.agents.tool_middleware import (
    ToolExecutionError,
    ToolExecutionLimitError,
    ToolPersistenceMiddleware,
)
from antang_api.chat import PreparedChatRun, prepare_chat_run
from antang_api.models import (
    AgentToolCall,
    AgentToolCallStatus,
    User,
)


class TrackingSessionFactory:
    """测试中确认工具 handler 执行时数据库会话已经关闭。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory
        self.active_sessions = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncSession]:
        self.active_sessions += 1
        try:
            async with self.factory() as session:
                yield session
        finally:
            self.active_sessions -= 1


class ToolLoopFakeModel(FakeMessagesListChatModel):
    """让 create_agent 可以给测试模型绑定工具。"""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Self:
        del tools, tool_choice, kwargs
        return self


@tool("web_search", description="返回合成搜索结果。")
async def fake_web_search(query: str) -> dict[str, str]:
    return {"query": query}


def make_session_factory(
    db_session: AsyncSession,
) -> async_sessionmaker[AsyncSession]:
    bind = db_session.bind
    if not isinstance(bind, AsyncConnection):
        raise TypeError("测试数据库会话必须绑定 AsyncConnection")

    return async_sessionmaker(
        bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


async def make_running_run(
    db_session: AsyncSession,
    prefix: str,
) -> tuple[User, PreparedChatRun]:
    username = f"{prefix}_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "测试工具调用",
    )
    return user, prepared_run


def make_request(
    *,
    context: CoreAgentContext,
    messages: list[BaseMessage],
    tool_call: ToolCall,
    activities: list[object] | None = None,
) -> ToolCallRequest:
    state: dict[str, Any] = {"messages": messages}
    runtime = ToolRuntime(
        state=state,
        context=context,
        config={},
        stream_writer=(
            activities.append if activities is not None else lambda _value: None
        ),
        tool_call_id=tool_call["id"],
        store=None,
        tools=[],
    )
    return ToolCallRequest(
        tool_call=tool_call,
        tool=None,
        state=state,
        runtime=cast(Any, runtime),
    )


def tool_call(call_id: str, query: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="web_search",
        args={"query": query},
        type="tool_call",
    )


async def load_only_tool_call(
    db_session: AsyncSession,
    agent_run_id: UUID,
) -> AgentToolCall:
    """只读取当前测试运行的工具记录，不依赖开发数据库为空。"""

    records = list(
        await db_session.scalars(
            select(AgentToolCall).where(AgentToolCall.agent_run_id == agent_run_id)
        )
    )
    assert len(records) == 1
    return records[0]


async def test_tool_success_uses_short_transactions_and_keeps_parallel_order(
    db_session: AsyncSession,
) -> None:
    user, prepared_run = await make_running_run(db_session, "tool_success")
    first_call = tool_call("call-first", "第一条")
    second_call = tool_call("call-second", "第二条")
    messages: list[BaseMessage] = [
        HumanMessage(content="测试工具调用"),
        AIMessage(content="", tool_calls=[first_call, second_call]),
    ]
    context = CoreAgentContext(
        user_id=user.id,
        run_id=prepared_run.run_id,
        input_message_count=1,
    )
    tracking_factory = TrackingSessionFactory(make_session_factory(db_session))
    middleware = ToolPersistenceMiddleware(
        cast("async_sessionmaker[AsyncSession]", tracking_factory),
        max_tool_rounds=3,
        max_parallel_tool_calls=5,
    )
    request = make_request(
        context=context,
        messages=messages,
        tool_call=second_call,
        activities=(activities := []),
    )
    result_blocks: list[str | dict[str, Any]] = [
        {"type": "text", "text": "搜索结果"},
        "来源二",
    ]

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        assert tracking_factory.active_sessions == 0
        return ToolMessage(
            content=result_blocks,
            tool_call_id="call-second",
        )

    result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert tracking_factory.active_sessions == 0
    db_session.expire_all()
    record = await load_only_tool_call(db_session, prepared_run.run_id)
    assert record.status == AgentToolCallStatus.COMPLETED
    assert record.model_turn_index == 1
    assert record.tool_call_index == 2
    assert record.arguments == {"query": "第二条"}
    assert record.result == result_blocks
    assert record.finished_at is not None
    assert activities == [
        {
            "event": "tool_activity",
            "tool_call_id": "call-second",
            "tool_name": "web_search",
            "status": "started",
        },
        {
            "event": "tool_activity",
            "tool_call_id": "call-second",
            "tool_name": "web_search",
            "status": "completed",
        },
    ]


async def test_create_agent_streams_real_tool_middleware_lifecycle(
    db_session: AsyncSession,
) -> None:
    """内部工具事件确实通过 LangGraph custom 流到达聊天服务。"""

    user, prepared_run = await make_running_run(db_session, "tool_graph_stream")
    model = ToolLoopFakeModel(
        responses=[
            AIMessage(
                content="我先查一下。",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "低血糖"},
                        "id": "graph-call",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="查询完成。"),
        ]
    )
    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=3,
        max_parallel_tool_calls=5,
    )
    agent = build_core_agent(
        model,
        InMemorySaver(),
        tools=(fake_web_search,),
        middleware=(middleware,),
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": str(prepared_run.run_id)}
    }
    context = CoreAgentContext(
        user_id=user.id,
        run_id=prepared_run.run_id,
        input_message_count=1,
    )

    custom_events = [
        cast(dict[str, Any], part["data"])
        async for part in agent.astream(
            {"messages": [HumanMessage(content="帮我查一下")]},
            config=config,
            context=context,
            stream_mode=["custom"],
            version="v2",
        )
    ]

    assert [event["status"] for event in custom_events] == [
        "started",
        "completed",
    ]
    db_session.expire_all()
    record = await load_only_tool_call(db_session, prepared_run.run_id)
    assert record.tool_call_id == "graph-call"
    assert record.status == AgentToolCallStatus.COMPLETED


async def test_tool_exception_is_saved_and_propagated(
    db_session: AsyncSession,
) -> None:
    user, prepared_run = await make_running_run(db_session, "tool_failure")
    call = tool_call("call-failure", "失败")
    context = CoreAgentContext(user.id, prepared_run.run_id, 1)
    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=3,
        max_parallel_tool_calls=5,
    )
    activities: list[object] = []
    request = make_request(
        context=context,
        messages=[
            HumanMessage(content="测试"),
            AIMessage(content="", tool_calls=[call]),
        ],
        tool_call=call,
        activities=activities,
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        raise ValueError("provider failed")

    with pytest.raises(ValueError, match="provider failed"):
        await middleware.awrap_tool_call(request, handler)

    db_session.expire_all()
    record = await load_only_tool_call(db_session, prepared_run.run_id)
    assert record.status == AgentToolCallStatus.FAILED
    assert record.error_type == "ValueError"
    assert record.error_message == "provider failed"
    assert record.result is None
    assert [activity["status"] for activity in cast(list[dict[str, Any]], activities)] == [
        "started",
        "failed",
    ]


async def test_error_tool_message_is_saved_then_fails_the_run(
    db_session: AsyncSession,
) -> None:
    user, prepared_run = await make_running_run(db_session, "tool_error_message")
    call = tool_call("call-error-message", "错误结果")
    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=3,
        max_parallel_tool_calls=5,
    )
    request = make_request(
        context=CoreAgentContext(user.id, prepared_run.run_id, 1),
        messages=[
            HumanMessage(content="测试"),
            AIMessage(content="", tool_calls=[call]),
        ],
        tool_call=call,
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        return ToolMessage(
            content="Tavily rejected the request",
            tool_call_id=call["id"],
            status="error",
        )

    with pytest.raises(ToolExecutionError, match="Tavily rejected"):
        await middleware.awrap_tool_call(request, handler)

    db_session.expire_all()
    record = await load_only_tool_call(db_session, prepared_run.run_id)
    assert record.status == AgentToolCallStatus.FAILED
    assert record.result == "Tavily rejected the request"
    assert record.error_type == "ToolExecutionError"


async def test_tool_invocation_error_is_wrapped_so_tool_node_cannot_recover(
    db_session: AsyncSession,
) -> None:
    class ToolArguments(BaseModel):
        count: int

    user, prepared_run = await make_running_run(db_session, "tool_invocation")
    call = tool_call("call-invocation", "参数错误")
    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=3,
        max_parallel_tool_calls=5,
    )
    request = make_request(
        context=CoreAgentContext(user.id, prepared_run.run_id, 1),
        messages=[
            HumanMessage(content="测试"),
            AIMessage(content="", tool_calls=[call]),
        ],
        tool_call=call,
    )

    try:
        ToolArguments.model_validate({"count": "not-a-number"})
    except ValidationError as validation_error:
        invocation_error = ToolInvocationError(
            "web_search",
            validation_error,
            {"count": "not-a-number"},
        )
    else:
        raise AssertionError("测试参数应触发 ValidationError")

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        raise invocation_error

    with pytest.raises(ToolExecutionError, match="工具调用参数无效"):
        await middleware.awrap_tool_call(request, handler)

    db_session.expire_all()
    record = await load_only_tool_call(db_session, prepared_run.run_id)
    assert record.status == AgentToolCallStatus.FAILED
    assert record.error_type == "ToolExecutionError"


async def test_cancelled_tool_is_saved_and_cancellation_propagates(
    db_session: AsyncSession,
) -> None:
    user, prepared_run = await make_running_run(db_session, "tool_cancel")
    call = tool_call("call-cancel", "取消")
    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=3,
        max_parallel_tool_calls=5,
    )
    request = make_request(
        context=CoreAgentContext(user.id, prepared_run.run_id, 1),
        messages=[
            HumanMessage(content="测试"),
            AIMessage(content="", tool_calls=[call]),
        ],
        tool_call=call,
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await middleware.awrap_tool_call(request, handler)

    db_session.expire_all()
    record = await load_only_tool_call(db_session, prepared_run.run_id)
    assert record.status == AgentToolCallStatus.CANCELLED
    assert record.error_type == "CancelledError"


@pytest.mark.parametrize(
    ("round_count", "parallel_count", "error_text"),
    [
        (4, 1, "最多执行 3 轮"),
        (1, 6, "每轮最多执行 5 个"),
    ],
)
async def test_tool_limits_fail_before_execution(
    db_session: AsyncSession,
    round_count: int,
    parallel_count: int,
    error_text: str,
) -> None:
    user, prepared_run = await make_running_run(db_session, "tool_limit")
    current_calls = [
        tool_call(f"call-limit-{index}", str(index)) for index in range(parallel_count)
    ]
    messages: list[BaseMessage] = [HumanMessage(content="测试")]
    for turn in range(round_count):
        calls = (
            current_calls
            if turn == round_count - 1
            else [tool_call(f"prior-{turn}", str(turn))]
        )
        messages.append(AIMessage(content="", tool_calls=calls))

    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=3,
        max_parallel_tool_calls=5,
    )
    request = make_request(
        context=CoreAgentContext(user.id, prepared_run.run_id, 1),
        messages=messages,
        tool_call=current_calls[0],
    )
    handler_called = False

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="不应执行", tool_call_id=current_calls[0]["id"])

    with pytest.raises(ToolExecutionLimitError, match=error_text):
        await middleware.awrap_tool_call(request, handler)

    assert handler_called is False
    current_run_records = list(
        await db_session.scalars(
            select(AgentToolCall).where(
                AgentToolCall.agent_run_id == prepared_run.run_id
            )
        )
    )
    assert current_run_records == []


def test_runtime_and_limits_reject_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="input_message_count"):
        CoreAgentContext(uuid4(), uuid4(), -1)

    with pytest.raises(ValueError, match="max_tool_rounds"):
        ToolPersistenceMiddleware(
            max_tool_rounds=0,
            max_parallel_tool_calls=5,
        )

    with pytest.raises(ValueError, match="max_parallel_tool_calls"):
        ToolPersistenceMiddleware(
            max_tool_rounds=3,
            max_parallel_tool_calls=0,
        )
