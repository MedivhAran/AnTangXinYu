from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID, uuid4

import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langchain_core.tools.structured import StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    CarePlan,
    CarePlanStatus,
    Message,
    MessageRole,
    MessageStatus,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    User,
)
from antang_api.proactive_care.service import manage_care_plan
from antang_api.tools.care_plan import CarePlanRequest, build_care_plan_tool


async def create_user_message(
    session: AsyncSession,
    content: str,
) -> tuple[User, Message]:
    username = f"care_plan_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    session.add(user)
    await session.flush()
    session.add(ProactiveCareSettings(user_id=user.id))
    message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content=content,
        completed_at=datetime.now(timezone.utc),
    )
    session.add(message)
    await session.flush()
    return user, message


def tool_coroutine(tool: BaseTool) -> Callable[..., Awaitable[object]]:
    assert isinstance(tool, StructuredTool)
    assert tool.coroutine is not None
    return cast("Callable[..., Awaitable[object]]", tool.coroutine)


def session_factory_for(
    session: AsyncSession,
) -> async_sessionmaker[AsyncSession]:
    assert isinstance(session.bind, AsyncConnection)
    return async_sessionmaker(
        session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


async def test_create_plan_schedules_one_idempotent_follow_up(
    db_session: AsyncSession,
) -> None:
    user, message = await create_user_message(
        db_session,
        "可以，周一晚上再问我跑步完成没有。",
    )
    now = datetime.now(timezone.utc)
    follow_up_at = now + timedelta(days=3)

    first = await manage_care_plan(
        db_session,
        user_id=user.id,
        trigger_message_id=message.id,
        action="create",
        authorization_quote="周一晚上再问我",
        summary="完成一次跑步",
        follow_up_at=follow_up_at,
        now=now,
    )
    replay = await manage_care_plan(
        db_session,
        user_id=user.id,
        trigger_message_id=message.id,
        action="create",
        authorization_quote="可以",
        summary="模型重试时给出的不同文字",
        follow_up_at=now + timedelta(days=4),
        now=now,
    )
    await db_session.flush()

    assert replay == first
    assert first["status"] == "active"
    assert first["revision"] == 1
    plans = list(
        await db_session.scalars(select(CarePlan).where(CarePlan.user_id == user.id))
    )
    tasks = list(
        await db_session.scalars(
            select(ProactiveCareTask).where(
                ProactiveCareTask.user_id == user.id,
                ProactiveCareTask.kind == ProactiveCareTaskKind.PLAN_FOLLOW_UP,
            )
        )
    )
    assert len(plans) == 1
    assert plans[0].summary == "完成一次跑步"
    assert len(tasks) == 1
    assert tasks[0].care_plan_id == plans[0].id
    assert tasks[0].care_plan_revision == 1
    assert tasks[0].status == ProactiveCareTaskStatus.SCHEDULED
    assert tasks[0].due_at == follow_up_at
    assert tasks[0].expires_at == follow_up_at + timedelta(hours=24)


async def test_update_and_complete_plan_end_obsolete_scheduled_tasks(
    db_session: AsyncSession,
) -> None:
    user, create_message = await create_user_message(
        db_session,
        "好，三天后问我散步计划。",
    )
    now = datetime.now(timezone.utc)
    created = await manage_care_plan(
        db_session,
        user_id=user.id,
        trigger_message_id=create_message.id,
        action="create",
        authorization_quote="三天后问我",
        summary="每天散步二十分钟",
        follow_up_at=now + timedelta(days=3),
        now=now,
    )

    update_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="改成周末再问我，目标是散步三十分钟。",
        completed_at=now,
    )
    db_session.add(update_message)
    await db_session.flush()
    updated = await manage_care_plan(
        db_session,
        user_id=user.id,
        trigger_message_id=update_message.id,
        action="update",
        authorization_quote="改成周末再问我",
        plan_id=UUID(created["plan_id"]),
        summary="散步三十分钟",
        follow_up_at=now + timedelta(days=5),
        now=now,
    )

    complete_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="已经完成这个计划了。",
        completed_at=now,
    )
    db_session.add(complete_message)
    await db_session.flush()
    completed = await manage_care_plan(
        db_session,
        user_id=user.id,
        trigger_message_id=complete_message.id,
        action="complete",
        authorization_quote="已经完成",
        plan_id=UUID(created["plan_id"]),
        now=now,
    )
    await db_session.flush()

    assert updated["revision"] == 2
    assert completed["status"] == "completed"
    assert completed["revision"] == 3
    plan = await db_session.get(CarePlan, UUID(created["plan_id"]))
    assert plan is not None
    assert plan.status == CarePlanStatus.COMPLETED
    assert plan.resolved_at == now
    tasks = list(
        await db_session.scalars(
            select(ProactiveCareTask)
            .where(ProactiveCareTask.care_plan_id == plan.id)
            .order_by(ProactiveCareTask.care_plan_revision)
        )
    )
    assert [task.care_plan_revision for task in tasks] == [1, 2]
    assert all(task.status == ProactiveCareTaskStatus.CANCELLED for task in tasks)
    assert all(task.outcome_reason == "plan_changed" for task in tasks)


async def test_plan_change_requires_current_users_verbatim_authorization(
    db_session: AsyncSession,
) -> None:
    first, message = await create_user_message(
        db_session,
        "过几天可以再聊聊。",
    )
    second, _ = await create_user_message(db_session, "另一个人的消息。")
    now = datetime.now(timezone.utc)

    with pytest.raises(ValueError, match="当前用户消息"):
        await manage_care_plan(
            db_session,
            user_id=first.id,
            trigger_message_id=message.id,
            action="create",
            authorization_quote="明天提醒我",
            summary="散步",
            follow_up_at=now + timedelta(days=1),
            now=now,
        )

    with pytest.raises(RuntimeError, match="当前用户"):
        await manage_care_plan(
            db_session,
            user_id=second.id,
            trigger_message_id=message.id,
            action="create",
            authorization_quote="过几天可以再聊聊",
            summary="散步",
            follow_up_at=now + timedelta(days=1),
            now=now,
        )


async def test_plan_requires_future_time_and_enabled_follow_up(
    db_session: AsyncSession,
) -> None:
    user, message = await create_user_message(
        db_session,
        "可以明天再问我。",
    )
    now = datetime.now(timezone.utc)

    with pytest.raises(ValueError, match="未来"):
        await manage_care_plan(
            db_session,
            user_id=user.id,
            trigger_message_id=message.id,
            action="create",
            authorization_quote="明天再问我",
            summary="散步",
            follow_up_at=now,
            now=now,
        )

    settings = await db_session.get(ProactiveCareSettings, user.id)
    assert settings is not None
    settings.plan_follow_up_enabled = False
    await db_session.flush()
    with pytest.raises(ValueError, match="已关闭"):
        await manage_care_plan(
            db_session,
            user_id=user.id,
            trigger_message_id=message.id,
            action="create",
            authorization_quote="明天再问我",
            summary="散步",
            follow_up_at=now + timedelta(days=1),
            now=now,
        )


async def test_core_tool_exposes_one_request_and_uses_the_trigger_message(
    db_session: AsyncSession,
) -> None:
    user, message = await create_user_message(
        db_session,
        "可以，后天晚上问我力量训练做了没有。",
    )
    run = AgentRun(
        user_id=user.id,
        trigger_message_id=message.id,
        trigger_care_task_id=None,
        parent_run_id=None,
        agent_name="core_agent",
        model="test-model",
        status=AgentRunStatus.RUNNING,
    )
    db_session.add(run)
    await db_session.flush()

    tool_call_id = "manage-plan-1"
    runtime = ToolRuntime(
        state={
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "manage_care_plan",
                            "args": {},
                            "id": tool_call_id,
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        },
        context=AgentContext(
            user_id=user.id,
            run_id=run.id,
            input_message_count=1,
        ),
        config={},
        stream_writer=lambda _value: None,
        tool_call_id=tool_call_id,
        store=None,
        tools=[],
    )
    tool = build_care_plan_tool(session_factory_for(db_session))
    follow_up_at = datetime.now(timezone.utc) + timedelta(days=2)

    result = cast(
        dict[str, object],
        await tool_coroutine(tool)(
            request=CarePlanRequest(
                action="create",
                authorization_quote="后天晚上问我",
                summary="完成一次力量训练",
                follow_up_at=follow_up_at,
            ),
            runtime=runtime,
        ),
    )

    assert set(tool.args) == {"request"}
    assert result["status"] == "active"
    plan = await db_session.get(CarePlan, UUID(str(result["plan_id"])))
    assert plan is not None
    assert plan.created_by_message_id == message.id
