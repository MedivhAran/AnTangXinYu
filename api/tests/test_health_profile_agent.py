from collections.abc import Sequence
from typing import Any, Self, cast
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from antang_api.agents.health_profile import (
    ProfileAgentGraph,
    build_profile_agent,
    extract_decision,
)
from antang_api.agents.runtime import AgentContext
from antang_api.tools.health_profile import build_profile_tool


class StructuredOutputFakeModel(FakeMessagesListChatModel):
    """允许测试模型绑定健康档案结构化输出工具。"""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Self:
        del tools, tool_choice, kwargs
        return self


async def test_health_profile_agent_returns_strict_structured_decision() -> None:
    model = StructuredOutputFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ProfileDecision",
                        "args": {"proposals": []},
                        "id": "structured-decision",
                        "type": "tool_call",
                    }
                ],
                usage_metadata={
                    "input_tokens": 12,
                    "output_tokens": 4,
                    "total_tokens": 16,
                },
            )
        ]
    )
    agent = build_profile_agent(model, InMemorySaver())
    run_id = uuid4()
    user_id = uuid4()
    final_state = await agent.ainvoke(
        {"messages": [HumanMessage(content='{"current_user_message":"你好"}')]},
        config={"configurable": {"thread_id": str(run_id)}},
        context=AgentContext(
            user_id=user_id,
            run_id=run_id,
            input_message_count=1,
        ),
    )

    decision, input_tokens, output_tokens = extract_decision(
        cast(dict[str, Any], final_state)
    )

    assert decision.proposals == []
    assert input_tokens == 12
    assert output_tokens == 4


def test_delegate_health_profile_has_no_model_visible_arguments() -> None:
    tool = build_profile_tool(
        cast("ProfileAgentGraph", object()),
        model_name="test-model",
    )

    assert tool.args == {}
