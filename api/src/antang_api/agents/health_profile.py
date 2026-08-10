from collections.abc import Sequence
from typing import Annotated, Any, TypeAlias, cast

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    InputAgentState,
    ModelCallLimitMiddleware,
    OutputAgentState,
)
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext
from antang_api.agents.tool_middleware import ToolPersistenceMiddleware
from antang_api.database import session_factory as default_session_factory
from antang_api.health_profile.types import ProfileProposal

PROFILE_AGENT_NAME = "health_profile_manager"
MAX_MODEL_CALLS = 4
MAX_TOOL_ROUNDS = 3
MAX_PARALLEL_TOOL_CALLS = 1

SYSTEM_PROMPT = """
你是安糖心语内部的健康档案管理 Agent。你不直接和用户聊天，只分析服务器给你的结构化上下文，并返回严格的档案候选。

输入包含 current_profile、recent_conversation 和 current_user_message。recent_conversation 最多五轮，只能帮助你理解代词、省略和用户正在回答的待处理卡片；不能根据无关历史内容主动重新写入。每一项候选的 evidence_quote 必须逐字摘自 current_user_message，不能引用历史助手消息。

current_profile.pending_changes 是尚未处理的卡片。如果当前消息是在回答其中一张 clarification 卡片，候选必须把那张卡片的准确 id 写入 resolves_change_id；把该卡片 proposed_value 中非 null 的局部信息原样带回，只用当前回答补齐 null 的部分，不能从其他历史内容猜值。只有当前回答完整地给出了替代内容时，才按用户的新内容纠正原局部信息。evidence_quote 仍逐字引用当前回答。不能按字段猜测，也不能引用不存在或已经结束的卡片。新的独立信息把 resolves_change_id 留空。clarification 候选不能解决另一张 clarification 卡片。

当前只管理两类内容：
1. 基础个人信息：sex、age_years、height_cm、weight_kg、resident_area、schedule_type、occupation。
2. 明确健康事实：medical_history、allergy、severe_hypoglycemia_history、treatment。

用户明确要求新增、修改、纠正、清空，或者清楚地陈述自己的完整信息时，mode 使用 direct。信息有明确候选但用户表达不确定时使用 confirmation。字段相关但缺少值、单位或关键细节，无法形成精确候选时使用 clarification，并且不要猜值。clarification 可以保留当前消息已经明确给出的局部字段，缺少的字段必须是 null；这些局部字段只用于补充信息卡片，应用程序不会执行它们。例如“体重 130”应返回 value=130、unit=null、clarification_reason=missing_unit。普通健康咨询、假设、第三人的信息、与支持范围无关的内容，以及没有任何新变化的消息，都返回空 proposals。

基础信息只用 set 或 clear。age_years 的 unit 必须是 years；height_cm 只接受 cm 或 m；weight_kg 只接受 kg、jin 或 lb；其他文本字段的 unit 必须为空。数字缺少单位时必须 clarification。value 只提取用户说出的原始数值，unit 单独填写，绝不能自行换算，例如“130斤”必须是 value=130、unit=jin，不能改成 65kg。单位换算只由应用程序完成。健康事实新增用 add；修改或撤回已有事实必须使用 current_profile 中真实存在的 target_id，分别使用 update 或 retract。不要模糊匹配、合并或自行创造数据库 ID。

设备数据只能通过提供的只读工具按当前消息的需要查询。你不能修改设备原始记录，不能把一次测量自动写成疾病或长期结论；只有用户当前消息明确要求把某项设备观测用于支持范围内的档案修改时，才可以据此产生候选。
""".strip()


class ProfileDecision(BaseModel):
    """子 Agent 唯一允许返回的结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    proposals: Annotated[list[ProfileProposal], Field(max_length=10)]


ProfileAgentGraph: TypeAlias = CompiledStateGraph[
    AgentState[ProfileDecision],
    AgentContext | None,
    InputAgentState,
    OutputAgentState[ProfileDecision],
]
ProfileAgentMiddleware: TypeAlias = AgentMiddleware[
    AgentState[Any], AgentContext | None, Any
]


def build_profile_agent(
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver,
    tools: Sequence[BaseTool] = (),
    *,
    session_factory: async_sessionmaker[AsyncSession] = default_session_factory,
) -> ProfileAgentGraph:
    """创建隔离的健康档案 Agent；工具和结构错误都立即结束运行。"""

    middleware: list[ProfileAgentMiddleware] = []
    if tools:
        middleware.append(
            cast(
                ProfileAgentMiddleware,
                ToolPersistenceMiddleware(
                    session_factory,
                    max_tool_rounds=MAX_TOOL_ROUNDS,
                    max_parallel_tool_calls=MAX_PARALLEL_TOOL_CALLS,
                    ignored_tool_names=frozenset({ProfileDecision.__name__}),
                    # Core 已经在 delegate_health_profile 上显示“更新档案”。
                    # 子 Agent 的内部手环工具不再向顶层流发送未知活动事件。
                    emit_activity=False,
                ),
            )
        )
    middleware.append(
        cast(
            ProfileAgentMiddleware,
            ModelCallLimitMiddleware(
                run_limit=MAX_MODEL_CALLS,
                exit_behavior="error",
            ),
        )
    )

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=middleware,
        response_format=ToolStrategy(
            ProfileDecision,
            handle_errors=False,
            tool_message_content="健康档案候选已经生成。",
        ),
        context_schema=AgentContext,
        checkpointer=checkpointer,
        name=PROFILE_AGENT_NAME,
    )


def extract_decision(
    final_state: dict[str, Any],
) -> tuple[ProfileDecision, int, int]:
    """取得严格结果和本次子 Agent 的真实 token 用量。"""

    structured_response = final_state.get("structured_response")
    if not isinstance(structured_response, ProfileDecision):
        raise RuntimeError("健康档案 Agent 缺少结构化结果")

    messages = cast(list[BaseMessage], final_state.get("messages", []))
    input_tokens = 0
    output_tokens = 0
    model_message_count = 0
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        model_message_count += 1
        usage = message.usage_metadata
        if usage is None:
            raise RuntimeError("健康档案模型响应缺少 usage_metadata")
        input_tokens += usage["input_tokens"]
        output_tokens += usage["output_tokens"]

    if model_message_count == 0:
        raise RuntimeError("健康档案 Agent 没有模型响应")

    return structured_response, input_tokens, output_tokens
