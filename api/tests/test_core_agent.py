from collections.abc import Sequence
from typing import Any, Self
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from antang_api.agents.core import build_core_agent
from antang_api.agents.runtime import CoreAgentContext


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
        context=CoreAgentContext(
            user_id=user_id,
            run_id=run_id,
            input_message_count=1,
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
