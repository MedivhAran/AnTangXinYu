from collections.abc import Sequence
from typing import Any, Self, cast
from uuid import uuid4

import pytest
from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime

from antang_api.agents.core import (
    SYSTEM_PROMPT,
    build_core_agent,
    render_core_system_prompt,
    runtime_system_prompt,
)
from antang_api.agents.runtime import AgentContext


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


@tool(description="返回传入的测试值。")
async def echo_tool(value: str) -> dict[str, str]:
    return {"value": value}


async def test_core_agent_executes_and_checkpoints_complete_tool_loop() -> None:
    model = ToolLoopFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo_tool",
                        "args": {"value": "ok"},
                        "id": "tool-call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="工具执行完成。"),
        ]
    )
    checkpointer = InMemorySaver()
    agent = build_core_agent(
        model,
        checkpointer,
        tools=(echo_tool,),
        middleware=(),
    )
    run_id = uuid4()
    user_id = uuid4()
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}

    final_state = await agent.ainvoke(
        {"messages": [HumanMessage(content="执行工具")]},
        config=config,
        context=AgentContext(
            user_id=user_id,
            run_id=run_id,
            input_message_count=1,
            rendered_system_prompt=SYSTEM_PROMPT,
        ),
    )
    checkpoint = await checkpointer.aget(config)

    assert [type(message) for message in final_state["messages"]] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
    ]
    assert checkpoint is not None
    checkpoint_messages = checkpoint["channel_values"]["messages"]
    assert [type(message) for message in checkpoint_messages] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
    ]
    assert checkpoint_messages[2].tool_call_id == "tool-call-1"
    assert checkpoint_messages[-1].text == "工具执行完成。"


async def test_core_model_uses_the_same_dynamic_prompt_that_was_counted() -> None:
    rendered_prompt = render_core_system_prompt('{"occupation":"学生"}')
    context = AgentContext(
        user_id=uuid4(),
        run_id=uuid4(),
        input_message_count=1,
        rendered_system_prompt=rendered_prompt,
    )
    messages: list[AnyMessage] = [HumanMessage(content="你好")]
    runtime: Runtime[AgentContext | None] = Runtime(context=context)
    request: ModelRequest[AgentContext | None] = ModelRequest(
        model=ToolLoopFakeModel(responses=[AIMessage(content="你好")]),
        messages=messages,
        tools=[],
        state=cast("AgentState[Any]", {"messages": messages}),
        runtime=runtime,
    )

    async def handler(
        modified_request: ModelRequest[AgentContext | None],
    ) -> ModelResponse[Any]:
        assert modified_request.system_prompt == rendered_prompt
        return ModelResponse(result=[AIMessage(content="你好")])

    await runtime_system_prompt.awrap_model_call(request, handler)


def test_core_prompt_marks_companion_memory_as_non_authoritative() -> None:
    rendered_prompt = render_core_system_prompt(
        '{"occupation":"学生"}',
        "- 用户曾说自己害怕夜间低血糖",
    )

    assert "[相关陪伴记忆]" in rendered_prompt
    assert "可能不完整、过时或错误" in rendered_prompt
    assert "以当前表达和健康档案为准" in rendered_prompt
    assert "用户曾说自己害怕夜间低血糖" in rendered_prompt


def test_core_prompt_exposes_current_plan_ids_for_later_changes() -> None:
    plan_id = str(uuid4())
    rendered_prompt = render_core_system_prompt(
        '{"occupation":"学生"}',
        care_plan_context=(
            f'[{{"plan_id":"{plan_id}","summary":"晚饭后散步",'
            '"follow_up_at":"2026-07-20T12:00:00+00:00","revision":1}]'
        ),
    )

    assert "[当前主动关怀计划]" in rendered_prompt
    assert plan_id in rendered_prompt
    assert "晚饭后散步" in rendered_prompt


def test_core_prompt_interprets_short_replies_from_the_latest_question() -> None:
    assert "最近一个明确问题" in SYSTEM_PROMPT
    assert "不要额外猜测原因" in SYSTEM_PROMPT


async def test_core_model_rejects_missing_dynamic_prompt() -> None:
    messages: list[AnyMessage] = [HumanMessage(content="你好")]
    request: ModelRequest[AgentContext | None] = ModelRequest(
        model=ToolLoopFakeModel(responses=[AIMessage(content="你好")]),
        messages=messages,
        tools=[],
        state=cast("AgentState[Any]", {"messages": messages}),
        runtime=Runtime(
            context=AgentContext(
                user_id=uuid4(),
                run_id=uuid4(),
                input_message_count=1,
            )
        ),
    )

    async def handler(
        _request: ModelRequest[AgentContext | None],
    ) -> ModelResponse[Any]:
        raise AssertionError("缺少完整提示词时不能调用模型")

    with pytest.raises(RuntimeError, match="缺少完整系统提示词"):
        await runtime_system_prompt.awrap_model_call(request, handler)
