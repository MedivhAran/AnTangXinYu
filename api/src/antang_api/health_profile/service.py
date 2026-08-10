import re
from hashlib import sha256
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import ValidationError
from pydantic_core import to_jsonable_python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.health_profile.errors import (
    HealthProfileInvariantError,
    HealthProfileChangedError,
    InvalidProfileCardDecisionError,
    InvalidProfileProposalError,
    ProfileClientActionConflictError,
    ProfileCardDecisionConflictError,
    ProfileCardNotFoundError,
)
from antang_api.health_profile.types import (
    AppliedProposalResult,
    ApplyProfileProposalsResult,
    ClarificationReason,
    HealthFactProposal,
    HealthFactSnapshot,
    HealthProfileSnapshot,
    PendingChangeSnapshot,
    PersonalProfileProposal,
    PersonalProfileSnapshot,
    PersonalProfileUnit,
    ProfileProposal,
)
from antang_api.models import (
    AgentRun,
    FactAssertion,
    FactTemporalStatus,
    HealthFact,
    HealthFactStatus,
    HealthFactType,
    HealthProfileChange,
    Message,
    MessageRole,
    PersonalProfile,
    PersonalProfileField,
    ProfileChangeMode,
    ProfileChangeStatus,
    ProfileOperation,
    ProfileTargetType,
)
from antang_api.schemas.health_profile import (
    HealthFactCardCustomAnswer,
    HealthProfileChangeRequest,
    PersonalProfileCardCustomAnswer,
    PersonalProfileChangeRequest,
    ProfileCardAnswerRequest,
    ProfileCardOption,
)

_PERSONAL_FIELD_LABELS = {
    PersonalProfileField.SEX: "性别",
    PersonalProfileField.AGE_YEARS: "年龄",
    PersonalProfileField.HEIGHT_CM: "身高",
    PersonalProfileField.WEIGHT_KG: "体重",
    PersonalProfileField.RESIDENT_AREA: "常驻区域",
    PersonalProfileField.SCHEDULE_TYPE: "作息类型",
    PersonalProfileField.OCCUPATION: "职业",
}
_HEALTH_FACT_LABELS = {
    HealthFactType.MEDICAL_HISTORY: "病史",
    HealthFactType.ALLERGY: "过敏信息",
    HealthFactType.SEVERE_HYPOGLYCEMIA_HISTORY: "严重低血糖史",
    HealthFactType.TREATMENT: "治疗情况",
}
_UNIT_LABELS = {
    PersonalProfileUnit.YEARS: "岁",
    PersonalProfileUnit.CENTIMETERS: "厘米",
    PersonalProfileUnit.METERS: "米",
    PersonalProfileUnit.KILOGRAMS: "公斤",
    PersonalProfileUnit.JIN: "斤",
    PersonalProfileUnit.POUNDS: "磅",
}
_CANONICAL_UNITS = {
    PersonalProfileField.AGE_YEARS: "years",
    PersonalProfileField.HEIGHT_CM: "cm",
    PersonalProfileField.WEIGHT_KG: "kg",
}
_TEXT_FIELD_LIMITS = {
    PersonalProfileField.SEX: 32,
    PersonalProfileField.RESIDENT_AREA: 255,
    PersonalProfileField.SCHEDULE_TYPE: 128,
    PersonalProfileField.OCCUPATION: 128,
}
_TWO_DECIMALS = Decimal("0.01")


async def load_health_profile_snapshot(
    session: AsyncSession,
    user_id: UUID,
) -> HealthProfileSnapshot:
    """读取 Agent 所需的当前档案；缺基础行代表数据库约束被破坏。"""

    profile = await session.get(PersonalProfile, user_id)
    if profile is None:
        raise HealthProfileInvariantError("personal profile row is missing")

    facts = list(
        await session.scalars(
            select(HealthFact)
            .where(
                HealthFact.user_id == user_id,
                HealthFact.status == HealthFactStatus.ACTIVE,
            )
            .order_by(HealthFact.created_at, HealthFact.id)
        )
    )
    pending = list(
        await session.scalars(
            select(HealthProfileChange)
            .where(
                HealthProfileChange.user_id == user_id,
                HealthProfileChange.status == ProfileChangeStatus.PENDING,
            )
            .order_by(HealthProfileChange.created_at, HealthProfileChange.id)
        )
    )

    pending_snapshots: list[PendingChangeSnapshot] = []
    for change in pending:
        if change.question is None:
            raise HealthProfileInvariantError("pending profile change has no question")
        pending_snapshots.append(PendingChangeSnapshot.model_validate(change))

    return HealthProfileSnapshot(
        personal_profile=PersonalProfileSnapshot.model_validate(profile),
        health_facts=[HealthFactSnapshot.model_validate(fact) for fact in facts],
        pending_changes=pending_snapshots,
    )


async def apply_health_profile_proposals(
    session: AsyncSession,
    *,
    user_id: UUID,
    trigger_message_id: UUID,
    agent_run_id: UUID,
    proposals: Sequence[ProfileProposal],
    expected_personal_field_revisions: Mapping[str, int] | None = None,
    expected_health_fact_revisions: Mapping[UUID, int] | None = None,
) -> ApplyProfileProposalsResult:
    """验证并保存一批候选，不提交事务。

    调用方可以把这些修改与子 AgentRun 的完成状态放在同一个事务里。
    """

    if len(proposals) > 10:
        raise InvalidProfileProposalError("proposal count cannot exceed 10")

    message = await session.get(Message, trigger_message_id)
    run = await session.get(AgentRun, agent_run_id)
    if (
        message is None
        or message.user_id != user_id
        or message.role is not MessageRole.USER
        or run is None
        or run.user_id != user_id
        or run.trigger_message_id != trigger_message_id
    ):
        raise InvalidProfileProposalError("proposal source does not match current run")

    if not proposals:
        return ApplyProfileProposalsResult(changes=[])

    profile = await session.scalar(
        select(PersonalProfile)
        .where(PersonalProfile.user_id == user_id)
        .with_for_update()
    )
    if profile is None:
        raise HealthProfileInvariantError("personal profile row is missing")

    _validate_unique_targets(proposals)
    target_facts = await _load_target_facts_for_update(session, user_id, proposals)
    resolved_clarifications = await _load_resolved_clarifications(
        session, user_id, proposals
    )

    if expected_personal_field_revisions is not None:
        for proposal in proposals:
            if not isinstance(proposal, PersonalProfileProposal):
                continue
            field_name = proposal.field_name.value
            if profile.field_revisions.get(
                field_name, 0
            ) != expected_personal_field_revisions.get(field_name, 0):
                raise HealthProfileChangedError
    if expected_health_fact_revisions is not None:
        for proposal in proposals:
            if not isinstance(proposal, HealthFactProposal):
                continue
            if proposal.target_id is None:
                continue
            target = target_facts.get(proposal.target_id)
            if (
                target is None
                or expected_health_fact_revisions.get(proposal.target_id)
                != target.revision
            ):
                raise HealthProfileChangedError

    prepared: list[
        tuple[
            ProfileProposal,
            dict[str, Any] | None,
            dict[str, Any] | None,
            int | None,
            str | None,
            ClarificationReason | None,
        ]
    ] = []
    for source_proposal in proposals:
        if source_proposal.evidence_quote not in message.content:
            raise InvalidProfileProposalError(
                "proposal evidence is not an exact substring of current user message"
            )

        proposal, promoted = _promote_bare_personal_candidate(source_proposal)

        if isinstance(proposal, PersonalProfileProposal):
            if proposal.mode is ProfileChangeMode.CLARIFICATION:
                normalized = None
                proposed = _personal_clarification_value(proposal)
            else:
                normalized = _normalize_personal_proposal(proposal)
                proposed = _personal_proposed_value(proposal, normalized)
            before = _personal_before_value(profile, proposal.field_name)
            expected_revision = profile.field_revisions.get(
                proposal.field_name.value, 0
            )
            question = _build_personal_question(proposal, promoted=promoted)
        else:
            target_fact = (
                target_facts.get(proposal.target_id)
                if proposal.target_id is not None
                else None
            )
            _validate_health_fact_target(proposal, target_fact)
            before = _fact_value(target_fact) if target_fact is not None else None
            expected_revision = (
                target_fact.revision if target_fact is not None else None
            )
            proposed = _health_fact_proposed_value(
                proposal,
                clarification=proposal.mode is ProfileChangeMode.CLARIFICATION,
            )
            question = _build_health_fact_question(proposal, target_fact)

        resolved_change = (
            resolved_clarifications.get(source_proposal.resolves_change_id)
            if source_proposal.resolves_change_id is not None
            else None
        )
        _validate_resolved_clarification(source_proposal, resolved_change)

        prepared.append(
            (
                proposal,
                before,
                proposed,
                expected_revision,
                question,
                source_proposal.clarification_reason,
            )
        )

    now = datetime.now(timezone.utc)
    created_changes: list[HealthProfileChange] = []

    for proposal_index, item in enumerate(prepared):
        (
            proposal,
            before,
            proposed,
            expected_revision,
            question,
            clarification_reason,
        ) = item
        status = (
            ProfileChangeStatus.APPLIED
            if proposal.mode is ProfileChangeMode.DIRECT
            else ProfileChangeStatus.PENDING
        )
        field_name = (
            proposal.field_name.value
            if isinstance(proposal, PersonalProfileProposal)
            else proposal.fact_type.value
        )
        change = HealthProfileChange(
            user_id=user_id,
            trigger_message_id=trigger_message_id,
            agent_run_id=agent_run_id,
            proposal_index=proposal_index,
            origin="agent",
            mode=proposal.mode,
            status=status,
            target_type=proposal.target_type,
            field_name=field_name,
            operation=proposal.operation,
            target_id=(
                proposal.target_id if isinstance(proposal, HealthFactProposal) else None
            ),
            before_value=before,
            proposed_value=proposed,
            expected_revision=expected_revision,
            result_revision=None,
            question=question,
            clarification_reason=(
                clarification_reason.value if clarification_reason is not None else None
            ),
            applied_at=now if status is ProfileChangeStatus.APPLIED else None,
        )
        session.add(change)
        created_changes.append(change)

        if proposal.mode is ProfileChangeMode.DIRECT:
            if isinstance(proposal, PersonalProfileProposal):
                normalized = _normalize_personal_proposal(proposal)
                _set_personal_field(profile, proposal.field_name, normalized, now)
                change.result_revision = profile.field_revisions[
                    proposal.field_name.value
                ]
            else:
                target = (
                    target_facts.get(proposal.target_id)
                    if proposal.target_id is not None
                    else None
                )
                fact = await _apply_health_fact_proposal(
                    session,
                    user_id=profile.user_id,
                    proposal=proposal,
                    target=target,
                    now=now,
                )
                await session.flush()
                change.target_id = fact.id
                change.result_revision = fact.revision

            if isinstance(proposal, PersonalProfileProposal):
                await _supersede_matching_cards(
                    session,
                    user_id=user_id,
                    target_type=ProfileTargetType.PERSONAL_PROFILE,
                    field_name=proposal.field_name.value,
                    target_id=None,
                    now=now,
                )
            elif proposal.target_id is not None:
                await _supersede_matching_cards(
                    session,
                    user_id=user_id,
                    target_type=ProfileTargetType.HEALTH_FACT,
                    field_name=proposal.fact_type.value,
                    target_id=proposal.target_id,
                    now=now,
                )

        if proposal.resolves_change_id is not None:
            resolved = resolved_clarifications[proposal.resolves_change_id]
            resolved.status = ProfileChangeStatus.SUPERSEDED
            resolved.resolved_at = now
            resolved.resolved_by_user_id = user_id

    await session.flush()

    return ApplyProfileProposalsResult(
        changes=[
            AppliedProposalResult(
                change_id=change.id,
                proposal_index=cast(int, change.proposal_index),
                status=change.status,
                target_type=change.target_type,
                field_name=change.field_name,
                operation=change.operation,
            )
            for change in created_changes
        ]
    )


async def apply_manual_health_profile_change(
    session: AsyncSession,
    *,
    user_id: UUID,
    request: HealthProfileChangeRequest,
) -> HealthProfileChange:
    """执行一项用户在档案页明确提交的修改，不提交事务。"""

    action_hash = _action_hash(
        {"kind": "manual", "request": request.model_dump(mode="json")}
    )
    # 同一用户的档案操作按用户行串行，使并发重试能看到首次已写入的幂等记录。
    profile = await session.scalar(
        select(PersonalProfile)
        .where(PersonalProfile.user_id == user_id)
        .with_for_update()
    )
    if profile is None:
        raise HealthProfileInvariantError("personal profile row is missing")
    existing = await session.scalar(
        select(HealthProfileChange).where(
            HealthProfileChange.user_id == user_id,
            HealthProfileChange.decision_client_action_id == request.client_action_id,
        )
    )
    if existing is not None:
        if (
            existing.origin != "user"
            or existing.decision != "manual"
            or existing.client_action_hash != action_hash
        ):
            raise ProfileClientActionConflictError("client_action_id already used")
        return existing

    now = datetime.now(timezone.utc)
    if isinstance(request, PersonalProfileChangeRequest):
        if profile.revision != request.expected_revision:
            raise HealthProfileChangedError

        proposal = _manual_personal_proposal(
            field=request.field_name,
            operation=request.operation,
            value=request.value,
            unit=request.unit,
            default_page_units=True,
        )
        before = _personal_before_value(profile, proposal.field_name)
        normalized = _normalize_personal_proposal(proposal)
        proposed = _personal_proposed_value(proposal, normalized)
        _set_personal_field(profile, proposal.field_name, normalized, now)
        field_name = proposal.field_name.value
        target_id = None
        result_revision = profile.revision
        await _supersede_matching_cards(
            session,
            user_id=user_id,
            target_type=ProfileTargetType.PERSONAL_PROFILE,
            field_name=field_name,
            target_id=None,
            now=now,
        )
    else:
        target = None
        if request.target_id is not None:
            target = await session.scalar(
                select(HealthFact)
                .where(
                    HealthFact.id == request.target_id,
                    HealthFact.user_id == user_id,
                )
                .with_for_update()
            )
            if (
                target is None
                or target.status is not HealthFactStatus.ACTIVE
                or target.fact_type is not request.fact_type
                or target.revision != request.expected_revision
            ):
                raise HealthProfileChangedError

        proposal = HealthFactProposal(
            fact_type=request.fact_type,
            operation=request.operation,
            mode=ProfileChangeMode.DIRECT,
            target_id=request.target_id,
            statement=request.statement,
            assertion=request.assertion,
            temporal_status=request.temporal_status,
            effective_start=None,
            effective_end=None,
            evidence_quote="manual profile edit",
        )
        before = _fact_value(target) if target is not None else None
        proposed = _health_fact_proposed_value(proposal, clarification=False)
        fact = await _apply_health_fact_proposal(
            session,
            user_id=user_id,
            proposal=proposal,
            target=target,
            now=now,
        )
        await session.flush()
        field_name = request.fact_type.value
        target_id = fact.id
        result_revision = fact.revision
        if request.target_id is not None:
            await _supersede_matching_cards(
                session,
                user_id=user_id,
                target_type=ProfileTargetType.HEALTH_FACT,
                field_name=field_name,
                target_id=request.target_id,
                now=now,
            )

    change = HealthProfileChange(
        user_id=user_id,
        trigger_message_id=None,
        agent_run_id=None,
        proposal_index=None,
        origin="user",
        mode=ProfileChangeMode.DIRECT,
        status=ProfileChangeStatus.APPLIED,
        target_type=request.target_type,
        field_name=field_name,
        operation=request.operation,
        target_id=target_id,
        before_value=before,
        proposed_value=proposed,
        expected_revision=request.expected_revision,
        result_revision=result_revision,
        question=None,
        clarification_reason=None,
        decision_client_action_id=request.client_action_id,
        client_action_hash=action_hash,
        decision="manual",
        answer_value=None,
        resolved_by_user_id=user_id,
        applied_at=now,
        resolved_at=now,
    )
    session.add(change)
    await session.flush()
    return change


def profile_card_options(
    card: HealthProfileChange,
) -> tuple[list[ProfileCardOption], bool, str | None]:
    """只用固定规则生成卡片选项，不使用模型文案。"""

    options: list[ProfileCardOption] = []
    if card.mode is ProfileChangeMode.CONFIRMATION:
        options.append(ProfileCardOption(id="accept", label="是，确认写入"))
    elif (
        card.target_type is ProfileTargetType.PERSONAL_PROFILE
        and card.field_name == PersonalProfileField.WEIGHT_KG.value
        and card.clarification_reason == ClarificationReason.MISSING_UNIT.value
        and card.proposed_value is not None
        and card.proposed_value.get("source_value") is not None
    ):
        value = card.proposed_value["source_value"]
        options.extend(
            (
                ProfileCardOption(
                    id="kg",
                    label=f"{value} 公斤",
                    value={"value": value, "unit": "kg"},
                ),
                ProfileCardOption(
                    id="jin",
                    label=f"{value} 斤",
                    value={"value": value, "unit": "jin"},
                ),
            )
        )
    options.append(ProfileCardOption(id="reject", label="暂不写入"))

    allow_custom = card.operation not in {
        ProfileOperation.CLEAR,
        ProfileOperation.RETRACT,
    }
    placeholder = None
    if allow_custom and card.target_type is ProfileTargetType.PERSONAL_PROFILE:
        placeholders = {
            PersonalProfileField.AGE_YEARS.value: "例如：25 岁",
            PersonalProfileField.HEIGHT_CM.value: "例如：1.68 米",
            PersonalProfileField.WEIGHT_KG.value: "例如：65 公斤",
        }
        placeholder = placeholders.get(card.field_name, "请输入正确内容")
    elif allow_custom:
        placeholder = "请补充这条健康情况"
    return options, allow_custom, placeholder


async def answer_profile_card(
    session: AsyncSession,
    *,
    user_id: UUID,
    card_id: UUID,
    request: ProfileCardAnswerRequest,
) -> ProfileChangeStatus:
    custom = (
        request.custom_answer.model_dump(mode="json")
        if request.custom_answer is not None
        else None
    )
    return await _answer_profile_card(
        session,
        user_id=user_id,
        card_id=card_id,
        client_action_id=request.client_action_id,
        option_id=request.option_id,
        custom_answer=request.custom_answer,
        action_hash=_action_hash(
            {
                "kind": "card",
                "card_id": str(card_id),
                "option": request.option_id,
                "custom": custom,
            }
        ),
    )


async def decide_profile_card(
    session: AsyncSession,
    *,
    user_id: UUID,
    card_id: UUID,
    client_action_id: UUID,
    decision: Literal["accept", "reject"],
) -> ProfileChangeStatus:
    """确认或拒绝卡片，不提交事务；重复的同一决定返回原结果。"""

    return await _answer_profile_card(
        session,
        user_id=user_id,
        card_id=card_id,
        client_action_id=client_action_id,
        option_id=decision,
        custom_answer=None,
        action_hash=_action_hash(
            {
                "kind": "card",
                "card_id": str(card_id),
                "option": decision,
                "custom": None,
            }
        ),
    )


async def _answer_profile_card(
    session: AsyncSession,
    *,
    user_id: UUID,
    card_id: UUID,
    client_action_id: UUID,
    option_id: Literal["accept", "reject", "kg", "jin"] | None,
    custom_answer: PersonalProfileCardCustomAnswer | HealthFactCardCustomAnswer | None,
    action_hash: str,
) -> ProfileChangeStatus:
    profile_lock = await session.scalar(
        select(PersonalProfile)
        .where(PersonalProfile.user_id == user_id)
        .with_for_update()
    )
    if profile_lock is None:
        raise HealthProfileInvariantError("personal profile row is missing")

    existing_action = await session.scalar(
        select(HealthProfileChange).where(
            HealthProfileChange.user_id == user_id,
            HealthProfileChange.decision_client_action_id == client_action_id,
        )
    )
    if existing_action is not None:
        expected_decision = option_id or "custom"
        if (
            existing_action.id != card_id
            or (
                existing_action.client_action_hash is not None
                and existing_action.client_action_hash != action_hash
            )
            or (
                existing_action.client_action_hash is None
                and existing_action.decision != expected_decision
            )
        ):
            raise ProfileCardDecisionConflictError("client_action_id already used")
        return existing_action.status

    card = await session.scalar(
        select(HealthProfileChange)
        .where(
            HealthProfileChange.id == card_id,
            HealthProfileChange.user_id == user_id,
        )
        .with_for_update()
    )
    if card is None:
        raise ProfileCardNotFoundError
    decision = option_id or "custom"
    if card.status is not ProfileChangeStatus.PENDING:
        if card.decision == decision and (
            card.client_action_hash is None or card.client_action_hash == action_hash
        ):
            return card.status
        raise ProfileCardDecisionConflictError("card is already resolved")
    if card.mode is ProfileChangeMode.DIRECT:
        raise HealthProfileInvariantError("direct change cannot be pending")

    options, allow_custom, _ = profile_card_options(card)
    allowed_options = {option.id for option in options}
    if option_id is not None and option_id not in allowed_options:
        raise InvalidProfileCardDecisionError("option is not allowed for this card")
    if custom_answer is not None and not allow_custom:
        raise InvalidProfileCardDecisionError("custom answer is not allowed")

    now = datetime.now(timezone.utc)
    card.decision_client_action_id = client_action_id
    card.client_action_hash = action_hash
    card.decision = decision
    card.answer_value = (
        custom_answer.model_dump(mode="json")
        if custom_answer is not None
        else next(
            (option.value for option in options if option.id == option_id),
            None,
        )
    )
    card.resolved_by_user_id = user_id
    card.resolved_at = now

    if decision == "reject":
        card.status = ProfileChangeStatus.REJECTED
        await session.flush()
        return card.status

    if option_id == "accept":
        applied = await _apply_pending_card(session, card, now)
        card.answer_value = card.proposed_value
    elif card.target_type is ProfileTargetType.PERSONAL_PROFILE:
        applied = await _apply_personal_card_answer(
            session,
            card=card,
            option_id=option_id,
            custom_answer=custom_answer,
            now=now,
        )
    else:
        applied = await _apply_health_fact_card_answer(
            session,
            card=card,
            custom_answer=custom_answer,
            now=now,
        )
    card.status = (
        ProfileChangeStatus.APPLIED if applied else ProfileChangeStatus.CONFLICTED
    )
    card.applied_at = now if applied else None
    if applied:
        await _supersede_matching_cards(
            session,
            user_id=user_id,
            target_type=card.target_type,
            field_name=card.field_name,
            target_id=card.target_id,
            now=now,
            exclude_id=card.id,
        )
    await session.flush()
    return card.status


async def _apply_personal_card_answer(
    session: AsyncSession,
    *,
    card: HealthProfileChange,
    option_id: Literal["accept", "reject", "kg", "jin"] | None,
    custom_answer: PersonalProfileCardCustomAnswer | HealthFactCardCustomAnswer | None,
    now: datetime,
) -> bool:
    if card.operation is not ProfileOperation.SET:
        raise InvalidProfileCardDecisionError("card does not accept a replacement")
    if custom_answer is not None and not isinstance(
        custom_answer, PersonalProfileCardCustomAnswer
    ):
        raise InvalidProfileCardDecisionError("custom answer type does not match card")

    profile = await session.scalar(
        select(PersonalProfile)
        .where(PersonalProfile.user_id == card.user_id)
        .with_for_update()
    )
    if profile is None:
        raise HealthProfileInvariantError("personal profile row is missing")
    if profile.field_revisions.get(card.field_name, 0) != card.expected_revision:
        return False

    field = PersonalProfileField(card.field_name)
    if option_id in {"kg", "jin"}:
        if card.proposed_value is None:
            raise HealthProfileInvariantError("unit card lost its partial value")
        value = card.proposed_value.get("source_value")
        unit = (
            PersonalProfileUnit.KILOGRAMS
            if option_id == "kg"
            else PersonalProfileUnit.JIN
        )
    elif isinstance(custom_answer, PersonalProfileCardCustomAnswer):
        value = custom_answer.value
        unit = custom_answer.unit
        if unit is None and card.proposed_value is not None:
            saved_unit = card.proposed_value.get("source_unit")
            if isinstance(saved_unit, str):
                unit = PersonalProfileUnit(saved_unit)
    else:
        raise InvalidProfileCardDecisionError("card answer is incomplete")

    proposal = _manual_personal_proposal(
        field=field,
        operation=ProfileOperation.SET,
        value=value,
        unit=unit,
        default_page_units=False,
    )
    normalized = _normalize_personal_proposal(proposal)
    _set_personal_field(profile, field, normalized, now)
    card.answer_value = _personal_proposed_value(proposal, normalized)
    card.result_revision = profile.field_revisions[field.value]
    return True


async def _apply_health_fact_card_answer(
    session: AsyncSession,
    *,
    card: HealthProfileChange,
    custom_answer: PersonalProfileCardCustomAnswer | HealthFactCardCustomAnswer | None,
    now: datetime,
) -> bool:
    if not isinstance(custom_answer, HealthFactCardCustomAnswer):
        raise InvalidProfileCardDecisionError("custom answer type does not match card")
    if card.operation is ProfileOperation.RETRACT:
        raise InvalidProfileCardDecisionError("retract card has no custom answer")

    target = None
    if card.target_id is not None:
        target = await session.scalar(
            select(HealthFact)
            .where(
                HealthFact.id == card.target_id,
                HealthFact.user_id == card.user_id,
            )
            .with_for_update()
        )
        if (
            target is None
            or target.status is not HealthFactStatus.ACTIVE
            or target.revision != card.expected_revision
        ):
            return False

    proposal = HealthFactProposal(
        fact_type=HealthFactType(card.field_name),
        operation=cast(
            Literal[ProfileOperation.ADD, ProfileOperation.UPDATE],
            card.operation,
        ),
        mode=ProfileChangeMode.DIRECT,
        target_id=card.target_id,
        statement=custom_answer.statement,
        assertion=custom_answer.assertion,
        temporal_status=custom_answer.temporal_status,
        effective_start=None,
        effective_end=None,
        evidence_quote="profile card answer",
    )
    fact = await _apply_health_fact_proposal(
        session,
        user_id=card.user_id,
        proposal=proposal,
        target=target,
        now=now,
    )
    await session.flush()
    card.target_id = fact.id
    card.result_revision = fact.revision
    card.answer_value = _health_fact_proposed_value(proposal, clarification=False)
    return True


async def _supersede_matching_cards(
    session: AsyncSession,
    *,
    user_id: UUID,
    target_type: ProfileTargetType,
    field_name: str,
    target_id: UUID | None,
    now: datetime,
    exclude_id: UUID | None = None,
) -> None:
    statement = select(HealthProfileChange).where(
        HealthProfileChange.user_id == user_id,
        HealthProfileChange.status == ProfileChangeStatus.PENDING,
        HealthProfileChange.target_type == target_type,
        HealthProfileChange.field_name == field_name,
    )
    if target_type is ProfileTargetType.HEALTH_FACT:
        if target_id is None:
            return
        statement = statement.where(HealthProfileChange.target_id == target_id)
    if exclude_id is not None:
        statement = statement.where(HealthProfileChange.id != exclude_id)
    cards = list(await session.scalars(statement.with_for_update()))
    for pending in cards:
        pending.status = ProfileChangeStatus.SUPERSEDED
        pending.resolved_at = now
        pending.resolved_by_user_id = user_id


def _manual_personal_proposal(
    *,
    field: PersonalProfileField,
    operation: Literal[ProfileOperation.SET, ProfileOperation.CLEAR],
    value: object,
    unit: PersonalProfileUnit | None,
    default_page_units: bool,
) -> PersonalProfileProposal:
    if operation is ProfileOperation.CLEAR:
        normalized_value: str | int | Decimal | None = None
        unit = None
    elif field in _TEXT_FIELD_LIMITS:
        if not isinstance(value, str):
            raise InvalidProfileProposalError("text field requires string")
        normalized_value = value
    elif field is PersonalProfileField.AGE_YEARS:
        try:
            number = Decimal(str(value))
        except (ArithmeticError, ValueError) as error:
            raise InvalidProfileProposalError("age requires a number") from error
        if number != number.to_integral_value():
            raise InvalidProfileProposalError("age requires whole years")
        normalized_value = int(number)
        unit = unit or PersonalProfileUnit.YEARS
    else:
        try:
            normalized_value = Decimal(str(value))
        except (ArithmeticError, ValueError) as error:
            raise InvalidProfileProposalError(
                "measurement requires a number"
            ) from error
        if unit is None and default_page_units:
            unit = (
                PersonalProfileUnit.CENTIMETERS
                if field is PersonalProfileField.HEIGHT_CM
                else PersonalProfileUnit.KILOGRAMS
            )

    try:
        return PersonalProfileProposal(
            field_name=field,
            operation=operation,
            mode=ProfileChangeMode.DIRECT,
            value=normalized_value,
            unit=unit,
            evidence_quote="manual profile input",
        )
    except ValidationError as error:
        raise InvalidProfileProposalError("invalid personal profile value") from error


def _action_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode()).hexdigest()


def _validate_unique_targets(proposals: Sequence[ProfileProposal]) -> None:
    targets: set[tuple[str, str]] = set()
    for proposal in proposals:
        if isinstance(proposal, PersonalProfileProposal):
            key = (proposal.target_type.value, proposal.field_name.value)
        elif proposal.target_id is not None:
            key = (proposal.target_type.value, str(proposal.target_id))
        else:
            # 多条新增事实是合法的；proposal 自身的序号会区分它们。
            continue
        if key in targets:
            raise InvalidProfileProposalError(
                "one batch cannot change one target twice"
            )
        targets.add(key)

    resolved_ids = [
        proposal.resolves_change_id
        for proposal in proposals
        if proposal.resolves_change_id is not None
    ]
    if len(resolved_ids) != len(set(resolved_ids)):
        raise InvalidProfileProposalError(
            "one pending clarification cannot be resolved twice"
        )


async def _load_resolved_clarifications(
    session: AsyncSession,
    user_id: UUID,
    proposals: Sequence[ProfileProposal],
) -> dict[UUID, HealthProfileChange]:
    ids = {
        proposal.resolves_change_id
        for proposal in proposals
        if proposal.resolves_change_id is not None
    }
    if not ids:
        return {}
    changes = list(
        await session.scalars(
            select(HealthProfileChange)
            .where(
                HealthProfileChange.user_id == user_id,
                HealthProfileChange.id.in_(ids),
            )
            .with_for_update()
        )
    )
    return {change.id: change for change in changes}


def _validate_resolved_clarification(
    proposal: ProfileProposal,
    change: HealthProfileChange | None,
) -> None:
    if proposal.resolves_change_id is None:
        return
    if (
        change is None
        or change.status is not ProfileChangeStatus.PENDING
        or change.mode is not ProfileChangeMode.CLARIFICATION
    ):
        raise InvalidProfileProposalError(
            "resolves_change_id is not a pending clarification"
        )
    if proposal.mode is ProfileChangeMode.CLARIFICATION:
        raise InvalidProfileProposalError(
            "a clarification cannot resolve another clarification"
        )
    field_name = (
        proposal.field_name.value
        if isinstance(proposal, PersonalProfileProposal)
        else proposal.fact_type.value
    )
    if (
        change.target_type is not proposal.target_type
        or change.field_name != field_name
    ):
        raise InvalidProfileProposalError(
            "resolved clarification target does not match proposal"
        )
    if change.operation is not proposal.operation:
        raise InvalidProfileProposalError(
            "resolved clarification operation does not match proposal"
        )
    if (
        isinstance(proposal, PersonalProfileProposal)
        and proposal.operation is ProfileOperation.SET
        and change.proposed_value is not None
    ):
        saved_value = change.proposed_value.get("source_value")
        saved_value_changed = (
            saved_value is not None
            and saved_value != to_jsonable_python(proposal.value)
        )
        saved_unit = change.proposed_value.get("source_unit")
        proposal_unit = proposal.unit.value if proposal.unit is not None else None
        saved_unit_changed = saved_unit is not None and saved_unit != proposal_unit
        replacement_is_explicit = _personal_replacement_is_explicit(proposal)
        if saved_value_changed and not replacement_is_explicit:
            raise InvalidProfileProposalError(
                "resolved clarification does not preserve its saved partial value"
            )
        if saved_unit_changed and not replacement_is_explicit:
            raise InvalidProfileProposalError(
                "resolved clarification does not preserve its saved partial unit"
            )
    if isinstance(proposal, HealthFactProposal):
        if change.target_id is not None and change.target_id != proposal.target_id:
            raise InvalidProfileProposalError(
                "resolved clarification health fact does not match proposal"
            )
        if change.proposed_value is not None:
            resolved_value = _health_fact_proposed_value(
                proposal,
                clarification=True,
            )
            replacement_is_explicit = (
                proposal.statement is not None
                and proposal.statement.casefold() in proposal.evidence_quote.casefold()
            )
            for key, saved_value in change.proposed_value.items():
                if (
                    saved_value is not None
                    and (
                        resolved_value is None or resolved_value.get(key) != saved_value
                    )
                    and not replacement_is_explicit
                ):
                    raise InvalidProfileProposalError(
                        "resolved clarification does not preserve its saved health fact"
                    )


def _personal_replacement_is_explicit(
    proposal: PersonalProfileProposal,
) -> bool:
    """完整的新值出现在本轮原文时，允许用户纠正上一轮的局部信息。"""

    if proposal.value is None:
        return False
    if proposal.field_name not in {
        PersonalProfileField.AGE_YEARS,
        PersonalProfileField.HEIGHT_CM,
        PersonalProfileField.WEIGHT_KG,
    }:
        return isinstance(proposal.value, str) and (
            proposal.value.casefold() in proposal.evidence_quote.casefold()
        )
    if proposal.unit is None:
        return False

    expected_value = Decimal(str(proposal.value))
    evidence = proposal.evidence_quote
    units = (proposal.unit.value, _UNIT_LABELS[proposal.unit])
    if proposal.unit is PersonalProfileUnit.KILOGRAMS:
        units += ("千克",)
    for unit in units:
        ascii_boundary = r"(?![A-Za-z])" if unit.isascii() else ""
        pattern = rf"(?<![\d.])([+-]?\d+(?:\.\d+)?)\s*{re.escape(unit)}{ascii_boundary}"
        for match in re.finditer(pattern, evidence, flags=re.IGNORECASE):
            if Decimal(match.group(1)) == expected_value:
                return True
    return False


async def _load_target_facts_for_update(
    session: AsyncSession,
    user_id: UUID,
    proposals: Sequence[ProfileProposal],
) -> dict[UUID, HealthFact]:
    ids = {
        proposal.target_id
        for proposal in proposals
        if isinstance(proposal, HealthFactProposal) and proposal.target_id is not None
    }
    if not ids:
        return {}
    facts = list(
        await session.scalars(
            select(HealthFact)
            .where(HealthFact.user_id == user_id, HealthFact.id.in_(ids))
            .with_for_update()
        )
    )
    return {fact.id: fact for fact in facts}


def _validate_health_fact_target(
    proposal: HealthFactProposal,
    target_fact: HealthFact | None,
) -> None:
    if proposal.target_id is None:
        return
    if target_fact is None:
        raise InvalidProfileProposalError("target health fact does not exist")
    if target_fact.fact_type is not proposal.fact_type:
        raise InvalidProfileProposalError("target health fact type does not match")
    if target_fact.status is not HealthFactStatus.ACTIVE:
        raise InvalidProfileProposalError("target health fact is not active")


def _promote_bare_personal_candidate(
    proposal: ProfileProposal,
) -> tuple[ProfileProposal, bool]:
    """对可以安全补全单位的裸数字只生成确认卡，不直接写入。"""

    if (
        not isinstance(proposal, PersonalProfileProposal)
        or proposal.mode is not ProfileChangeMode.CLARIFICATION
        or proposal.clarification_reason is not ClarificationReason.MISSING_UNIT
        or proposal.operation is not ProfileOperation.SET
        or proposal.unit is not None
    ):
        return proposal, False

    unit: PersonalProfileUnit | None = None
    value = proposal.value
    if proposal.field_name is PersonalProfileField.AGE_YEARS:
        if type(value) is int and 0 <= value <= 150:
            unit = PersonalProfileUnit.YEARS
    elif proposal.field_name is PersonalProfileField.HEIGHT_CM:
        if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
            number = Decimal(value)
            if Decimal("100") <= number <= Decimal("250"):
                unit = PersonalProfileUnit.CENTIMETERS
            elif Decimal("1") <= number <= Decimal("2.5"):
                unit = PersonalProfileUnit.METERS

    if unit is None:
        return proposal, False

    return (
        PersonalProfileProposal(
            field_name=proposal.field_name,
            operation=proposal.operation,
            mode=ProfileChangeMode.CONFIRMATION,
            value=value,
            unit=unit,
            evidence_quote=proposal.evidence_quote,
            resolves_change_id=proposal.resolves_change_id,
        ),
        True,
    )


def _normalize_personal_proposal(
    proposal: PersonalProfileProposal,
) -> str | int | Decimal | None:
    if proposal.operation is ProfileOperation.CLEAR:
        return None
    value = proposal.value
    field = proposal.field_name

    if field in _TEXT_FIELD_LIMITS:
        if not isinstance(value, str):
            raise InvalidProfileProposalError("text field requires string")
        normalized = value.strip()
        if not normalized or len(normalized) > _TEXT_FIELD_LIMITS[field]:
            raise InvalidProfileProposalError("text field length is invalid")
        return normalized

    if field is PersonalProfileField.AGE_YEARS:
        if type(value) is not int or proposal.unit is not PersonalProfileUnit.YEARS:
            raise InvalidProfileProposalError("age requires integer years")
        if value < 0 or value > 150:
            raise InvalidProfileProposalError("age is outside supported database range")
        return value

    if not isinstance(value, (int, Decimal)) or isinstance(value, bool):
        raise InvalidProfileProposalError("measurement requires numeric value")
    number = Decimal(value)
    if number <= 0:
        raise InvalidProfileProposalError("measurement must be positive")

    if field is PersonalProfileField.HEIGHT_CM:
        if proposal.unit is PersonalProfileUnit.METERS:
            number *= Decimal(100)
        elif proposal.unit is not PersonalProfileUnit.CENTIMETERS:
            raise InvalidProfileProposalError("unsupported height unit")
        if number > Decimal("999.99"):
            raise InvalidProfileProposalError("height exceeds database range")
    elif field is PersonalProfileField.WEIGHT_KG:
        if proposal.unit is PersonalProfileUnit.JIN:
            number *= Decimal("0.5")
        elif proposal.unit is PersonalProfileUnit.POUNDS:
            number *= Decimal("0.45359237")
        elif proposal.unit is not PersonalProfileUnit.KILOGRAMS:
            raise InvalidProfileProposalError("unsupported weight unit")
        if number > Decimal("9999.99"):
            raise InvalidProfileProposalError("weight exceeds database range")
    else:
        raise InvalidProfileProposalError("unsupported personal profile field")

    return number.quantize(_TWO_DECIMALS, rounding=ROUND_HALF_UP)


def _personal_before_value(
    profile: PersonalProfile,
    field: PersonalProfileField,
) -> dict[str, Any]:
    value = getattr(profile, field.value)
    result: dict[str, Any] = {"value": to_jsonable_python(value)}
    if field is PersonalProfileField.AGE_YEARS:
        result["age_as_of_date"] = to_jsonable_python(profile.age_as_of_date)
    return result


def _personal_proposed_value(
    proposal: PersonalProfileProposal,
    normalized: str | int | Decimal | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "value": to_jsonable_python(normalized),
        "canonical_unit": _CANONICAL_UNITS.get(proposal.field_name),
        "source_value": to_jsonable_python(proposal.value),
        "source_unit": proposal.unit.value if proposal.unit is not None else None,
    }
    if proposal.field_name is PersonalProfileField.AGE_YEARS:
        result["age_as_of_date"] = datetime.now(timezone.utc).date().isoformat()
    return result


def _personal_clarification_value(
    proposal: PersonalProfileProposal,
) -> dict[str, Any] | None:
    """保存用户已经给出的局部值，但不生成任何可执行的规范化值。"""

    if proposal.value is None and proposal.unit is None:
        return None
    return {
        "source_value": to_jsonable_python(proposal.value),
        "source_unit": proposal.unit.value if proposal.unit is not None else None,
    }


def _fact_value(fact: HealthFact) -> dict[str, Any]:
    return {
        "fact_type": fact.fact_type.value,
        "statement": fact.statement,
        "assertion": fact.assertion.value,
        "temporal_status": fact.temporal_status.value,
        "effective_start": to_jsonable_python(fact.effective_start),
        "effective_end": to_jsonable_python(fact.effective_end),
    }


def _health_fact_proposed_value(
    proposal: HealthFactProposal,
    *,
    clarification: bool,
) -> dict[str, Any] | None:
    if not clarification and proposal.operation is ProfileOperation.RETRACT:
        return {"target_id": str(proposal.target_id)}
    values = {
        "statement": proposal.statement,
        "assertion": proposal.assertion.value if proposal.assertion else None,
        "temporal_status": (
            proposal.temporal_status.value if proposal.temporal_status else None
        ),
        "effective_start": to_jsonable_python(proposal.effective_start),
        "effective_end": to_jsonable_python(proposal.effective_end),
    }
    if clarification:
        return values if any(value is not None for value in values.values()) else None
    return {"fact_type": proposal.fact_type.value, **values}


def _build_personal_question(
    proposal: PersonalProfileProposal,
    *,
    promoted: bool = False,
) -> str | None:
    if proposal.mode is ProfileChangeMode.DIRECT:
        return None
    label = _PERSONAL_FIELD_LABELS[proposal.field_name]
    if promoted:
        if proposal.unit is PersonalProfileUnit.CENTIMETERS:
            return f"你是说你的{label}是 {proposal.value} cm 吗？"
        elif proposal.unit is None:
            raise HealthProfileInvariantError("promoted card has no unit")
        return f"你是说你的{label}是 {proposal.value} {_UNIT_LABELS[proposal.unit]}吗？"
    if proposal.mode is ProfileChangeMode.CLARIFICATION:
        if proposal.clarification_reason is ClarificationReason.MISSING_UNIT:
            if (
                proposal.field_name is PersonalProfileField.WEIGHT_KG
                and proposal.value is not None
            ):
                return f"你说的体重 {proposal.value} 是公斤还是斤？"
            return f"请选择{label}的单位。"
        return f"请补充{label}的具体内容。"
    if proposal.operation is ProfileOperation.CLEAR:
        return f"确认清除档案中的{label}吗？"
    unit = _UNIT_LABELS.get(proposal.unit) if proposal.unit is not None else ""
    return f"确认把{label}更新为{proposal.value}{unit}吗？"


def _build_health_fact_question(
    proposal: HealthFactProposal,
    target_fact: HealthFact | None,
) -> str | None:
    if proposal.mode is ProfileChangeMode.DIRECT:
        return None
    label = _HEALTH_FACT_LABELS[proposal.fact_type]
    if proposal.mode is ProfileChangeMode.CLARIFICATION:
        if proposal.clarification_reason is ClarificationReason.UNCERTAIN_FACT:
            return f"请确认这条{label}是否确实发生过。"
        return f"请补充这条{label}的必要信息。"
    if proposal.operation is ProfileOperation.RETRACT:
        if target_fact is None:
            raise InvalidProfileProposalError("retract target is missing")
        return f"确认撤回这条{label}：{target_fact.statement}？"
    return f"确认将这条{label}写入健康档案：{proposal.statement}？"


def _set_personal_field(
    profile: PersonalProfile,
    field: PersonalProfileField,
    value: str | int | Decimal | None,
    now: datetime,
) -> None:
    setattr(profile, field.value, value)
    if field is PersonalProfileField.AGE_YEARS:
        profile.age_as_of_date = now.date() if value is not None else None
    revisions = dict(profile.field_revisions)
    revisions[field.value] = revisions.get(field.value, 0) + 1
    profile.field_revisions = revisions
    profile.revision += 1
    profile.updated_at = now


async def _apply_health_fact_proposal(
    session: AsyncSession,
    *,
    user_id: UUID,
    proposal: HealthFactProposal,
    target: HealthFact | None,
    now: datetime,
) -> HealthFact:
    if proposal.operation is ProfileOperation.ADD:
        if (
            proposal.statement is None
            or proposal.assertion is None
            or proposal.temporal_status is None
        ):
            raise HealthProfileInvariantError("complete add proposal lost its value")
        fact = HealthFact(
            user_id=user_id,
            fact_type=proposal.fact_type,
            statement=proposal.statement,
            assertion=proposal.assertion,
            temporal_status=proposal.temporal_status,
            effective_start=proposal.effective_start,
            effective_end=proposal.effective_end,
            status=HealthFactStatus.ACTIVE,
            revision=1,
            updated_at=now,
        )
        session.add(fact)
        return fact

    if target is None:
        raise HealthProfileInvariantError("validated fact target disappeared")
    if proposal.operation is ProfileOperation.RETRACT:
        target.status = HealthFactStatus.RETRACTED
        target.retracted_at = now
    else:
        if (
            proposal.statement is None
            or proposal.assertion is None
            or proposal.temporal_status is None
        ):
            raise HealthProfileInvariantError("complete update proposal lost its value")
        target.statement = proposal.statement
        target.assertion = proposal.assertion
        target.temporal_status = proposal.temporal_status
        target.effective_start = proposal.effective_start
        target.effective_end = proposal.effective_end
    target.revision += 1
    target.updated_at = now
    return target


async def _apply_pending_card(
    session: AsyncSession,
    card: HealthProfileChange,
    now: datetime,
) -> bool:
    proposed = card.proposed_value
    if proposed is None:
        raise InvalidProfileCardDecisionError("card has no executable candidate")

    if card.target_type is ProfileTargetType.PERSONAL_PROFILE:
        profile = await session.scalar(
            select(PersonalProfile)
            .where(PersonalProfile.user_id == card.user_id)
            .with_for_update()
        )
        if profile is None:
            raise HealthProfileInvariantError("personal profile row is missing")
        current_revision = profile.field_revisions.get(card.field_name, 0)
        if current_revision != card.expected_revision:
            return False
        field = PersonalProfileField(card.field_name)
        value = _restore_personal_value(field, proposed.get("value"))
        _set_personal_field(profile, field, value, now)
        card.result_revision = profile.field_revisions[field.value]
    else:
        fact = None
        if card.target_id is not None:
            fact = await session.scalar(
                select(HealthFact)
                .where(
                    HealthFact.id == card.target_id,
                    HealthFact.user_id == card.user_id,
                )
                .with_for_update()
            )
            if (
                fact is None
                or fact.status is not HealthFactStatus.ACTIVE
                or fact.revision != card.expected_revision
            ):
                return False
        fact = await _apply_stored_health_fact_change(session, card, fact, now)
        await session.flush()
        card.target_id = fact.id
        card.result_revision = fact.revision

    return True


def _restore_personal_value(
    field: PersonalProfileField,
    raw_value: object,
) -> str | int | Decimal | None:
    if raw_value is None:
        return None
    if field is PersonalProfileField.AGE_YEARS:
        if type(raw_value) is not int:
            raise HealthProfileInvariantError("stored age candidate is invalid")
        return raw_value
    if field in {PersonalProfileField.HEIGHT_CM, PersonalProfileField.WEIGHT_KG}:
        if not isinstance(raw_value, (int, float, str)) or isinstance(raw_value, bool):
            raise HealthProfileInvariantError("stored measurement candidate is invalid")
        return Decimal(str(raw_value))
    if not isinstance(raw_value, str):
        raise HealthProfileInvariantError("stored text candidate is invalid")
    return raw_value


async def _apply_stored_health_fact_change(
    session: AsyncSession,
    card: HealthProfileChange,
    target: HealthFact | None,
    now: datetime,
) -> HealthFact:
    proposed = card.proposed_value
    if proposed is None:
        raise HealthProfileInvariantError("stored health fact candidate is missing")
    if card.operation is ProfileOperation.RETRACT:
        if target is None:
            raise HealthProfileInvariantError("stored retract target is missing")
        target.status = HealthFactStatus.RETRACTED
        target.retracted_at = now
        target.revision += 1
        target.updated_at = now
        return target

    try:
        fact_type = HealthFactType(str(proposed["fact_type"]))
        statement = proposed["statement"]
        assertion = FactAssertion(str(proposed["assertion"]))
        temporal_status = FactTemporalStatus(str(proposed["temporal_status"]))
        effective_start = _restore_date(proposed.get("effective_start"))
        effective_end = _restore_date(proposed.get("effective_end"))
    except (KeyError, TypeError, ValueError) as error:
        raise HealthProfileInvariantError(
            "stored health fact candidate is invalid"
        ) from error
    if not isinstance(statement, str) or not statement.strip():
        raise HealthProfileInvariantError("stored health fact statement is invalid")

    if card.operation is ProfileOperation.ADD:
        fact = HealthFact(
            user_id=card.user_id,
            fact_type=fact_type,
            statement=statement,
            assertion=assertion,
            temporal_status=temporal_status,
            effective_start=effective_start,
            effective_end=effective_end,
            status=HealthFactStatus.ACTIVE,
            revision=1,
            updated_at=now,
        )
        session.add(fact)
        return fact
    if target is None:
        raise HealthProfileInvariantError("stored update target is missing")
    target.statement = statement
    target.assertion = assertion
    target.temporal_status = temporal_status
    target.effective_start = effective_start
    target.effective_end = effective_end
    target.revision += 1
    target.updated_at = now
    return target


def _restore_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("date must be an ISO string")
    return datetime.fromisoformat(value).date()
