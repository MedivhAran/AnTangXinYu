"""Offline heart-rate-shadow-v0 replay.

The replay loads legacy batches into the same pure minute-window rule used by
production. It only adds historical scanning and report aggregation, without
mutating imports or creating care tasks.
"""

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    PersonalProfile,
    WearableImport,
    WearableObservation,
    WearableRecordType,
)
from antang_api.proactive_care.heart_rate_shadow import (
    HEART_RATE_RULE_ID,
    HEART_RATE_RULE_VERSION,
    SOURCE_PACKAGES,
    HeartSample,
    HeartRateShadowResult,
    MinuteSamples,
    _AUTOMATICALLY_RECORDED,
    _EPISODE_GAP,
    _EXERCISE_MARGIN,
    _MAXIMUM_DELAY,
    _WINDOW_MINUTES,
    _evaluate_window,
    _evaluated_source_package,
    _samples,
)

ReplayHistory = tuple[
    PersonalProfile,
    dict[UUID, WearableImport],
    MinuteSamples,
    list[HeartSample],
    dict[UUID, list[datetime]],
    dict[datetime, set[UUID]],
    list[WearableObservation],
    str,
]


async def _load_history(
    session: AsyncSession,
    *,
    user_id: UUID,
    start: datetime,
    end: datetime,
) -> ReplayHistory:
    profile = await session.get(PersonalProfile, user_id)
    if profile is None:
        raise RuntimeError("用户缺少基础档案")

    history_start = start - timedelta(minutes=_WINDOW_MINUTES - 1)
    heart_observations = list(
        await session.scalars(
            select(WearableObservation).where(
                WearableObservation.user_id == user_id,
                WearableObservation.record_type == WearableRecordType.HEART_RATE,
                WearableObservation.source_package.in_(SOURCE_PACKAGES),
                WearableObservation.recording_method == _AUTOMATICALLY_RECORDED,
                WearableObservation.deleted_at.is_(None),
                WearableObservation.end_time >= history_start,
                WearableObservation.start_time < end,
            )
        )
    )
    source_package = _evaluated_source_package(heart_observations)
    context_observations = list(
        await session.scalars(
            select(WearableObservation).where(
                WearableObservation.user_id == user_id,
                WearableObservation.record_type.in_(
                    {
                        WearableRecordType.EXERCISE,
                        WearableRecordType.SLEEP,
                        WearableRecordType.STEPS,
                    }
                ),
                WearableObservation.deleted_at.is_(None),
                WearableObservation.end_time
                >= history_start - _EXERCISE_MARGIN,
                WearableObservation.start_time < end + _EXERCISE_MARGIN,
            )
        )
    )

    minute_samples: MinuteSamples = defaultdict(list)
    target_samples: list[HeartSample] = []
    batch_times: dict[UUID, list[datetime]] = defaultdict(list)
    import_ids_by_minute: dict[datetime, set[UUID]] = defaultdict(set)
    for sample in _samples(heart_observations):
        observed_at, _value, observation = sample
        minute = observed_at.replace(second=0, microsecond=0)
        if history_start <= observed_at < end:
            minute_samples[minute].append(sample)
        if start <= observed_at < end:
            target_samples.append(sample)
            batch_times[observation.last_import_id].append(observed_at)
            import_ids_by_minute[minute].add(observation.last_import_id)

    imports = {
        imported.id: imported
        for imported in await session.scalars(
            select(WearableImport).where(
                WearableImport.user_id == user_id,
                WearableImport.id.in_(batch_times),
            )
        )
    }
    if len(imports) != len(batch_times):
        raise RuntimeError("历史心率记录缺少对应导入批次")
    return (
        profile,
        imports,
        minute_samples,
        target_samples,
        batch_times,
        import_ids_by_minute,
        context_observations,
        source_package,
    )


def _result_view(
    results: list[HeartRateShadowResult],
    *,
    start: datetime,
    simulation_only: bool,
) -> dict[str, Any]:
    episodes: list[dict[str, Any]] = []
    blocked_evidence_groups: list[dict[str, Any]] = []
    for result in sorted(
        (
            item
            for item in results
            if item.decision in {"candidate", "blocked"}
            and item.direction is not None
            and item.sample_start is not None
            and item.sample_end is not None
        ),
        key=lambda item: (item.sample_start or start, item.sample_end or start),
    ):
        if (
            result.direction is None
            or result.sample_start is None
            or result.sample_end is None
        ):
            raise TypeError("心率窗口缺少 episode 证据")
        target = (
            episodes
            if result.decision == "candidate"
            else blocked_evidence_groups
        )
        evidence = result.evidence()
        previous = target[-1] if target else None
        if (
            previous is not None
            and previous["direction"] == result.direction
            and previous["reason"] == result.reason
            and result.sample_start
            <= datetime.fromisoformat(str(previous["sample_end"]))
            + _EPISODE_GAP
        ):
            previous["sample_end"] = max(
                datetime.fromisoformat(str(previous["sample_end"])),
                result.sample_end,
            ).isoformat()
            previous["window_count"] += 1
            previous["windows"].append(evidence)
            previous["source_records"].update(result.source_records)
            previous["context_records"].update(result.context_records)
            continue
        target.append(
            {
                "reason": result.reason,
                "direction": result.direction,
                "sample_start": result.sample_start.isoformat(),
                "sample_end": result.sample_end.isoformat(),
                "window_count": 1,
                "source_records": dict(result.source_records),
                "context_records": dict(result.context_records),
                "windows": [evidence],
            }
        )
    return {
        "simulation_only": simulation_only,
        "summary": {
            "evaluation_count": len(results),
            "decisions": dict(
                sorted(Counter(item.decision for item in results).items())
            ),
            "reasons": dict(
                sorted(Counter(item.reason for item in results).items())
            ),
        },
        "episodes": episodes,
        "blocked_evidence_groups": blocked_evidence_groups,
    }


async def replay_heart_rate_shadow(
    session: AsyncSession,
    *,
    user_id: UUID,
    start_at: datetime,
    end_at: datetime,
    timezone_name: str,
    assume_adult: bool = False,
    assume_context_complete: bool = False,
) -> dict[str, Any]:
    """离线重放历史心率，只返回报告，不写任务、消息或原始记录。"""

    if (
        start_at.tzinfo is None
        or start_at.utcoffset() is None
        or end_at.tzinfo is None
        or end_at.utcoffset() is None
    ):
        raise ValueError("心率回放时间范围必须包含时区")
    start = start_at.astimezone(timezone.utc)
    end = end_at.astimezone(timezone.utc)
    if end <= start:
        raise ValueError("心率回放结束时间必须晚于开始时间")

    (
        profile,
        imports,
        minute_samples,
        target_samples,
        batch_times,
        import_ids_by_minute,
        context_observations,
        source_package,
    ) = await _load_history(
        session,
        user_id=user_id,
        start=start,
        end=end,
    )

    snapshot_results: list[HeartRateShadowResult] = []
    batch_delays: list[int] = []
    sample_delays: list[int] = []
    fresh_batches = stale_batches = future_batches = 0
    fresh_samples = stale_samples = future_samples = 0
    for import_id, current_times in sorted(
        batch_times.items(),
        key=lambda item: (imports[item[0]].created_at, str(item[0])),
    ):
        imported = imports[import_id]
        received = imported.created_at.astimezone(timezone.utc)
        latest = max(current_times)
        batch_delay = int((received - latest).total_seconds())
        batch_delays.append(batch_delay)
        if any(observed_at > received for observed_at in current_times):
            future_batches += 1
        elif received - latest > _MAXIMUM_DELAY:
            stale_batches += 1
        else:
            fresh_batches += 1
        for observed_at in current_times:
            sample_delay = int((received - observed_at).total_seconds())
            sample_delays.append(sample_delay)
            if observed_at > received:
                future_samples += 1
            elif received - observed_at > _MAXIMUM_DELAY:
                stale_samples += 1
            else:
                fresh_samples += 1
        snapshot_results.append(
            _evaluate_window(
                age_years=profile.age_years,
                timezone_name=timezone_name,
                source_package=source_package,
                minute_samples=minute_samples,
                context_observations=context_observations,
                received_at=received,
                current_times=current_times,
                adult_assumed=False,
                context_complete=imported.health_context_complete,
            )
        )

    target_times_by_minute: dict[datetime, list[datetime]] = defaultdict(list)
    for observed_at, _value, _observation in target_samples:
        target_times_by_minute[
            observed_at.replace(second=0, microsecond=0)
        ].append(observed_at)

    counterfactual_results: list[HeartRateShadowResult] = []
    all_imports_context_unknown = not any(
        imported.health_context_complete for imported in imports.values()
    )
    if target_samples and (
        (profile.age_years is None and not assume_adult)
        or (profile.age_years is not None and profile.age_years < 18)
        or (all_imports_context_unknown and not assume_context_complete)
    ):
        current_times = [sample[0] for sample in target_samples]
        counterfactual_results.append(
            _evaluate_window(
                age_years=profile.age_years,
                timezone_name=timezone_name,
                source_package=source_package,
                minute_samples=minute_samples,
                context_observations=context_observations,
                received_at=max(current_times),
                current_times=current_times,
                adult_assumed=assume_adult,
                context_complete=assume_context_complete,
            )
        )
    else:
        for minute, current_times in sorted(target_times_by_minute.items()):
            context_complete = assume_context_complete or all(
                imports[import_id].health_context_complete
                for import_id in import_ids_by_minute[minute]
            )
            counterfactual_results.append(
                _evaluate_window(
                    age_years=profile.age_years,
                    timezone_name=timezone_name,
                    source_package=source_package,
                    minute_samples=minute_samples,
                    context_observations=context_observations,
                    received_at=max(current_times),
                    current_times=current_times,
                    adult_assumed=assume_adult,
                    context_complete=context_complete,
                )
            )

    view_payloads = {
        "current_snapshot": _result_view(
            snapshot_results,
            start=start,
            simulation_only=True,
        ),
        "on_time_counterfactual": _result_view(
            counterfactual_results,
            start=start,
            simulation_only=True,
        ),
    }
    view_payloads["current_snapshot"].update(
        {
            "historical_reconstruction": False,
            "basis": "current_records_and_last_import_times",
        }
    )

    view_payloads["current_snapshot"]["summary"].update(
        {
            "batch_count": len(batch_times),
            "fresh_batches": fresh_batches,
            "stale_batches": stale_batches,
            "future_batches": future_batches,
            "sample_count": len(target_samples),
            "fresh_samples": fresh_samples,
            "stale_samples": stale_samples,
            "future_samples": future_samples,
            "age_unknown_batches": (
                len(batch_times) if profile.age_years is None else 0
            ),
            "context_unknown_batches": sum(
                not imports[import_id].health_context_complete
                for import_id in batch_times
            ),
            "last_import_batch_delay_seconds": (
                {
                    "minimum": min(batch_delays),
                    "median": median(batch_delays),
                    "maximum": max(batch_delays),
                }
                if batch_delays
                else None
            ),
            "last_import_sample_delay_seconds": (
                {
                    "minimum": min(sample_delays),
                    "median": median(sample_delays),
                    "maximum": max(sample_delays),
                }
                if sample_delays
                else None
            ),
        }
    )

    return {
        "rule_id": HEART_RATE_RULE_ID,
        "rule_version": HEART_RATE_RULE_VERSION,
        "user_id": str(user_id),
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "replay_only": True,
        "production_side_effects": False,
        "counterfactual_assumptions": {
            "on_time_arrival": True,
            "adult_when_age_unknown": assume_adult,
            "context_complete": assume_context_complete,
        },
        **view_payloads,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay heart-rate-shadow-v0 without production side effects.",
    )
    parser.add_argument("--user-id", required=True, type=UUID)
    parser.add_argument("--start-at", required=True)
    parser.add_argument("--end-at", required=True)
    parser.add_argument("--timezone", required=True, dest="timezone_name")
    parser.add_argument(
        "--assume-adult",
        action="store_true",
        help="Only counterfactual: treat an unknown-age user as an adult.",
    )
    parser.add_argument(
        "--assume-context-complete",
        action="store_true",
        help="Only counterfactual: treat historical activity context as complete.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="JSON filename under api/evals/reports (directories are rejected).",
    )
    args = parser.parse_args()
    output_name = Path(args.output)
    if output_name.name != args.output or output_name.suffix != ".json":
        parser.error("--output must be a .json filename without directories")
    try:
        start_at = datetime.fromisoformat(args.start_at)
        end_at = datetime.fromisoformat(args.end_at)
    except ValueError as error:
        parser.error(f"invalid ISO 8601 replay time: {error}")

    async def run() -> dict[str, Any]:
        from antang_api.database import engine, session_factory

        try:
            async with session_factory() as session:
                return await replay_heart_rate_shadow(
                    session,
                    user_id=args.user_id,
                    start_at=start_at,
                    end_at=end_at,
                    timezone_name=args.timezone_name,
                    assume_adult=args.assume_adult,
                    assume_context_complete=args.assume_context_complete,
                )
        finally:
            await engine.dispose()

    report = asyncio.run(run())
    output_path = (
        Path(__file__).resolve().parents[3] / "evals" / "reports" / output_name
    )
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
