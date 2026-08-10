import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.context.health_profile import (
    load_profile_input,
    render_core_profile,
)
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    HealthProfileChange,
    Message,
    MessageRole,
    MessageStatus,
    PersonalProfile,
    ProfileChangeMode,
    ProfileChangeStatus,
    ProfileOperation,
    ProfileTargetType,
    User,
)


async def _create_user(db_session: AsyncSession) -> User:
    username = f"profile_context_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(PersonalProfile(user_id=user.id, occupation="学生"))
    await db_session.flush()
    return user


async def _completed_turn(
    db_session: AsyncSession,
    user: User,
    index: int,
) -> tuple[Message, Message, AgentRun]:
    now = datetime.now(timezone.utc)
    user_message = Message(
        client_message_id=uuid4(),
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content=f"用户第{index}轮",
    )
    assistant_message = Message(
        client_message_id=None,
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content=f"助手第{index}轮",
        completed_at=now,
    )
    db_session.add_all([user_message, assistant_message])
    await db_session.flush()
    run = AgentRun(
        user_id=user.id,
        trigger_message_id=user_message.id,
        result_message_id=assistant_message.id,
        parent_run_id=None,
        parent_tool_call_id=None,
        agent_name="core_agent",
        model="test-model",
        status=AgentRunStatus.COMPLETED,
        input_tokens=1,
        output_tokens=1,
        finished_at=now,
    )
    db_session.add(run)
    await db_session.flush()
    return user_message, assistant_message, run


async def test_manager_context_keeps_only_five_complete_turns_and_pending_cards(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session)
    completed = [
        await _completed_turn(db_session, user, index) for index in range(1, 7)
    ]
    current_message = Message(
        client_message_id=uuid4(),
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="65公斤",
    )
    db_session.add(current_message)
    await db_session.flush()
    db_session.add(
        HealthProfileChange(
            user_id=user.id,
            trigger_message_id=completed[-1][0].id,
            agent_run_id=completed[-1][2].id,
            proposal_index=1,
            mode=ProfileChangeMode.CLARIFICATION,
            status=ProfileChangeStatus.PENDING,
            target_type=ProfileTargetType.PERSONAL_PROFILE,
            field_name="weight_kg",
            operation=ProfileOperation.SET,
            target_id=None,
            before_value=None,
            proposed_value=None,
            expected_revision=0,
            result_revision=None,
            question="你的体重具体是多少公斤？",
        )
    )
    await db_session.flush()

    manager_input = await load_profile_input(
        db_session,
        user.id,
        current_message.id,
    )

    assert [turn.user for turn in manager_input.recent_conversation] == [
        f"用户第{index}轮" for index in range(2, 7)
    ]
    assert manager_input.current_user_message == "65公斤"
    assert manager_input.current_profile.personal_profile.occupation == "学生"
    assert manager_input.personal_field_revisions == {}
    assert "personal_field_revisions" not in json.loads(manager_input.to_model_text())
    assert manager_input.current_profile.pending_changes[0].question == (
        "你的体重具体是多少公斤？"
    )

    rendered = json.loads(await render_core_profile(db_session, user.id))
    assert set(rendered) == {
        "personal_profile",
        "health_facts",
        "pending_changes",
    }
    assert rendered["pending_changes"][0]["field_name"] == "weight_kg"
