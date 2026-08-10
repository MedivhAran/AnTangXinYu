import asyncio
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import session_factory
from antang_api.health_profile.errors import (
    InvalidProfileCardDecisionError,
    InvalidProfileProposalError,
    ProfileCardDecisionConflictError,
)
from antang_api.health_profile.service import (
    answer_profile_card,
    apply_health_profile_proposals,
    apply_manual_health_profile_change,
    decide_profile_card,
    load_health_profile_snapshot,
    profile_card_options,
)
from antang_api.health_profile.types import (
    ClarificationReason,
    HealthFactProposal,
    PersonalProfileProposal,
    PersonalProfileUnit,
)
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    FactAssertion,
    FactTemporalStatus,
    HealthFact,
    HealthFactStatus,
    HealthFactType,
    HealthProfileChange,
    Message,
    MessageRole,
    MessageStatus,
    PersonalProfile,
    PersonalProfileField,
    ProfileChangeMode,
    ProfileChangeStatus,
    ProfileOperation,
    ProfileTargetType,
    User,
)
from antang_api.schemas.health_profile import (
    HealthFactChangeRequest,
    PersonalProfileCardCustomAnswer,
    PersonalProfileChangeRequest,
    ProfileCardAnswerRequest,
)


async def _create_user(session: AsyncSession) -> User:
    suffix = uuid4().hex[:16]
    user = User(
        username=f"profile_{suffix}",
        username_normalized=f"profile_{suffix}",
        password_hash="test-only",
    )
    session.add(user)
    await session.flush()
    session.add(PersonalProfile(user_id=user.id))
    await session.flush()
    return user


async def _create_run(
    session: AsyncSession,
    *,
    user: User,
    content: str,
    run_number: int,
) -> tuple[Message, AgentRun]:
    message = Message(
        client_message_id=uuid4(),
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content=content,
        sources=[],
    )
    session.add(message)
    await session.flush()
    run = AgentRun(
        user_id=user.id,
        trigger_message_id=message.id,
        result_message_id=None,
        parent_run_id=None,
        parent_tool_call_id=None,
        agent_name=f"test_health_profile_{run_number}",
        model="test-model",
        status=AgentRunStatus.COMPLETED,
    )
    session.add(run)
    await session.flush()
    return message, run


async def _single_change(
    session: AsyncSession,
    result_change_id: UUID,
) -> HealthProfileChange:
    change = await session.get(HealthProfileChange, result_change_id)
    assert change is not None
    return change


async def test_direct_personal_update_and_fixed_template_card(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    message, run = await _create_run(
        db_session,
        user=user,
        content="我现在130斤，职业应该算学生，身高大概一米多",
        run_number=1,
    )
    result = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=message.id,
        agent_run_id=run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.DIRECT,
                value=130,
                unit=PersonalProfileUnit.JIN,
                evidence_quote="130斤",
            ),
            PersonalProfileProposal(
                field_name=PersonalProfileField.OCCUPATION,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CONFIRMATION,
                value="学生",
                unit=None,
                evidence_quote="职业应该算学生",
            ),
            PersonalProfileProposal(
                field_name=PersonalProfileField.HEIGHT_CM,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CLARIFICATION,
                value=None,
                unit=None,
                evidence_quote="身高大概一米多",
                clarification_reason=ClarificationReason.MISSING_VALUE,
            ),
        ],
    )

    assert [item.status for item in result.changes] == [
        ProfileChangeStatus.APPLIED,
        ProfileChangeStatus.PENDING,
        ProfileChangeStatus.PENDING,
    ]
    profile = await db_session.get(PersonalProfile, user.id)
    assert profile is not None
    assert profile.weight_kg == Decimal("65.00")
    assert profile.occupation is None

    occupation = await _single_change(db_session, result.changes[1].change_id)
    clarification = await _single_change(db_session, result.changes[2].change_id)
    assert occupation.question == "确认把职业更新为学生吗？"
    assert clarification.question == "请补充身高的具体内容。"
    assert clarification.proposed_value is None


async def test_clarification_preserves_partial_value_without_applying(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    message, run = await _create_run(
        db_session,
        user=user,
        content="我的体重是130",
        run_number=1,
    )

    result = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=message.id,
        agent_run_id=run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CLARIFICATION,
                value=130,
                unit=None,
                evidence_quote="体重是130",
                clarification_reason=ClarificationReason.MISSING_UNIT,
            )
        ],
    )

    profile = await db_session.get(PersonalProfile, user.id)
    change = await _single_change(db_session, result.changes[0].change_id)
    assert profile is not None
    assert profile.weight_kg is None
    assert profile.revision == 0
    assert change.status is ProfileChangeStatus.PENDING
    assert change.proposed_value == {
        "source_value": 130,
        "source_unit": None,
    }
    assert change.question == "你说的体重 130 是公斤还是斤？"


async def test_missing_unit_reply_applies_saved_weight_value(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    first_message, first_run = await _create_run(
        db_session,
        user=user,
        content="我的体重是130",
        run_number=1,
    )
    first = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=first_message.id,
        agent_run_id=first_run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CLARIFICATION,
                value=130,
                unit=None,
                evidence_quote="体重是130",
                clarification_reason=ClarificationReason.MISSING_UNIT,
            )
        ],
    )
    clarification_id = first.changes[0].change_id

    second_message, second_run = await _create_run(
        db_session,
        user=user,
        content="斤",
        run_number=2,
    )
    await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=second_message.id,
        agent_run_id=second_run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.DIRECT,
                value=130,
                unit=PersonalProfileUnit.JIN,
                evidence_quote="斤",
                resolves_change_id=clarification_id,
            )
        ],
    )

    profile = await db_session.get(PersonalProfile, user.id)
    clarification = await db_session.get(HealthProfileChange, clarification_id)
    assert profile is not None
    assert profile.weight_kg == Decimal("65.00")
    assert clarification is not None
    assert clarification.status is ProfileChangeStatus.SUPERSEDED


@pytest.mark.parametrize(
    ("operation", "value", "unit", "error_message"),
    [
        (
            ProfileOperation.SET,
            120,
            PersonalProfileUnit.JIN,
            "saved partial value",
        ),
        (ProfileOperation.CLEAR, None, None, "operation does not match"),
    ],
)
async def test_missing_unit_reply_cannot_replace_saved_weight_information(
    db_session: AsyncSession,
    operation: Literal[ProfileOperation.SET, ProfileOperation.CLEAR],
    value: int | None,
    unit: PersonalProfileUnit | None,
    error_message: str,
) -> None:
    user = await _create_user(db_session)
    first_message, first_run = await _create_run(
        db_session,
        user=user,
        content="我的体重是130",
        run_number=1,
    )
    first = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=first_message.id,
        agent_run_id=first_run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CLARIFICATION,
                value=130,
                unit=None,
                evidence_quote="体重是130",
                clarification_reason=ClarificationReason.MISSING_UNIT,
            )
        ],
    )
    clarification_id = first.changes[0].change_id

    second_message, second_run = await _create_run(
        db_session,
        user=user,
        content="斤",
        run_number=2,
    )
    with pytest.raises(InvalidProfileProposalError, match=error_message):
        await apply_health_profile_proposals(
            db_session,
            user_id=user.id,
            trigger_message_id=second_message.id,
            agent_run_id=second_run.id,
            proposals=[
                PersonalProfileProposal(
                    field_name=PersonalProfileField.WEIGHT_KG,
                    operation=operation,
                    mode=ProfileChangeMode.DIRECT,
                    value=value,
                    unit=unit,
                    evidence_quote="斤",
                    resolves_change_id=clarification_id,
                )
            ],
        )

    profile = await db_session.get(PersonalProfile, user.id)
    clarification = await db_session.get(HealthProfileChange, clarification_id)
    assert profile is not None
    assert profile.weight_kg is None
    assert clarification is not None
    assert clarification.status is ProfileChangeStatus.PENDING


async def test_saved_weight_value_requires_a_complete_explicit_correction(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    first_message, first_run = await _create_run(
        db_session,
        user=user,
        content="我的体重是130",
        run_number=1,
    )
    first = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=first_message.id,
        agent_run_id=first_run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CLARIFICATION,
                value=130,
                unit=None,
                evidence_quote="体重是130",
                clarification_reason=ClarificationReason.MISSING_UNIT,
            )
        ],
    )
    clarification_id = first.changes[0].change_id
    second_message, second_run = await _create_run(
        db_session,
        user=user,
        content="130斤",
        run_number=2,
    )

    with pytest.raises(InvalidProfileProposalError, match="saved partial value"):
        await apply_health_profile_proposals(
            db_session,
            user_id=user.id,
            trigger_message_id=second_message.id,
            agent_run_id=second_run.id,
            proposals=[
                PersonalProfileProposal(
                    field_name=PersonalProfileField.WEIGHT_KG,
                    operation=ProfileOperation.SET,
                    mode=ProfileChangeMode.DIRECT,
                    value=30,
                    unit=PersonalProfileUnit.JIN,
                    evidence_quote="130斤",
                    resolves_change_id=clarification_id,
                )
            ],
        )

    correction_message, correction_run = await _create_run(
        db_session,
        user=user,
        content="刚才说错了，其实是120.0千克",
        run_number=3,
    )

    await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=correction_message.id,
        agent_run_id=correction_run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.DIRECT,
                value=Decimal("120.0"),
                unit=PersonalProfileUnit.KILOGRAMS,
                evidence_quote="其实是120.0千克",
                resolves_change_id=clarification_id,
            )
        ],
    )

    profile = await db_session.get(PersonalProfile, user.id)
    clarification = await db_session.get(HealthProfileChange, clarification_id)
    assert profile is not None
    assert profile.weight_kg == Decimal("120.00")
    assert clarification is not None
    assert clarification.status is ProfileChangeStatus.SUPERSEDED


async def test_clarification_cannot_replace_a_saved_unit_without_evidence(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    first_message, first_run = await _create_run(
        db_session,
        user=user,
        content="我的体重单位是斤",
        run_number=1,
    )
    first = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=first_message.id,
        agent_run_id=first_run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CLARIFICATION,
                value=None,
                unit=PersonalProfileUnit.JIN,
                evidence_quote="体重单位是斤",
                clarification_reason=ClarificationReason.MISSING_VALUE,
            )
        ],
    )
    clarification_id = first.changes[0].change_id
    second_message, second_run = await _create_run(
        db_session,
        user=user,
        content="130",
        run_number=2,
    )

    with pytest.raises(InvalidProfileProposalError, match="saved partial unit"):
        await apply_health_profile_proposals(
            db_session,
            user_id=user.id,
            trigger_message_id=second_message.id,
            agent_run_id=second_run.id,
            proposals=[
                PersonalProfileProposal(
                    field_name=PersonalProfileField.WEIGHT_KG,
                    operation=ProfileOperation.SET,
                    mode=ProfileChangeMode.DIRECT,
                    value=130,
                    unit=PersonalProfileUnit.KILOGRAMS,
                    evidence_quote="130",
                    resolves_change_id=clarification_id,
                )
            ],
        )

    profile = await db_session.get(PersonalProfile, user.id)
    clarification = await db_session.get(HealthProfileChange, clarification_id)
    assert profile is not None
    assert profile.weight_kg is None
    assert clarification is not None
    assert clarification.status is ProfileChangeStatus.PENDING


async def test_card_decision_is_idempotent_and_checks_only_target_field(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    message, run = await _create_run(
        db_session,
        user=user,
        content="体重可能是65公斤",
        run_number=1,
    )
    result = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=message.id,
        agent_run_id=run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CONFIRMATION,
                value=65,
                unit=PersonalProfileUnit.KILOGRAMS,
                evidence_quote="体重可能是65公斤",
            )
        ],
    )
    card_id = result.changes[0].change_id

    # 不相关字段改变不会让体重卡片过期。
    profile = await db_session.get(PersonalProfile, user.id)
    assert profile is not None
    profile.occupation = "学生"
    profile.field_revisions = {"occupation": 1}
    profile.revision = 1
    await db_session.flush()

    first = await decide_profile_card(
        db_session,
        user_id=user.id,
        card_id=card_id,
        client_action_id=uuid4(),
        decision="accept",
    )
    second = await decide_profile_card(
        db_session,
        user_id=user.id,
        card_id=card_id,
        client_action_id=uuid4(),
        decision="accept",
    )
    assert first is second is ProfileChangeStatus.APPLIED
    assert profile.weight_kg == Decimal("65.00")

    with pytest.raises(ProfileCardDecisionConflictError):
        await decide_profile_card(
            db_session,
            user_id=user.id,
            card_id=card_id,
            client_action_id=uuid4(),
            decision="reject",
        )


async def test_agent_direct_change_supersedes_same_field_confirmation(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    first_message, first_run = await _create_run(
        db_session,
        user=user,
        content="身高可能是175厘米，职业可能是学生",
        run_number=1,
    )
    pending = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=first_message.id,
        agent_run_id=first_run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.HEIGHT_CM,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CONFIRMATION,
                value=175,
                unit=PersonalProfileUnit.CENTIMETERS,
                evidence_quote="身高可能是175厘米",
            ),
            PersonalProfileProposal(
                field_name=PersonalProfileField.OCCUPATION,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CONFIRMATION,
                value="学生",
                evidence_quote="职业可能是学生",
            ),
        ],
    )

    second_message, second_run = await _create_run(
        db_session,
        user=user,
        content="我身高178厘米",
        run_number=2,
    )
    await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=second_message.id,
        agent_run_id=second_run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.HEIGHT_CM,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.DIRECT,
                value=178,
                unit=PersonalProfileUnit.CENTIMETERS,
                evidence_quote="我身高178厘米",
            )
        ],
    )

    card = await _single_change(db_session, pending.changes[0].change_id)
    unrelated = await _single_change(db_session, pending.changes[1].change_id)
    assert card.status is ProfileChangeStatus.SUPERSEDED
    assert unrelated.status is ProfileChangeStatus.PENDING
    with pytest.raises(ProfileCardDecisionConflictError):
        await decide_profile_card(
            db_session,
            user_id=user.id,
            card_id=card.id,
            client_action_id=uuid4(),
            decision="accept",
        )


async def test_clarification_can_only_be_resolved_by_exact_new_proposal(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    first_message, first_run = await _create_run(
        db_session,
        user=user,
        content="我体重六十多",
        run_number=1,
    )
    first = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=first_message.id,
        agent_run_id=first_run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CLARIFICATION,
                value=None,
                unit=None,
                evidence_quote="体重六十多",
                clarification_reason=ClarificationReason.MISSING_VALUE,
            )
        ],
    )
    clarification_id = first.changes[0].change_id

    with pytest.raises(InvalidProfileCardDecisionError):
        await decide_profile_card(
            db_session,
            user_id=user.id,
            card_id=clarification_id,
            client_action_id=uuid4(),
            decision="accept",
        )

    second_message, second_run = await _create_run(
        db_session,
        user=user,
        content="是65公斤",
        run_number=2,
    )
    await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=second_message.id,
        agent_run_id=second_run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.DIRECT,
                value=65,
                unit=PersonalProfileUnit.KILOGRAMS,
                evidence_quote="65公斤",
                resolves_change_id=clarification_id,
            )
        ],
    )
    old_change = await db_session.get(HealthProfileChange, clarification_id)
    assert old_change is not None
    assert old_change.status is ProfileChangeStatus.SUPERSEDED
    assert old_change.resolved_by_user_id == user.id


async def test_agent_direct_fact_update_supersedes_same_target_card(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    fact = HealthFact(
        user_id=user.id,
        fact_type=HealthFactType.ALLERGY,
        statement="对青霉素过敏",
        assertion=FactAssertion.PRESENT,
        temporal_status=FactTemporalStatus.CURRENT,
        status=HealthFactStatus.ACTIVE,
        revision=1,
    )
    db_session.add(fact)
    await db_session.flush()
    first_message, first_run = await _create_run(
        db_session,
        user=user,
        content="可能是对青霉素严重过敏",
        run_number=1,
    )
    pending = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=first_message.id,
        agent_run_id=first_run.id,
        proposals=[
            HealthFactProposal(
                fact_type=HealthFactType.ALLERGY,
                operation=ProfileOperation.UPDATE,
                target_id=fact.id,
                mode=ProfileChangeMode.CONFIRMATION,
                statement="对青霉素严重过敏",
                assertion=FactAssertion.PRESENT,
                temporal_status=FactTemporalStatus.CURRENT,
                evidence_quote="可能是对青霉素严重过敏",
            )
        ],
    )
    second_message, second_run = await _create_run(
        db_session,
        user=user,
        content="我确定是对青霉素严重过敏",
        run_number=2,
    )
    await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=second_message.id,
        agent_run_id=second_run.id,
        proposals=[
            HealthFactProposal(
                fact_type=HealthFactType.ALLERGY,
                operation=ProfileOperation.UPDATE,
                target_id=fact.id,
                mode=ProfileChangeMode.DIRECT,
                statement="对青霉素严重过敏",
                assertion=FactAssertion.PRESENT,
                temporal_status=FactTemporalStatus.CURRENT,
                evidence_quote="确定是对青霉素严重过敏",
            )
        ],
    )
    card = await _single_change(db_session, pending.changes[0].change_id)
    assert card.status is ProfileChangeStatus.SUPERSEDED


async def test_health_fact_clarification_requires_saved_or_explicit_new_content(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    first_message, first_run = await _create_run(
        db_session,
        user=user,
        content="我对青霉素过敏，但不确定现在还算不算",
        run_number=1,
    )
    first = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=first_message.id,
        agent_run_id=first_run.id,
        proposals=[
            HealthFactProposal(
                fact_type=HealthFactType.ALLERGY,
                operation=ProfileOperation.ADD,
                mode=ProfileChangeMode.CLARIFICATION,
                statement="对青霉素过敏",
                assertion=FactAssertion.PRESENT,
                temporal_status=None,
                evidence_quote="对青霉素过敏，但不确定现在还算不算",
                clarification_reason=ClarificationReason.MISSING_DETAILS,
            )
        ],
    )
    clarification_id = first.changes[0].change_id

    wrong_message, wrong_run = await _create_run(
        db_session,
        user=user,
        content="现在仍然如此",
        run_number=2,
    )
    with pytest.raises(InvalidProfileProposalError, match="saved health fact"):
        await apply_health_profile_proposals(
            db_session,
            user_id=user.id,
            trigger_message_id=wrong_message.id,
            agent_run_id=wrong_run.id,
            proposals=[
                HealthFactProposal(
                    fact_type=HealthFactType.ALLERGY,
                    operation=ProfileOperation.ADD,
                    mode=ProfileChangeMode.DIRECT,
                    statement="对花生过敏",
                    assertion=FactAssertion.PRESENT,
                    temporal_status=FactTemporalStatus.CURRENT,
                    evidence_quote="现在仍然如此",
                    resolves_change_id=clarification_id,
                )
            ],
        )

    correct_message, correct_run = await _create_run(
        db_session,
        user=user,
        content="刚才说错了，其实是对花生过敏",
        run_number=3,
    )
    await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=correct_message.id,
        agent_run_id=correct_run.id,
        proposals=[
            HealthFactProposal(
                fact_type=HealthFactType.ALLERGY,
                operation=ProfileOperation.ADD,
                mode=ProfileChangeMode.DIRECT,
                statement="对花生过敏",
                assertion=FactAssertion.PRESENT,
                temporal_status=FactTemporalStatus.CURRENT,
                evidence_quote="对花生过敏",
                resolves_change_id=clarification_id,
            )
        ],
    )

    facts = list(
        await db_session.scalars(
            select(HealthFact).where(HealthFact.user_id == user.id)
        )
    )
    clarification = await db_session.get(HealthProfileChange, clarification_id)
    assert [fact.statement for fact in facts] == ["对花生过敏"]
    assert clarification is not None
    assert clarification.status is ProfileChangeStatus.SUPERSEDED


async def test_health_facts_are_not_fuzzily_deduplicated(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    message, run = await _create_run(
        db_session,
        user=user,
        content="我对青霉素过敏，再说一次，我对青霉素过敏",
        run_number=1,
    )
    proposals = [
        HealthFactProposal(
            fact_type=HealthFactType.ALLERGY,
            operation=ProfileOperation.ADD,
            mode=ProfileChangeMode.DIRECT,
            statement="对青霉素过敏",
            assertion=FactAssertion.PRESENT,
            temporal_status=FactTemporalStatus.CURRENT,
            evidence_quote="我对青霉素过敏",
        ),
        HealthFactProposal(
            fact_type=HealthFactType.ALLERGY,
            operation=ProfileOperation.ADD,
            mode=ProfileChangeMode.DIRECT,
            statement="对青霉素过敏",
            assertion=FactAssertion.PRESENT,
            temporal_status=FactTemporalStatus.CURRENT,
            evidence_quote="我对青霉素过敏",
        ),
    ]
    await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=message.id,
        agent_run_id=run.id,
        proposals=proposals,
    )
    facts = list(
        await db_session.scalars(
            select(HealthFact).where(
                HealthFact.user_id == user.id,
                HealthFact.status == HealthFactStatus.ACTIVE,
            )
        )
    )
    assert len(facts) == 2


async def test_empty_proposals_are_a_valid_no_change(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    message, run = await _create_run(
        db_session,
        user=user,
        content="今天天气不错",
        run_number=1,
    )
    result = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=message.id,
        agent_run_id=run.id,
        proposals=[],
    )
    assert result.changes == []
    snapshot = await load_health_profile_snapshot(db_session, user.id)
    assert snapshot.health_facts == []
    assert snapshot.pending_changes == []


async def test_evidence_must_be_from_current_user_message(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    message, run = await _create_run(
        db_session,
        user=user,
        content="聊点别的",
        run_number=1,
    )
    with pytest.raises(InvalidProfileProposalError, match="evidence"):
        await apply_health_profile_proposals(
            db_session,
            user_id=user.id,
            trigger_message_id=message.id,
            agent_run_id=run.id,
            proposals=[
                PersonalProfileProposal(
                    field_name=PersonalProfileField.OCCUPATION,
                    operation=ProfileOperation.SET,
                    mode=ProfileChangeMode.DIRECT,
                    value="学生",
                    unit=None,
                    evidence_quote="我是学生",
                )
            ],
        )


async def test_bare_height_becomes_confirmation_and_accepts_inline_correction(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    message, run = await _create_run(
        db_session,
        user=user,
        content="我身高169",
        run_number=1,
    )
    result = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=message.id,
        agent_run_id=run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.HEIGHT_CM,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CLARIFICATION,
                value=169,
                unit=None,
                evidence_quote="身高169",
                clarification_reason=ClarificationReason.MISSING_UNIT,
            )
        ],
    )
    card = await _single_change(db_session, result.changes[0].change_id)
    assert card.mode is ProfileChangeMode.CONFIRMATION
    assert card.question == "你是说你的身高是 169 cm 吗？"
    assert [option.id for option in profile_card_options(card)[0]] == [
        "accept",
        "reject",
    ]

    status = await answer_profile_card(
        db_session,
        user_id=user.id,
        card_id=card.id,
        request=ProfileCardAnswerRequest(
            client_action_id=uuid4(),
            custom_answer=PersonalProfileCardCustomAnswer(
                value="1.68",
                unit=PersonalProfileUnit.METERS,
            ),
        ),
    )
    profile = await db_session.get(PersonalProfile, user.id)
    assert status is ProfileChangeStatus.APPLIED
    assert profile is not None
    assert profile.height_cm == Decimal("168.00")


async def test_manual_personal_edit_supersedes_only_same_field_card(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    message, run = await _create_run(
        db_session,
        user=user,
        content="职业可能是学生，住在北京也不太确定",
        run_number=1,
    )
    pending = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=message.id,
        agent_run_id=run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.OCCUPATION,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CONFIRMATION,
                value="学生",
                evidence_quote="职业可能是学生",
            ),
            PersonalProfileProposal(
                field_name=PersonalProfileField.RESIDENT_AREA,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CONFIRMATION,
                value="北京",
                evidence_quote="住在北京也不太确定",
            ),
        ],
    )

    change = await apply_manual_health_profile_change(
        db_session,
        user_id=user.id,
        request=PersonalProfileChangeRequest(
            client_action_id=uuid4(),
            expected_revision=0,
            target_type=ProfileTargetType.PERSONAL_PROFILE,
            field_name=PersonalProfileField.OCCUPATION,
            operation=ProfileOperation.SET,
            value="程序员",
        ),
    )
    occupation_card = await _single_change(db_session, pending.changes[0].change_id)
    area_card = await _single_change(db_session, pending.changes[1].change_id)
    assert change.origin == "user"
    assert change.trigger_message_id is None
    assert change.agent_run_id is None
    assert occupation_card.status is ProfileChangeStatus.SUPERSEDED
    assert area_card.status is ProfileChangeStatus.PENDING


async def test_manual_health_fact_lifecycle_is_audited_and_clears_dates(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    add_request = HealthFactChangeRequest(
        client_action_id=uuid4(),
        expected_revision=None,
        target_type=ProfileTargetType.HEALTH_FACT,
        operation=ProfileOperation.ADD,
        fact_type=HealthFactType.ALLERGY,
        statement="对青霉素过敏",
        assertion=FactAssertion.PRESENT,
        temporal_status=FactTemporalStatus.CURRENT,
    )
    added = await apply_manual_health_profile_change(
        db_session,
        user_id=user.id,
        request=add_request,
    )
    repeated = await apply_manual_health_profile_change(
        db_session,
        user_id=user.id,
        request=add_request,
    )
    assert repeated.id == added.id
    assert added.target_id is not None

    fact = await db_session.get(HealthFact, added.target_id)
    assert fact is not None
    fact.effective_start = date(2020, 1, 1)
    fact.effective_end = date(2021, 1, 1)
    await db_session.flush()

    updated = await apply_manual_health_profile_change(
        db_session,
        user_id=user.id,
        request=HealthFactChangeRequest(
            client_action_id=uuid4(),
            expected_revision=1,
            target_type=ProfileTargetType.HEALTH_FACT,
            operation=ProfileOperation.UPDATE,
            fact_type=HealthFactType.ALLERGY,
            target_id=fact.id,
            statement="对青霉素严重过敏",
            assertion=FactAssertion.PRESENT,
            temporal_status=FactTemporalStatus.CURRENT,
        ),
    )
    assert updated.result_revision == 2
    assert fact.effective_start is None
    assert fact.effective_end is None

    retracted = await apply_manual_health_profile_change(
        db_session,
        user_id=user.id,
        request=HealthFactChangeRequest(
            client_action_id=uuid4(),
            expected_revision=2,
            target_type=ProfileTargetType.HEALTH_FACT,
            operation=ProfileOperation.RETRACT,
            fact_type=HealthFactType.ALLERGY,
            target_id=fact.id,
        ),
    )
    assert retracted.result_revision == 3
    assert fact.status is HealthFactStatus.RETRACTED


@pytest.mark.parametrize(
    ("field", "value", "unit", "expected"),
    [
        (PersonalProfileField.SEX, "女", None, "女"),
        (PersonalProfileField.AGE_YEARS, 28, None, 28),
        (
            PersonalProfileField.HEIGHT_CM,
            Decimal("1.69"),
            PersonalProfileUnit.METERS,
            Decimal("169.00"),
        ),
        (
            PersonalProfileField.WEIGHT_KG,
            130,
            PersonalProfileUnit.JIN,
            Decimal("65.00"),
        ),
        (PersonalProfileField.RESIDENT_AREA, "杭州", None, "杭州"),
        (PersonalProfileField.SCHEDULE_TYPE, "早睡早起", None, "早睡早起"),
        (PersonalProfileField.OCCUPATION, "教师", None, "教师"),
    ],
)
async def test_manual_page_can_set_and_clear_every_personal_field(
    db_session: AsyncSession,
    field: PersonalProfileField,
    value: str | int | Decimal,
    unit: PersonalProfileUnit | None,
    expected: str | int | Decimal,
) -> None:
    user = await _create_user(db_session)
    await apply_manual_health_profile_change(
        db_session,
        user_id=user.id,
        request=PersonalProfileChangeRequest(
            client_action_id=uuid4(),
            expected_revision=0,
            target_type=ProfileTargetType.PERSONAL_PROFILE,
            field_name=field,
            operation=ProfileOperation.SET,
            value=value,
            unit=unit,
        ),
    )
    profile = await db_session.get(PersonalProfile, user.id)
    assert profile is not None
    assert getattr(profile, field.value) == expected

    await apply_manual_health_profile_change(
        db_session,
        user_id=user.id,
        request=PersonalProfileChangeRequest(
            client_action_id=uuid4(),
            expected_revision=1,
            target_type=ProfileTargetType.PERSONAL_PROFILE,
            field_name=field,
            operation=ProfileOperation.CLEAR,
        ),
    )
    assert getattr(profile, field.value) is None


async def test_bare_age_becomes_a_years_confirmation(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    message, run = await _create_run(
        db_session,
        user=user,
        content="我今年28",
        run_number=1,
    )
    result = await apply_health_profile_proposals(
        db_session,
        user_id=user.id,
        trigger_message_id=message.id,
        agent_run_id=run.id,
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.AGE_YEARS,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CLARIFICATION,
                value=28,
                unit=None,
                evidence_quote="今年28",
                clarification_reason=ClarificationReason.MISSING_UNIT,
            )
        ],
    )
    card = await _single_change(db_session, result.changes[0].change_id)
    assert card.mode is ProfileChangeMode.CONFIRMATION
    assert card.question == "你是说你的年龄是 28 岁吗？"


@pytest.mark.parametrize("fact_type", list(HealthFactType))
async def test_manual_page_can_add_every_supported_health_fact_type(
    db_session: AsyncSession,
    fact_type: HealthFactType,
) -> None:
    user = await _create_user(db_session)
    change = await apply_manual_health_profile_change(
        db_session,
        user_id=user.id,
        request=HealthFactChangeRequest(
            client_action_id=uuid4(),
            expected_revision=None,
            target_type=ProfileTargetType.HEALTH_FACT,
            operation=ProfileOperation.ADD,
            fact_type=fact_type,
            statement=f"{fact_type.value} 测试内容",
            assertion=FactAssertion.PRESENT,
            temporal_status=FactTemporalStatus.UNKNOWN,
        ),
    )
    fact = await db_session.get(HealthFact, change.target_id)
    assert fact is not None
    assert fact.fact_type is fact_type


async def test_concurrent_manual_add_retry_creates_one_fact(
    committed_user_id: UUID,
) -> None:
    request = HealthFactChangeRequest(
        client_action_id=uuid4(),
        expected_revision=None,
        target_type=ProfileTargetType.HEALTH_FACT,
        operation=ProfileOperation.ADD,
        fact_type=HealthFactType.TREATMENT,
        statement="使用胰岛素治疗",
        assertion=FactAssertion.PRESENT,
        temporal_status=FactTemporalStatus.CURRENT,
    )

    async def apply_once() -> UUID:
        async with session_factory() as session:
            change = await apply_manual_health_profile_change(
                session,
                user_id=committed_user_id,
                request=request,
            )
            await session.commit()
            return change.id

    first_id, second_id = await asyncio.gather(apply_once(), apply_once())
    assert first_id == second_id
    async with session_factory() as session:
        facts = list(
            await session.scalars(
                select(HealthFact).where(HealthFact.user_id == committed_user_id)
            )
        )
    assert len(facts) == 1


def test_clarification_cannot_create_clear_or_retract_dead_card() -> None:
    with pytest.raises(ValidationError, match="clear operation"):
        PersonalProfileProposal(
            field_name=PersonalProfileField.OCCUPATION,
            operation=ProfileOperation.CLEAR,
            mode=ProfileChangeMode.CLARIFICATION,
            evidence_quote="职业不确定",
            clarification_reason=ClarificationReason.MISSING_VALUE,
        )
    with pytest.raises(ValidationError, match="retract operation"):
        HealthFactProposal(
            fact_type=HealthFactType.ALLERGY,
            operation=ProfileOperation.RETRACT,
            target_id=uuid4(),
            mode=ProfileChangeMode.CLARIFICATION,
            evidence_quote="过敏不确定",
            clarification_reason=ClarificationReason.UNCERTAIN_FACT,
        )


async def test_two_cards_cannot_concurrently_reuse_one_client_action_id(
    committed_user_id: UUID,
) -> None:
    async with session_factory() as session:
        user = await session.get(User, committed_user_id)
        assert user is not None
        first_message, first_run = await _create_run(
            session,
            user=user,
            content="职业可能是教师",
            run_number=1,
        )
        first = await apply_health_profile_proposals(
            session,
            user_id=user.id,
            trigger_message_id=first_message.id,
            agent_run_id=first_run.id,
            proposals=[
                PersonalProfileProposal(
                    field_name=PersonalProfileField.OCCUPATION,
                    operation=ProfileOperation.SET,
                    mode=ProfileChangeMode.CONFIRMATION,
                    value="教师",
                    evidence_quote="职业可能是教师",
                )
            ],
        )
        second_message, second_run = await _create_run(
            session,
            user=user,
            content="可能住在杭州",
            run_number=2,
        )
        second = await apply_health_profile_proposals(
            session,
            user_id=user.id,
            trigger_message_id=second_message.id,
            agent_run_id=second_run.id,
            proposals=[
                PersonalProfileProposal(
                    field_name=PersonalProfileField.RESIDENT_AREA,
                    operation=ProfileOperation.SET,
                    mode=ProfileChangeMode.CONFIRMATION,
                    value="杭州",
                    evidence_quote="可能住在杭州",
                )
            ],
        )
        await session.commit()

    action_id = uuid4()

    async def answer(card_id: UUID) -> ProfileChangeStatus | str:
        async with session_factory() as session:
            try:
                result = await answer_profile_card(
                    session,
                    user_id=committed_user_id,
                    card_id=card_id,
                    request=ProfileCardAnswerRequest(
                        client_action_id=action_id,
                        option_id="accept",
                    ),
                )
                await session.commit()
                return result
            except ProfileCardDecisionConflictError:
                await session.rollback()
                return "conflict"

    results = await asyncio.gather(
        answer(first.changes[0].change_id),
        answer(second.changes[0].change_id),
    )
    assert set(results) == {ProfileChangeStatus.APPLIED, "conflict"}
