from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    CarePlan,
    CarePlanStatus,
    LoginSession,
    Message,
    MessageRole,
    MessageStatus,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    PushDelivery,
    PushDeliveryStatus,
    PushInstallation,
    PushPermissionState,
    PushPlatform,
    RoutineCareCadence,
    User,
)


async def test_proactive_run_does_not_block_a_core_run(
    db_session: AsyncSession,
) -> None:
    """后台关怀和用户聊天可以同时拥有各自的根运行。"""

    username = f"care_run_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    care_task = ProactiveCareTask(
        user_id=user.id,
        kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        status=ProactiveCareTaskStatus.RUNNING,
        due_at=now,
        expires_at=now + timedelta(hours=12),
        lease_token=uuid4(),
        lease_expires_at=now + timedelta(minutes=20),
    )
    user_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="我现在想聊聊。",
    )
    assistant_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.GENERATING,
        content="",
    )
    db_session.add_all([care_task, user_message, assistant_message])
    await db_session.flush()

    proactive_run = AgentRun(
        user_id=user.id,
        trigger_message_id=None,
        trigger_care_task_id=care_task.id,
        result_message_id=None,
        parent_run_id=None,
        agent_name="proactive_care_agent",
        model="deepseek-v4-pro",
        status=AgentRunStatus.RUNNING,
    )
    db_session.add_all(
        [
            proactive_run,
            AgentRun(
                user_id=user.id,
                trigger_message_id=user_message.id,
                trigger_care_task_id=None,
                result_message_id=assistant_message.id,
                parent_run_id=None,
                agent_name="core_agent",
                model="deepseek-v4-pro",
                status=AgentRunStatus.RUNNING,
            ),
        ]
    )

    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                AgentRun(
                    user_id=user.id,
                    trigger_message_id=None,
                    trigger_care_task_id=care_task.id,
                    result_message_id=None,
                    parent_run_id=None,
                    agent_name="proactive_care_agent",
                    model="deepseek-v4-pro",
                    status=AgentRunStatus.RUNNING,
                )
            )
            await db_session.flush()

async def test_agent_run_requires_a_single_trigger(db_session: AsyncSession) -> None:
    """运行不能既没有用户消息，也没有服务器关怀任务。"""

    username = f"care_trigger_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                AgentRun(
                    user_id=user.id,
                    trigger_message_id=None,
                    trigger_care_task_id=None,
                    result_message_id=None,
                    parent_run_id=None,
                    agent_name="proactive_care_agent",
                    model="deepseek-v4-pro",
                    status=AgentRunStatus.RUNNING,
                )
            )
            await db_session.flush()

    now = datetime.now(timezone.utc)
    user_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="测试两个触发来源",
    )
    care_task = ProactiveCareTask(
        user_id=user.id,
        kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        status=ProactiveCareTaskStatus.SCHEDULED,
        due_at=now,
        expires_at=now + timedelta(hours=12),
    )
    db_session.add_all([user_message, care_task])
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                AgentRun(
                    user_id=user.id,
                    trigger_message_id=user_message.id,
                    trigger_care_task_id=care_task.id,
                    result_message_id=None,
                    parent_run_id=None,
                    agent_name="proactive_care_agent",
                    model="deepseek-v4-pro",
                    status=AgentRunStatus.RUNNING,
                )
            )
            await db_session.flush()


async def test_care_settings_start_without_unsolicited_contact(
    db_session: AsyncSession,
) -> None:
    """默认设置不会在用户尚未选择时开启日常或健康关怀。"""

    username = f"care_settings_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    settings = ProactiveCareSettings(user_id=user.id)
    db_session.add(settings)
    await db_session.flush()

    assert settings.routine_cadence == RoutineCareCadence.DISABLED
    assert settings.plan_follow_up_enabled is True
    assert settings.health_events_enabled is False
    assert settings.health_notification_preview_enabled is False
    assert settings.timezone == "Asia/Shanghai"


async def test_scheduled_task_cannot_keep_a_worker_lease(
    db_session: AsyncSession,
) -> None:
    """租约只属于 running 状态，任务重排后不能残留旧 Worker 的写权限。"""

    username = f"care_lease_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    now = datetime.now(timezone.utc)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                ProactiveCareTask(
                    user_id=user.id,
                    kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
                    status=ProactiveCareTaskStatus.SCHEDULED,
                    due_at=now,
                    expires_at=now + timedelta(hours=12),
                    lease_token=uuid4(),
                    lease_expires_at=now + timedelta(minutes=20),
                )
            )
            await db_session.flush()


async def test_each_care_plan_revision_has_one_follow_up_task(
    db_session: AsyncSession,
) -> None:
    """计划的同一版本即使被重试，也只能登记一项回访任务。"""

    username = f"care_plan_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    user_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="那我们明天下午再复盘这次散步。",
    )
    db_session.add(user_message)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    plan = CarePlan(
        user_id=user.id,
        summary="复盘散步后的身体感受",
        status=CarePlanStatus.ACTIVE,
        follow_up_at=now + timedelta(days=1),
        revision=1,
        created_by_message_id=user_message.id,
        last_changed_by_message_id=user_message.id,
    )
    db_session.add(plan)
    await db_session.flush()

    task = ProactiveCareTask(
        user_id=user.id,
        kind=ProactiveCareTaskKind.PLAN_FOLLOW_UP,
        status=ProactiveCareTaskStatus.SCHEDULED,
        due_at=plan.follow_up_at,
        expires_at=plan.follow_up_at + timedelta(hours=24),
        care_plan_id=plan.id,
        care_plan_revision=plan.revision,
    )
    db_session.add(task)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                ProactiveCareTask(
                    user_id=user.id,
                    kind=ProactiveCareTaskKind.PLAN_FOLLOW_UP,
                    status=ProactiveCareTaskStatus.SCHEDULED,
                    due_at=plan.follow_up_at,
                    expires_at=plan.follow_up_at + timedelta(hours=24),
                    care_plan_id=plan.id,
                    care_plan_revision=plan.revision,
                )
            )
            await db_session.flush()


async def test_each_message_has_one_delivery_per_installation(
    db_session: AsyncSession,
) -> None:
    """同一条聊天消息发往同一台安装时，重试只复用一条发送记录。"""

    username = f"push_delivery_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    login_session = LoginSession(
        user_id=user.id,
        refresh_token_hash=uuid4().hex + uuid4().hex,
        expires_at=now + timedelta(days=30),
    )
    message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="今天感觉怎么样？",
        completed_at=now,
    )
    db_session.add_all([login_session, message])
    await db_session.flush()

    installation = PushInstallation(
        id=uuid4(),
        login_session_id=login_session.id,
        expo_push_token="ExponentPushToken[test-only]",
        permission=PushPermissionState.GRANTED,
        platform=PushPlatform.ANDROID,
        app_version="1.0.0",
        last_seen_at=now,
    )
    db_session.add(installation)
    await db_session.flush()
    assert installation.registration_revision == 1

    delivery = PushDelivery(
        message_id=message.id,
        installation_id=installation.id,
        show_message_preview=True,
        status=PushDeliveryStatus.PENDING,
        next_attempt_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db_session.add(delivery)
    await db_session.flush()
    assert delivery.installation_revision == 1
    assert delivery.receipt_attempt_count == 0

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                PushDelivery(
                    message_id=message.id,
                    installation_id=installation.id,
                    show_message_preview=True,
                    status=PushDeliveryStatus.PENDING,
                    next_attempt_at=now,
                    expires_at=now + timedelta(hours=24),
                )
            )
            await db_session.flush()


async def test_user_deletion_removes_a_care_plan(db_session: AsyncSession) -> None:
    """用户删除账号时，计划对原消息的审计引用不能挡住级联清理。"""

    username = f"delete_plan_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="明天再复盘。",
    )
    db_session.add(message)
    await db_session.flush()
    db_session.add(
        CarePlan(
            user_id=user.id,
            summary="复盘",
            status=CarePlanStatus.ACTIVE,
            follow_up_at=datetime.now(timezone.utc) + timedelta(days=1),
            revision=1,
            created_by_message_id=message.id,
            last_changed_by_message_id=message.id,
        )
    )
    await db_session.flush()

    await db_session.delete(user)
    await db_session.flush()
