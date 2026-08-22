from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext
from antang_api.database import session_factory as default_session_factory
from antang_api.models import AgentRun, AgentRunStatus
from antang_api.proactive_care.service import (
    CarePlanResult,
    manage_care_plan,
)

CARE_PLAN_TOOL_NAME = "manage_care_plan"


class CarePlanRequest(BaseModel):
    """Core 依据当前用户原话提出的一次计划变更。"""

    model_config = ConfigDict(extra="forbid")

    action: Annotated[
        Literal["create", "update", "complete", "cancel"],
        Field(description="创建、修改、完成或取消一项回访计划。"),
    ]
    authorization_quote: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="逐字复制当前用户消息中授权这次操作的原话。",
        ),
    ]
    plan_id: Annotated[
        UUID | None,
        Field(description="update、complete、cancel 必须填写已有计划 ID。"),
    ] = None
    summary: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=500,
            description="create、update 使用的简短提醒或回访内容。",
        ),
    ] = None
    follow_up_at: Annotated[
        datetime | None,
        Field(description="create、update 使用的、带时区的明确回访时间。"),
    ] = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> "CarePlanRequest":
        if self.action == "create":
            if self.plan_id is not None:
                raise ValueError("create 不能提供 plan_id")
            if self.summary is None or self.follow_up_at is None:
                raise ValueError("create 必须提供 summary 和 follow_up_at")
        elif self.action == "update":
            if (
                self.plan_id is None
                or self.summary is None
                or self.follow_up_at is None
            ):
                raise ValueError(
                    "update 必须提供 plan_id、summary 和 follow_up_at"
                )
        elif (
            self.plan_id is None
            or self.summary is not None
            or self.follow_up_at is not None
        ):
            raise ValueError(
                "complete、cancel 只接受 plan_id 和 authorization_quote"
            )
        return self


def build_care_plan_tool(
    sessions: async_sessionmaker[AsyncSession] = default_session_factory,
) -> BaseTool:
    """创建只允许 Core 按当前用户明确授权管理回访计划的工具。"""

    @tool(CARE_PLAN_TOOL_NAME)
    async def care_plan_tool(
        request: CarePlanRequest,
        runtime: ToolRuntime[AgentContext],
    ) -> CarePlanResult:
        """管理用户明确要求的一次性提醒或计划回访。

        只有用户明确要求创建、修改、完成或取消，并且对话中已有明确的
        提醒或回访内容和时间时才能调用。相对时间可以根据服务器提供的当前时间换算。
        含糊的“以后再说”和模型自行提议不能创建；信息不足时先追问。
        调用必须独占当前工具轮次。
        """

        tool_call_id = runtime.tool_call_id
        messages = runtime.state.get("messages")
        if (
            not isinstance(tool_call_id, str)
            or not tool_call_id
            or not isinstance(messages, list)
            or not messages
            or not isinstance(messages[-1], AIMessage)
            or len(messages[-1].tool_calls) != 1
            or messages[-1].tool_calls[0].get("id") != tool_call_id
        ):
            raise RuntimeError("计划管理必须独占当前工具轮次")

        async with sessions() as session:
            root_run = await session.get(
                AgentRun,
                runtime.context.run_id,
                with_for_update=True,
            )
            if (
                root_run is None
                or root_run.user_id != runtime.context.user_id
                or root_run.parent_run_id is not None
                or root_run.agent_name != "core_agent"
                or root_run.status != AgentRunStatus.RUNNING
                or root_run.trigger_message_id is None
            ):
                raise RuntimeError("计划管理需要由当前用户消息的 Core Agent 调用")

            result = await manage_care_plan(
                session,
                user_id=root_run.user_id,
                trigger_message_id=root_run.trigger_message_id,
                action=request.action,
                authorization_quote=request.authorization_quote,
                plan_id=request.plan_id,
                summary=request.summary,
                follow_up_at=request.follow_up_at,
            )
            await session.commit()
            return result

    return care_plan_tool
