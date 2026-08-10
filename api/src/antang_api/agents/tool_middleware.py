import asyncio
import math
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Literal
from uuid import UUID

from anyio import CancelScope
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import (
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext, ToolActivity, ToolResponseError
from antang_api.database import session_factory as default_session_factory
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
)


class ToolExecutionLimitError(RuntimeError):
    """模型请求的工具轮数或单轮调用数超过了固定上限。"""


class ToolExecutionError(RuntimeError):
    """工具调用不能交给模型自行纠正，当前 AgentRun 必须失败。"""


def _provider_number(value: object, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ToolExecutionError(f"工具 artifact 中的 {field} 不是数值")
    if not math.isfinite(value) or value < 0:
        raise ToolExecutionError(f"工具 artifact 中的 {field} 必须是非负有限数值")
    return value


def _provider_metadata_from_artifact(
    artifact: object,
) -> dict[str, Any] | None:
    """严格拆出应用审计数据，避免把任意 artifact 写进数据库。"""

    if artifact is None:
        return None
    if not isinstance(artifact, dict) or set(artifact) != {"provider_metadata"}:
        raise ToolExecutionError("工具 artifact 必须且只能包含 provider_metadata")

    metadata = artifact.get("provider_metadata")
    if not isinstance(metadata, dict) or set(metadata) != {
        "request_id",
        "response_time",
        "usage",
    }:
        raise ToolExecutionError("工具 provider_metadata 结构无效")

    request_id = metadata.get("request_id")
    if (
        not isinstance(request_id, str)
        or not request_id
        or request_id != request_id.strip()
    ):
        raise ToolExecutionError("工具 provider_metadata.request_id 无效")

    usage = metadata.get("usage")
    if not isinstance(usage, dict) or set(usage) != {"credits"}:
        raise ToolExecutionError("工具 provider_metadata.usage 结构无效")

    return {
        "request_id": request_id,
        "response_time": _provider_number(
            metadata.get("response_time"),
            "provider_metadata.response_time",
        ),
        "usage": {
            "credits": _provider_number(
                usage.get("credits"),
                "provider_metadata.usage.credits",
            )
        },
    }


def _current_tool_position(
    messages: Sequence[BaseMessage],
    input_message_count: int,
    tool_call_id: str,
    max_tool_rounds: int,
    max_parallel_tool_calls: int,
) -> tuple[int, int]:
    """根据当前运行产生的 AIMessage 得到一基的轮次和并行顺序。"""

    tool_rounds = _tool_rounds(messages, input_message_count)

    if not tool_rounds:
        raise RuntimeError("工具执行状态中缺少发起调用的 AIMessage")

    model_turn_index = len(tool_rounds)
    current_calls = tool_rounds[-1].tool_calls

    if model_turn_index > max_tool_rounds:
        raise ToolExecutionLimitError(
            f"一次 AgentRun 最多执行 {max_tool_rounds} 轮工具调用"
        )

    if len(current_calls) > max_parallel_tool_calls:
        raise ToolExecutionLimitError(
            f"每轮最多执行 {max_parallel_tool_calls} 个工具调用"
        )

    for tool_call_index, tool_call in enumerate(current_calls, start=1):
        if tool_call.get("id") == tool_call_id:
            return model_turn_index, tool_call_index

    raise RuntimeError(f"当前 AIMessage 中找不到工具调用：{tool_call_id}")


def _tool_rounds(
    messages: Sequence[BaseMessage],
    input_message_count: int,
) -> list[AIMessage]:
    """只统计本次 AgentRun 已生成的工具调用轮次。"""

    if input_message_count > len(messages):
        raise RuntimeError("input_message_count 超过工具运行状态中的消息数量")

    return [
        message
        for message in messages[input_message_count:]
        if isinstance(message, AIMessage) and message.tool_calls
    ]


def _matching_tool_message(
    result: ToolMessage | Command[Any],
    tool_call_id: str,
) -> ToolMessage:
    """取得模型最终会看到的 ToolMessage，拒绝缺失或错配的调用结果。"""

    if isinstance(result, ToolMessage):
        if result.tool_call_id != tool_call_id:
            raise RuntimeError("ToolMessage 的 tool_call_id 与请求不一致")
        return result

    update = result.update
    if isinstance(update, dict):
        updated_messages = update.get("messages", [])
    elif isinstance(update, list):
        updated_messages = update
    else:
        updated_messages = []

    for message in updated_messages:
        if isinstance(message, ToolMessage) and message.tool_call_id == tool_call_id:
            return message

    raise RuntimeError("Command 中缺少与工具调用匹配的 ToolMessage")


class ToolPersistenceMiddleware(
    AgentMiddleware[AgentState[Any], AgentContext | None, Any]
):
    """在工具执行的每个阶段，往数据库写记录、往手机发状态事件，并且强制上限。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = default_session_factory,
        *,
        max_tool_rounds: int,
        max_parallel_tool_calls: int,
        ignored_tool_names: frozenset[str] = frozenset(),
        emit_activity: bool = True,
    ) -> None:
        if max_tool_rounds < 1:
            raise ValueError("max_tool_rounds 必须大于 0")
        if max_parallel_tool_calls < 1:
            raise ValueError("max_parallel_tool_calls 必须大于 0")
        if any(not name.strip() for name in ignored_tool_names):
            raise ValueError("ignored_tool_names 不能包含空白名称")

        self._session_factory = session_factory
        self._max_tool_rounds = max_tool_rounds
        self._max_parallel_tool_calls = max_parallel_tool_calls
        self._ignored_tool_names = ignored_tool_names
        self._emit_activity = emit_activity

    async def awrap_model_call(
        self,
        request: ModelRequest[AgentContext | None],
        handler: Callable[
            [ModelRequest[AgentContext | None]],
            Awaitable[ModelResponse[Any]],
        ],
    ) -> ModelResponse[Any] | AIMessage:
        """按剩余轮次缩小模型可见的工具集合，硬上限仍由执行阶段校验。"""

        context = request.runtime.context
        if not isinstance(context, AgentContext):
            raise RuntimeError("模型调用缺少 AgentContext")

        completed_rounds = len(
            _tool_rounds(request.messages, context.input_message_count)
        )
        if completed_rounds >= self._max_tool_rounds:
            # DeepSeek Anthropic 明确支持 tool_choice={"type": "none"}。
            # 保留工具定义是因为 LangChain 在 tools=[] 时不会发送 tool_choice。
            return await handler(request.override(tool_choice={"type": "none"}))
        if completed_rounds == self._max_tool_rounds - 1:
            return await handler(
                request.override(
                    tools=[
                        tool
                        for tool in request.tools
                        if (
                            tool.get("name") != "web_search"
                            if isinstance(tool, dict)
                            else tool.name != "web_search"
                        )
                    ]
                )
            )
        return await handler(request)

    async def _start_call(
        self,
        *,
        context: AgentContext,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        model_turn_index: int,
        tool_call_index: int,
    ) -> tuple[UUID, float]:
        """每个工具调用在开始执行前落库，状态为running，记录归属哪个AgentRun,工具名、参数、第几轮第几个并行调用、开始时间"""
        async with self._session_factory() as session:
            agent_run = await session.get(AgentRun, context.run_id)

            if agent_run is None:
                raise RuntimeError(f"找不到 AgentRun：{context.run_id}")
            if agent_run.user_id != context.user_id:
                raise RuntimeError("AgentRun 不属于当前用户")
            if agent_run.status != AgentRunStatus.RUNNING:
                raise RuntimeError(
                    f"AgentRun 状态应为 running，实际为 {agent_run.status.value}"
                )

            started_monotonic = monotonic()
            record = AgentToolCall(
                agent_run_id=context.run_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                model_turn_index=model_turn_index,
                tool_call_index=tool_call_index,
                arguments=arguments,
                status=AgentToolCallStatus.RUNNING,
                # 墙上时钟只记录真实起点；持续时间由单调时钟计算。
                started_at=datetime.now(timezone.utc),
            )
            session.add(record)
            await session.commit()
            return record.id, started_monotonic

    async def _finish_call(
        self,
        record_id: UUID,
        *,
        started_monotonic: float,
        status: AgentToolCallStatus,
        result: str | list[str | dict[str, Any]] | None = None,
        provider_metadata: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        async with self._session_factory() as session:
            record = await session.get(
                AgentToolCall,
                record_id,
                with_for_update=True,
            )

            if record is None:
                raise RuntimeError(f"找不到 AgentToolCall：{record_id}")
            if record.status != AgentToolCallStatus.RUNNING:
                raise RuntimeError(
                    f"AgentToolCall 状态应为 running，实际为 {record.status.value}"
                )

            elapsed_seconds = monotonic() - started_monotonic
            record.status = status
            record.result = result
            record.provider_metadata = provider_metadata
            # UTC 墙上时钟可能被 WSL 或宿主机校时。用单调时钟计算耗时，
            # 再从真实开始时间推导结束时间，避免出现负耗时。
            record.finished_at = record.started_at + timedelta(seconds=elapsed_seconds)

            if error is not None:
                record.error_type = type(error).__name__
                record.error_message = str(error)

            await session.commit()

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest],
            Awaitable[ToolMessage | Command[Any]],
        ],
    ) -> ToolMessage | Command[Any]:
        context = request.runtime.context
        if not isinstance(context, AgentContext):
            raise RuntimeError("工具调用缺少 AgentContext")

        tool_call_id = request.tool_call.get("id")
        tool_name = request.tool_call.get("name")
        arguments = request.tool_call.get("args")

        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise RuntimeError("工具调用缺少 tool_call_id")
        if not isinstance(tool_name, str) or not tool_name:
            raise RuntimeError("工具调用缺少工具名称")
        if not isinstance(arguments, dict):
            raise RuntimeError("工具调用参数必须是对象")
        # LangChain 的 ToolStrategy 也会产生一个内部“工具调用”来承载最终
        # 结构化结果。它不读取外部数据，不属于业务工具审计范围。
        if tool_name in self._ignored_tool_names:
            return await handler(request)
        messages = request.state["messages"]
        model_turn_index, tool_call_index = _current_tool_position(
            messages,
            context.input_message_count,
            tool_call_id,
            self._max_tool_rounds,
            self._max_parallel_tool_calls,
        )
        record_id, started_monotonic = await self._start_call(
            context=context,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            model_turn_index=model_turn_index,
            tool_call_index=tool_call_index,
        )

        stream_writer = request.runtime.stream_writer

        def emit(
            status: Literal["started", "completed", "failed", "cancelled"],
        ) -> None:
            if not self._emit_activity:
                return
            stream_writer(
                ToolActivity(
                    event="tool_activity",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    status=status,
                )
            )

        emit("started")

        try:
            if model_turn_index == self._max_tool_rounds and tool_name == "web_search":
                raise ToolExecutionLimitError(
                    "最后一轮工具调用不能重新搜索，因为已经没有后续轮次读取网页"
                )
            handler_result = await handler(request)
            tool_message = _matching_tool_message(handler_result, tool_call_id)
            provider_metadata = (
                None
                if tool_message.status == "error"
                else _provider_metadata_from_artifact(tool_message.artifact)
            )
        except asyncio.CancelledError as error:
            with CancelScope(shield=True):
                await self._finish_call(
                    record_id,
                    started_monotonic=started_monotonic,
                    status=AgentToolCallStatus.CANCELLED,
                    error=error,
                )
                emit("cancelled")
            raise
        except Exception as error:
            exposed_error: Exception
            if isinstance(error, ToolInvocationError):
                exposed_error = ToolExecutionError(f"工具调用参数无效：{error}")
            else:
                exposed_error = error

            provider_metadata = (
                _provider_metadata_from_artifact(error.artifact)
                if isinstance(error, ToolResponseError)
                else None
            )

            await self._finish_call(
                record_id,
                started_monotonic=started_monotonic,
                status=AgentToolCallStatus.FAILED,
                provider_metadata=provider_metadata,
                error=exposed_error,
            )
            emit("failed")
            if exposed_error is error:
                raise
            raise exposed_error from error

        if tool_message.status == "error":
            error = ToolExecutionError(f"工具返回错误：{tool_message.text}")
            await self._finish_call(
                record_id,
                started_monotonic=started_monotonic,
                status=AgentToolCallStatus.FAILED,
                result=tool_message.content,
                error=error,
            )
            emit("failed")
            raise error

        await self._finish_call(
            record_id,
            started_monotonic=started_monotonic,
            status=AgentToolCallStatus.COMPLETED,
            result=tool_message.content,
            provider_metadata=provider_metadata,
        )
        emit("completed")
        return handler_result
