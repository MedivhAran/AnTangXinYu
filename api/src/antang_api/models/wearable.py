from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from antang_api.database import Base
from antang_api.models.chat import enum_values


class WearableRecordType(str, Enum):
    STEPS = "steps"
    EXERCISE = "exercise"
    DISTANCE = "distance"
    ELEVATION_GAINED = "elevation_gained"
    WEIGHT = "weight"
    RESPIRATORY_RATE = "respiratory_rate"
    RESTING_HEART_RATE = "resting_heart_rate"
    HEART_RATE = "heart_rate"
    SLEEP = "sleep"
    OXYGEN_SATURATION = "oxygen_saturation"


class WearableImport(Base):
    """手机的一次原子上传，用请求哈希保证网络重试不会重复处理。"""

    __tablename__ = "wearable_imports"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "client_sync_id",
            name="uq_wearable_imports_user_client_sync",
        ),
        CheckConstraint(
            "records_created >= 0 AND records_updated >= 0 "
            "AND records_unchanged >= 0 AND records_deleted >= 0",
            name="ck_wearable_imports_counts",
        ),
        CheckConstraint(
            "NOT health_context_complete OR "
            "(record_type IS NOT NULL AND record_type = 'heart_rate')",
            name="ck_wearable_imports_health_context",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    client_sync_id: Mapped[UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    # 旧数据迁移时无法可靠推断空页和纯删除页的来源类型，所以允许 NULL；
    # 新 API 请求始终明确提供类型。
    record_type: Mapped[WearableRecordType | None] = mapped_column(
        SqlEnum(
            WearableRecordType,
            name="wearable_record_type",
            values_callable=enum_values,
        )
    )
    health_context_complete: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
    )
    records_created: Mapped[int] = mapped_column(Integer)
    records_updated: Mapped[int] = mapped_column(Integer)
    records_unchanged: Mapped[int] = mapped_column(Integer)
    records_deleted: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WearableObservation(Base):
    """Health Connect 的规范化原始记录；data 由严格的记录类型模型验证。"""

    __tablename__ = "wearable_observations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "external_record_id",
            name="uq_wearable_observations_user_provider_external",
        ),
        Index(
            "ix_wearable_observations_user_type_start",
            "user_id",
            "record_type",
            "start_time",
        ),
        CheckConstraint(
            "end_time >= start_time",
            name="ck_wearable_observations_time_range",
        ),
        CheckConstraint(
            "start_zone_offset_seconds IS NULL OR "
            "start_zone_offset_seconds BETWEEN -64800 AND 64800",
            name="ck_wearable_observations_start_offset",
        ),
        CheckConstraint(
            "end_zone_offset_seconds IS NULL OR "
            "end_zone_offset_seconds BETWEEN -64800 AND 64800",
            name="ck_wearable_observations_end_offset",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(
        String(32), default="health_connect", server_default="health_connect"
    )
    external_record_id: Mapped[str] = mapped_column(String(255))
    record_type: Mapped[WearableRecordType] = mapped_column(
        SqlEnum(
            WearableRecordType,
            name="wearable_record_type",
            values_callable=enum_values,
        )
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_zone_offset_seconds: Mapped[int | None] = mapped_column(Integer)
    end_zone_offset_seconds: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    data_hash: Mapped[str] = mapped_column(String(64))
    source_package: Mapped[str] = mapped_column(String(255))
    recording_method: Mapped[int | None] = mapped_column(Integer)
    device: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_last_modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_import_id: Mapped[UUID] = mapped_column(
        ForeignKey("wearable_imports.id", ondelete="RESTRICT")
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WearableRecordTombstone(Base):
    """Health Connect 已删除记录的墓碑，阻止旧上传把它重新创建。"""

    __tablename__ = "wearable_record_tombstones"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "external_record_id",
            name="uq_wearable_tombstones_user_provider_external",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(
        String(32), default="health_connect", server_default="health_connect"
    )
    external_record_id: Mapped[str] = mapped_column(String(255))
    import_id: Mapped[UUID] = mapped_column(
        ForeignKey("wearable_imports.id", ondelete="RESTRICT")
    )
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
