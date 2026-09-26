from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.health_profile.errors import (
    WearableImportConflictError,
    WearableObservationNotFoundError,
)
from antang_api.health_profile.wearable_service import (
    list_health_connect_record_ids,
    process_wearable_import,
    read_latest_wearable_observations,
    read_wearable_observation_detail,
)
from antang_api.models import (
    PersonalProfile,
    User,
    WearableImport,
    WearableRecordType,
)
from antang_api.schemas.health_profile import WearableImportRequest


async def _create_user(session: AsyncSession) -> User:
    suffix = uuid4().hex[:16]
    user = User(
        username=f"wearable_{suffix}",
        username_normalized=f"wearable_{suffix}",
        password_hash="test-only",
    )
    session.add(user)
    await session.flush()
    session.add(PersonalProfile(user_id=user.id))
    await session.flush()
    return user


def _steps_request(
    *,
    sync_id: str,
    count: int,
    modified_at: datetime,
    external_id: str = "steps-record-1",
    source_package: str = "com.huami.watch.hmwatchmanager",
) -> WearableImportRequest:
    start = datetime(2026, 7, 14, 8, tzinfo=timezone.utc)
    return WearableImportRequest.model_validate(
        {
            "client_sync_id": sync_id,
            "record_type": "steps",
            "records": [
                {
                    "record_type": "steps",
                    "external_record_id": external_id,
                    "start_time": start.isoformat(),
                    "end_time": (start + timedelta(hours=1)).isoformat(),
                    "start_zone_offset_seconds": 28800,
                    "end_zone_offset_seconds": 28800,
                    "source_package": source_package,
                    "recording_method": 1,
                    "device": {
                        "manufacturer": "Amazfit",
                        "model": "Active 2",
                        "device_type": 2,
                    },
                    "source_last_modified_at": modified_at.isoformat(),
                    "data": {"count": count},
                }
            ],
        }
    )


async def test_recovery_ids_are_paginated_and_scoped_to_user_and_zepp(
    db_session: AsyncSession,
) -> None:
    first_user = await _create_user(db_session)
    second_user = await _create_user(db_session)
    modified = datetime(2026, 7, 14, 9, tzinfo=timezone.utc)
    for user_id, external_id, source_package in (
        (first_user.id, "zepp-a", "com.huami.watch.hmwatchmanager"),
        (first_user.id, "zepp-b", "com.huami.watch.hmwatchmanager"),
        (first_user.id, "gadgetbridge-a", "nodomain.freeyourgadget.gadgetbridge"),
        (second_user.id, "other-user", "com.huami.watch.hmwatchmanager"),
    ):
        await process_wearable_import(
            db_session,
            user_id=user_id,
            request=_steps_request(
                sync_id=str(uuid4()),
                count=100,
                modified_at=modified,
                external_id=external_id,
                source_package=source_package,
            ),
        )

    first, after = await list_health_connect_record_ids(
        db_session,
        user_id=first_user.id,
        record_type=WearableRecordType.STEPS,
        after=None,
        limit=1,
    )
    second, next_after = await list_health_connect_record_ids(
        db_session,
        user_id=first_user.id,
        record_type=WearableRecordType.STEPS,
        after=after,
        limit=1,
    )
    assert first == ["zepp-a"]
    assert second == ["zepp-b"]
    assert after == "zepp-a"
    assert next_after is None


async def test_wearable_import_idempotency_update_and_tombstone(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    user_id = user.id
    modified = datetime(2026, 7, 14, 9, tzinfo=timezone.utc)
    sync_id = str(uuid4())
    first_request = _steps_request(
        sync_id=sync_id,
        count=100,
        modified_at=modified,
    )
    first = await process_wearable_import(
        db_session, user_id=user_id, request=first_request
    )
    repeated = await process_wearable_import(
        db_session, user_id=user_id, request=first_request
    )
    assert first == repeated
    assert first.records_created == 1
    await db_session.commit()

    different_body = _steps_request(
        sync_id=sync_id,
        count=101,
        modified_at=modified,
    )
    with pytest.raises(WearableImportConflictError, match="payload_mismatch"):
        await process_wearable_import(
            db_session, user_id=user_id, request=different_body
        )
    await db_session.rollback()

    newer = _steps_request(
        sync_id=str(uuid4()),
        count=150,
        modified_at=modified + timedelta(minutes=1),
    )
    updated = await process_wearable_import(db_session, user_id=user_id, request=newer)
    assert updated.records_updated == 1
    await db_session.commit()

    same_content_new_export = _steps_request(
        sync_id=str(uuid4()),
        count=150,
        modified_at=modified + timedelta(minutes=2),
    )
    unchanged = await process_wearable_import(
        db_session, user_id=user_id, request=same_content_new_export
    )
    assert unchanged.records_unchanged == 1
    await db_session.commit()

    latest = await read_latest_wearable_observations(
        db_session,
        user_id=user_id,
        record_types=[WearableRecordType.STEPS],
    )
    assert latest[0].data == {"count": 150}

    delete_request = WearableImportRequest(
        client_sync_id=uuid4(),
        record_type=WearableRecordType.STEPS,
        records=[],
        deleted_record_ids=["steps-record-1"],
    )
    deleted = await process_wearable_import(
        db_session, user_id=user_id, request=delete_request
    )
    assert deleted.records_deleted == 1
    await db_session.commit()

    resurrect = _steps_request(
        sync_id=str(uuid4()),
        count=200,
        modified_at=modified + timedelta(minutes=2),
    )
    ignored = await process_wearable_import(
        db_session, user_id=user_id, request=resurrect
    )
    assert ignored.records_unchanged == 1
    await db_session.commit()
    assert (
        await read_latest_wearable_observations(
            db_session,
            user_id=user_id,
            record_types=[WearableRecordType.STEPS],
        )
        == []
    )


async def test_same_source_version_with_different_content_fails(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    modified = datetime(2026, 7, 14, 9, tzinfo=timezone.utc)
    first = _steps_request(sync_id=str(uuid4()), count=100, modified_at=modified)
    await process_wearable_import(db_session, user_id=user.id, request=first)
    await db_session.commit()

    conflicting = _steps_request(sync_id=str(uuid4()), count=200, modified_at=modified)
    with pytest.raises(WearableImportConflictError, match="same_version"):
        await process_wearable_import(
            db_session,
            user_id=user.id,
            request=conflicting,
        )
    await db_session.rollback()


async def test_wearable_reads_are_strictly_scoped_to_user(
    db_session: AsyncSession,
) -> None:
    first_user = await _create_user(db_session)
    second_user = await _create_user(db_session)
    modified = datetime(2026, 7, 14, 9, tzinfo=timezone.utc)
    await process_wearable_import(
        db_session,
        user_id=first_user.id,
        request=_steps_request(
            sync_id=str(uuid4()),
            count=100,
            modified_at=modified,
            external_id="first-users-steps",
        ),
    )
    await process_wearable_import(
        db_session,
        user_id=second_user.id,
        request=_steps_request(
            sync_id=str(uuid4()),
            count=200,
            modified_at=modified,
            external_id="second-users-steps",
        ),
    )

    first_latest = await read_latest_wearable_observations(
        db_session,
        user_id=first_user.id,
        record_types=[WearableRecordType.STEPS],
    )
    second_latest = await read_latest_wearable_observations(
        db_session,
        user_id=second_user.id,
        record_types=[WearableRecordType.STEPS],
    )

    assert [item.data for item in first_latest] == [{"count": 100}]
    assert [item.data for item in second_latest] == [{"count": 200}]
    with pytest.raises(WearableObservationNotFoundError):
        await read_wearable_observation_detail(
            db_session,
            user_id=first_user.id,
            observation_id=second_latest[0].id,
        )


def test_wearable_request_is_strict() -> None:
    now = datetime.now(timezone.utc)
    base = {
        "client_sync_id": str(uuid4()),
        "record_type": "exercise",
        "records": [
            {
                "record_type": "exercise",
                "external_record_id": "exercise-1",
                "start_time": now.isoformat(),
                "end_time": (now + timedelta(minutes=10)).isoformat(),
                "source_package": "com.huami.watch.hmwatchmanager",
                "source_last_modified_at": now.isoformat(),
                "data": {"exercise_type": "running"},
            }
        ],
    }
    with pytest.raises(ValidationError):
        WearableImportRequest.model_validate(base)

    base["records"][0]["data"] = {"exercise_type": 56}
    base["records"][0]["unexpected"] = True
    with pytest.raises(ValidationError):
        WearableImportRequest.model_validate(base)

    del base["records"][0]["unexpected"]
    base["records"][0]["source_package"] = "com.example.other-health-app"
    with pytest.raises(ValidationError):
        WearableImportRequest.model_validate(base)


def test_wearable_import_accepts_gadgetbridge_source() -> None:
    """华为机型没有 Health Connect 通路，Gadgetbridge 手动导入用自己的包名。"""

    now = datetime.now(timezone.utc)
    request = WearableImportRequest.model_validate(
        {
            "client_sync_id": str(uuid4()),
            "record_type": "heart_rate",
            "records": [
                {
                    "record_type": "heart_rate",
                    "external_record_id": "gadgetbridge-heart-rate-1",
                    "start_time": now.isoformat(),
                    "end_time": now.isoformat(),
                    "source_package": "nodomain.freeyourgadget.gadgetbridge",
                    "recording_method": 2,
                    "device": {"manufacturer": "Huawei", "model": "Kimi-B19FB"},
                    "source_last_modified_at": now.isoformat(),
                    "data": {"samples": [{"time": now.isoformat(), "beats_per_minute": 62}]},
                }
            ],
        }
    )
    assert (
        request.records[0].source_package
        == "nodomain.freeyourgadget.gadgetbridge"
    )
    with pytest.raises(ValidationError, match="Gadgetbridge imports lack verified"):
        WearableImportRequest.model_validate(
            {
                **request.model_dump(mode="json"),
                "health_context_complete": True,
            }
        )


def test_wearable_import_declares_one_record_type_and_heart_context() -> None:
    now = datetime.now(timezone.utc)
    heart_rate = {
        "record_type": "heart_rate",
        "external_record_id": "heart-rate-1",
        "start_time": now.isoformat(),
        "end_time": now.isoformat(),
        "source_package": "com.huami.watch.hmwatchmanager",
        "recording_method": 2,
        "source_last_modified_at": now.isoformat(),
        "data": {"samples": [{"time": now.isoformat(), "beats_per_minute": 80}]},
    }
    request = WearableImportRequest.model_validate(
        {
            "client_sync_id": str(uuid4()),
            "record_type": "heart_rate",
            "health_context_complete": True,
            "records": [heart_rate],
        }
    )
    assert request.record_type is WearableRecordType.HEART_RATE
    assert request.health_context_complete is True

    with pytest.raises(ValidationError, match="record_type"):
        WearableImportRequest.model_validate(
            {
                "client_sync_id": str(uuid4()),
                "record_type": "steps",
                "records": [heart_rate],
            }
        )
    with pytest.raises(ValidationError, match="health_context_complete"):
        WearableImportRequest.model_validate(
            {
                "client_sync_id": str(uuid4()),
                "record_type": "steps",
                "health_context_complete": True,
                "records": [],
            }
        )


async def test_database_rejects_complete_health_context_without_heart_rate_type(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                WearableImport(
                    user_id=user.id,
                    client_sync_id=uuid4(),
                    request_hash="f" * 64,
                    record_type=None,
                    health_context_complete=True,
                    records_created=0,
                    records_updated=0,
                    records_unchanged=0,
                    records_deleted=0,
                )
            )
            await db_session.flush()
