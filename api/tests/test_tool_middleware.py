import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Self, cast
from uuid import UUID, uuid4

import pytest
from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import (
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    ToolCall,
)
from langchain_core.messages.tool import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
)

from antang_api.agents.core import SYSTEM_PROMPT, build_core_agent
from antang_api.agents.runtime import AgentContext, ToolResponseError
from antang_api.agents.tool_middleware import (
    ToolExecutionError,
    ToolExecutionLimitError,
    ToolPersistenceMiddleware,
)
from antang_api.chat import PreparedChatRun, prepare_chat_run
from antang_api.models import (
    AgentToolCall,
    AgentToolCallStatus,
    ProactiveCareSettings,
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


@tool("web_fetch", description="返回合成网页内容。")
async def fake_web_fetch(url: str, query: str) -> dict[str, str]:
    return {"url": url, "query": query}


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
    db_session.add(ProactiveCareSettings(user_id=user.id))
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
    context: AgentContext,
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


def completed_tool_rounds(count: int) -> list[AnyMessage]:
    messages: list[AnyMessage] = [HumanMessage(content="测试工具轮次")]
    for index in range(1, count + 1):
        call_id = f"completed-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "web_fetch",
                            "args": {
                                "url": "https://example.com",
                                "query": "测试",
                            },
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content="合成结果",
                    tool_call_id=call_id,
                    name="web_fetch",
                ),
            ]
        )
    return messages


@pytest.mark.parametrize(
    ("completed_round_count", "expected_tools", "expected_tool_choice"),
    [
        (0, ["web_search", "web_fetch"], None),
        (9, ["web_fetch"], None),
        (10, ["web_search", "web_fetch"], "none"),
    ],
)
async def test_model_only_sees_tools_valid_for_remaining_rounds(
    db_session: AsyncSession,
    completed_round_count: int,
    expected_tools: list[str],
    expected_tool_choice: object,
) -> None:
    messages = completed_tool_rounds(completed_round_count)
    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=10,
        max_parallel_tool_calls=5,
    )
    context = AgentContext(
        user_id=uuid4(),
        run_id=uuid4(),
        input_message_count=1,
    )
    runtime: Runtime[AgentContext | None] = Runtime(context=context)
    request: ModelRequest[AgentContext | None] = ModelRequest(
        model=ToolLoopFakeModel(responses=[AIMessage(content="完成")]),
        messages=messages,
        tools=[fake_web_search, fake_web_fetch],
        state=cast("AgentState[Any]", {"messages": messages}),
        runtime=runtime,
    )
    visible_tools: list[str] = []
    visible_tool_choice: object = None

    async def handler(
        modified_request: ModelRequest[AgentContext | None],
    ) -> ModelResponse[Any]:
        nonlocal visible_tool_choice
        visible_tools.extend(
            tool["name"] if isinstance(tool, dict) else tool.name
            for tool in modified_request.tools
        )
        visible_tool_choice = modified_request.tool_choice
        return ModelResponse(result=[AIMessage(content="完成")])

    await middleware.awrap_model_call(request, handler)

    assert visible_tools == expected_tools
    assert visible_tool_choice == expected_tool_choice


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
    context = AgentContext(
        user_id=user.id,
        run_id=prepared_run.run_id,
        input_message_count=1,
        rendered_system_prompt=SYSTEM_PROMPT,
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
    assert record.provider_metadata is None
    assert record.finished_at is not None
    assert record.finished_at >= record.started_at
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


async def test_tool_artifact_metadata_is_saved_separately_from_model_content(
    db_session: AsyncSession,
) -> None:
    user, prepared_run = await make_running_run(db_session, "tool_metadata")
    call = tool_call("call-metadata", "元数据")
    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=3,
        max_parallel_tool_calls=5,
    )
    request = make_request(
        context=AgentContext(user.id, prepared_run.run_id, 1),
        messages=[
            HumanMessage(content="测试"),
            AIMessage(content="", tool_calls=[call]),
        ],
        tool_call=call,
    )
    model_content = '{"results":[{"title":"标题"}]}'
    provider_metadata = {
        "request_id": "request-123",
        "response_time": 0.42,
        "usage": {"credits": 2},
    }

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        return ToolMessage(
            content=model_content,
            artifact={"provider_metadata": provider_metadata},
            tool_call_id=call["id"],
        )

    result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.content == model_content
    assert "request-123" not in result.text
    db_session.expire_all()
    record = await load_only_tool_call(db_session, prepared_run.run_id)
    assert record.status == AgentToolCallStatus.COMPLETED
    assert record.result == model_content
    assert record.provider_metadata == provider_metadata


async def test_invalid_tool_artifact_fails_once_and_is_not_persisted(
    db_session: AsyncSession,
) -> None:
    user, prepared_run = await make_running_run(db_session, "tool_bad_metadata")
    call = tool_call("call-bad-metadata", "坏元数据")
    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=3,
        max_parallel_tool_calls=5,
    )
    request = make_request(
        context=AgentContext(user.id, prepared_run.run_id, 1),
        messages=[
            HumanMessage(content="测试"),
            AIMessage(content="", tool_calls=[call]),
        ],
        tool_call=call,
    )
    handler_calls = 0

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        nonlocal handler_calls
        handler_calls += 1
        return ToolMessage(
            content="模型可见结果",
            artifact={
                "provider_metadata": {
                    "request_id": "request-123",
                    "response_time": 0.42,
                    "usage": {},
                }
            },
            tool_call_id=call["id"],
        )

    with pytest.raises(ToolExecutionError, match="usage"):
        await middleware.awrap_tool_call(request, handler)

    assert handler_calls == 1
    db_session.expire_all()
    record = await load_only_tool_call(db_session, prepared_run.run_id)
    assert record.status == AgentToolCallStatus.FAILED
    assert record.result is None
    assert record.provider_metadata is None
    assert record.error_type == "ToolExecutionError"


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
    config: RunnableConfig = {"configurable": {"thread_id": str(prepared_run.run_id)}}
    context = AgentContext(
        user_id=user.id,
        run_id=prepared_run.run_id,
        input_message_count=1,
        rendered_system_prompt=SYSTEM_PROMPT,
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
    context = AgentContext(user.id, prepared_run.run_id, 1)
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
    assert [
        activity["status"] for activity in cast(list[dict[str, Any]], activities)
    ] == [
        "started",
        "failed",
    ]


async def test_failed_provider_response_keeps_audit_metadata(
    db_session: AsyncSession,
) -> None:
    user, prepared_run = await make_running_run(db_session, "tool_response")
    call = tool_call("call-response-failure", "失败响应")
    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=3,
        max_parallel_tool_calls=5,
    )
    request = make_request(
        context=AgentContext(user.id, prepared_run.run_id, 1),
        messages=[
            HumanMessage(content="测试"),
            AIMessage(content="", tool_calls=[call]),
        ],
        tool_call=call,
    )
    provider_metadata = {
        "request_id": "failed-request-123",
        "response_time": 0.52,
        "usage": {"credits": 2},
    }

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        raise ToolResponseError(
            "供应商内容结构无效",
            artifact={"provider_metadata": provider_metadata},
        )

    with pytest.raises(ToolResponseError, match="结构无效"):
        await middleware.awrap_tool_call(request, handler)

    db_session.expire_all()
    record = await load_only_tool_call(db_session, prepared_run.run_id)
    assert record.status == AgentToolCallStatus.FAILED
    assert record.provider_metadata == provider_metadata
    assert record.error_type == "ToolResponseError"


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
        context=AgentContext(user.id, prepared_run.run_id, 1),
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
        context=AgentContext(user.id, prepared_run.run_id, 1),
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
        context=AgentContext(user.id, prepared_run.run_id, 1),
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


async def test_last_tool_round_rejects_new_search_before_provider_call(
    db_session: AsyncSession,
) -> None:
    user, prepared_run = await make_running_run(db_session, "last_round_search")
    call = tool_call("call-last-round-search", "不应执行")
    messages = [*completed_tool_rounds(9), AIMessage(content="", tool_calls=[call])]
    middleware = ToolPersistenceMiddleware(
        make_session_factory(db_session),
        max_tool_rounds=10,
        max_parallel_tool_calls=5,
    )
    activities: list[object] = []
    request = make_request(
        context=AgentContext(user.id, prepared_run.run_id, 1),
        messages=messages,
        tool_call=call,
        activities=activities,
    )
    handler_called = False

    async def handler(_request: ToolCallRequest) -> ToolMessage | Command[Any]:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="不应执行", tool_call_id=call["id"])

    with pytest.raises(ToolExecutionLimitError, match="最后一轮"):
        await middleware.awrap_tool_call(request, handler)

    assert handler_called is False
    db_session.expire_all()
    record = await load_only_tool_call(db_session, prepared_run.run_id)
    assert record.status == AgentToolCallStatus.FAILED
    assert record.error_type == "ToolExecutionLimitError"
    assert [
        activity["status"] for activity in cast(list[dict[str, Any]], activities)
    ] == ["started", "failed"]


@pytest.mark.parametrize(
    ("round_count", "parallel_count", "error_text"),
    [
        (11, 1, "最多执行 10 轮"),
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
        max_tool_rounds=10,
        max_parallel_tool_calls=5,
    )
    request = make_request(
        context=AgentContext(user.id, prepared_run.run_id, 1),
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
        AgentContext(uuid4(), uuid4(), -1)

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
