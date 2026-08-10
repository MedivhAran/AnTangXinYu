from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
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

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]
EvidenceQuote = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]
PersonalProfileValue = str | int | Decimal


class ClarificationReason(str, Enum):
    MISSING_VALUE = "missing_value"
    MISSING_UNIT = "missing_unit"
    UNCERTAIN_FACT = "uncertain_fact"
    MISSING_DETAILS = "missing_details"


class PersonalProfileUnit(str, Enum):
    YEARS = "years"
    CENTIMETERS = "cm"
    METERS = "m"
    KILOGRAMS = "kg"
    JIN = "jin"
    POUNDS = "lb"


class ProposalBase(BaseModel):
    """模型只能给出结构化候选；它不能提供卡片文案或数据库字段。"""

    mode: ProfileChangeMode
    evidence_quote: EvidenceQuote
    clarification_reason: ClarificationReason | None = None
    resolves_change_id: UUID | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_clarification_reason(self) -> "ProposalBase":
        if self.mode is ProfileChangeMode.CLARIFICATION:
            if self.clarification_reason is None:
                raise ValueError("clarification proposal requires clarification_reason")
        elif self.clarification_reason is not None:
            raise ValueError("only clarification proposal accepts clarification_reason")
        return self


class PersonalProfileProposal(ProposalBase):
    target_type: Literal[ProfileTargetType.PERSONAL_PROFILE] = (
        ProfileTargetType.PERSONAL_PROFILE
    )
    field_name: PersonalProfileField
    operation: Literal[ProfileOperation.SET, ProfileOperation.CLEAR]
    value: PersonalProfileValue | None = None
    unit: PersonalProfileUnit | None = None

    @model_validator(mode="after")
    def validate_candidate(self) -> "PersonalProfileProposal":
        if isinstance(self.value, bool):
            raise ValueError("boolean is not a personal profile value")

        if self.mode is ProfileChangeMode.CLARIFICATION:
            if self.operation is ProfileOperation.CLEAR:
                raise ValueError("clear operation cannot be a clarification")
            # 澄清候选不会被执行，因此可以保留用户已经说出的半截信息。
            # 例如“体重 130”可以保存 source_value=130，但单位仍为空；
            # 应用程序只生成卡片，绝不会把 130 猜成公斤或斤。
            return self

        if self.operation is ProfileOperation.CLEAR:
            if self.value is not None:
                raise ValueError("clear operation cannot contain value")
            if self.unit is not None:
                raise ValueError("clear operation cannot contain unit")
            return self
        if self.value is None:
            raise ValueError("set operation requires value")

        if self.field_name is PersonalProfileField.AGE_YEARS:
            if (
                type(self.value) is not int
                or self.unit is not PersonalProfileUnit.YEARS
            ):
                raise ValueError("age requires an integer value with years unit")
        elif self.field_name is PersonalProfileField.HEIGHT_CM:
            if not isinstance(self.value, (int, Decimal)):
                raise ValueError("height requires a numeric value")
            if self.unit not in {
                PersonalProfileUnit.CENTIMETERS,
                PersonalProfileUnit.METERS,
            }:
                raise ValueError("height requires cm or m unit")
        elif self.field_name is PersonalProfileField.WEIGHT_KG:
            if not isinstance(self.value, (int, Decimal)):
                raise ValueError("weight requires a numeric value")
            if self.unit not in {
                PersonalProfileUnit.KILOGRAMS,
                PersonalProfileUnit.JIN,
                PersonalProfileUnit.POUNDS,
            }:
                raise ValueError("weight requires kg, jin, or lb unit")
        elif not isinstance(self.value, str) or self.unit is not None:
            raise ValueError("text fields require a string value without unit")
        return self


class HealthFactProposal(ProposalBase):
    target_type: Literal[ProfileTargetType.HEALTH_FACT] = ProfileTargetType.HEALTH_FACT
    fact_type: HealthFactType
    operation: Literal[
        ProfileOperation.ADD,
        ProfileOperation.UPDATE,
        ProfileOperation.RETRACT,
    ]
    target_id: UUID | None = None
    statement: NonEmptyText | None = None
    assertion: FactAssertion | None = None
    temporal_status: FactTemporalStatus | None = None
    effective_start: date | None = None
    effective_end: date | None = None

    @model_validator(mode="after")
    def validate_candidate(self) -> "HealthFactProposal":
        if self.effective_start and self.effective_end:
            if self.effective_end < self.effective_start:
                raise ValueError("effective_end cannot be before effective_start")

        if self.mode is ProfileChangeMode.CLARIFICATION:
            if self.operation is ProfileOperation.RETRACT:
                raise ValueError("retract operation cannot be a clarification")
            if self.operation is ProfileOperation.ADD and self.target_id is not None:
                raise ValueError("add operation cannot target an existing fact")
            if self.operation is ProfileOperation.UPDATE and self.target_id is None:
                raise ValueError("update operation requires target_id")
            # 与基础信息相同，澄清候选可以携带用户明确说出的局部事实。
            # mode=clarification 保证这些字段只用于后续补充，不会直接写档案。
            return self

        complete = (
            bool(self.statement)
            and self.assertion is not None
            and self.temporal_status is not None
        )
        if self.operation is ProfileOperation.ADD:
            if self.target_id is not None:
                raise ValueError("add operation cannot target an existing fact")
            if not complete:
                raise ValueError("add operation requires a complete fact")
        else:
            if self.target_id is None:
                raise ValueError("update and retract require target_id")
            if self.operation is ProfileOperation.UPDATE and not complete:
                raise ValueError("update operation requires a complete fact")
            if self.operation is ProfileOperation.RETRACT and any(
                value is not None
                for value in (
                    self.statement,
                    self.assertion,
                    self.temporal_status,
                    self.effective_start,
                    self.effective_end,
                )
            ):
                raise ValueError("retract operation cannot contain replacement content")
        return self


ProfileProposal = Annotated[
    PersonalProfileProposal | HealthFactProposal,
    Field(discriminator="target_type"),
]


class PersonalProfileSnapshot(BaseModel):
    sex: str | None
    age_years: int | None
    age_as_of_date: date | None
    height_cm: Decimal | None
    weight_kg: Decimal | None
    resident_area: str | None
    schedule_type: str | None
    occupation: str | None
    revision: int
    # 只用于子 Agent 运行期的精确冲突检查，不属于对外档案响应。
    field_revisions: dict[str, int] = Field(exclude=True)
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HealthFactSnapshot(BaseModel):
    id: UUID
    fact_type: HealthFactType
    statement: str
    assertion: FactAssertion
    temporal_status: FactTemporalStatus
    effective_start: date | None
    effective_end: date | None
    revision: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PendingChangeSnapshot(BaseModel):
    id: UUID
    mode: Literal[ProfileChangeMode.CONFIRMATION, ProfileChangeMode.CLARIFICATION]
    target_type: ProfileTargetType
    field_name: str
    operation: ProfileOperation
    target_id: UUID | None
    proposed_value: dict[str, object] | None
    question: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HealthProfileSnapshot(BaseModel):
    personal_profile: PersonalProfileSnapshot
    health_facts: list[HealthFactSnapshot]
    pending_changes: list[PendingChangeSnapshot]


class AppliedProposalResult(BaseModel):
    change_id: UUID
    proposal_index: int
    status: ProfileChangeStatus
    target_type: ProfileTargetType
    field_name: str
    operation: ProfileOperation


class ApplyProfileProposalsResult(BaseModel):
    changes: list[AppliedProposalResult]


class WearableObservationSnapshot(BaseModel):
    id: UUID
    record_type: WearableRecordType
    start_time: datetime
    end_time: datetime
    start_zone_offset_seconds: int | None
    end_zone_offset_seconds: int | None
    data: dict[str, object]
    source_package: str
    source_last_modified_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WearableDailySummary(BaseModel):
    day: date
    record_type: WearableRecordType
    data: dict[str, object]
