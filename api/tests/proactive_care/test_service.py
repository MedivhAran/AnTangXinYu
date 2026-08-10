from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.chat import prepare_chat_run
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    Message,
    MessageRole,
    MessageStatus,
    PersonalProfile,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    RoutineCareCadence,
    User,
    WearableImport,
    WearableObservation,
    WearableRecordTombstone,
    WearableRecordType,
)
from antang_api.proactive_care.heart_rate_shadow import evaluate_heart_rate_shadow
from antang_api.proactive_care.service import (
    record_health_import,
    record_user_activity,
)


async def _evaluated_heart_rate_task(
    session: AsyncSession,
) -> tuple[User, ProactiveCareTask, WearableObservation, datetime]:
    received_at = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    suffix = uuid4().hex[:12]
    user = User(
        username=f"care_recheck_{suffix}",
        username_normalized=f"care_recheck_{suffix}",
        password_hash="test-only-password-hash",
    )
    session.add(user)
    await session.flush()
    session.add_all(
        [
            PersonalProfile(user_id=user.id, age_years=30),
            ProactiveCareSettings(user_id=user.id, timezone="UTC"),
        ]
    )
    imported = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash="1" * 64,
        record_type=WearableRecordType.HEART_RATE,
        health_context_complete=True,
        records_created=1,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
        created_at=received_at,
    )
    session.add(imported)
    await session.flush()

    start = received_at - timedelta(minutes=30)
    observation = WearableObservation(
        user_id=user.id,
        external_record_id=f"heart-{suffix}",
        record_type=WearableRecordType.HEART_RATE,
        start_time=start,
        end_time=received_at - timedelta(minutes=1),
        start_zone_offset_seconds=0,
        end_zone_offset_seconds=0,
        data={
            "samples": [
                {
                    "time": (start + timedelta(minutes=index)).isoformat(),
                    "beats_per_minute": 110,
                }
                for index in range(30)
            ]
        },
        data_hash="2" * 64,
        source_package="com.huami.watch.hmwatchmanager",
        recording_method=2,
        device=None,
        source_last_modified_at=received_at,
        last_import_id=imported.id,
    )
    task = ProactiveCareTask(
        user_id=user.id,
        kind=ProactiveCareTaskKind.HEALTH_EVENT,
        status=ProactiveCareTaskStatus.SKIPPED,
        due_at=received_at,
        expires_at=received_at + timedelta(hours=24),
        wearable_import_id=imported.id,
        source_version_hash=imported.request_hash,
        outcome_reason="shadow_candidate",
        finished_at=received_at,
    )
    session.add_all([observation, task])
    await session.flush()
    result = await evaluate_heart_rate_shadow(
        session,
        task=task,
        timezone_name="UTC",
    )
    assert result.reason == "candidate"
    return user, task, observation, received_at


async def test_user_message_records_a_reply_and_restarts_routine_cadence(
    db_session: AsyncSession,
) -> None:
    """用户回复主动消息后，回复关系和下一次日常候选一起保存。"""

    username = f"care_activity_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        ProactiveCareSettings(
            user_id=user.id,
            routine_cadence=RoutineCareCadence.EVERY_3_DAYS,
        )
    )

    now = datetime.now(timezone.utc)
    previous_task = ProactiveCareTask(
        user_id=user.id,
        kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        status=ProactiveCareTaskStatus.COMPLETED,
        due_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=11),
        finished_at=now,
    )
    proactive_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="这两天过得怎么样？",
        completed_at=now,
    )
    db_session.add_all([previous_task, proactive_message])
    await db_session.flush()
    db_session.add(
        AgentRun(
            user_id=user.id,
            trigger_message_id=None,
            trigger_care_task_id=previous_task.id,
            result_message_id=proactive_message.id,
            parent_run_id=None,
            agent_name="proactive_care_agent",
            model="deepseek-v4-pro",
            status=AgentRunStatus.COMPLETED,
            finished_at=now,
        )
    )
    await db_session.commit()

    prepared = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "还不错。",
    )

    await db_session.refresh(previous_task)
    assert previous_task.response_message_id == prepared.user_message_id

    user_message = await db_session.get(Message, prepared.user_message_id)
    assert user_message is not None
    scheduled_task = await db_session.scalar(
        select(ProactiveCareTask).where(
            ProactiveCareTask.user_id == user.id,
            ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
        )
    )
    assert scheduled_task is not None
    assert scheduled_task.due_at == user_message.created_at + timedelta(days=3)
    assert scheduled_task.expires_at == scheduled_task.due_at + timedelta(hours=12)


async def test_health_import_with_changes_registers_one_task(
    db_session: AsyncSession,
) -> None:
    """同一次手环导入即使接口重放，也只登记一项后台检查任务。"""

    username = f"care_import_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    changed_import = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash="a" * 64,
        record_type=WearableRecordType.HEART_RATE,
        health_context_complete=True,
        records_created=1,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
    )
    unchanged_import = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash="b" * 64,
        record_type=WearableRecordType.HEART_RATE,
        health_context_complete=True,
        records_created=0,
        records_updated=0,
        records_unchanged=1,
        records_deleted=0,
    )
    db_session.add_all([changed_import, unchanged_import])
    await db_session.flush()

    first_task_id = await record_health_import(
        db_session,
        user_id=user.id,
        import_id=changed_import.id,
    )
    repeated_task_id = await record_health_import(
        db_session,
        user_id=user.id,
        import_id=changed_import.id,
    )
    unchanged_task_id = await record_health_import(
        db_session,
        user_id=user.id,
        import_id=unchanged_import.id,
    )
    await db_session.flush()

    assert first_task_id is not None
    assert repeated_task_id == first_task_id
    assert unchanged_task_id is None
    tasks = list(
        await db_session.scalars(
            select(ProactiveCareTask).where(
                ProactiveCareTask.wearable_import_id.in_(
                    [changed_import.id, unchanged_import.id]
                )
            )
        )
    )
    assert [task.id for task in tasks] == [first_task_id]


async def test_non_heart_rate_import_does_not_register_health_task(
    db_session: AsyncSession,
) -> None:
    username = f"care_non_heart_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    imported = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash="d" * 64,
        record_type=WearableRecordType.STEPS,
        health_context_complete=False,
        records_created=1,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
    )
    db_session.add(imported)
    await db_session.flush()

    assert (
        await record_health_import(
            db_session,
            user_id=user.id,
            import_id=imported.id,
        )
        is None
    )


@pytest.mark.parametrize("change", ["update", "delete"])
async def test_heart_rate_change_rechecks_tasks_that_used_the_record(
    db_session: AsyncSession,
    change: str,
) -> None:
    user, previous_task, observation, received_at = (
        await _evaluated_heart_rate_task(db_session)
    )
    changed_import = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash="3" * 64,
        record_type=WearableRecordType.HEART_RATE,
        health_context_complete=True,
        records_created=0,
        records_updated=1 if change == "update" else 0,
        records_unchanged=0,
        records_deleted=1 if change == "delete" else 0,
        created_at=received_at + timedelta(minutes=5),
    )
    db_session.add(changed_import)
    await db_session.flush()
    if change == "update":
        observation.data_hash = "4" * 64
        observation.last_import_id = changed_import.id
    else:
        observation.deleted_at = changed_import.created_at
        db_session.add(
            WearableRecordTombstone(
                user_id=user.id,
                external_record_id=observation.external_record_id,
                import_id=changed_import.id,
                deleted_at=changed_import.created_at,
            )
        )
    await db_session.flush()

    new_task_id = await record_health_import(
        db_session,
        user_id=user.id,
        import_id=changed_import.id,
    )

    assert new_task_id is not None
    assert new_task_id != previous_task.id
    assert previous_task.health_event_key is None
    assert previous_task.outcome_reason == "shadow_no_current_samples"
    assert previous_task.health_evidence is not None
    assert previous_task.health_evidence["reason"] == "no_current_samples"


async def test_context_changes_recheck_affected_heart_rate_task_without_new_task(
    db_session: AsyncSession,
) -> None:
    user, heart_task, heart_rate, received_at = await _evaluated_heart_rate_task(
        db_session
    )
    exercise_import = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash="5" * 64,
        record_type=WearableRecordType.EXERCISE,
        health_context_complete=False,
        records_created=1,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
        created_at=received_at + timedelta(minutes=1),
    )
    db_session.add(exercise_import)
    await db_session.flush()
    exercise = WearableObservation(
        user_id=user.id,
        external_record_id=f"exercise-{uuid4().hex}",
        record_type=WearableRecordType.EXERCISE,
        start_time=heart_rate.start_time,
        end_time=heart_rate.end_time,
        start_zone_offset_seconds=0,
        end_zone_offset_seconds=0,
        data={"exercise_type": 56, "title": None},
        data_hash="6" * 64,
        source_package="com.huami.watch.hmwatchmanager",
        recording_method=2,
        device=None,
        source_last_modified_at=received_at,
        last_import_id=exercise_import.id,
    )
    db_session.add(exercise)
    await db_session.flush()

    assert (
        await record_health_import(
            db_session,
            user_id=user.id,
            import_id=exercise_import.id,
        )
        is None
    )
    assert heart_task.outcome_reason == "shadow_exercise_context"
    assert heart_task.health_event_key is None
    assert heart_task.status == ProactiveCareTaskStatus.SKIPPED

    moved_import = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash="7" * 64,
        record_type=WearableRecordType.EXERCISE,
        health_context_complete=False,
        records_created=0,
        records_updated=1,
        records_unchanged=0,
        records_deleted=0,
        created_at=received_at + timedelta(minutes=5),
    )
    db_session.add(moved_import)
    await db_session.flush()
    exercise.start_time = received_at + timedelta(hours=3)
    exercise.end_time = received_at + timedelta(hours=4)
    exercise.data_hash = "8" * 64
    exercise.last_import_id = moved_import.id
    await db_session.flush()

    assert (
        await record_health_import(
            db_session,
            user_id=user.id,
            import_id=moved_import.id,
        )
        is None
    )
    assert heart_task.outcome_reason is None
    assert heart_task.health_event_key is not None
    assert heart_task.status == ProactiveCareTaskStatus.SCHEDULED
    assert heart_task.due_at == moved_import.created_at
    assert heart_task.finished_at is None

    assert (
        await record_health_import(
            db_session,
            user_id=user.id,
            import_id=moved_import.id,
        )
        is None
    )
    scheduled_health_tasks = list(
        await db_session.scalars(
            select(ProactiveCareTask).where(
                ProactiveCareTask.user_id == user.id,
                ProactiveCareTask.kind == ProactiveCareTaskKind.HEALTH_EVENT,
                ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
            )
        )
    )
    assert [task.id for task in scheduled_health_tasks] == [heart_task.id]

    sleep_import = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash="9" * 64,
        record_type=WearableRecordType.SLEEP,
        health_context_complete=False,
        records_created=1,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
        created_at=received_at + timedelta(minutes=10),
    )
    db_session.add(sleep_import)
    await db_session.flush()
    sleep = WearableObservation(
        user_id=user.id,
        external_record_id=f"sleep-{uuid4().hex}",
        record_type=WearableRecordType.SLEEP,
        start_time=heart_rate.start_time,
        end_time=heart_rate.end_time,
        start_zone_offset_seconds=0,
        end_zone_offset_seconds=0,
        data={"title": None, "stages": []},
        data_hash="a" * 64,
        source_package="com.huami.watch.hmwatchmanager",
        recording_method=2,
        device=None,
        source_last_modified_at=received_at,
        last_import_id=sleep_import.id,
    )
    db_session.add(sleep)
    await db_session.flush()
    await record_health_import(
        db_session,
        user_id=user.id,
        import_id=sleep_import.id,
    )
    assert heart_task.outcome_reason == "shadow_sleep_context"
    assert heart_task.status == ProactiveCareTaskStatus.SKIPPED
    assert heart_task.finished_at == sleep_import.created_at

    delete_import = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash="b" * 64,
        record_type=WearableRecordType.SLEEP,
        health_context_complete=False,
        records_created=0,
        records_updated=0,
        records_unchanged=0,
        records_deleted=1,
        created_at=received_at + timedelta(minutes=15),
    )
    db_session.add(delete_import)
    await db_session.flush()
    sleep.deleted_at = delete_import.created_at
    db_session.add(
        WearableRecordTombstone(
            user_id=user.id,
            external_record_id=sleep.external_record_id,
            import_id=delete_import.id,
            deleted_at=delete_import.created_at,
        )
    )
    await db_session.flush()

    assert (
        await record_health_import(
            db_session,
            user_id=user.id,
            import_id=delete_import.id,
        )
        is None
    )
    assert heart_task.outcome_reason is None
    assert heart_task.health_event_key is not None
    assert heart_task.status == ProactiveCareTaskStatus.SCHEDULED
    assert heart_task.due_at == delete_import.created_at
    assert heart_task.finished_at is None


async def test_completed_health_message_keeps_its_sent_evidence(
    db_session: AsyncSession,
) -> None:
    """后到的运动数据不能改写已经发给用户的心率事实。"""

    user, task, heart_rate, received_at = await _evaluated_heart_rate_task(db_session)
    task.status = ProactiveCareTaskStatus.COMPLETED
    task.outcome_reason = "message_created"
    task.finished_at = received_at
    sent_event_key = task.health_event_key
    sent_evidence = deepcopy(task.health_evidence)
    message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="这是已经发出的心率关怀。",
        completed_at=received_at,
    )
    db_session.add(message)
    await db_session.flush()
    db_session.add(
        AgentRun(
            user_id=user.id,
            trigger_message_id=None,
            trigger_care_task_id=task.id,
            result_message_id=message.id,
            parent_run_id=None,
            agent_name="proactive_care_agent",
            model="deepseek-v4-pro",
            status=AgentRunStatus.COMPLETED,
            finished_at=received_at,
        )
    )

    exercise_import = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash="e" * 64,
        record_type=WearableRecordType.EXERCISE,
        health_context_complete=False,
        records_created=1,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
        created_at=received_at + timedelta(minutes=5),
    )
    db_session.add(exercise_import)
    await db_session.flush()
    db_session.add(
        WearableObservation(
            user_id=user.id,
            external_record_id=f"exercise-{uuid4().hex}",
            record_type=WearableRecordType.EXERCISE,
            start_time=heart_rate.start_time,
            end_time=heart_rate.end_time,
            start_zone_offset_seconds=0,
            end_zone_offset_seconds=0,
            data={"exercise_type": 56, "title": None},
            data_hash="f" * 64,
            source_package="com.huami.watch.hmwatchmanager",
            recording_method=2,
            device=None,
            source_last_modified_at=received_at,
            last_import_id=exercise_import.id,
        )
    )
    await db_session.flush()

    assert (
        await record_health_import(
            db_session,
            user_id=user.id,
            import_id=exercise_import.id,
        )
        is None
    )
    assert task.status == ProactiveCareTaskStatus.COMPLETED
    assert task.outcome_reason == "message_created"
    assert task.health_event_key == sent_event_key
    assert task.health_evidence == sent_evidence


async def test_health_import_cannot_register_another_users_import(
    db_session: AsyncSession,
) -> None:
    first_name = f"care_import_a_{uuid4().hex[:12]}"
    second_name = f"care_import_b_{uuid4().hex[:12]}"
    first = User(
        username=first_name,
        username_normalized=first_name,
        password_hash="test-only-password-hash",
    )
    second = User(
        username=second_name,
        username_normalized=second_name,
        password_hash="test-only-password-hash",
    )
    db_session.add_all([first, second])
    await db_session.flush()
    imported = WearableImport(
        user_id=first.id,
        client_sync_id=uuid4(),
        request_hash="c" * 64,
        record_type=WearableRecordType.HEART_RATE,
        health_context_complete=True,
        records_created=1,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
    )
    db_session.add(imported)
    await db_session.flush()

    with pytest.raises(RuntimeError, match="找不到当前用户的手环导入记录"):
        await record_health_import(
            db_session,
            user_id=second.id,
            import_id=imported.id,
        )


async def test_user_activity_does_not_link_another_users_care_task(
    db_session: AsyncSession,
) -> None:
    first_name = f"care_reply_a_{uuid4().hex[:12]}"
    second_name = f"care_reply_b_{uuid4().hex[:12]}"
    first = User(
        username=first_name,
        username_normalized=first_name,
        password_hash="test-only-password-hash",
    )
    second = User(
        username=second_name,
        username_normalized=second_name,
        password_hash="test-only-password-hash",
    )
    db_session.add_all([first, second])
    await db_session.flush()
    db_session.add(ProactiveCareSettings(user_id=second.id))

    now = datetime.now(timezone.utc)
    first_task = ProactiveCareTask(
        user_id=first.id,
        kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        status=ProactiveCareTaskStatus.COMPLETED,
        due_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=11),
        finished_at=now,
    )
    second_assistant_message = Message(
        user_id=second.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="第二位用户看到的消息",
        completed_at=now,
    )
    db_session.add_all([first_task, second_assistant_message])
    await db_session.flush()
    db_session.add(
        AgentRun(
            user_id=first.id,
            trigger_message_id=None,
            trigger_care_task_id=first_task.id,
            result_message_id=second_assistant_message.id,
            parent_run_id=None,
            agent_name="proactive_care_agent",
            model="deepseek-v4-pro",
            status=AgentRunStatus.COMPLETED,
            finished_at=now,
        )
    )
    await db_session.flush()
    second_user_message = Message(
        user_id=second.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="好的",
    )
    db_session.add(second_user_message)
    await db_session.flush()

    await record_user_activity(
        db_session,
        user_id=second.id,
        message_id=second_user_message.id,
    )

    await db_session.refresh(first_task)
    assert first_task.response_message_id is None
