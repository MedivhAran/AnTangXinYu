import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast
from uuid import UUID

from anyio import CancelScope
from langchain.agents.middleware import InputAgentState
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from loguru import logger
from psycopg.errors import UniqueViolation
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.core import (
    CoreAgentGraph,
    render_core_system_prompt,
)
from antang_api.agents.runtime import AgentContext, ToolActivity
from antang_api.citations import (
    VISIBLE_TURN_SEPARATOR,
    VisibleCitationTurn,
    load_and_validate_citations,
)
from antang_api.companion_memory import CompanionMemory
from antang_api.context import prepare_chat_context
from antang_api.context.health_profile import render_core_profile
from antang_api.database import lock_user_conversation
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
    CarePlan,
    CarePlanStatus,
    Message,
    MessageRole,
    MessageStatus,
)
from antang_api.schemas.chat import (
    ActivityPhase,
    AgentActivityEvent,
    ChatStreamEvent,
    ChatSource,
    MessageCompletedEvent,
    MessageFailedEvent,
    MessageStartedEvent,
    TextDeltaEvent,
)
from antang_api.proactive_care.service import record_user_activity
from antang_api.settings import settings


class DuplicateClientMessageError(Exception):
    """手机重复提交了同一个 client_message_id。"""


class ActiveAgentRunError(Exception):
    """当前用户已经有一个正在执行的顶层 Agent。"""


class ChatRetryError(Exception):
    """指定的失败回答不存在，或者当前已经不能重试。"""

    def __init__(
        self,
        code: Literal["retry_message_not_found", "chat_retry_not_allowed"],
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


TOOL_ACTIVITY_PHASES: dict[str, ActivityPhase] = {
    "web_search": "searching",
    "web_fetch": "reading",
    "read_wearable_data": "reading",
    "delegate_health_profile": "organizing",
    "manage_care_plan": "organizing",
}


@dataclass(frozen=True)
class PreparedChatRun:
    """一次已经写入数据库、可以交给 Core Agent 执行的聊天任务。"""

    user_message_id: UUID
    assistant_message_id: UUID
    run_id: UUID


async def prepare_chat_run(
    session: AsyncSession,
    user_id: UUID,
    client_message_id: UUID,
    content: str,
) -> PreparedChatRun:
    """原子创建用户消息、待生成消息和 Core Agent 运行记录，提交到数据库"""

    user_message = Message(
        client_message_id=client_message_id,
        user_id=user_id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content=content,
    )
    try:
        await lock_user_conversation(session, user_id)
        session.add(user_message)
        await session.flush()
        await record_user_activity(
            session,
            user_id=user_id,
            message_id=user_message.id,
        )

        assistant_message = Message(
            client_message_id=None,
            user_id=user_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.GENERATING,
            content="",
        )
        session.add(assistant_message)
        await session.flush()

        agent_run = AgentRun(
            user_id=user_id,
            trigger_message_id=user_message.id,
            result_message_id=assistant_message.id,
            parent_run_id=None,
            agent_name="core_agent",
            model=settings.deepseek_model,
            status=AgentRunStatus.RUNNING,
        )
        session.add(agent_run)
        await session.flush()
        await session.commit()

        return PreparedChatRun(
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            run_id=agent_run.id,
        )
    except IntegrityError as error:
        await session.rollback()

        if isinstance(error.orig, UniqueViolation):
            constraint_name = error.orig.diag.constraint_name

            if constraint_name == "uq_messages_user_client_message_id":
                raise DuplicateClientMessageError() from error

            if constraint_name == "uq_agent_runs_active_root_user":
                raise ActiveAgentRunError() from error

        raise


async def prepare_chat_retry(
    session: AsyncSession,
    user_id: UUID,
    failed_assistant_message_id: UUID,
) -> PreparedChatRun:
    """为失败或取消的回答重建 Core 运行，继续使用原用户消息。"""

    await lock_user_conversation(session, user_id)

    failed_message = await session.scalar(
        select(Message).where(
            Message.id == failed_assistant_message_id,
            Message.user_id == user_id,
            Message.role == MessageRole.ASSISTANT,
        )
    )
    if failed_message is None:
        raise ChatRetryError("retry_message_not_found", "找不到要重试的回答")
    if failed_message.status not in {
        MessageStatus.FAILED,
        MessageStatus.CANCELLED,
    }:
        raise ChatRetryError("chat_retry_not_allowed", "这条回答当前不能重试")

    original_run = await session.scalar(
        select(AgentRun).where(
            AgentRun.user_id == user_id,
            AgentRun.result_message_id == failed_message.id,
            AgentRun.parent_run_id.is_(None),
            AgentRun.agent_name == "core_agent",
        )
    )
    if original_run is None:
        raise ChatRetryError("retry_message_not_found", "找不到要重试的运行记录")
    if original_run.status not in {
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }:
        raise ChatRetryError("chat_retry_not_allowed", "这条回答当前不能重试")
    if original_run.trigger_message_id is None:
        raise RuntimeError("Core AgentRun 缺少用户消息触发来源")

    later_message = await session.scalar(
        select(Message.id)
        .where(
            Message.user_id == user_id,
            Message.id > failed_message.id,
        )
        .limit(1)
    )
    if later_message is not None:
        raise ChatRetryError(
            "chat_retry_not_allowed",
            "对话已经继续，不能再重试这条回答",
        )

    assistant_message = Message(
        client_message_id=None,
        user_id=user_id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.GENERATING,
        content="",
    )
    session.add(assistant_message)

    try:
        await session.flush()
        agent_run = AgentRun(
            user_id=user_id,
            trigger_message_id=original_run.trigger_message_id,
            result_message_id=assistant_message.id,
            parent_run_id=None,
            agent_name="core_agent",
            model=settings.deepseek_model,
            status=AgentRunStatus.RUNNING,
        )
        session.add(agent_run)
        await session.flush()
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        if (
            isinstance(error.orig, UniqueViolation)
            and error.orig.diag.constraint_name == "uq_agent_runs_active_root_user"
        ):
            raise ActiveAgentRunError() from error
        raise

    return PreparedChatRun(
        user_message_id=original_run.trigger_message_id,
        assistant_message_id=assistant_message.id,
        run_id=agent_run.id,
    )


async def _load_running_chat_run(
    session: AsyncSession,
    prepared_run: PreparedChatRun,
) -> tuple[Message, AgentRun]:
    """读取运行中的 AI 消息和 AgentRun，并验证数据库状态。"""

    assistant_message = await session.get(Message, prepared_run.assistant_message_id)
    agent_run = await session.get(AgentRun, prepared_run.run_id)

    if assistant_message is None:
        raise RuntimeError(f"找不到 AI 消息：{prepared_run.assistant_message_id}")

    if agent_run is None:
        raise RuntimeError(f"找不到 AgentRun：{prepared_run.run_id}")

    if assistant_message.status != MessageStatus.GENERATING:
        raise RuntimeError(
            f"AI 消息状态应为 generating，实际为 {assistant_message.status.value}"
        )

    if agent_run.status != AgentRunStatus.RUNNING:
        raise RuntimeError(
            f"AgentRun 状态应为 running，实际为 {agent_run.status.value}"
        )

    return assistant_message, agent_run


def extract_agent_result(
    final_state: dict[str, Any],
    input_message_count: int,
) -> tuple[str, tuple[VisibleCitationTurn, ...], int, int]:
    """从最终状态取得全部可见文字和真实 token 用量。"""

    messages = cast(list[BaseMessage], final_state["messages"])

    if len(messages) <= input_message_count:
        raise RuntimeError("Core Agent 没有生成任何新消息")

    generated_messages = messages[input_message_count:]
    final_message = generated_messages[-1]

    if not isinstance(final_message, AIMessage):
        raise RuntimeError(
            f"Core Agent 最后一条消息类型错误：{type(final_message).__name__}"
        )

    if final_message.tool_calls:
        raise RuntimeError("Core Agent 结束时仍有未执行的工具调用")

    if not final_message.text.strip():
        raise RuntimeError("Core Agent 返回了空回答")

    available_tool_call_ids: set[str] = set()
    visible_turns: list[VisibleCitationTurn] = []
    for message in generated_messages:
        if isinstance(message, ToolMessage) and message.status != "error":
            available_tool_call_ids.add(message.tool_call_id)
        elif isinstance(message, AIMessage) and message.text:
            visible_turns.append(
                VisibleCitationTurn(
                    content=message.text,
                    available_tool_call_ids=frozenset(available_tool_call_ids),
                )
            )

    content = VISIBLE_TURN_SEPARATOR.join(turn.content for turn in visible_turns)

    if not content.strip():
        raise RuntimeError("Core Agent 返回了空回答")

    input_tokens = 0
    output_tokens = 0

    for message in generated_messages:
        if not isinstance(message, AIMessage):
            continue

        usage = message.usage_metadata

        if usage is None:
            raise RuntimeError("模型响应缺少 usage_metadata")

        input_tokens += usage["input_tokens"]
        output_tokens += usage["output_tokens"]

    return content, tuple(visible_turns), input_tokens, output_tokens


def _has_thinking_content(chunk: AIMessageChunk) -> bool:
    """只识别 Anthropic 协议的 thinking 块，不读取或传播其内容。"""

    if not isinstance(chunk.content, list):
        return False

    return any(
        isinstance(block, dict) and block.get("type") == "thinking"
        for block in chunk.content
    )


def _parse_tool_activity(value: object) -> ToolActivity:
    """验证工具中间件发出的内部事件，避免把未知数据发给 App。"""

    if not isinstance(value, dict) or value.get("event") != "tool_activity":
        raise RuntimeError("Core Agent 发出了未知的自定义流事件")

    tool_call_id = value.get("tool_call_id")
    tool_name = value.get("tool_name")
    status = value.get("status")

    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise RuntimeError("工具活动事件缺少 tool_call_id")
    if not isinstance(tool_name, str) or not tool_name:
        raise RuntimeError("工具活动事件缺少 tool_name")
    if status not in {"started", "completed", "failed", "cancelled"}:
        raise RuntimeError("工具活动事件的 status 无效")

    return cast(ToolActivity, value)


def _is_user_visible_model_event(metadata: dict[str, Any]) -> bool:
    """只允许顶层 Core 模型的文字进入 App，嵌套 Agent 一律不可见。"""

    return (
        metadata.get("langgraph_node") == "model"
        and metadata.get("stream_visibility") == "user"
    )


async def _complete_chat_run(
    session: AsyncSession,
    prepared_run: PreparedChatRun,
    content: str,
    input_tokens: int,
    output_tokens: int,
    sources: list[ChatSource],
) -> None:
    """保存完整回答，并将 AgentRun 标记为完成。"""

    assistant_message, agent_run = await _load_running_chat_run(
        session,
        prepared_run,
    )
    now = datetime.now(timezone.utc)

    assistant_message.content = content
    assistant_message.sources = [source.model_dump() for source in sources]
    assistant_message.status = MessageStatus.COMPLETED
    assistant_message.completed_at = now

    agent_run.status = AgentRunStatus.COMPLETED
    agent_run.input_tokens = input_tokens
    agent_run.output_tokens = output_tokens
    agent_run.finished_at = now

    await session.commit()


async def _fail_chat_run(
    session: AsyncSession,
    prepared_run: PreparedChatRun,
    partial_content: str,
    error: Exception,
) -> None:
    """保存执行失败状态和已经发送给手机的部分文字。"""

    await session.rollback()
    assistant_message, agent_run = await _load_running_chat_run(
        session,
        prepared_run,
    )
    now = datetime.now(timezone.utc)

    assistant_message.content = partial_content
    assistant_message.sources = []
    assistant_message.status = MessageStatus.FAILED
    assistant_message.completed_at = now

    agent_run.status = AgentRunStatus.FAILED
    agent_run.error_type = type(error).__name__
    agent_run.error_message = str(error)
    agent_run.finished_at = now

    await session.execute(
        update(AgentToolCall)
        .where(
            AgentToolCall.agent_run_id == prepared_run.run_id,
            AgentToolCall.status == AgentToolCallStatus.RUNNING,
        )
        .values(
            status=AgentToolCallStatus.FAILED,
            error_type=type(error).__name__,
            error_message=str(error),
            finished_at=now,
        )
    )

    await session.commit()


async def _cancel_chat_run(
    session: AsyncSession,
    prepared_run: PreparedChatRun,
    partial_content: str,
) -> None:
    """客户端断开连接时保存取消状态和已经生成的部分文字。"""

    await session.rollback()
    assistant_message, agent_run = await _load_running_chat_run(
        session,
        prepared_run,
    )
    now = datetime.now(timezone.utc)

    assistant_message.content = partial_content
    assistant_message.sources = []
    assistant_message.status = MessageStatus.CANCELLED
    assistant_message.completed_at = now

    agent_run.status = AgentRunStatus.CANCELLED
    agent_run.finished_at = now

    await session.execute(
        update(AgentToolCall)
        .where(
            AgentToolCall.agent_run_id == prepared_run.run_id,
            AgentToolCall.status == AgentToolCallStatus.RUNNING,
        )
        .values(
            status=AgentToolCallStatus.CANCELLED,
            error_type="CancelledError",
            error_message="聊天运行被取消",
            finished_at=now,
        )
    )

    await session.commit()


async def stream_chat_run(
    session: AsyncSession,
    model: ChatAnthropic,
    agent: CoreAgentGraph,
    tools: Sequence[BaseTool],
    companion_memory: CompanionMemory,
    user_id: UUID,
    prepared_run: PreparedChatRun,
) -> AsyncIterator[ChatStreamEvent]:
    """准备上下文、执行 Core Agent，并持续产生聊天流事件。"""

    started_at = time.perf_counter()
    streamed_parts: list[str] = []
    terminal_event: MessageCompletedEvent | MessageFailedEvent | None = None
    current_activity: ActivityPhase | None = None
    active_tool_phases: dict[str, ActivityPhase] = {}
    has_completed_tool = False
    run_log = logger.bind(
        run_id=str(prepared_run.run_id),
        user_id=str(user_id),
        model=settings.deepseek_model,
    )
    run_log.bind(status=AgentRunStatus.RUNNING.value).info("chat_run_started")

    try:
        yield MessageStartedEvent(
            user_message_id=prepared_run.user_message_id,
            assistant_message_id=prepared_run.assistant_message_id,
            run_id=prepared_run.run_id,
        )

        current_activity = "thinking"
        yield AgentActivityEvent(phase=current_activity)

        health_profile_context = await render_core_profile(
            session,
            user_id,
        )
        companion_memory_context = await companion_memory.prepare_context(
            session,
            user_id,
            prepared_run.user_message_id,
        )
        active_plans = list(
            await session.scalars(
                select(CarePlan)
                .where(
                    CarePlan.user_id == user_id,
                    CarePlan.status == CarePlanStatus.ACTIVE,
                )
                .order_by(CarePlan.follow_up_at, CarePlan.id)
            )
        )
        care_plan_context = json.dumps(
            [
                {
                    "plan_id": str(plan.id),
                    "summary": plan.summary,
                    "follow_up_at": plan.follow_up_at.isoformat(),
                    "revision": plan.revision,
                }
                for plan in active_plans
            ],
            ensure_ascii=False,
        )
        rendered_system_prompt = render_core_system_prompt(
            health_profile_context,
            companion_memory_context=companion_memory_context,
            care_plan_context=care_plan_context,
        )

        prepared_context = await prepare_chat_context(
            session=session,
            model=model,
            system_prompt=rendered_system_prompt,
            user_id=user_id,
            through_message_id=prepared_run.user_message_id,
            compaction_trigger_tokens=settings.context_compaction_trigger_tokens,
            recent_messages_to_keep=settings.context_recent_messages_to_keep,
            tool_result_cleanup_trigger_tokens=(
                settings.context_tool_result_cleanup_trigger_tokens
            ),
            recent_tool_results_to_keep=(settings.context_recent_tool_results_to_keep),
            tools=tools,
        )
        run_log.bind(
            context_tokens=prepared_context.input_tokens,
            compacted=prepared_context.was_compacted,
            cleared_tool_result_count=(prepared_context.cleared_tool_result_count),
        ).info("chat_context_prepared")

        input_message_count = len(prepared_context.context.messages)
        final_state: dict[str, Any] | None = None
        run_config: RunnableConfig = {
            "configurable": {"thread_id": str(prepared_run.run_id)},
            "metadata": {
                "run_id": str(prepared_run.run_id),
                "user_id": str(user_id),
                "stream_visibility": "user",
            },
        }
        runtime_context = AgentContext(
            user_id=user_id,
            run_id=prepared_run.run_id,
            input_message_count=input_message_count,
            rendered_system_prompt=rendered_system_prompt,
        )
        last_text_model_step: int | None = None
        agent_input = cast(
            InputAgentState,
            {"messages": list(prepared_context.context.messages)},
        )

        async for stream_part in agent.astream(
            agent_input,
            config=run_config,
            context=runtime_context,
            stream_mode=["messages", "custom", "values"],
            version="v2",
        ):
            stream_type = stream_part["type"]

            if stream_type == "messages":
                # stream_part的格式为： {"type": "messages", "data": (chunk, metadata)}
                chunk, metadata = cast(
                    tuple[BaseMessage, dict[str, Any]],
                    stream_part["data"],
                )

                if not _is_user_visible_model_event(metadata):
                    continue

                if not isinstance(chunk, AIMessageChunk):
                    raise TypeError(f"流式消息类型错误：{type(chunk).__name__}")

                model_step = metadata.get("langgraph_step")

                if not isinstance(model_step, int):
                    raise RuntimeError("模型流事件缺少 langgraph_step")

                if _has_thinking_content(chunk):
                    thinking_phase: ActivityPhase = (
                        "organizing" if has_completed_tool else "thinking"
                    )
                    if thinking_phase != current_activity:
                        current_activity = thinking_phase
                        yield AgentActivityEvent(phase=thinking_phase)

                delta = chunk.text

                if delta:
                    if (
                        last_text_model_step is not None
                        and last_text_model_step != model_step
                    ):
                        streamed_parts.append(VISIBLE_TURN_SEPARATOR)
                        yield TextDeltaEvent(delta=VISIBLE_TURN_SEPARATOR)

                    last_text_model_step = model_step
                    # App 收到普通文字后会隐藏状态栏；后续的
                    # reasoning 或工具事件必须可以重新显示它。
                    current_activity = None
                    streamed_parts.append(delta)
                    yield TextDeltaEvent(delta=delta)

            elif stream_type == "custom":
                activity = _parse_tool_activity(stream_part["data"])
                tool_call_id = activity["tool_call_id"]
                tool_name = activity["tool_name"]
                status = activity["status"]
                phase = TOOL_ACTIVITY_PHASES.get(tool_name)

                if phase is None:
                    raise RuntimeError(f"工具缺少活动阶段配置：{tool_name}")

                if status == "started":
                    active_tool_phases[tool_call_id] = phase
                    next_activity = phase
                else:
                    active_tool_phases.pop(tool_call_id, None)
                    if status == "completed":
                        has_completed_tool = True

                    if active_tool_phases:
                        next_activity = list(active_tool_phases.values())[-1]
                    elif status == "completed":
                        next_activity = "organizing"
                    else:
                        # 终止事件会立即让 App 清除状态栏。
                        current_activity = None
                        continue

                if next_activity != current_activity:
                    current_activity = next_activity
                    yield AgentActivityEvent(phase=next_activity)

            elif stream_type == "values":
                # {"type": "values","data": {完整的 LangGraph 状态字典}}
                final_state = cast(dict[str, Any], stream_part["data"])
            else:
                raise RuntimeError(f"收到未知的 LangGraph 流类型：{stream_type}")

        if final_state is None:
            raise RuntimeError("LangGraph 没有返回最终状态")

        content, visible_turns, input_tokens, output_tokens = extract_agent_result(
            final_state,
            input_message_count,
        )
        streamed_content = "".join(streamed_parts)

        if streamed_content != content:
            raise RuntimeError("流式文字与最终回答内容不一致")

        sources = await load_and_validate_citations(
            session,
            agent_run_id=prepared_run.run_id,
            content=content,
            visible_turns=visible_turns,
        )

        await _complete_chat_run(
            session=session,
            prepared_run=prepared_run,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            sources=sources,
        )
        latency_ms = round((time.perf_counter() - started_at) * 1000)
        run_log.bind(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            status=AgentRunStatus.COMPLETED.value,
        ).info("chat_run_finished")
        terminal_event = MessageCompletedEvent(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            sources=sources,
        )
    except (asyncio.CancelledError, GeneratorExit):
        # Starlette 取消流任务后，普通 await 也可能继续收到取消信号。
        # shield 保证终止状态先写入数据库。
        with CancelScope(shield=True):
            await _cancel_chat_run(
                session,
                prepared_run,
                "".join(streamed_parts),
            )
            latency_ms = round((time.perf_counter() - started_at) * 1000)
            run_log.bind(
                latency_ms=latency_ms,
                status=AgentRunStatus.CANCELLED.value,
            ).warning("chat_run_cancelled")
        raise
    except Exception as error:
        await _fail_chat_run(
            session=session,
            prepared_run=prepared_run,
            partial_content="".join(streamed_parts),
            error=error,
        )
        latency_ms = round((time.perf_counter() - started_at) * 1000)
        run_log.bind(
            latency_ms=latency_ms,
            status=AgentRunStatus.FAILED.value,
            error_type=type(error).__name__,
        ).error("chat_run_failed")
        terminal_event = MessageFailedEvent(error_type=type(error).__name__)

    if terminal_event is None:
        raise RuntimeError("聊天流缺少结束事件")

    # 结束事件在 try 块外发送。手机在此刻断开时，数据库里的完成状态仍然有效。
    yield terminal_event


async def recover_interrupted_chat_runs(session: AsyncSession) -> None:
    """API 启动时只恢复由用户消息触发、属于本进程的运行。"""

    runs = list(
        await session.scalars(
            select(AgentRun).where(
                AgentRun.status == AgentRunStatus.RUNNING,
                AgentRun.trigger_care_task_id.is_(None),
            )
        )
    )
    tool_calls = list(
        await session.scalars(
            select(AgentToolCall)
            .join(AgentRun, AgentRun.id == AgentToolCall.agent_run_id)
            .where(
                AgentToolCall.status == AgentToolCallStatus.RUNNING,
                AgentRun.trigger_care_task_id.is_(None),
            )
        )
    )

    if not runs and not tool_calls:
        return

    now = datetime.now(timezone.utc)

    for run in runs:
        run.status = AgentRunStatus.FAILED
        run.error_type = "ServerRestarted"
        run.error_message = "服务重启，中断了正在执行的 AgentRun"
        run.finished_at = now

        if run.result_message_id is not None:
            message = await session.get(Message, run.result_message_id)

            if message is not None and message.status == MessageStatus.GENERATING:
                message.status = MessageStatus.FAILED
                message.completed_at = now

    for tool_call in tool_calls:
        tool_call.status = AgentToolCallStatus.FAILED
        tool_call.error_type = "ServerRestarted"
        tool_call.error_message = "服务重启，中断了正在执行的工具调用"
        tool_call.finished_at = now

    await session.commit()

    for run in runs:
        logger.bind(
            run_id=str(run.id),
            user_id=str(run.user_id),
            model=run.model,
            status=AgentRunStatus.FAILED.value,
            error_type="ServerRestarted",
        ).warning("chat_run_recovered_after_restart")

    for tool_call in tool_calls:
        logger.bind(
            agent_run_id=str(tool_call.agent_run_id),
            tool_call_id=tool_call.tool_call_id,
            tool_name=tool_call.tool_name,
            status=AgentToolCallStatus.FAILED.value,
            error_type="ServerRestarted",
        ).warning("tool_call_recovered_after_restart")
