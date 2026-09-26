"""Build a bounded, source-separated dashboard from saved observations."""

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import WearableObservation, WearableRecordType
from antang_api.schemas.health_profile import (
    DistanceData,
    HeartRateData,
    OxygenSaturationData,
    RestingHeartRateData,
    SleepData,
    StepsData,
    WearableDashboardDay,
    WearableDashboardHeartRatePoint,
    WearableDashboardResponse,
    WearableDashboardSleepSession,
    WearableDashboardSource,
    WearableDashboardTotals,
    WearableDevice,
)

_SOURCE_PACKAGES = (
    "com.huami.watch.hmwatchmanager",
    "nodomain.freeyourgadget.gadgetbridge",
)
_DASHBOARD_TYPES = (
    WearableRecordType.STEPS,
    WearableRecordType.DISTANCE,
    WearableRecordType.HEART_RATE,
    WearableRecordType.OXYGEN_SATURATION,
    WearableRecordType.RESTING_HEART_RATE,
    WearableRecordType.SLEEP,
)
_ASLEEP_STAGES = {"light", "deep", "rem", "sleeping"}
_TREND_POINTS = 240
_SLEEP_SESSIONS = 7


@dataclass
class _Daily:
    steps: int = 0
    distance_meters: Decimal = Decimal(0)


@dataclass
class _Source:
    latest_observed_at: datetime
    device: WearableDevice | None = None
    daily: dict[date, _Daily] = field(default_factory=dict)
    heart_rate_trend: deque[WearableDashboardHeartRatePoint] = field(
        default_factory=lambda: deque(maxlen=_TREND_POINTS)
    )
    sleep_sessions: deque[WearableDashboardSleepSession] = field(
        default_factory=lambda: deque(maxlen=_SLEEP_SESSIONS)
    )
    heart_rate_samples: int = 0
    heart_rate_sum: int = 0
    heart_rate_minimum: int | None = None
    heart_rate_maximum: int | None = None
    oxygen_saturation_samples: int = 0
    oxygen_saturation_sum: Decimal = Decimal(0)
    oxygen_saturation_minimum: Decimal | None = None
    resting_heart_rate_samples: int = 0
    resting_heart_rate_minimum: int | None = None
    sleep_session_count: int = 0
    asleep_seconds: Decimal = Decimal(0)


def _minimum(current: int | Decimal | None, value: int | Decimal) -> int | Decimal:
    return value if current is None or value < current else current


async def read_wearable_dashboard(
    session: AsyncSession,
    *,
    user_id: UUID,
    now: datetime | None = None,
) -> WearableDashboardResponse:
    """Summarize the last 30 days without sending raw history to the App."""

    period_end = now or datetime.now(UTC)
    period_start = period_end - timedelta(days=30)
    result = await session.execute(
        select(
            WearableObservation.source_package,
            WearableObservation.record_type,
            WearableObservation.start_time,
            WearableObservation.end_time,
            WearableObservation.start_zone_offset_seconds,
            WearableObservation.data,
            WearableObservation.device,
        )
        .where(
            WearableObservation.user_id == user_id,
            WearableObservation.provider == "health_connect",
            WearableObservation.source_package.in_(_SOURCE_PACKAGES),
            WearableObservation.record_type.in_(_DASHBOARD_TYPES),
            WearableObservation.deleted_at.is_(None),
            WearableObservation.end_time >= period_start,
            WearableObservation.end_time <= period_end,
        )
        .order_by(WearableObservation.start_time, WearableObservation.id)
    )
    sources: dict[str, _Source] = {}
    for (
        source_package,
        record_type,
        start_time,
        end_time,
        start_offset,
        data,
        device,
    ) in result:
        summary = sources.setdefault(source_package, _Source(latest_observed_at=end_time))
        if end_time >= summary.latest_observed_at:
            summary.latest_observed_at = end_time
            if device is not None:
                summary.device = WearableDevice.model_validate(device)

        if record_type in {WearableRecordType.STEPS, WearableRecordType.DISTANCE}:
            local_day = (start_time + timedelta(seconds=start_offset or 0)).date()
            daily = summary.daily.setdefault(local_day, _Daily())
            if record_type is WearableRecordType.STEPS:
                daily.steps += StepsData.model_validate(data).count
            else:
                daily.distance_meters += DistanceData.model_validate(data).meters
        elif record_type is WearableRecordType.HEART_RATE:
            for sample in HeartRateData.model_validate(data).samples:
                if not period_start <= sample.time <= period_end:
                    continue
                value = sample.beats_per_minute
                summary.heart_rate_samples += 1
                summary.heart_rate_sum += value
                summary.heart_rate_minimum = int(
                    _minimum(summary.heart_rate_minimum, value)
                )
                summary.heart_rate_maximum = max(
                    summary.heart_rate_maximum or value, value
                )
                summary.heart_rate_trend.append(
                    WearableDashboardHeartRatePoint(
                        observed_at=sample.time, beats_per_minute=value
                    )
                )
        elif record_type is WearableRecordType.OXYGEN_SATURATION:
            value = OxygenSaturationData.model_validate(data).percentage
            summary.oxygen_saturation_samples += 1
            summary.oxygen_saturation_sum += value
            summary.oxygen_saturation_minimum = Decimal(
                _minimum(summary.oxygen_saturation_minimum, value)
            )
        elif record_type is WearableRecordType.RESTING_HEART_RATE:
            value = RestingHeartRateData.model_validate(data).beats_per_minute
            summary.resting_heart_rate_samples += 1
            summary.resting_heart_rate_minimum = int(
                _minimum(summary.resting_heart_rate_minimum, value)
            )
        elif record_type is WearableRecordType.SLEEP:
            stages = SleepData.model_validate(data).stages
            summary.sleep_session_count += 1
            summary.asleep_seconds += sum(
                (
                    Decimal(str((stage.end_time - stage.start_time).total_seconds()))
                    for stage in stages
                    if stage.stage in _ASLEEP_STAGES
                ),
                start=Decimal(0),
            )
            summary.sleep_sessions.append(
                WearableDashboardSleepSession(
                    start_time=start_time, end_time=end_time, stages=stages
                )
            )

    dashboards: list[WearableDashboardSource] = []
    for source_package, summary in sources.items():
        days = [
            WearableDashboardDay(
                day=day,
                steps=value.steps,
                distance_meters=float(value.distance_meters),
            )
            for day, value in sorted(summary.daily.items())
        ]
        dashboards.append(
            WearableDashboardSource(
                source_package=source_package,
                device=summary.device,
                latest_observed_at=summary.latest_observed_at,
                daily=days,
                heart_rate_trend=list(summary.heart_rate_trend),
                sleep_sessions=list(summary.sleep_sessions),
                totals=WearableDashboardTotals(
                    heart_rate_samples=summary.heart_rate_samples,
                    heart_rate_minimum=summary.heart_rate_minimum,
                    heart_rate_maximum=summary.heart_rate_maximum,
                    heart_rate_average=(
                        round(summary.heart_rate_sum / summary.heart_rate_samples, 1)
                        if summary.heart_rate_samples
                        else None
                    ),
                    steps=sum(day.steps for day in days),
                    distance_meters=sum(day.distance_meters for day in days),
                    oxygen_saturation_samples=summary.oxygen_saturation_samples,
                    oxygen_saturation_minimum=(
                        float(summary.oxygen_saturation_minimum)
                        if summary.oxygen_saturation_minimum is not None
                        else None
                    ),
                    oxygen_saturation_average=(
                        round(
                            float(
                                summary.oxygen_saturation_sum
                                / summary.oxygen_saturation_samples
                            ),
                            1,
                        )
                        if summary.oxygen_saturation_samples
                        else None
                    ),
                    resting_heart_rate_samples=summary.resting_heart_rate_samples,
                    resting_heart_rate_minimum=summary.resting_heart_rate_minimum,
                    sleep_sessions=summary.sleep_session_count,
                    sleep_minutes=round(float(summary.asleep_seconds / 60)),
                ),
            )
        )

    dashboards.sort(key=lambda item: item.latest_observed_at, reverse=True)
    return WearableDashboardResponse(
        period_start=period_start, period_end=period_end, sources=dashboards
    )
