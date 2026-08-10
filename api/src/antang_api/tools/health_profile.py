import asyncio
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict, cast
from uuid import UUID

from anyio import CancelScope
from langchain.agents.middleware import InputAgentState
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.health_profile import (
    PROFILE_AGENT_NAME,
    ProfileAgentGraph,
    ProfileDecision,
    extract_decision,
)
from antang_api.agents.runtime import AgentContext
from antang_api.context.health_profile import load_profile_input
from antang_api.database import session_factory as default_session_factory
from antang_api.health_profile.service import apply_health_profile_proposals
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
    HealthProfileChange,
)

PROFILE_TOOL_NAME = "delegate_health_profile"


class ProfileAgentError(RuntimeError):
    """健康档案子运行失败；错误正文不会带入用户健康信息。"""


class DuplicateProfileRunError(RuntimeError):
    """同一个 Core AgentRun 已经创建过健康档案子运行。"""


class ProfileChangeResult(TypedDict):
    change_id: str
    status: str
    target_type: str
    field_name: str
    operation: str


class ProfileToolResult(TypedDict):
    status: Literal["completed"]
    changes: list[ProfileChangeResult]


class ProfileAgentRunner:
    """能够创建、执行并原子终结一次独立的健康档案的 AgentRun。"""

    def __init__(
        self,
        agent: ProfileAgentGraph,
        *,
        model_name: str,
        session_factory: async_sessionmaker[AsyncSession] = default_session_factory,
    ) -> None:
        if not model_name.strip():
            raise ValueError("健康档案 Agent 的 model_name 不能为空")
        self._agent = agent
        self._model_name = model_name
        self._session_factory = session_factory

    async def _start_run(
        self,
        context: AgentContext,
        parent_tool_call_id: str,
    ) -> AgentRun | ProfileToolResult:
        async with self._session_factory() as session:
            parent_run = await session.get(
                AgentRun,
                context.run_id,
                with_for_update=True,
            )
            if parent_run is None:
                raise RuntimeError(f"找不到父 AgentRun：{context.run_id}")
            if parent_run.user_id != context.user_id:
                raise RuntimeError("父 AgentRun 不属于当前用户")
            if parent_run.parent_run_id is not None:
                raise RuntimeError("健康档案管理只能由顶层 Core Agent 委派")
            if (
                parent_run.agent_name != "core_agent"
                or parent_run.trigger_message_id is None
            ):
                raise RuntimeError("健康档案管理需要由用户消息触发的 Core Agent")
            if parent_run.status != AgentRunStatus.RUNNING:
                raise RuntimeError("父 AgentRun 已不在运行中")

            parent_call = await session.scalar(
                select(AgentToolCall)
                .where(
                    AgentToolCall.agent_run_id == context.run_id,
                    AgentToolCall.tool_call_id == parent_tool_call_id,
                )
                .with_for_update()
            )
            if parent_call is None:
                raise RuntimeError("找不到创建健康档案子运行的父工具调用")
            if parent_call.tool_name != PROFILE_TOOL_NAME:
                raise RuntimeError("父工具调用不是健康档案委派")
            if parent_call.status != AgentToolCallStatus.RUNNING:
                raise RuntimeError("父工具调用已不在运行中")

            existing_child_run_id = await session.scalar(
                select(AgentRun.id).where(
                    AgentRun.parent_run_id == parent_run.id,
                    AgentRun.agent_name == PROFILE_AGENT_NAME,
                )
            )
            if existing_child_run_id is not None:
                raise DuplicateProfileRunError(
                    "当前 Core AgentRun 已经委派过健康档案管理"
                )

            completed_child_run_id = await session.scalar(
                select(AgentRun.id)
                .where(
                    AgentRun.user_id == context.user_id,
                    AgentRun.trigger_message_id == parent_run.trigger_message_id,
                    AgentRun.parent_run_id.is_not(None),
                    AgentRun.agent_name == PROFILE_AGENT_NAME,
                    AgentRun.status == AgentRunStatus.COMPLETED,
                )
                .order_by(AgentRun.id)
                .limit(1)
            )
            if completed_child_run_id is not None:
                changes = list(
                    await session.scalars(
                        select(HealthProfileChange)
                        .where(
                            HealthProfileChange.agent_run_id == completed_child_run_id
                        )
                        .order_by(HealthProfileChange.proposal_index)
                    )
                )
                return {
                    "status": "completed",
                    "changes": [
                        {
                            "change_id": str(change.id),
                            "status": change.status.value,
                            "target_type": change.target_type.value,
                            "field_name": change.field_name,
                            "operation": change.operation.value,
                        }
                        for change in changes
                    ],
                }

            child_run = AgentRun(
                user_id=context.user_id,
                trigger_message_id=parent_run.trigger_message_id,
                result_message_id=None,
                parent_run_id=parent_run.id,
                parent_tool_call_id=parent_call.id,
                agent_name=PROFILE_AGENT_NAME,
                model=self._model_name,
                status=AgentRunStatus.RUNNING,
            )
            session.add(child_run)
            await session.commit()
            return child_run

    async def _complete_run(
        self,
        child_run_id: UUID,
        *,
        decision: ProfileDecision,
        expected_personal_field_revisions: dict[str, int],
        expected_health_fact_revisions: dict[UUID, int],
        input_tokens: int,
        output_tokens: int,
    ) -> ProfileToolResult:
        async with self._session_factory() as session:
            child_run = await session.get(
                AgentRun,
                child_run_id,
                with_for_update=True,
            )
            if child_run is None:
                raise RuntimeError(f"找不到健康档案 AgentRun：{child_run_id}")
            if child_run.status != AgentRunStatus.RUNNING:
                raise RuntimeError("健康档案 AgentRun 已不在运行中")
            if child_run.trigger_message_id is None:
                raise RuntimeError("健康档案 AgentRun 缺少用户消息触发来源")

            apply_result = await apply_health_profile_proposals(
                session,
                user_id=child_run.user_id,
                trigger_message_id=child_run.trigger_message_id,
                agent_run_id=child_run.id,
                proposals=decision.proposals,
                expected_personal_field_revisions=(expected_personal_field_revisions),
                expected_health_fact_revisions=expected_health_fact_revisions,
            )
            tool_result: ProfileToolResult = {
                "status": "completed",
                "changes": [
                    {
                        "change_id": str(change.change_id),
                        "status": change.status.value,
                        "target_type": change.target_type.value,
                        "field_name": change.field_name,
                        "operation": change.operation.value,
                    }
                    for change in apply_result.changes
                ],
            }
            child_run.status = AgentRunStatus.COMPLETED
            child_run.input_tokens = input_tokens
            child_run.output_tokens = output_tokens
            child_run.finished_at = datetime.now(timezone.utc)
            await session.commit()
            return tool_result

    async def _mark_terminal(
        self,
        child_run_id: UUID,
        *,
        status: Literal[AgentRunStatus.FAILED, AgentRunStatus.CANCELLED],
        error_type: str,
    ) -> None:
        async with self._session_factory() as session:
            child_run = await session.get(
                AgentRun,
                child_run_id,
                with_for_update=True,
            )
            if child_run is None:
                raise RuntimeError(f"找不到健康档案 AgentRun：{child_run_id}")
            # 提交完成后到达的取消信号不能撤销已落库的档案修改。
            if child_run.status == AgentRunStatus.COMPLETED:
                return
            if child_run.status != AgentRunStatus.RUNNING:
                raise RuntimeError("健康档案 AgentRun 已有冲突的终态")

            child_run.status = status
            child_run.error_type = error_type
            child_run.error_message = (
                "健康档案管理被取消"
                if status == AgentRunStatus.CANCELLED
                else "健康档案管理运行失败"
            )
            child_run.finished_at = datetime.now(timezone.utc)
            await session.commit()

    async def run(
        self,
        runtime: ToolRuntime[AgentContext],
    ) -> ProfileToolResult:
        tool_call_id = runtime.tool_call_id
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise RuntimeError("健康档案委派缺少 tool_call_id")
        messages = runtime.state.get("messages")
        if (
            not isinstance(messages, list)
            or not messages
            or not isinstance(messages[-1], AIMessage)
            or len(messages[-1].tool_calls) != 1
            or messages[-1].tool_calls[0].get("id") != tool_call_id
        ):
            raise RuntimeError("健康档案委派必须独占当前工具轮次")

        child_run: AgentRun | None = None
        started: AgentRun | ProfileToolResult | None = None
        try:
            # 子运行的创建事务很短，先让它得到确定的数据库状态；若请求已经
            # 被取消，退出 shield 后马上进入取消分支并终结这条子运行。
            with CancelScope(shield=True):
                started = await self._start_run(
                    runtime.context,
                    tool_call_id,
                )
            if started is None:
                raise RuntimeError("健康档案子运行创建后缺少结果")
            if isinstance(started, dict):
                return started
            child_run = started
            if child_run.trigger_message_id is None:
                raise RuntimeError("健康档案 AgentRun 缺少用户消息触发来源")
            async with self._session_factory() as session:
                profile_input = await load_profile_input(
                    session,
                    runtime.context.user_id,
                    child_run.trigger_message_id,
                    recent_turns=5,
                )

            run_config: RunnableConfig = {
                "configurable": {"thread_id": str(child_run.id)},
                "metadata": {
                    "run_id": str(child_run.id),
                    "user_id": str(runtime.context.user_id),
                    "stream_visibility": "internal",
                },
            }
            agent_input = cast(
                InputAgentState,
                {"messages": [HumanMessage(content=profile_input.to_model_text())]},
            )
            final_state = cast(
                dict[str, Any],
                await self._agent.ainvoke(
                    agent_input,
                    config=run_config,
                    context=AgentContext(
                        user_id=runtime.context.user_id,
                        run_id=child_run.id,
                        input_message_count=1,
                    ),
                ),
            )
            decision, input_tokens, output_tokens = extract_decision(final_state)
            return await self._complete_run(
                child_run.id,
                decision=decision,
                expected_personal_field_revisions=dict(
                    profile_input.personal_field_revisions
                ),
                expected_health_fact_revisions={
                    fact.id: fact.revision
                    for fact in profile_input.current_profile.health_facts
                },
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except asyncio.CancelledError:
            if child_run is None:
                raise
            with CancelScope(shield=True):
                await self._mark_terminal(
                    child_run.id,
                    status=AgentRunStatus.CANCELLED,
                    error_type="CancelledError",
                )
            raise
        except Exception as error:
            if child_run is None:
                raise
            await self._mark_terminal(
                child_run.id,
                status=AgentRunStatus.FAILED,
                error_type=type(error).__name__,
            )
            raise ProfileAgentError("健康档案管理失败") from error


def build_profile_tool(
    agent: ProfileAgentGraph,
    *,
    model_name: str,
    session_factory: async_sessionmaker[AsyncSession] = default_session_factory,
) -> BaseTool:
    """创建没有模型可填参数的 Core→健康档案委派工具。"""

    runner = ProfileAgentRunner(
        agent,
        model_name=model_name,
        session_factory=session_factory,
    )

    @tool(PROFILE_TOOL_NAME)
    async def delegate_health_profile(
        runtime: ToolRuntime[AgentContext],
    ) -> ProfileToolResult:
        """处理当前消息中的本人健康档案变更。

        仅在用户明确新增、修改、更正、清除，或者表达了尚需确认或补充的本人
        档案信息时调用。当前范围是性别、年龄、身高、体重、常驻区域、作息
        类型、职业，以及病史、过敏、严重低血糖史和治疗情况。普通咨询、
        假设、第三人的情况、回顾已有记录和无关聊天不要调用。此工具无参数，
        一次顶层运行最多调用一次，并且必须独占当前工具轮次。
        """

        return await runner.run(runtime)

    return delegate_health_profile
