import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.health_profile.errors import (
    InvalidProfileProposalError,
    WearableImportConflictError,
    WearableObservationNotFoundError,
)
from antang_api.health_profile.types import (
    WearableDailySummary,
    WearableObservationSnapshot,
)
from antang_api.models import (
    WearableImport,
    WearableObservation,
    WearableRecordTombstone,
    WearableRecordType,
)
from antang_api.schemas.health_profile import (
    WearableImportRequest,
    WearableImportResponse,
    WearableRecord,
)

_PROVIDER = "health_connect"
_ZEPP_SOURCE_PACKAGE = "com.huami.watch.hmwatchmanager"
_TWO_DECIMALS = Decimal("0.01")


async def process_wearable_import(
    session: AsyncSession,
    *,
    user_id: UUID,
    request: WearableImportRequest,
) -> WearableImportResponse:
    """原子处理一页 Health Connect 变化，不提交事务。"""

    request_hash = _request_hash(request)
    # 同一用户和同步 ID 的并发请求串行处理，避免靠捕获唯一约束做半事务恢复。
    lock_key = f"wearable-import:{user_id}:{request.client_sync_id}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": lock_key},
    )

    existing_import = await session.scalar(
        select(WearableImport).where(
            WearableImport.user_id == user_id,
            WearableImport.client_sync_id == request.client_sync_id,
        )
    )
    if existing_import is not None:
        if existing_import.request_hash != request_hash:
            raise WearableImportConflictError("client_sync_id_payload_mismatch")
        return _import_response(existing_import)

    imported = WearableImport(
        user_id=user_id,
        client_sync_id=request.client_sync_id,
        request_hash=request_hash,
        record_type=request.record_type,
        health_context_complete=request.health_context_complete,
        records_created=0,
        records_updated=0,
        records_unchanged=0,
        records_deleted=0,
    )
    session.add(imported)
    await session.flush()

    all_ids = {
        *(record.external_record_id for record in request.records),
        *request.deleted_record_ids,
    }
    observations = {
        observation.external_record_id: observation
        for observation in await session.scalars(
            select(WearableObservation)
            .where(
                WearableObservation.user_id == user_id,
                WearableObservation.provider == _PROVIDER,
                WearableObservation.external_record_id.in_(all_ids),
            )
            .with_for_update()
        )
    }
    tombstones = {
        tombstone.external_record_id: tombstone
        for tombstone in await session.scalars(
            select(WearableRecordTombstone)
            .where(
                WearableRecordTombstone.user_id == user_id,
                WearableRecordTombstone.provider == _PROVIDER,
                WearableRecordTombstone.external_record_id.in_(all_ids),
            )
            .with_for_update()
        )
    }

    now = datetime.now(timezone.utc)
    for external_record_id in request.deleted_record_ids:
        if external_record_id in tombstones:
            continue
        tombstone = WearableRecordTombstone(
            user_id=user_id,
            provider=_PROVIDER,
            external_record_id=external_record_id,
            import_id=imported.id,
            deleted_at=now,
        )
        session.add(tombstone)
        tombstones[external_record_id] = tombstone
        imported.records_deleted += 1

        observation = observations.get(external_record_id)
        if (
            observation is not None
            and observation.record_type is not request.record_type
        ):
            raise WearableImportConflictError(
                "deleted_record_type_mismatch",
                record_id=external_record_id,
            )
        if observation is not None and observation.deleted_at is None:
            observation.deleted_at = now
            observation.updated_at = now

    for record in request.records:
        external_record_id = record.external_record_id
        if external_record_id in tombstones:
            # Changes API 的旧页可能在删除页之后重放；墓碑永远优先。
            imported.records_unchanged += 1
            continue

        data_hash = _record_hash(record)
        observation = observations.get(external_record_id)
        if observation is None:
            observation = _new_observation(
                user_id=user_id,
                import_id=imported.id,
                record=record,
                data_hash=data_hash,
            )
            session.add(observation)
            observations[external_record_id] = observation
            imported.records_created += 1
            continue

        if observation.record_type is not record.record_type:
            raise WearableImportConflictError(
                "record_type_changed",
                record_id=external_record_id,
            )
        if observation.data_hash == data_hash:
            if record.source_last_modified_at > observation.source_last_modified_at:
                observation.source_last_modified_at = record.source_last_modified_at
            imported.records_unchanged += 1
            continue
        if record.source_last_modified_at < observation.source_last_modified_at:
            imported.records_unchanged += 1
            continue
        if record.source_last_modified_at == observation.source_last_modified_at:
            raise WearableImportConflictError(
                "same_version_different_content",
                record_id=external_record_id,
            )

        _update_observation(
            observation,
            import_id=imported.id,
            record=record,
            data_hash=data_hash,
            now=now,
        )
        imported.records_updated += 1

    await session.flush()
    return _import_response(imported)


async def read_latest_wearable_observations(
    session: AsyncSession,
    *,
    user_id: UUID,
    record_types: Sequence[WearableRecordType] | None = None,
) -> list[WearableObservationSnapshot]:
    """每种类型最多返回一条最新有效观测。"""

    query = select(WearableObservation).where(
        WearableObservation.user_id == user_id,
        WearableObservation.provider == _PROVIDER,
        WearableObservation.deleted_at.is_(None),
    )
    if record_types is not None:
        if not record_types:
            return []
        query = query.where(WearableObservation.record_type.in_(set(record_types)))
    observations = list(
        await session.scalars(
            query.distinct(WearableObservation.record_type).order_by(
                WearableObservation.record_type,
                WearableObservation.end_time.desc(),
                WearableObservation.id.desc(),
            )
        )
    )
    return [WearableObservationSnapshot.model_validate(item) for item in observations]


async def list_health_connect_record_ids(
    session: AsyncSession,
    *,
    user_id: UUID,
    record_type: WearableRecordType,
    after: str | None,
    limit: int = 1000,
) -> tuple[list[str], str | None]:
    """Page through this user's active Zepp IDs for full-history reconciliation."""

    query = select(WearableObservation.external_record_id).where(
        WearableObservation.user_id == user_id,
        WearableObservation.provider == _PROVIDER,
        WearableObservation.source_package == _ZEPP_SOURCE_PACKAGE,
        WearableObservation.record_type == record_type,
        WearableObservation.deleted_at.is_(None),
    )
    if after is not None:
        query = query.where(WearableObservation.external_record_id > after)
    ids = list(
        await session.scalars(
            query.order_by(WearableObservation.external_record_id).limit(limit + 1)
        )
    )
    return (ids[:limit], ids[limit - 1]) if len(ids) > limit else (ids, None)


async def read_wearable_observations(
    session: AsyncSession,
    *,
    user_id: UUID,
    record_types: Sequence[WearableRecordType],
    start: datetime,
    end: datetime,
    source_package: str | None = None,
    limit: int = 200,
) -> list[WearableObservationSnapshot]:
    """读取有明确类型、时间范围和数量上限的原始观测。"""

    _validate_read_range(record_types, start, end)
    if limit < 1 or limit > 5000:
        raise InvalidProfileProposalError(
            "wearable query limit must be between 1 and 5000"
        )
    query = select(WearableObservation).where(
        WearableObservation.user_id == user_id,
        WearableObservation.provider == _PROVIDER,
        WearableObservation.record_type.in_(set(record_types)),
        WearableObservation.deleted_at.is_(None),
        WearableObservation.end_time >= start,
        WearableObservation.start_time <= end,
    )
    if source_package is not None:
        query = query.where(WearableObservation.source_package == source_package)
    observations = list(
        await session.scalars(
            query.order_by(
                WearableObservation.start_time.desc(),
                WearableObservation.id.desc(),
            ).limit(limit)
        )
    )
    return [WearableObservationSnapshot.model_validate(item) for item in observations]


async def read_wearable_observation_detail(
    session: AsyncSession,
    *,
    user_id: UUID,
    observation_id: UUID,
) -> WearableObservationSnapshot:
    observation = await session.scalar(
        select(WearableObservation).where(
            WearableObservation.id == observation_id,
            WearableObservation.user_id == user_id,
            WearableObservation.provider == _PROVIDER,
            WearableObservation.deleted_at.is_(None),
        )
    )
    if observation is None:
        raise WearableObservationNotFoundError(observation_id)
    return WearableObservationSnapshot.model_validate(observation)


async def read_wearable_daily_summaries(
    session: AsyncSession,
    *,
    user_id: UUID,
    record_types: Sequence[WearableRecordType],
    start: datetime,
    end: datetime,
) -> list[WearableDailySummary]:
    """用确定性代码汇总最多 31 天的数据；结果可以从原记录重算。"""

    _validate_read_range(record_types, start, end)
    # 分钟级心率一个月会自然超过 5,000 条。汇总不能复用详情查询的
    # limit，否则会悄悄拿最近一小段冒充整段时间。这里读取完整窗口，
    # 但只把按日聚合后的结果交给模型。
    observations = [
        WearableObservationSnapshot.model_validate(item)
        for item in await session.scalars(
            select(WearableObservation)
            .where(
                WearableObservation.user_id == user_id,
                WearableObservation.provider == _PROVIDER,
                WearableObservation.record_type.in_(set(record_types)),
                WearableObservation.deleted_at.is_(None),
                WearableObservation.end_time >= start,
                WearableObservation.start_time <= end,
            )
            .order_by(
                WearableObservation.start_time,
                WearableObservation.id,
            )
        )
    ]
    grouped: dict[
        tuple[date, WearableRecordType], list[WearableObservationSnapshot]
    ] = defaultdict(list)
    for observation in observations:
        offset = observation.start_zone_offset_seconds or 0
        local_day = (observation.start_time + timedelta(seconds=offset)).date()
        grouped[(local_day, observation.record_type)].append(observation)

    return [
        WearableDailySummary(
            day=day,
            record_type=record_type,
            data=_summarize_observations(record_type, values),
        )
        for (day, record_type), values in sorted(
            grouped.items(), key=lambda item: (item[0][0], item[0][1].value)
        )
    ]


def _request_hash(request: WearableImportRequest) -> str:
    payload = request.model_dump(mode="json")
    payload["records"] = sorted(
        payload["records"], key=lambda item: item["external_record_id"]
    )
    payload["deleted_record_ids"] = sorted(payload["deleted_record_ids"])
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _record_hash(record: WearableRecord) -> str:
    payload = record.model_dump(mode="json")
    payload.pop("source_last_modified_at")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _new_observation(
    *,
    user_id: UUID,
    import_id: UUID,
    record: WearableRecord,
    data_hash: str,
) -> WearableObservation:
    return WearableObservation(
        user_id=user_id,
        provider=_PROVIDER,
        external_record_id=record.external_record_id,
        record_type=record.record_type,
        start_time=record.start_time,
        end_time=record.end_time,
        start_zone_offset_seconds=record.start_zone_offset_seconds,
        end_zone_offset_seconds=record.end_zone_offset_seconds,
        data=record.data.model_dump(mode="json"),
        data_hash=data_hash,
        source_package=record.source_package,
        recording_method=record.recording_method,
        device=(
            record.device.model_dump(mode="json") if record.device is not None else None
        ),
        source_last_modified_at=record.source_last_modified_at,
        last_import_id=import_id,
    )


def _update_observation(
    observation: WearableObservation,
    *,
    import_id: UUID,
    record: WearableRecord,
    data_hash: str,
    now: datetime,
) -> None:
    observation.start_time = record.start_time
    observation.end_time = record.end_time
    observation.start_zone_offset_seconds = record.start_zone_offset_seconds
    observation.end_zone_offset_seconds = record.end_zone_offset_seconds
    observation.data = record.data.model_dump(mode="json")
    observation.data_hash = data_hash
    observation.source_package = record.source_package
    observation.recording_method = record.recording_method
    observation.device = (
        record.device.model_dump(mode="json") if record.device is not None else None
    )
    observation.source_last_modified_at = record.source_last_modified_at
    observation.last_import_id = import_id
    observation.updated_at = now


def _import_response(imported: WearableImport) -> WearableImportResponse:
    return WearableImportResponse(
        import_id=imported.id,
        records_created=imported.records_created,
        records_updated=imported.records_updated,
        records_unchanged=imported.records_unchanged,
        records_deleted=imported.records_deleted,
    )


def _validate_read_range(
    record_types: Sequence[WearableRecordType],
    start: datetime,
    end: datetime,
) -> None:
    if not record_types:
        raise InvalidProfileProposalError(
            "at least one wearable record type is required"
        )
    if start.tzinfo is None or end.tzinfo is None:
        raise InvalidProfileProposalError("wearable query times require timezone")
    if end <= start or end - start > timedelta(days=31):
        raise InvalidProfileProposalError("wearable query range must be within 31 days")


def _summarize_observations(
    record_type: WearableRecordType,
    observations: Sequence[WearableObservationSnapshot],
) -> dict[str, object]:
    if record_type is WearableRecordType.STEPS:
        return {"count": sum(int(str(item.data["count"])) for item in observations)}
    if record_type in {
        WearableRecordType.DISTANCE,
        WearableRecordType.ELEVATION_GAINED,
    }:
        total = sum(
            (Decimal(str(item.data["meters"])) for item in observations),
            start=Decimal(0),
        )
        return {"meters": _decimal_json(total)}
    if record_type is WearableRecordType.EXERCISE:
        seconds = sum(
            (
                Decimal(str((item.end_time - item.start_time).total_seconds()))
                for item in observations
            ),
            start=Decimal(0),
        )
        return {"sessions": len(observations), "seconds": _decimal_json(seconds)}
    if record_type is WearableRecordType.WEIGHT:
        latest = max(observations, key=lambda item: item.end_time)
        return {
            "latest_kilograms": latest.data["kilograms"],
            "observed_at": latest.end_time.isoformat(),
        }
    if record_type is WearableRecordType.SLEEP:
        stage_seconds: dict[str, Decimal] = defaultdict(Decimal)
        for observation in observations:
            stages = observation.data.get("stages", [])
            if not isinstance(stages, list):
                raise TypeError("validated sleep stages are not a list")
            for stage in stages:
                if not isinstance(stage, dict):
                    raise TypeError("validated sleep stage is not an object")
                start = datetime.fromisoformat(str(stage["start_time"]))
                end = datetime.fromisoformat(str(stage["end_time"]))
                stage_seconds[str(stage["stage"])] += Decimal(
                    str((end - start).total_seconds())
                )
        return {
            "seconds_by_stage": {
                key: _decimal_json(value)
                for key, value in sorted(stage_seconds.items())
            }
        }

    values: list[Decimal] = []
    value_key: str
    if record_type is WearableRecordType.HEART_RATE:
        value_key = "beats_per_minute"
        for observation in observations:
            samples = observation.data.get("samples", [])
            if not isinstance(samples, list):
                raise TypeError("validated heart rate samples are not a list")
            values.extend(
                Decimal(str(sample[value_key]))
                for sample in samples
                if isinstance(sample, dict)
            )
    else:
        value_key = {
            WearableRecordType.RESPIRATORY_RATE: "breaths_per_minute",
            WearableRecordType.RESTING_HEART_RATE: "beats_per_minute",
            WearableRecordType.OXYGEN_SATURATION: "percentage",
        }[record_type]
        values = [Decimal(str(item.data[value_key])) for item in observations]

    if not values:
        raise TypeError("validated wearable observations contain no values")
    average = sum(values) / Decimal(len(values))
    return {
        "samples": len(values),
        "average": _decimal_json(average),
        "minimum": _decimal_json(min(values)),
        "maximum": _decimal_json(max(values)),
    }


def _decimal_json(value: Decimal) -> str:
    return str(value.quantize(_TWO_DECIMALS, rounding=ROUND_HALF_UP))
