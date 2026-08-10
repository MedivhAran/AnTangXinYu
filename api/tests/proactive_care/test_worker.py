import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update

from antang_api.chat import prepare_chat_run
from antang_api.database import session_factory
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
    Message,
    MessageRole,
    MessageStatus,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    RoutineCareCadence,
    User,
)
from antang_api.proactive_care.worker import ProactiveCareWorker
from antang_api.settings import settings


async def test_expired_routine_task_schedules_the_next_cadence(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.DAILY
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(hours=13),
            expires_at=now - timedelta(hours=1),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def must_not_run(_task_id: UUID, _lease_token: UUID) -> None:
        raise AssertionError("过期任务不应进入处理函数")

    assert (
        await ProactiveCareWorker(process_task=must_not_run).run_once(now=now) is True
    )
    async with session_factory() as session:
        expired = await session.get(ProactiveCareTask, task_id)
        assert expired is not None
        assert expired.status == ProactiveCareTaskStatus.EXPIRED
        replacement = await session.scalar(
            select(ProactiveCareTask).where(
                ProactiveCareTask.user_id == committed_user_id,
                ProactiveCareTask.id != task_id,
                ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
                ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
            )
        )
        assert replacement is not None
        assert replacement.due_at == now + timedelta(days=1)


async def test_two_workers_process_a_due_task_once(
    committed_user_id: UUID,
) -> None:
    """两个 Worker 同时扫描时，只有拿到租约的一个进入处理函数。"""

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    processing_started = asyncio.Event()
    allow_completion = asyncio.Event()
    calls: list[tuple[UUID, UUID]] = []

    async def process_task(claimed_task_id: UUID, lease_token: UUID) -> None:
        calls.append((claimed_task_id, lease_token))
        processing_started.set()
        await allow_completion.wait()

        async with session_factory() as session:
            claimed = await session.scalar(
                select(ProactiveCareTask)
                .where(
                    ProactiveCareTask.id == claimed_task_id,
                    ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                    ProactiveCareTask.lease_token == lease_token,
                )
                .with_for_update()
            )
            assert claimed is not None
            claimed.status = ProactiveCareTaskStatus.COMPLETED
            claimed.lease_token = None
            claimed.lease_expires_at = None
            claimed.finished_at = now
            await session.commit()

    first_worker = ProactiveCareWorker(process_task=process_task)
    second_worker = ProactiveCareWorker(process_task=process_task)

    first_run = asyncio.create_task(first_worker.run_once(now=now))
    await asyncio.wait_for(processing_started.wait(), timeout=2)

    async with session_factory() as chat_session:
        prepared_chat = await asyncio.wait_for(
            prepare_chat_run(
                chat_session,
                committed_user_id,
                UUID("00000000-0000-0000-0000-000000000010"),
                "我现在想聊聊。",
            ),
            timeout=2,
        )
    assert prepared_chat.user_message_id is not None
    assert await second_worker.run_once(now=now) is False

    allow_completion.set()
    assert await asyncio.wait_for(first_run, timeout=2) is True
    assert [call[0] for call in calls] == [task_id]

    async with session_factory() as session:
        completed = await session.get(ProactiveCareTask, task_id)
        assert completed is not None
        assert completed.status == ProactiveCareTaskStatus.COMPLETED
        assert completed.attempt_count == 1
    assert completed.lease_token is None


async def test_claim_remembers_the_latest_chat_message(
    committed_user_id: UUID,
) -> None:
    """发送前可以判断 Agent 起草期间用户是否又说了话。"""

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        latest_message = Message(
            user_id=committed_user_id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="这是领取任务前的最后一条消息。",
        )
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
        session.add_all([latest_message, task])
        await session.commit()
        latest_message_id = latest_message.id
        task_id = task.id

    async def complete_task(claimed_task_id: UUID, lease_token: UUID) -> None:
        async with session_factory() as session:
            claimed = await session.scalar(
                select(ProactiveCareTask)
                .where(
                    ProactiveCareTask.id == claimed_task_id,
                    ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                    ProactiveCareTask.lease_token == lease_token,
                )
                .with_for_update()
            )
            assert claimed is not None
            assert claimed.claimed_through_message_id == latest_message_id
            claimed.status = ProactiveCareTaskStatus.COMPLETED
            claimed.lease_token = None
            claimed.lease_expires_at = None
            claimed.finished_at = now
            await session.commit()

    worker = ProactiveCareWorker(process_task=complete_task)

    assert await worker.run_once(now=now) is True

    async with session_factory() as session:
        completed = await session.get(ProactiveCareTask, task_id)
        assert completed is not None
        assert completed.claimed_through_message_id == latest_message_id


async def test_worker_waits_until_the_active_core_run_finishes(
    committed_user_id: UUID,
) -> None:
    """用户正在等 Core 回复时，后台任务留在队列中。"""

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        await prepare_chat_run(
            session,
            committed_user_id,
            UUID("00000000-0000-0000-0000-000000000020"),
            "先回答我这条消息。",
        )

    async with session_factory() as session:
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    called = False

    async def process_task(_task_id: UUID, _lease_token: UUID) -> None:
        nonlocal called
        called = True

    assert (
        await ProactiveCareWorker(process_task=process_task).run_once(now=now) is False
    )
    assert called is False

    async with session_factory() as session:
        waiting = await session.get(ProactiveCareTask, task_id)
        assert waiting is not None
        assert waiting.status == ProactiveCareTaskStatus.SCHEDULED
        assert waiting.attempt_count == 0


async def test_active_core_user_does_not_block_another_users_task(
    committed_user_id: UUID,
) -> None:
    """队首用户正在聊天时，Worker 仍可领取其他用户的到期任务。"""

    now = datetime.now(timezone.utc)
    suffix = uuid4().hex[:16]
    async with session_factory() as session:
        await prepare_chat_run(
            session,
            committed_user_id,
            uuid4(),
            "先完成我的回复。",
        )
        second_user = User(
            username=f"worker_{suffix}",
            username_normalized=f"worker_{suffix}",
            password_hash="test-only-password-hash",
        )
        session.add(second_user)
        await session.flush()
        blocked_task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=2),
            expires_at=now + timedelta(hours=1),
        )
        available_task = ProactiveCareTask(
            user_id=second_user.id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
        session.add_all([blocked_task, available_task])
        await session.commit()
        second_user_id = second_user.id
        blocked_task_id = blocked_task.id
        available_task_id = available_task.id

    calls: list[UUID] = []

    async def complete_task(task_id: UUID, lease_token: UUID) -> None:
        calls.append(task_id)
        async with session_factory() as session:
            task = await session.scalar(
                select(ProactiveCareTask)
                .where(
                    ProactiveCareTask.id == task_id,
                    ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                    ProactiveCareTask.lease_token == lease_token,
                )
                .with_for_update()
            )
            assert task is not None
            task.status = ProactiveCareTaskStatus.COMPLETED
            task.lease_token = None
            task.lease_expires_at = None
            task.finished_at = now
            await session.commit()

    try:
        assert (
            await ProactiveCareWorker(process_task=complete_task).run_once(now=now)
            is True
        )
        assert calls == [available_task_id]
        async with session_factory() as session:
            blocked = await session.get(ProactiveCareTask, blocked_task_id)
            assert blocked is not None
            assert blocked.status == ProactiveCareTaskStatus.SCHEDULED
    finally:
        async with session_factory() as session:
            await session.execute(delete(User).where(User.id == second_user_id))
            await session.commit()


async def test_locked_expired_task_does_not_make_a_scheduled_sibling_running(
    committed_user_id: UUID,
) -> None:
    """过期 running 行被别处锁住时，不能越过它领取同用户的另一项任务。"""

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        expired_running = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.RUNNING,
            due_at=now - timedelta(hours=2),
            expires_at=now + timedelta(hours=1),
            attempt_count=1,
            lease_token=UUID("00000000-0000-0000-0000-000000000030"),
            lease_expires_at=now - timedelta(minutes=1),
        )
        scheduled = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
        session.add_all([expired_running, scheduled])
        await session.commit()
        expired_id = expired_running.id
        scheduled_id = scheduled.id

    called = False

    async def process_task(_task_id: UUID, _lease_token: UUID) -> None:
        nonlocal called
        called = True

    async with session_factory() as blocker:
        locked = await blocker.scalar(
            select(ProactiveCareTask)
            .where(ProactiveCareTask.id == expired_id)
            .with_for_update()
        )
        assert locked is not None

        assert (
            await ProactiveCareWorker(process_task=process_task).run_once(now=now)
            is False
        )

    assert called is False
    async with session_factory() as session:
        waiting = await session.get(ProactiveCareTask, scheduled_id)
        assert waiting is not None
        assert waiting.status == ProactiveCareTaskStatus.SCHEDULED


async def test_expired_lease_is_recovered_with_a_new_token(
    committed_user_id: UUID,
) -> None:
    """租约过期后旧运行会失败，旧 Worker 的 token 也不能再写回。"""

    now = datetime.now(timezone.utc)
    old_token = UUID("00000000-0000-0000-0000-000000000001")
    async with session_factory() as session:
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.RUNNING,
            due_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=1),
            attempt_count=1,
            lease_token=old_token,
            lease_expires_at=now - timedelta(seconds=1),
            started_at=now - timedelta(minutes=20),
        )
        session.add(task)
        await session.flush()
        interrupted_run = AgentRun(
            user_id=committed_user_id,
            trigger_message_id=None,
            trigger_care_task_id=task.id,
            result_message_id=None,
            parent_run_id=None,
            agent_name="proactive_care_agent",
            model="deepseek-v4-pro",
            status=AgentRunStatus.RUNNING,
        )
        session.add(interrupted_run)
        await session.commit()
        task_id = task.id
        interrupted_run_id = interrupted_run.id

    seen_token: UUID | None = None

    async def process_task(claimed_task_id: UUID, lease_token: UUID) -> None:
        nonlocal seen_token
        seen_token = lease_token
        async with session_factory() as session:
            claimed = await session.scalar(
                select(ProactiveCareTask)
                .where(
                    ProactiveCareTask.id == claimed_task_id,
                    ProactiveCareTask.lease_token == lease_token,
                )
                .with_for_update()
            )
            assert claimed is not None
            claimed.status = ProactiveCareTaskStatus.COMPLETED
            claimed.lease_token = None
            claimed.lease_expires_at = None
            claimed.finished_at = now
            await session.commit()

    worker = ProactiveCareWorker(process_task=process_task)
    assert await worker.run_once(now=now) is True
    assert seen_token is not None
    assert seen_token != old_token

    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        interrupted_run = await session.get(AgentRun, interrupted_run_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.COMPLETED
        assert task.attempt_count == 2
        assert interrupted_run is not None
        assert interrupted_run.status == AgentRunStatus.FAILED
        assert interrupted_run.error_type == "CareLeaseExpired"

        stale_write = await session.execute(
            update(ProactiveCareTask)
            .where(
                ProactiveCareTask.id == task_id,
                ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                ProactiveCareTask.lease_token == old_token,
            )
            .values(status=ProactiveCareTaskStatus.FAILED)
        )
        assert stale_write.rowcount == 0


async def test_worker_stops_retrying_after_three_processor_failures(
    committed_user_id: UUID,
) -> None:
    """处理失败会明确重排；第三次仍失败后落为终态，不会无限重试。"""

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=1),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    async def fail_task(_task_id: UUID, _lease_token: UUID) -> None:
        raise RuntimeError("test processor failure")

    worker = ProactiveCareWorker(process_task=fail_task)
    for attempt in range(1, 4):
        assert await worker.run_once(now=now + timedelta(hours=attempt)) is True

    async with session_factory() as session:
        failed = await session.get(ProactiveCareTask, task_id)
        assert failed is not None
        assert failed.status == ProactiveCareTaskStatus.FAILED
        assert failed.attempt_count == 3
        assert failed.lease_token is None
        assert failed.outcome_reason == "processor_error:RuntimeError"


async def test_processor_failure_cancels_routine_when_replacement_exists(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        care_settings = await session.get(ProactiveCareSettings, committed_user_id)
        assert care_settings is not None
        care_settings.routine_cadence = RoutineCareCadence.DAILY
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=1),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    replacement_id: UUID | None = None

    async def fail_after_replacement(
        claimed_task_id: UUID,
        _lease_token: UUID,
    ) -> None:
        nonlocal replacement_id
        async with session_factory() as session:
            claimed = await session.get(ProactiveCareTask, claimed_task_id)
            assert claimed is not None
            replacement = ProactiveCareTask(
                user_id=claimed.user_id,
                kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
                status=ProactiveCareTaskStatus.SCHEDULED,
                due_at=now + timedelta(days=1),
                expires_at=now + timedelta(days=1, hours=12),
            )
            session.add(replacement)
            await session.commit()
            replacement_id = replacement.id
        raise RuntimeError("test processor failure")

    assert (
        await ProactiveCareWorker(process_task=fail_after_replacement).run_once(now=now)
        is True
    )
    assert replacement_id is not None

    async with session_factory() as session:
        old_task = await session.get(ProactiveCareTask, task_id)
        replacement = await session.get(ProactiveCareTask, replacement_id)
        assert old_task is not None
        assert old_task.status == ProactiveCareTaskStatus.CANCELLED
        assert old_task.outcome_reason == "routine_replaced"
        assert old_task.lease_token is None
        assert old_task.lease_expires_at is None
        assert replacement is not None
        assert replacement.status == ProactiveCareTaskStatus.SCHEDULED


async def test_processor_failure_closes_the_attempt_agent_run(
    committed_user_id: UUID,
) -> None:
    """处理链路失败时，本次已经创建的 AgentRun 也必须结束。"""

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    run_id: UUID | None = None
    tool_call_id: UUID | None = None

    async def fail_after_starting_agent(
        claimed_task_id: UUID,
        _lease_token: UUID,
    ) -> None:
        nonlocal run_id, tool_call_id
        async with session_factory() as session:
            run = AgentRun(
                user_id=committed_user_id,
                trigger_message_id=None,
                trigger_care_task_id=claimed_task_id,
                result_message_id=None,
                parent_run_id=None,
                agent_name="proactive_care_agent",
                model="deepseek-v4-pro",
                status=AgentRunStatus.RUNNING,
            )
            session.add(run)
            await session.flush()
            tool_call = AgentToolCall(
                agent_run_id=run.id,
                tool_call_id="failing-care-tool-call",
                tool_name="test_tool",
                model_turn_index=1,
                tool_call_index=0,
                arguments={},
                status=AgentToolCallStatus.RUNNING,
            )
            session.add(tool_call)
            await session.commit()
            run_id = run.id
            tool_call_id = tool_call.id
        raise RuntimeError("test processor failure")

    worker = ProactiveCareWorker(process_task=fail_after_starting_agent)
    assert await worker.run_once(now=now) is True
    assert run_id is not None
    assert tool_call_id is not None

    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        run = await session.get(AgentRun, run_id)
        tool_call = await session.get(AgentToolCall, tool_call_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.SCHEDULED
        assert run is not None
        assert run.status == AgentRunStatus.FAILED
        assert run.error_type == "CareTaskProcessorError"
        assert tool_call is not None
        assert tool_call.status == AgentToolCallStatus.FAILED
        assert tool_call.error_type == "CareTaskProcessorError"


async def test_claim_closes_a_stale_agent_run_on_a_scheduled_task(
    committed_user_id: UUID,
) -> None:
    """旧故障若留下 active AgentRun，新租约仍能开始新的尝试。"""

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
        session.add(task)
        await session.flush()
        stale_run = AgentRun(
            user_id=committed_user_id,
            trigger_message_id=None,
            trigger_care_task_id=task.id,
            result_message_id=None,
            parent_run_id=None,
            agent_name="proactive_care_agent",
            model="deepseek-v4-pro",
            status=AgentRunStatus.RUNNING,
        )
        session.add(stale_run)
        await session.flush()
        stale_tool_call = AgentToolCall(
            agent_run_id=stale_run.id,
            tool_call_id="stale-care-tool-call",
            tool_name="test_tool",
            model_turn_index=1,
            tool_call_index=0,
            arguments={},
            status=AgentToolCallStatus.RUNNING,
        )
        session.add(stale_tool_call)
        await session.commit()
        stale_run_id = stale_run.id
        stale_tool_call_id = stale_tool_call.id

    replacement_run_id: UUID | None = None

    async def complete_with_replacement_run(
        claimed_task_id: UUID,
        lease_token: UUID,
    ) -> None:
        nonlocal replacement_run_id
        async with session_factory() as session:
            claimed = await session.scalar(
                select(ProactiveCareTask)
                .where(
                    ProactiveCareTask.id == claimed_task_id,
                    ProactiveCareTask.status == ProactiveCareTaskStatus.RUNNING,
                    ProactiveCareTask.lease_token == lease_token,
                )
                .with_for_update()
            )
            assert claimed is not None
            replacement_run = AgentRun(
                user_id=committed_user_id,
                trigger_message_id=None,
                trigger_care_task_id=claimed_task_id,
                result_message_id=None,
                parent_run_id=None,
                agent_name="proactive_care_agent",
                model="deepseek-v4-pro",
                status=AgentRunStatus.COMPLETED,
                finished_at=now,
            )
            session.add(replacement_run)
            claimed.status = ProactiveCareTaskStatus.COMPLETED
            claimed.lease_token = None
            claimed.lease_expires_at = None
            claimed.finished_at = now
            await session.commit()
            replacement_run_id = replacement_run.id

    worker = ProactiveCareWorker(process_task=complete_with_replacement_run)
    assert await worker.run_once(now=now) is True
    assert replacement_run_id is not None

    async with session_factory() as session:
        stale_run = await session.get(AgentRun, stale_run_id)
        stale_tool_call = await session.get(AgentToolCall, stale_tool_call_id)
        replacement_run = await session.get(AgentRun, replacement_run_id)
        assert stale_run is not None
        assert stale_run.status == AgentRunStatus.FAILED
        assert stale_run.error_type == "CareAttemptAbandoned"
        assert stale_tool_call is not None
        assert stale_tool_call.status == AgentToolCallStatus.FAILED
        assert stale_tool_call.error_type == "CareAttemptAbandoned"
        assert replacement_run is not None
        assert replacement_run.status == AgentRunStatus.COMPLETED


async def test_processor_timeout_releases_the_only_worker_loop(
    committed_user_id: UUID,
    monkeypatch,
) -> None:
    """处理协程即使卡住，也会在租约窗口内失败并重新进入扫描。"""

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        task = ProactiveCareTask(
            user_id=committed_user_id,
            kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            status=ProactiveCareTaskStatus.SCHEDULED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    monkeypatch.setattr(settings, "proactive_care_lease_minutes", 0.001)

    async def never_returns(_task_id: UUID, _lease_token: UUID) -> None:
        await asyncio.Event().wait()

    worker = ProactiveCareWorker(process_task=never_returns)
    assert await asyncio.wait_for(worker.run_once(now=now), timeout=1) is True

    async with session_factory() as session:
        task = await session.get(ProactiveCareTask, task_id)
        assert task is not None
        assert task.status == ProactiveCareTaskStatus.SCHEDULED
        assert task.outcome_reason == "processor_error:TimeoutError"
