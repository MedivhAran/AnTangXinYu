from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    PersonalProfile,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    User,
    WearableImport,
    WearableObservation,
    WearableRecordType,
)
from antang_api.proactive_care.heart_rate_shadow import (
    HEART_RATE_RULE_ID,
    HEART_RATE_RULE_VERSION,
    evaluate_heart_rate_shadow,
)


async def _heart_rate_task(
    session: AsyncSession,
    *,
    received_at: datetime,
    values: list[int],
    age_years: int | None = 30,
    context_complete: bool = True,
    external_id: str | None = None,
    source_package: str = "com.huami.watch.hmwatchmanager",
    existing_user: User | None = None,
) -> tuple[ProactiveCareTask, WearableObservation]:
    suffix = uuid4().hex[:12]
    user = existing_user
    if user is None:
        user = User(
            username=f"heart_shadow_{suffix}",
            username_normalized=f"heart_shadow_{suffix}",
            password_hash="test-only",
        )
        session.add(user)
        await session.flush()
        session.add(PersonalProfile(user_id=user.id, age_years=age_years))
    imported = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash=uuid4().hex + uuid4().hex,
        record_type=WearableRecordType.HEART_RATE,
        health_context_complete=context_complete,
        records_created=1,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
        created_at=received_at,
    )
    session.add(imported)
    await session.flush()

    start = received_at - timedelta(minutes=len(values))
    samples = [
        {
            "time": (start + timedelta(minutes=index)).isoformat(),
            "beats_per_minute": value,
        }
        for index, value in enumerate(values)
    ]
    observation = WearableObservation(
        user_id=user.id,
        external_record_id=external_id or f"heart-rate-{suffix}",
        record_type=WearableRecordType.HEART_RATE,
        start_time=start,
        end_time=start + timedelta(minutes=len(values) - 1),
        start_zone_offset_seconds=28800,
        end_zone_offset_seconds=28800,
        data={"samples": samples},
        data_hash=uuid4().hex + uuid4().hex,
        source_package=source_package,
        recording_method=2,
        device={"manufacturer": "Amazfit", "model": "Active 2"},
        source_last_modified_at=received_at,
        last_import_id=imported.id,
    )
    task = ProactiveCareTask(
        user_id=user.id,
        kind=ProactiveCareTaskKind.HEALTH_EVENT,
        status=ProactiveCareTaskStatus.SCHEDULED,
        due_at=received_at,
        expires_at=received_at + timedelta(hours=24),
        wearable_import_id=imported.id,
        source_version_hash=imported.request_hash,
    )
    session.add_all([observation, task])
    await session.flush()
    return task, observation


async def test_sustained_recent_automatic_high_rate_becomes_shadow_candidate(
    db_session: AsyncSession,
) -> None:
    received_at = datetime(2026, 7, 18, 10, 0, 30, tzinfo=timezone.utc)
    task, _ = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=[110] * 30,
    )

    result = await evaluate_heart_rate_shadow(
        db_session,
        task=task,
        timezone_name="Asia/Shanghai",
    )

    assert result.decision == "candidate"
    assert result.reason == "candidate"
    assert result.direction == "high"
    assert result.covered_minutes == 30
    assert result.sample_count == 30
    assert result.abnormal_minutes == 30
    assert result.minimum_bpm == result.median_bpm == result.maximum_bpm == 110
    assert result.event_key is not None
    assert result.event_key.startswith("heart-rate-shadow-v0:high:")
    assert result.duplicate_of_task_id is None
    assert task.rule_id == HEART_RATE_RULE_ID
    assert task.rule_version == HEART_RATE_RULE_VERSION
    assert task.health_event_key == result.event_key
    assert task.health_evidence == result.evidence()


async def test_context_must_be_complete_before_a_window_can_match(
    db_session: AsyncSession,
) -> None:
    task, _ = await _heart_rate_task(
        db_session,
        received_at=datetime(2026, 7, 18, 10, tzinfo=timezone.utc),
        values=[110] * 30,
        context_complete=False,
    )

    result = await evaluate_heart_rate_shadow(
        db_session,
        task=task,
        timezone_name="UTC",
    )

    assert result.decision == "blocked"
    assert result.reason == "context_unknown"
    assert result.event_key is None


async def test_low_rate_needs_eighty_percent_of_covered_minutes(
    db_session: AsyncSession,
) -> None:
    received_at = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    candidate_task, _ = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=([45] * 4 + [70]) * 6,
    )
    below_threshold_task, _ = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=([45] * 3 + [70]) * 7 + [45, 45],
    )

    candidate = await evaluate_heart_rate_shadow(
        db_session,
        task=candidate_task,
        timezone_name="UTC",
    )
    below_threshold = await evaluate_heart_rate_shadow(
        db_session,
        task=below_threshold_task,
        timezone_name="UTC",
    )

    assert candidate.decision == "candidate"
    assert candidate.direction == "low"
    assert candidate.abnormal_fraction == 0.8
    assert (below_threshold.decision, below_threshold.reason) == (
        "no_match",
        "threshold_not_sustained",
    )


async def test_exercise_context_blocks_high_heart_rate(
    db_session: AsyncSession,
) -> None:
    received_at = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    task, observation = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=[112] * 30,
    )
    db_session.add(
        WearableObservation(
            user_id=task.user_id,
            external_record_id=f"exercise-{uuid4().hex}",
            record_type=WearableRecordType.EXERCISE,
            start_time=observation.start_time - timedelta(minutes=5),
            end_time=observation.end_time,
            start_zone_offset_seconds=28800,
            end_zone_offset_seconds=28800,
            data={"exercise_type": 56, "title": None},
            data_hash=uuid4().hex + uuid4().hex,
            source_package="com.huami.watch.hmwatchmanager",
            recording_method=2,
            device=None,
            source_last_modified_at=received_at,
            last_import_id=observation.last_import_id,
        )
    )
    await db_session.flush()

    result = await evaluate_heart_rate_shadow(
        db_session,
        task=task,
        timezone_name="Asia/Shanghai",
    )

    assert result.decision == "blocked"
    assert result.reason == "exercise_context"
    assert result.exercise_overlap is True
    assert result.event_key is None


async def test_step_activity_blocks_and_is_saved_with_the_evidence(
    db_session: AsyncSession,
) -> None:
    received_at = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    task, heart_rate = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=[112] * 30,
    )
    step_id = f"steps-{uuid4().hex}"
    step_hash = uuid4().hex + uuid4().hex
    db_session.add(
        WearableObservation(
            user_id=task.user_id,
            external_record_id=step_id,
            record_type=WearableRecordType.STEPS,
            start_time=heart_rate.start_time,
            end_time=heart_rate.end_time,
            start_zone_offset_seconds=28800,
            end_zone_offset_seconds=28800,
            data={"count": 25},
            data_hash=step_hash,
            source_package="com.huami.watch.hmwatchmanager",
            recording_method=2,
            device=None,
            source_last_modified_at=received_at,
            last_import_id=heart_rate.last_import_id,
        )
    )
    await db_session.flush()

    result = await evaluate_heart_rate_shadow(
        db_session,
        task=task,
        timezone_name="Asia/Shanghai",
    )

    assert (result.decision, result.reason) == ("blocked", "step_activity")
    assert result.step_count == 25
    assert result.context_records == {step_id: step_hash}
    assert result.event_key is None


async def test_old_and_minor_data_never_become_candidates(
    db_session: AsyncSession,
) -> None:
    received_at = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    stale_task, stale_observation = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=[110] * 30,
    )
    stale_observation.start_time -= timedelta(hours=2)
    stale_observation.end_time -= timedelta(hours=2)
    stale_observation.data = {
        "samples": [
            {
                **sample,
                "time": (
                    datetime.fromisoformat(str(sample["time"])) - timedelta(hours=2)
                ).isoformat(),
            }
            for sample in stale_observation.data["samples"]
        ]
    }
    minor_task, _ = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=[110] * 30,
        age_years=17,
    )
    await db_session.flush()

    stale = await evaluate_heart_rate_shadow(
        db_session,
        task=stale_task,
        timezone_name="UTC",
    )
    minor = await evaluate_heart_rate_shadow(
        db_session,
        task=minor_task,
        timezone_name="UTC",
    )

    assert (stale.decision, stale.reason) == ("blocked", "stale_samples")
    assert (minor.decision, minor.reason) == ("blocked", "not_adult")


async def test_fresh_normal_sample_cannot_make_an_old_abnormal_window_fresh(
    db_session: AsyncSession,
) -> None:
    received_at = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    task, observation = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=[110] * 30,
    )
    old_start = received_at - timedelta(days=2)
    observation.start_time = old_start
    observation.end_time = received_at - timedelta(minutes=1)
    observation.data = {
        "samples": [
            {
                "time": (old_start + timedelta(minutes=index)).isoformat(),
                "beats_per_minute": 110,
            }
            for index in range(30)
        ]
        + [
            {
                "time": (received_at - timedelta(minutes=1)).isoformat(),
                "beats_per_minute": 75,
            }
        ]
    }
    await db_session.flush()

    result = await evaluate_heart_rate_shadow(
        db_session,
        task=task,
        timezone_name="UTC",
    )

    assert (result.decision, result.reason) == (
        "no_match",
        "insufficient_coverage",
    )
    assert result.event_key is None


async def test_adjacent_same_direction_windows_are_linked_to_one_episode(
    db_session: AsyncSession,
) -> None:
    first_task, _ = await _heart_rate_task(
        db_session,
        received_at=datetime(2026, 7, 18, 8, tzinfo=timezone.utc),
        values=[110] * 30,
    )
    first = await evaluate_heart_rate_shadow(
        db_session,
        task=first_task,
        timezone_name="Asia/Shanghai",
    )
    first_user = await db_session.get(User, first_task.user_id)
    assert first_user is not None
    second_task, _ = await _heart_rate_task(
        db_session,
        received_at=datetime(2026, 7, 18, 8, 20, tzinfo=timezone.utc),
        values=[115] * 30,
        existing_user=first_user,
    )

    second = await evaluate_heart_rate_shadow(
        db_session,
        task=second_task,
        timezone_name="Asia/Shanghai",
    )

    assert first.decision == "candidate"
    assert second.decision == "candidate"
    assert second.reason == "duplicate_candidate"
    assert second.event_key == first.event_key
    assert second.duplicate_of_task_id == first_task.id
    assert second_task.duplicate_of_task_id == first_task.id


    third_task, _ = await _heart_rate_task(
        db_session,
        received_at=datetime(2026, 7, 18, 9, tzinfo=timezone.utc),
        values=[113] * 30,
        existing_user=first_user,
    )
    third = await evaluate_heart_rate_shadow(
        db_session,
        task=third_task,
        timezone_name="Asia/Shanghai",
    )

    assert third.reason == "duplicate_candidate"
    assert third.event_key == first.event_key
    assert third.duplicate_of_task_id == first_task.id


async def test_separate_same_day_windows_start_separate_episodes(
    db_session: AsyncSession,
) -> None:
    first_task, _ = await _heart_rate_task(
        db_session,
        received_at=datetime(2026, 7, 18, 8, tzinfo=timezone.utc),
        values=[110] * 30,
    )
    first = await evaluate_heart_rate_shadow(
        db_session,
        task=first_task,
        timezone_name="Asia/Shanghai",
    )
    user = await db_session.get(User, first_task.user_id)
    assert user is not None
    second_task, _ = await _heart_rate_task(
        db_session,
        received_at=datetime(2026, 7, 18, 11, tzinfo=timezone.utc),
        values=[115] * 30,
        existing_user=user,
    )

    second = await evaluate_heart_rate_shadow(
        db_session,
        task=second_task,
        timezone_name="Asia/Shanghai",
    )

    assert first.decision == second.decision == "candidate"
    assert second.reason == "candidate"
    assert second.event_key != first.event_key
    assert second.duplicate_of_task_id is None


async def test_deleted_source_does_not_absorb_a_later_candidate(
    db_session: AsyncSession,
) -> None:
    received_at = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    first_task, first_observation = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=[110] * 30,
    )
    first = await evaluate_heart_rate_shadow(
        db_session,
        task=first_task,
        timezone_name="UTC",
    )
    first_observation.deleted_at = received_at + timedelta(minutes=1)
    user = await db_session.get(User, first_task.user_id)
    assert user is not None
    later_task, _ = await _heart_rate_task(
        db_session,
        received_at=received_at + timedelta(hours=2),
        values=[112] * 30,
        existing_user=user,
    )
    await db_session.flush()

    later = await evaluate_heart_rate_shadow(
        db_session,
        task=later_task,
        timezone_name="UTC",
    )

    assert first.decision == "candidate"
    assert later.reason == "candidate"
    assert later.duplicate_of_task_id is None


async def test_gadgetbridge_source_is_evaluated_and_recorded_in_evidence(
    db_session: AsyncSession,
) -> None:
    """Gadgetbridge 与 Zepp 读的是同一只手环，规则必须同等对待并如实标注来源。"""

    received_at = datetime(2026, 7, 18, 10, 0, 30, tzinfo=timezone.utc)
    task, _ = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=[110] * 30,
        source_package="nodomain.freeyourgadget.gadgetbridge",
    )

    result = await evaluate_heart_rate_shadow(
        db_session,
        task=task,
        timezone_name="Asia/Shanghai",
    )

    assert result.decision == "candidate"
    assert result.direction == "high"
    assert result.source_package == "nodomain.freeyourgadget.gadgetbridge"
    assert task.health_evidence == result.evidence()


async def test_zepp_window_does_not_mix_overlapping_gadgetbridge_heart_rates(
    db_session: AsyncSession,
) -> None:
    received_at = datetime(2026, 7, 18, 10, 0, 30, tzinfo=timezone.utc)
    gadgetbridge_task, gadgetbridge_observation = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=[45] * 30,
        context_complete=False,
        source_package="nodomain.freeyourgadget.gadgetbridge",
    )
    user = await db_session.get(User, gadgetbridge_observation.user_id)
    assert user is not None
    zepp_task, _ = await _heart_rate_task(
        db_session,
        received_at=received_at,
        values=[110] * 30,
        source_package="com.huami.watch.hmwatchmanager",
        existing_user=user,
    )

    blocked = await evaluate_heart_rate_shadow(
        db_session, task=gadgetbridge_task, timezone_name="UTC"
    )
    zepp = await evaluate_heart_rate_shadow(
        db_session, task=zepp_task, timezone_name="UTC"
    )

    assert blocked.reason == "context_unknown"
    assert blocked.source_package == "nodomain.freeyourgadget.gadgetbridge"
    assert zepp.decision == "candidate"
    assert zepp.median_bpm == 110
    assert zepp.source_package == "com.huami.watch.hmwatchmanager"
