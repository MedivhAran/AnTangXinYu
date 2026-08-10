from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    model_validator,
)

from antang_api.health_profile.types import (
    HealthFactSnapshot,
    PersonalProfileUnit,
    PersonalProfileSnapshot,
)
from antang_api.models import (
    FactAssertion,
    FactTemporalStatus,
    HealthFactType,
    PersonalProfileField,
    ProfileChangeMode,
    ProfileChangeStatus,
    ProfileOperation,
    ProfileTargetType,
    WearableRecordType,
)

ExternalRecordId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
PositiveDecimal = Annotated[Decimal, Field(gt=0)]


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WearableDevice(StrictApiModel):
    manufacturer: ShortText | None = None
    model: ShortText | None = None
    device_type: Annotated[int, Field(ge=0)] | None = None


class StepsData(StrictApiModel):
    count: NonNegativeInt


class ExerciseData(StrictApiModel):
    exercise_type: NonNegativeInt
    title: ShortText | None = None


class DistanceData(StrictApiModel):
    meters: NonNegativeDecimal


class ElevationGainedData(StrictApiModel):
    meters: NonNegativeDecimal


class WeightData(StrictApiModel):
    kilograms: PositiveDecimal


class RespiratoryRateData(StrictApiModel):
    breaths_per_minute: PositiveDecimal


class RestingHeartRateData(StrictApiModel):
    beats_per_minute: PositiveInt


class HeartRateSample(StrictApiModel):
    time: datetime
    beats_per_minute: PositiveInt

    @model_validator(mode="after")
    def require_timezone(self) -> "HeartRateSample":
        if self.time.tzinfo is None or self.time.utcoffset() is None:
            raise ValueError("heart rate sample time requires timezone")
        return self


class HeartRateData(StrictApiModel):
    samples: Annotated[list[HeartRateSample], Field(min_length=1, max_length=10000)]

    @model_validator(mode="after")
    def require_sorted_unique_samples(self) -> "HeartRateData":
        times = [sample.time for sample in self.samples]
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError("heart rate samples must be sorted and unique")
        return self


class SleepStage(StrictApiModel):
    start_time: datetime
    end_time: datetime
    stage: Literal[
        "unknown",
        "awake",
        "sleeping",
        "out_of_bed",
        "awake_in_bed",
        "light",
        "deep",
        "rem",
    ]

    @model_validator(mode="after")
    def validate_interval(self) -> "SleepStage":
        if (
            self.start_time.tzinfo is None
            or self.start_time.utcoffset() is None
            or self.end_time.tzinfo is None
            or self.end_time.utcoffset() is None
        ):
            raise ValueError("sleep stage times require timezone")
        if self.end_time <= self.start_time:
            raise ValueError("sleep stage end_time must be after start_time")
        return self


class SleepData(StrictApiModel):
    stages: Annotated[list[SleepStage], Field(max_length=10000)]

    @model_validator(mode="after")
    def require_ordered_non_overlapping_stages(self) -> "SleepData":
        previous_end: datetime | None = None
        for stage in self.stages:
            if previous_end is not None and stage.start_time < previous_end:
                raise ValueError("sleep stages must be ordered and non-overlapping")
            previous_end = stage.end_time
        return self


class OxygenSaturationData(StrictApiModel):
    percentage: Annotated[Decimal, Field(ge=0, le=100)]


class WearableRecordBase(StrictApiModel):
    external_record_id: ExternalRecordId
    start_time: datetime
    end_time: datetime
    start_zone_offset_seconds: Annotated[int, Field(ge=-64800, le=64800)] | None = None
    end_zone_offset_seconds: Annotated[int, Field(ge=-64800, le=64800)] | None = None
    # 首版只接收目标设备实际使用的 Google Play 版 Zepp 数据源。
    # 这同时避免把手机或其他 App 的累计步数、距离再次相加。
    source_package: Literal["com.huami.watch.hmwatchmanager"]
    recording_method: Annotated[int, Field(ge=0)] | None = None
    device: WearableDevice | None = None
    source_last_modified_at: datetime

    @model_validator(mode="after")
    def validate_times(self) -> "WearableRecordBase":
        for value in (self.start_time, self.end_time, self.source_last_modified_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("wearable record times require timezone")
        if self.end_time < self.start_time:
            raise ValueError("end_time cannot be before start_time")
        return self


class StepsRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.STEPS]
    data: StepsData


class ExerciseRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.EXERCISE]
    data: ExerciseData


class DistanceRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.DISTANCE]
    data: DistanceData


class ElevationGainedRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.ELEVATION_GAINED]
    data: ElevationGainedData


class WeightRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.WEIGHT]
    data: WeightData


class RespiratoryRateRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.RESPIRATORY_RATE]
    data: RespiratoryRateData


class RestingHeartRateRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.RESTING_HEART_RATE]
    data: RestingHeartRateData


class HeartRateRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.HEART_RATE]
    data: HeartRateData

    @model_validator(mode="after")
    def samples_must_be_inside_record(self) -> "HeartRateRecord":
        if any(
            sample.time < self.start_time or sample.time > self.end_time
            for sample in self.data.samples
        ):
            raise ValueError("heart rate samples must be inside record interval")
        return self


class SleepRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.SLEEP]
    data: SleepData

    @model_validator(mode="after")
    def stages_must_be_inside_record(self) -> "SleepRecord":
        if any(
            stage.start_time < self.start_time or stage.end_time > self.end_time
            for stage in self.data.stages
        ):
            raise ValueError("sleep stages must be inside record interval")
        return self


class OxygenSaturationRecord(WearableRecordBase):
    record_type: Literal[WearableRecordType.OXYGEN_SATURATION]
    data: OxygenSaturationData


WearableRecord = Annotated[
    StepsRecord
    | ExerciseRecord
    | DistanceRecord
    | ElevationGainedRecord
    | WeightRecord
    | RespiratoryRateRecord
    | RestingHeartRateRecord
    | HeartRateRecord
    | SleepRecord
    | OxygenSaturationRecord,
    Field(discriminator="record_type"),
]


class WearableImportRequest(StrictApiModel):
    client_sync_id: UUID
    record_type: WearableRecordType
    health_context_complete: bool = False
    records: Annotated[list[WearableRecord], Field(max_length=1000)]
    deleted_record_ids: Annotated[list[ExternalRecordId], Field(max_length=1000)] = (
        Field(default_factory=list)
    )

    @model_validator(mode="after")
    def validate_record_ids(self) -> "WearableImportRequest":
        if any(record.record_type != self.record_type for record in self.records):
            raise ValueError("every record must match request record_type")
        if (
            self.health_context_complete
            and self.record_type is not WearableRecordType.HEART_RATE
        ):
            raise ValueError(
                "health_context_complete is only valid for heart_rate imports"
            )
        record_ids = [record.external_record_id for record in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("records contain duplicate external_record_id")
        if len(self.deleted_record_ids) != len(set(self.deleted_record_ids)):
            raise ValueError("deleted_record_ids contain duplicates")
        if set(record_ids) & set(self.deleted_record_ids):
            raise ValueError("a record cannot be uploaded and deleted in one import")
        return self


class WearableImportResponse(BaseModel):
    import_id: UUID
    status: Literal["completed"] = "completed"
    records_created: int
    records_updated: int
    records_unchanged: int
    records_deleted: int


class WearableLatestResponse(BaseModel):
    record_type: WearableRecordType
    observed_at: datetime
    data: dict[str, object]
    source_package: str


class ProfileCardResponse(BaseModel):
    id: UUID
    kind: Literal[ProfileChangeMode.CONFIRMATION, ProfileChangeMode.CLARIFICATION]
    target_type: ProfileTargetType
    field_name: str
    operation: ProfileOperation
    question: str
    proposed_value: dict[str, object] | None
    options: list["ProfileCardOption"]
    allow_custom_input: bool
    custom_input_placeholder: str | None
    created_at: datetime


class ProfileCardsResponse(BaseModel):
    cards: list[ProfileCardResponse]


class ProfileCardOption(BaseModel):
    id: Literal["accept", "reject", "kg", "jin"]
    label: str
    value: dict[str, object] | None = None


class PersonalProfileCardCustomAnswer(StrictApiModel):
    value: str | int | Decimal
    unit: PersonalProfileUnit | None = None


class HealthFactCardCustomAnswer(StrictApiModel):
    statement: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    assertion: FactAssertion
    temporal_status: FactTemporalStatus


class ProfileCardAnswerRequest(StrictApiModel):
    client_action_id: UUID
    option_id: Literal["accept", "reject", "kg", "jin"] | None = None
    custom_answer: (
        PersonalProfileCardCustomAnswer | HealthFactCardCustomAnswer | None
    ) = None

    @model_validator(mode="after")
    def require_one_answer(self) -> "ProfileCardAnswerRequest":
        if (self.option_id is None) == (self.custom_answer is None):
            raise ValueError("provide exactly one of option_id or custom_answer")
        return self


class ProfileCardDecisionRequest(StrictApiModel):
    client_action_id: UUID
    decision: Literal["accept", "reject"]


class ProfileCardDecisionResponse(BaseModel):
    card_id: UUID
    status: Literal[
        ProfileChangeStatus.APPLIED,
        ProfileChangeStatus.REJECTED,
        ProfileChangeStatus.CONFLICTED,
    ]


class PersonalProfileChangeRequest(StrictApiModel):
    client_action_id: UUID
    expected_revision: NonNegativeInt
    target_type: Literal[ProfileTargetType.PERSONAL_PROFILE]
    field_name: PersonalProfileField
    operation: Literal[ProfileOperation.SET, ProfileOperation.CLEAR]
    value: str | int | Decimal | None = None
    unit: PersonalProfileUnit | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "PersonalProfileChangeRequest":
        if self.operation is ProfileOperation.CLEAR:
            if self.value is not None or self.unit is not None:
                raise ValueError("clear operation cannot contain value or unit")
        elif self.value is None:
            raise ValueError("set operation requires value")
        return self


class HealthFactChangeRequest(StrictApiModel):
    client_action_id: UUID
    expected_revision: NonNegativeInt | None = None
    target_type: Literal[ProfileTargetType.HEALTH_FACT]
    operation: Literal[
        ProfileOperation.ADD,
        ProfileOperation.UPDATE,
        ProfileOperation.RETRACT,
    ]
    fact_type: HealthFactType
    target_id: UUID | None = None
    statement: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
        ]
        | None
    ) = None
    assertion: FactAssertion | None = None
    temporal_status: FactTemporalStatus | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "HealthFactChangeRequest":
        complete = (
            self.statement is not None
            and self.assertion is not None
            and self.temporal_status is not None
        )
        if self.operation is ProfileOperation.ADD:
            if self.target_id is not None or self.expected_revision is not None:
                raise ValueError("add operation cannot target an existing fact")
            if not complete:
                raise ValueError("add operation requires a complete fact")
        elif self.operation is ProfileOperation.UPDATE:
            if self.target_id is None or self.expected_revision is None:
                raise ValueError("update operation requires target and revision")
            if not complete:
                raise ValueError("update operation requires a complete fact")
        else:
            if self.target_id is None or self.expected_revision is None:
                raise ValueError("retract operation requires target and revision")
            if any(
                value is not None
                for value in (self.statement, self.assertion, self.temporal_status)
            ):
                raise ValueError("retract operation cannot contain replacement content")
        return self


HealthProfileChangeRequest = Annotated[
    PersonalProfileChangeRequest | HealthFactChangeRequest,
    Field(discriminator="target_type"),
]


class HealthProfileChangeResponse(BaseModel):
    change_id: UUID
    status: Literal[ProfileChangeStatus.APPLIED]
    target_type: ProfileTargetType
    field_name: str
    operation: ProfileOperation
    target_id: UUID | None
    result_revision: int


class HealthProfileResponse(BaseModel):
    personal_profile: PersonalProfileSnapshot
    health_facts: list[HealthFactSnapshot]
    wearable_latest: list[WearableLatestResponse]
