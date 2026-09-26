from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    PersonalProfile,
    ProactiveCareTask,
    WearableImport,
    WearableObservation,
    WearableRecordType,
)

HEART_RATE_RULE_ID = "heart_rate_window"
HEART_RATE_RULE_VERSION = "heart-rate-shadow-v0"

_AUTOMATICALLY_RECORDED = 2
_WINDOW_MINUTES = 30
_MINIMUM_COVERED_MINUTES = 20
_ABNORMAL_FRACTION = 0.8
_MAXIMUM_DELAY = timedelta(minutes=60)
_EXERCISE_MARGIN = timedelta(minutes=30)
_EPISODE_GAP = timedelta(minutes=30)
_ZEPP_PACKAGE = "com.huami.watch.hmwatchmanager"
_GADGETBRIDGE_PACKAGE = "nodomain.freeyourgadget.gadgetbridge"
# 两种来源可能对应不同手环；心率窗口与活动上下文必须分别计算。
SOURCE_PACKAGES = (_ZEPP_PACKAGE, _GADGETBRIDGE_PACKAGE)

HeartSample = tuple[datetime, int, WearableObservation]
MinuteSamples = dict[datetime, list[HeartSample]]


class HeartRateShadowResult(BaseModel):
    """一项可落库、可按规则版本复核的 shadow 判定。"""

    decision: Literal["candidate", "blocked", "no_match"]
    reason: Literal[
        "candidate",
        "duplicate_candidate",
        "age_unknown",
        "not_adult",
        "context_unknown",
        "no_current_samples",
        "future_samples",
        "stale_samples",
        "insufficient_coverage",
        "threshold_not_sustained",
        "conflicting_directions",
        "exercise_context",
        "sleep_context",
        "step_activity",
    ]
    received_at: datetime
    age_years: int | None
    context_complete: bool
    timezone: str
    source_package: Literal[
        "com.huami.watch.hmwatchmanager",
        "nodomain.freeyourgadget.gadgetbridge",
    ]
    recording_method: Literal[2]
    minute_aggregation: Literal["median"]
    direction: Literal["high", "low"] | None = None
    sample_start: datetime | None = None
    sample_end: datetime | None = None
    delay_seconds: int | None = None
    sample_count: int = 0
    covered_minutes: int = 0
    abnormal_minutes: int = 0
    abnormal_fraction: float | None = None
    minimum_bpm: float | None = None
    median_bpm: float | None = None
    maximum_bpm: float | None = None
    exercise_overlap: bool = False
    sleep_overlap: bool = False
    step_count: int = 0
    source_records: dict[str, str] = Field(default_factory=dict)
    context_records: dict[str, str] = Field(default_factory=dict)
    event_key: str | None = None
    duplicate_of_task_id: UUID | None = None

    model_config = ConfigDict(extra="forbid")

    def evidence(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"event_key", "duplicate_of_task_id"},
        )


def _store_result(
    task: ProactiveCareTask,
    result: HeartRateShadowResult,
) -> HeartRateShadowResult:
    task.rule_id = HEART_RATE_RULE_ID
    task.rule_version = HEART_RATE_RULE_VERSION
    task.health_event_key = result.event_key
    task.duplicate_of_task_id = result.duplicate_of_task_id
    task.health_evidence = result.evidence()
    return result


def _samples(
    observations: list[WearableObservation],
) -> list[HeartSample]:
    values: list[HeartSample] = []
    for observation in observations:
        samples = observation.data.get("samples")
        if not isinstance(samples, list):
            raise TypeError("validated heart rate samples are not a list")
        for sample in samples:
            if not isinstance(sample, dict):
                raise TypeError("validated heart rate sample is not an object")
            observed_at = datetime.fromisoformat(str(sample["time"]))
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise TypeError("validated heart rate sample has no timezone")
            beats_per_minute = sample["beats_per_minute"]
            if type(beats_per_minute) is not int or beats_per_minute < 1:
                raise TypeError("validated heart rate sample has invalid bpm")
            values.append(
                (observed_at.astimezone(timezone.utc), beats_per_minute, observation)
            )
    return values


def _evaluated_source_package(
    observations: list[WearableObservation],
) -> str:
    """本次评估所依据的心率记录来源，用于如实标注证据。

    一次评估只允许出现一个来源：证据字段是单值，混用来源无法如实描述。
    没有加载到任何记录时返回规则声明的基准来源，此时没有任何记录被评估。
    """

    packages = {observation.source_package for observation in observations}
    if len(packages) > 1:
        raise RuntimeError("心率评估混用了多个数据来源")
    return packages.pop() if packages else _ZEPP_PACKAGE


def _evaluate_window(
    *,
    age_years: int | None,
    timezone_name: str,
    source_package: str,
    minute_samples: MinuteSamples,
    context_observations: list[WearableObservation],
    received_at: datetime,
    current_times: list[datetime],
    adult_assumed: bool,
    context_complete: bool,
) -> HeartRateShadowResult:
    """Apply heart-rate-shadow-v0 to already loaded, normalized observations."""

    received = received_at.astimezone(timezone.utc)
    base = {
        "received_at": received,
        "age_years": age_years,
        "context_complete": context_complete,
        "timezone": timezone_name,
        "source_package": source_package,
        "recording_method": _AUTOMATICALLY_RECORDED,
        "minute_aggregation": "median",
    }
    if age_years is None and not adult_assumed:
        return HeartRateShadowResult(
            decision="blocked",
            reason="age_unknown",
            **base,
        )
    if age_years is not None and age_years < 18:
        return HeartRateShadowResult(
            decision="blocked",
            reason="not_adult",
            **base,
        )
    if not context_complete:
        return HeartRateShadowResult(
            decision="blocked",
            reason="context_unknown",
            **base,
        )
    if not current_times:
        return HeartRateShadowResult(
            decision="no_match",
            reason="no_current_samples",
            **base,
        )
    if any(observed_at > received for observed_at in current_times):
        return HeartRateShadowResult(
            decision="blocked",
            reason="future_samples",
            **base,
        )

    fresh_current_times = [
        observed_at
        for observed_at in current_times
        if received - observed_at <= _MAXIMUM_DELAY
    ]
    if not fresh_current_times:
        delay = received - max(current_times)
        return HeartRateShadowResult(
            decision="blocked",
            reason="stale_samples",
            delay_seconds=int(delay.total_seconds()),
            **base,
        )

    latest_current = max(fresh_current_times)
    delay = received - latest_current
    search_start = min(fresh_current_times) - timedelta(
        minutes=_WINDOW_MINUTES - 1
    )
    available_samples: list[HeartSample] = []
    values_by_minute: dict[datetime, list[int]] = defaultdict(list)
    minute = search_start.replace(second=0, microsecond=0)
    last_minute = latest_current.replace(second=0, microsecond=0)
    while minute <= last_minute:
        for sample in minute_samples.get(minute, []):
            if search_start <= sample[0] <= latest_current:
                available_samples.append(sample)
                values_by_minute[minute].append(sample[1])
        minute += timedelta(minutes=1)
    minute_values = {
        minute: float(median(values))
        for minute, values in values_by_minute.items()
    }

    covered_window_found = False
    matches: list[
        tuple[
            datetime,
            datetime,
            Literal["high", "low"],
            list[float],
        ]
    ] = []
    for window_end in sorted(
        {
            value.replace(second=0, microsecond=0)
            for value in fresh_current_times
        }
    ):
        window_start = window_end - timedelta(minutes=_WINDOW_MINUTES - 1)
        window_values = [
            value
            for minute, value in minute_values.items()
            if window_start <= minute <= window_end
        ]
        if len(window_values) < _MINIMUM_COVERED_MINUTES:
            continue
        covered_window_found = True
        center = float(median(window_values))
        high = sum(value > 100 for value in window_values)
        low = sum(value < 50 for value in window_values)
        if high / len(window_values) >= _ABNORMAL_FRACTION and center > 100:
            matches.append((window_start, window_end, "high", window_values))
        if low / len(window_values) >= _ABNORMAL_FRACTION and center < 50:
            matches.append((window_start, window_end, "low", window_values))

    if not matches:
        return HeartRateShadowResult(
            decision="no_match",
            reason=(
                "threshold_not_sustained"
                if covered_window_found
                else "insufficient_coverage"
            ),
            delay_seconds=int(delay.total_seconds()),
            covered_minutes=len(minute_values),
            **base,
        )
    if len({match[2] for match in matches}) > 1:
        return HeartRateShadowResult(
            decision="blocked",
            reason="conflicting_directions",
            delay_seconds=int(delay.total_seconds()),
            **base,
        )

    window_start, window_end, direction, window_values = max(
        matches,
        key=lambda item: item[1],
    )
    selected_samples = [
        sample
        for sample in available_samples
        if window_start
        <= sample[0].replace(second=0, microsecond=0)
        <= window_end
    ]
    sample_start = min(sample[0] for sample in selected_samples)
    sample_end = max(sample[0] for sample in selected_samples)
    abnormal_minutes = sum(
        value > 100 if direction == "high" else value < 50
        for value in window_values
    )
    relevant_context = [
        observation
        for observation in context_observations
        if observation.end_time >= window_start - _EXERCISE_MARGIN
        and observation.start_time <= window_end + _EXERCISE_MARGIN
    ]
    exercise_overlap = any(
        observation.record_type is WearableRecordType.EXERCISE
        and observation.end_time >= window_start - _EXERCISE_MARGIN
        and observation.start_time <= window_end + _EXERCISE_MARGIN
        for observation in relevant_context
    )
    sleep_overlap = any(
        observation.record_type is WearableRecordType.SLEEP
        and observation.end_time >= window_start
        and observation.start_time <= window_end
        for observation in relevant_context
    )
    step_count = sum(
        int(observation.data["count"])
        for observation in relevant_context
        if observation.record_type is WearableRecordType.STEPS
        and observation.end_time >= window_start
        and observation.start_time <= window_end
    )
    result_values = {
        **base,
        "direction": direction,
        "sample_start": sample_start,
        "sample_end": sample_end,
        "delay_seconds": int((received - sample_end).total_seconds()),
        "sample_count": len(selected_samples),
        "covered_minutes": len(window_values),
        "abnormal_minutes": abnormal_minutes,
        "abnormal_fraction": abnormal_minutes / len(window_values),
        "minimum_bpm": min(window_values),
        "median_bpm": float(median(window_values)),
        "maximum_bpm": max(window_values),
        "exercise_overlap": exercise_overlap,
        "sleep_overlap": sleep_overlap,
        "step_count": step_count,
        "source_records": {
            sample[2].external_record_id: sample[2].data_hash
            for sample in selected_samples
        },
        "context_records": {
            observation.external_record_id: observation.data_hash
            for observation in relevant_context
        },
    }
    if exercise_overlap:
        return HeartRateShadowResult(
            decision="blocked",
            reason="exercise_context",
            **result_values,
        )
    if sleep_overlap:
        return HeartRateShadowResult(
            decision="blocked",
            reason="sleep_context",
            **result_values,
        )
    if step_count > 0:
        return HeartRateShadowResult(
            decision="blocked",
            reason="step_activity",
            **result_values,
        )
    return HeartRateShadowResult(
        decision="candidate",
        reason="candidate",
        event_key=(
            f"{HEART_RATE_RULE_VERSION}:{direction}:"
            f"{sample_start.astimezone(timezone.utc).isoformat()}"
        ),
        **result_values,
    )


async def _current_primary_candidate(
    session: AsyncSession,
    *,
    task: ProactiveCareTask,
    direction: Literal["high", "low"],
    sample_start: datetime,
    sample_end: datetime,
) -> ProactiveCareTask | None:
    candidates = list(
        await session.scalars(
            select(ProactiveCareTask)
            .where(
                ProactiveCareTask.user_id == task.user_id,
                ProactiveCareTask.id != task.id,
                ProactiveCareTask.rule_id == HEART_RATE_RULE_ID,
                ProactiveCareTask.rule_version == HEART_RATE_RULE_VERSION,
                ProactiveCareTask.health_event_key.is_not(None),
            )
            .order_by(ProactiveCareTask.id)
        )
    )
    candidate_data: list[
        tuple[ProactiveCareTask, str, datetime, datetime, dict[str, str]]
    ] = []
    referenced_ids: set[str] = set()
    for candidate in candidates:
        evidence = candidate.health_evidence
        if not isinstance(evidence, dict):
            raise TypeError("stored heart rate evidence is not an object")
        source_records = evidence.get("source_records")
        if not isinstance(source_records, dict) or not source_records:
            raise TypeError("stored heart rate evidence has no source records")
        context_records = evidence.get("context_records", {})
        if not isinstance(context_records, dict):
            raise TypeError("stored heart rate context records are not an object")
        records = {**source_records, **context_records}
        candidate_data.append(
            (
                candidate,
                str(evidence.get("direction")),
                datetime.fromisoformat(str(evidence["sample_start"])),
                datetime.fromisoformat(str(evidence["sample_end"])),
                records,
            )
        )
        referenced_ids.update(records)

    current_records = {
        observation.external_record_id: observation.data_hash
        for observation in await session.scalars(
            select(WearableObservation).where(
                WearableObservation.user_id == task.user_id,
                WearableObservation.external_record_id.in_(referenced_ids),
                WearableObservation.deleted_at.is_(None),
            )
        )
    }
    current_task_ids = {
        candidate.id
        for candidate, _direction, _start, _end, records in candidate_data
        if {record_id: current_records.get(record_id) for record_id in records}
        == records
    }
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    for candidate, candidate_direction, start, end, _records in candidate_data:
        if (
            candidate.id not in current_task_ids
            or candidate_direction != direction
            or start > sample_end + _EPISODE_GAP
            or end < sample_start - _EPISODE_GAP
        ):
            continue
        primary_id = candidate.duplicate_of_task_id or candidate.id
        if primary_id == task.id or primary_id not in current_task_ids:
            continue
        return candidates_by_id[primary_id]
    return None


async def evaluate_heart_rate_shadow(
    session: AsyncSession,
    *,
    task: ProactiveCareTask,
    timezone_name: str,
) -> HeartRateShadowResult:
    """评估一页新心率；只写 shadow 证据，不创建消息或调用 Agent。"""

    imported = await session.get(WearableImport, task.wearable_import_id)
    if (
        imported is None
        or imported.user_id != task.user_id
        or imported.record_type is not WearableRecordType.HEART_RATE
    ):
        raise RuntimeError("健康任务没有对应的心率导入")
    profile = await session.get(PersonalProfile, task.user_id)
    if profile is None:
        raise RuntimeError("用户缺少基础档案")

    current_observations = list(
        await session.scalars(
            select(WearableObservation).where(
                WearableObservation.user_id == task.user_id,
                WearableObservation.record_type == WearableRecordType.HEART_RATE,
                WearableObservation.last_import_id == imported.id,
                WearableObservation.recording_method == _AUTOMATICALLY_RECORDED,
                WearableObservation.deleted_at.is_(None),
            )
        )
    )
    source_package = _evaluated_source_package(current_observations)
    current_times: list[datetime] = []
    minute_samples: MinuteSamples = defaultdict(list)
    context_observations: list[WearableObservation] = []
    if (
        profile.age_years is not None
        and profile.age_years >= 18
        and imported.health_context_complete
    ):
        current_times = [sample[0] for sample in _samples(current_observations)]
        received_at = imported.created_at.astimezone(timezone.utc)
        if current_times and not any(
            observed_at > received_at for observed_at in current_times
        ):
            fresh_current_times = [
                observed_at
                for observed_at in current_times
                if received_at - observed_at <= _MAXIMUM_DELAY
            ]
            if fresh_current_times:
                latest_current = max(fresh_current_times)
                search_start = min(fresh_current_times) - timedelta(
                    minutes=_WINDOW_MINUTES - 1
                )
                all_observations = list(
                    await session.scalars(
                        select(WearableObservation).where(
                            WearableObservation.user_id == task.user_id,
                            WearableObservation.record_type
                            == WearableRecordType.HEART_RATE,
                            WearableObservation.source_package == source_package,
                            WearableObservation.recording_method
                            == _AUTOMATICALLY_RECORDED,
                            WearableObservation.deleted_at.is_(None),
                            WearableObservation.end_time >= search_start,
                            WearableObservation.start_time <= latest_current,
                        )
                    )
                )
                for sample in _samples(all_observations):
                    if search_start <= sample[0] <= latest_current:
                        minute = sample[0].replace(second=0, microsecond=0)
                        minute_samples[minute].append(sample)
                context_observations = list(
                    await session.scalars(
                        select(WearableObservation).where(
                            WearableObservation.user_id == task.user_id,
                            WearableObservation.record_type.in_(
                                {
                                    WearableRecordType.EXERCISE,
                                    WearableRecordType.SLEEP,
                                    WearableRecordType.STEPS,
                                }
                            ),
                            WearableObservation.source_package == source_package,
                            WearableObservation.deleted_at.is_(None),
                            WearableObservation.end_time
                            >= search_start - _EXERCISE_MARGIN,
                            WearableObservation.start_time
                            <= latest_current + _EXERCISE_MARGIN,
                        )
                    )
                )

    result = _evaluate_window(
        age_years=profile.age_years,
        timezone_name=timezone_name,
        source_package=source_package,
        minute_samples=minute_samples,
        context_observations=context_observations,
        received_at=imported.created_at,
        current_times=current_times,
        adult_assumed=False,
        context_complete=imported.health_context_complete,
    )
    if result.decision != "candidate":
        return _store_result(task, result)
    if (
        result.direction is None
        or result.sample_start is None
        or result.sample_end is None
    ):
        raise TypeError("候选心率窗口缺少 episode 证据")

    primary = await _current_primary_candidate(
        session,
        task=task,
        direction=result.direction,
        sample_start=result.sample_start,
        sample_end=result.sample_end,
    )
    if primary is not None:
        result = result.model_copy(
            update={
                "reason": "duplicate_candidate",
                "event_key": primary.health_event_key,
                "duplicate_of_task_id": primary.id,
            }
        )
    return _store_result(task, result)
