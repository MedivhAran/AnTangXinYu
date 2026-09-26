from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.health_profile.wearable_dashboard import read_wearable_dashboard
from antang_api.health_profile.wearable_service import (
    process_wearable_import,
    read_wearable_observations,
)
from antang_api.models import User, WearableRecordType
from antang_api.schemas.health_profile import WearableImportRequest

ZEPP = "com.huami.watch.hmwatchmanager"
GADGETBRIDGE = "nodomain.freeyourgadget.gadgetbridge"


async def _import(
    session: AsyncSession,
    *,
    user: User,
    source: str,
    record_type: str,
    external_id: str,
    start: datetime,
    end: datetime,
    data: dict[str, object],
) -> None:
    request = WearableImportRequest.model_validate(
        {
            "client_sync_id": str(uuid4()),
            "record_type": record_type,
            "records": [
                {
                    "record_type": record_type,
                    "external_record_id": external_id,
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "start_zone_offset_seconds": 28800,
                    "end_zone_offset_seconds": 28800,
                    "source_package": source,
                    "recording_method": 2,
                    "device": {"manufacturer": "Amazfit", "model": "Active 2"},
                    "source_last_modified_at": end.isoformat(),
                    "data": data,
                }
            ],
        }
    )
    await process_wearable_import(session, user_id=user.id, request=request)


async def test_dashboard_uses_saved_history_and_keeps_sources_separate(
    db_session: AsyncSession,
) -> None:
    now = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    suffix = uuid4().hex[:12]
    other_suffix = uuid4().hex[:12]
    user = User(
        username=f"dashboard_{suffix}",
        username_normalized=f"dashboard_{suffix}",
        password_hash="test-only",
    )
    other = User(
        username=f"other_{other_suffix}",
        username_normalized=f"other_{other_suffix}",
        password_hash="test-only",
    )
    db_session.add_all([user, other])
    await db_session.flush()
    start = now - timedelta(hours=1)
    await _import(
        db_session, user=user, source=ZEPP, record_type="steps",
        external_id="zepp-steps", start=start, end=start + timedelta(minutes=1),
        data={"count": 100},
    )
    await _import(
        db_session, user=user, source=GADGETBRIDGE, record_type="steps",
        external_id="gb-steps", start=start, end=start + timedelta(minutes=1),
        data={"count": 200},
    )
    await _import(
        db_session, user=user, source=ZEPP, record_type="heart_rate",
        external_id="zepp-heart", start=start, end=start + timedelta(minutes=1),
        data={"samples": [
            {"time": start.isoformat(), "beats_per_minute": 80},
            {"time": (start + timedelta(minutes=1)).isoformat(), "beats_per_minute": 90},
        ]},
    )
    await _import(
        db_session, user=user, source=GADGETBRIDGE, record_type="heart_rate",
        external_id="gb-heart", start=start, end=start,
        data={"samples": [{"time": start.isoformat(), "beats_per_minute": 70}]},
    )
    await _import(
        db_session, user=user, source=GADGETBRIDGE, record_type="sleep",
        external_id="gb-sleep", start=start - timedelta(hours=10),
        end=start - timedelta(hours=9, minutes=20),
        data={"stages": [
            {"start_time": (start - timedelta(hours=10)).isoformat(),
             "end_time": (start - timedelta(hours=9, minutes=50)).isoformat(),
             "stage": "awake"},
            {"start_time": (start - timedelta(hours=9, minutes=50)).isoformat(),
             "end_time": (start - timedelta(hours=9, minutes=20)).isoformat(),
             "stage": "light"},
        ]},
    )
    await _import(
        db_session, user=user, source=ZEPP, record_type="steps",
        external_id="old-steps", start=now - timedelta(days=40),
        end=now - timedelta(days=40) + timedelta(minutes=1),
        data={"count": 900},
    )
    await _import(
        db_session, user=other, source=ZEPP, record_type="steps",
        external_id="other-steps", start=start, end=start + timedelta(minutes=1),
        data={"count": 1000},
    )

    dashboard = await read_wearable_dashboard(db_session, user_id=user.id, now=now)
    sources = {item.source_package: item for item in dashboard.sources}
    assert set(sources) == {ZEPP, GADGETBRIDGE}
    assert sources[ZEPP].totals.steps == 100
    assert sources[ZEPP].totals.heart_rate_average == 85.0
    assert [point.beats_per_minute for point in sources[ZEPP].heart_rate_trend] == [80, 90]
    assert sources[GADGETBRIDGE].totals.steps == 200
    assert sources[GADGETBRIDGE].totals.heart_rate_samples == 1
    assert sources[GADGETBRIDGE].totals.sleep_minutes == 30
    assert len(sources[GADGETBRIDGE].sleep_sessions) == 1
    zepp_hearts = await read_wearable_observations(
        db_session,
        user_id=user.id,
        record_types=[WearableRecordType.HEART_RATE],
        start=now - timedelta(hours=2),
        end=now,
        source_package=ZEPP,
    )
    assert [item.source_package for item in zepp_hearts] == [ZEPP]
