from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    Message,
    PersonalProfile,
    ProactiveCareTask,
    User,
    WearableImport,
    WearableObservation,
    WearableRecordType,
)
from antang_api.proactive_care.heart_rate_replay import replay_heart_rate_shadow


async def test_replay_keeps_current_snapshot_and_labels_explicit_counterfactuals(
    db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex[:12]
    user = User(
        username=f"heart_replay_{suffix}",
        username_normalized=f"heart_replay_{suffix}",
        password_hash="test-only",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(PersonalProfile(user_id=user.id, age_years=None))

    sample_start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    range_end = datetime(2026, 6, 3, 0, 0, tzinfo=timezone.utc)
    received_at = datetime(2026, 6, 20, 0, 0, tzinfo=timezone.utc)
    heart_import = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash=uuid4().hex + uuid4().hex,
        record_type=None,
        health_context_complete=False,
        records_created=2,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
        created_at=received_at,
    )
    context_import = WearableImport(
        user_id=user.id,
        client_sync_id=uuid4(),
        request_hash=uuid4().hex + uuid4().hex,
        record_type=None,
        health_context_complete=False,
        records_created=1,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
        created_at=received_at,
    )
    db_session.add_all([heart_import, context_import])
    await db_session.flush()

    heart_id = f"heart-{suffix}"
    heart_hash = uuid4().hex + uuid4().hex
    tail_time = range_end - timedelta(minutes=1)
    step_observation = WearableObservation(
        user_id=user.id,
        external_record_id=f"steps-{suffix}",
        record_type=WearableRecordType.STEPS,
        start_time=sample_start,
        end_time=sample_start + timedelta(minutes=29),
        start_zone_offset_seconds=0,
        end_zone_offset_seconds=0,
        data={"count": 0},
        data_hash=uuid4().hex + uuid4().hex,
        source_package="com.huami.watch.hmwatchmanager",
        recording_method=2,
        device=None,
        source_last_modified_at=received_at,
        last_import_id=context_import.id,
    )
    db_session.add_all(
        [
            WearableObservation(
                user_id=user.id,
                external_record_id=heart_id,
                record_type=WearableRecordType.HEART_RATE,
                start_time=sample_start,
                end_time=sample_start + timedelta(minutes=29),
                start_zone_offset_seconds=0,
                end_zone_offset_seconds=0,
                data={
                    "samples": [
                        {
                            "time": (sample_start + timedelta(minutes=index)).isoformat(),
                            "beats_per_minute": 110,
                        }
                        for index in range(30)
                    ]
                },
                data_hash=heart_hash,
                source_package="com.huami.watch.hmwatchmanager",
                recording_method=2,
                device={"manufacturer": "Amazfit", "model": "Active 2"},
                source_last_modified_at=received_at,
                last_import_id=heart_import.id,
            ),
            WearableObservation(
                user_id=user.id,
                external_record_id=f"heart-tail-{suffix}",
                record_type=WearableRecordType.HEART_RATE,
                start_time=tail_time,
                end_time=tail_time,
                start_zone_offset_seconds=0,
                end_zone_offset_seconds=0,
                data={
                    "samples": [
                        {
                            "time": tail_time.isoformat(),
                            "beats_per_minute": 75,
                        }
                    ]
                },
                data_hash=uuid4().hex + uuid4().hex,
                source_package="com.huami.watch.hmwatchmanager",
                recording_method=2,
                device={"manufacturer": "Amazfit", "model": "Active 2"},
                source_last_modified_at=received_at,
                last_import_id=heart_import.id,
            ),
            step_observation,
        ]
    )
    await db_session.flush()

    default_report = await replay_heart_rate_shadow(
        db_session,
        user_id=user.id,
        start_at=sample_start,
        end_at=range_end,
        timezone_name="UTC",
    )
    adult_only_report = await replay_heart_rate_shadow(
        db_session,
        user_id=user.id,
        start_at=sample_start,
        end_at=range_end,
        timezone_name="UTC",
        assume_adult=True,
    )
    signal_report = await replay_heart_rate_shadow(
        db_session,
        user_id=user.id,
        start_at=sample_start,
        end_at=range_end,
        timezone_name="UTC",
        assume_adult=True,
        assume_context_complete=True,
    )

    assert default_report["counterfactual_assumptions"] == {
        "on_time_arrival": True,
        "adult_when_age_unknown": False,
        "context_complete": False,
    }
    assert "observed" not in default_report
    snapshot = default_report["current_snapshot"]
    assert snapshot["simulation_only"] is True
    assert snapshot["historical_reconstruction"] is False
    assert snapshot["basis"] == "current_records_and_last_import_times"
    summary = snapshot["summary"]
    assert summary["batch_count"] == 1
    assert summary["fresh_batches"] == 0
    assert summary["stale_batches"] == 1
    assert summary["future_batches"] == 0
    assert summary["sample_count"] == 31
    assert summary["fresh_samples"] == 0
    assert summary["stale_samples"] == 31
    assert summary["future_samples"] == 0
    assert summary["age_unknown_batches"] == 1
    assert summary["context_unknown_batches"] == 1
    assert summary["last_import_batch_delay_seconds"] == {
        "minimum": int((received_at - tail_time).total_seconds()),
        "median": int((received_at - tail_time).total_seconds()),
        "maximum": int((received_at - tail_time).total_seconds()),
    }
    assert summary["decisions"] == {"blocked": 1}
    assert summary["reasons"] == {"age_unknown": 1}
    assert default_report["on_time_counterfactual"]["summary"]["reasons"] == {
        "age_unknown": 1
    }
    assert adult_only_report["on_time_counterfactual"]["summary"]["reasons"] == {
        "context_unknown": 1
    }

    counterfactual = signal_report["on_time_counterfactual"]
    assert counterfactual["simulation_only"] is True
    assert counterfactual["summary"]["reasons"]["candidate"] == 11
    assert len(counterfactual["episodes"]) == 1
    episode = counterfactual["episodes"][0]
    assert episode["direction"] == "high"
    assert episode["sample_start"] == sample_start.isoformat()
    assert episode["sample_end"] == (sample_start + timedelta(minutes=29)).isoformat()
    assert episode["window_count"] == 11
    assert episode["source_records"] == {heart_id: heart_hash}
    assert len(episode["windows"]) == 11
    assert episode["windows"][-1]["median_bpm"] == 110

    repeated_report = await replay_heart_rate_shadow(
        db_session,
        user_id=user.id,
        start_at=sample_start,
        end_at=range_end,
        timezone_name="UTC",
        assume_adult=True,
        assume_context_complete=True,
    )
    assert repeated_report == signal_report

    step_observation.data = {"count": 12}
    step_observation.data_hash = uuid4().hex + uuid4().hex
    await db_session.flush()
    blocked_report = await replay_heart_rate_shadow(
        db_session,
        user_id=user.id,
        start_at=sample_start,
        end_at=range_end,
        timezone_name="UTC",
        assume_adult=True,
        assume_context_complete=True,
    )
    blocked_counterfactual = blocked_report["on_time_counterfactual"]
    assert blocked_counterfactual["summary"]["reasons"]["step_activity"] == 11
    assert blocked_counterfactual["episodes"] == []
    assert len(blocked_counterfactual["blocked_evidence_groups"]) == 1
    blocked_group = blocked_counterfactual["blocked_evidence_groups"][0]
    assert blocked_group["reason"] == "step_activity"
    assert blocked_group["direction"] == "high"
    assert blocked_group["window_count"] == 11
    assert blocked_group["sample_start"] == sample_start.isoformat()
    assert blocked_group["sample_end"] == (
        sample_start + timedelta(minutes=29)
    ).isoformat()

    await db_session.refresh(heart_import)
    assert heart_import.record_type is None
    assert heart_import.health_context_complete is False
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ProactiveCareTask)
            .where(ProactiveCareTask.user_id == user.id)
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.user_id == user.id)
        )
        == 0
    )
