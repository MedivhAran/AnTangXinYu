import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Any, Self, cast
from uuid import UUID, uuid4

import pytest
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
)

import antang_api.tools.wearable as wearable_tools
from antang_api.agents.health_profile import (
    PROFILE_AGENT_NAME,
    ProfileAgentDecision,
    ProfileAgentProposal,
    ProfileAgentGraph,
    ProfileDecision,
    build_profile_agent,
)
from antang_api.agents.runtime import AgentContext
from antang_api.health_profile.types import (
    ClarificationReason,
    HealthFactProposal,
    PersonalProfileProposal,
)
from antang_api.health_profile.service import apply_manual_health_profile_change
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
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
    WearableRecordType,
)
from antang_api.schemas.health_profile import (
    HealthFactChangeRequest,
    PersonalProfileChangeRequest,
)
from antang_api.tools.health_profile import (
    DuplicateProfileRunError,
    PROFILE_TOOL_NAME,
    ProfileAgentError,
    ProfileAgentRunner,
)
from antang_api.tools.wearable import (
    WearableReadRequest,
    build_wearable_read_tool,
)


class FakeHealthProfileGraph:
    """记录 runner 真正交给子 Agent 的隔离输入和运行配置。"""

    def __init__(
        self,
        decision: ProfileDecision,
        *,
        error: Exception | None = None,
        before_return: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.decision = decision
        self.error = error
        self.before_return = before_return
        self.call_count = 0
        self.agent_input: object | None = None
        self.config: RunnableConfig | None = None
        self.context: AgentContext | None = None

    async def ainvoke(
        self,
        agent_input: object,
        *,
        config: RunnableConfig,
        context: AgentContext,
    ) -> dict[str, object]:
        self.call_count += 1
        self.agent_input = agent_input
        self.config = config
        self.context = context
        if self.error is not None:
            raise self.error
        if self.before_return is not None:
            await self.before_return()
        return {
            "messages": [
                AIMessage(
                    content="",
                    usage_metadata={
                        "input_tokens": 17,
                        "output_tokens": 5,
                        "total_tokens": 22,
                    },
                )
            ],
            "structured_response": ProfileAgentDecision(
                proposals=[
                    ProfileAgentProposal.model_validate(proposal.model_dump())
                    for proposal in self.decision.proposals
                ]
            ),
        }


class ToolLoopFakeModel(FakeMessagesListChatModel):
    """允许测试模型同时绑定手环工具和结构化结果工具。"""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Self:
        del tools, tool_choice, kwargs
        return self


def make_session_factory(
    db_session: AsyncSession,
) -> async_sessionmaker[AsyncSession]:
    bind = db_session.bind
    if not isinstance(bind, AsyncConnection):
        raise TypeError("测试数据库会话必须绑定 AsyncConnection")
    return async_sessionmaker(
        bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


async def create_parent_delegation(
    db_session: AsyncSession,
    *,
    message_text: str,
) -> tuple[User, PersonalProfile, Message, AgentRun, AgentToolCall]:
    username = f"profile_agent_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    profile = PersonalProfile(user_id=user.id, occupation="学生")
    message = Message(
        client_message_id=uuid4(),
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content=message_text,
    )
    db_session.add_all([profile, message])
    await db_session.flush()

    root_run = AgentRun(
        user_id=user.id,
        trigger_message_id=message.id,
        result_message_id=None,
        parent_run_id=None,
        parent_tool_call_id=None,
        agent_name="core_agent",
        model="test-core-model",
        status=AgentRunStatus.RUNNING,
    )
    db_session.add(root_run)
    await db_session.flush()

    parent_call = AgentToolCall(
        agent_run_id=root_run.id,
        tool_call_id="delegate-profile-1",
        tool_name=PROFILE_TOOL_NAME,
        model_turn_index=1,
        tool_call_index=1,
        arguments={},
        status=AgentToolCallStatus.RUNNING,
    )
    db_session.add(parent_call)
    await db_session.flush()
    return user, profile, message, root_run, parent_call


def make_runtime(
    user_id: UUID,
    root_run_id: UUID,
    tool_call_id: str,
) -> ToolRuntime[AgentContext]:
    runtime = ToolRuntime(
        state={
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": PROFILE_TOOL_NAME,
                            "args": {},
                            "id": tool_call_id,
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        },
        context=AgentContext(
            user_id=user_id,
            run_id=root_run_id,
            input_message_count=1,
        ),
        config={},
        stream_writer=lambda _value: None,
        tool_call_id=tool_call_id,
        store=None,
        tools=[],
    )
    return cast("ToolRuntime[AgentContext]", runtime)


def occupation_decision() -> ProfileDecision:
    return ProfileDecision(
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.OCCUPATION,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.DIRECT,
                value="自由职业者",
                unit=None,
                evidence_quote="职业改成自由职业者",
            )
        ]
    )


def weight_clarification_decision() -> ProfileDecision:
    return ProfileDecision(
        proposals=[
            PersonalProfileProposal(
                field_name=PersonalProfileField.WEIGHT_KG,
                operation=ProfileOperation.SET,
                mode=ProfileChangeMode.CLARIFICATION,
                value=130,
                unit=None,
                evidence_quote="体重130",
                clarification_reason=ClarificationReason.MISSING_UNIT,
            )
        ]
    )


def allergy_update_decision(fact_id: UUID) -> ProfileDecision:
    return ProfileDecision(
        proposals=[
            HealthFactProposal(
                fact_type=HealthFactType.ALLERGY,
                operation=ProfileOperation.UPDATE,
                target_id=fact_id,
                mode=ProfileChangeMode.DIRECT,
                statement="对青霉素严重过敏",
                assertion=FactAssertion.PRESENT,
                temporal_status=FactTemporalStatus.CURRENT,
                evidence_quote="过敏改成对青霉素严重过敏",
            )
        ]
    )


async def test_child_runner_applies_profile_and_keeps_internal_context(
    db_session: AsyncSession,
) -> None:
    user, profile, message, root_run, parent_call = await create_parent_delegation(
        db_session,
        message_text="帮我把职业改成自由职业者",
    )
    fake_graph = FakeHealthProfileGraph(occupation_decision())
    runner = ProfileAgentRunner(
        cast("ProfileAgentGraph", fake_graph),
        model_name="test-profile-model",
        session_factory=make_session_factory(db_session),
    )

    result = await runner.run(
        make_runtime(user.id, root_run.id, parent_call.tool_call_id)
    )

    assert result["status"] == "completed"
    assert result["changes"][0]["status"] == ProfileChangeStatus.APPLIED.value
    await db_session.refresh(profile)
    assert profile.occupation == "自由职业者"

    child_runs = list(
        await db_session.scalars(
            select(AgentRun).where(AgentRun.parent_run_id == root_run.id)
        )
    )
    assert len(child_runs) == 1
    child_run = child_runs[0]
    assert child_run.parent_tool_call_id == parent_call.id
    assert child_run.agent_name == PROFILE_AGENT_NAME
    assert child_run.status == AgentRunStatus.COMPLETED
    assert child_run.input_tokens == 17
    assert child_run.output_tokens == 5

    assert fake_graph.config is not None
    metadata = fake_graph.config.get("metadata")
    assert metadata is not None
    assert metadata["stream_visibility"] == "internal"
    assert fake_graph.context == AgentContext(
        user_id=user.id,
        run_id=child_run.id,
        input_message_count=1,
    )
    assert isinstance(fake_graph.agent_input, dict)
    child_messages = fake_graph.agent_input["messages"]
    assert len(child_messages) == 1
    manager_payload = json.loads(child_messages[0].content)
    assert manager_payload["current_user_message"] == message.content
    assert manager_payload["recent_conversation"] == []
    assert "hindsight" not in manager_payload
    assert "personal_field_revisions" not in manager_payload


async def test_page_edit_of_same_field_invalidates_agent_snapshot(
    db_session: AsyncSession,
) -> None:
    user, profile, _message, root_run, parent_call = await create_parent_delegation(
        db_session,
        message_text="帮我把职业改成自由职业者",
    )
    factory = make_session_factory(db_session)

    async def edit_from_profile_page() -> None:
        async with factory() as session:
            await apply_manual_health_profile_change(
                session,
                user_id=user.id,
                request=PersonalProfileChangeRequest(
                    client_action_id=uuid4(),
                    expected_revision=0,
                    target_type=ProfileTargetType.PERSONAL_PROFILE,
                    field_name=PersonalProfileField.OCCUPATION,
                    operation=ProfileOperation.SET,
                    value="护士",
                ),
            )
            await session.commit()

    fake_graph = FakeHealthProfileGraph(
        occupation_decision(),
        before_return=edit_from_profile_page,
    )
    runner = ProfileAgentRunner(
        cast("ProfileAgentGraph", fake_graph),
        model_name="test-profile-model",
        session_factory=factory,
    )

    with pytest.raises(ProfileAgentError, match="健康档案管理失败"):
        await runner.run(make_runtime(user.id, root_run.id, parent_call.tool_call_id))

    await db_session.refresh(profile)
    assert profile.occupation == "护士"
    changes = list(
        await db_session.scalars(
            select(HealthProfileChange).where(HealthProfileChange.user_id == user.id)
        )
    )
    assert len(changes) == 1
    assert changes[0].origin == "user"
    child_run = await db_session.scalar(
        select(AgentRun).where(AgentRun.parent_run_id == root_run.id)
    )
    assert child_run is not None
    assert child_run.status is AgentRunStatus.FAILED
    assert child_run.error_type == "HealthProfileChangedError"


async def test_page_edit_of_unrelated_field_does_not_invalidate_agent_snapshot(
    db_session: AsyncSession,
) -> None:
    user, profile, _message, root_run, parent_call = await create_parent_delegation(
        db_session,
        message_text="帮我把职业改成自由职业者",
    )
    factory = make_session_factory(db_session)

    async def edit_from_profile_page() -> None:
        async with factory() as session:
            await apply_manual_health_profile_change(
                session,
                user_id=user.id,
                request=PersonalProfileChangeRequest(
                    client_action_id=uuid4(),
                    expected_revision=0,
                    target_type=ProfileTargetType.PERSONAL_PROFILE,
                    field_name=PersonalProfileField.RESIDENT_AREA,
                    operation=ProfileOperation.SET,
                    value="杭州",
                ),
            )
            await session.commit()

    runner = ProfileAgentRunner(
        cast(
            "ProfileAgentGraph",
            FakeHealthProfileGraph(
                occupation_decision(),
                before_return=edit_from_profile_page,
            ),
        ),
        model_name="test-profile-model",
        session_factory=factory,
    )
    result = await runner.run(
        make_runtime(user.id, root_run.id, parent_call.tool_call_id)
    )

    await db_session.refresh(profile)
    assert result["changes"][0]["status"] == ProfileChangeStatus.APPLIED.value
    assert profile.occupation == "自由职业者"
    assert profile.resident_area == "杭州"


async def test_page_edit_of_same_fact_invalidates_agent_snapshot(
    db_session: AsyncSession,
) -> None:
    user, _profile, _message, root_run, parent_call = await create_parent_delegation(
        db_session,
        message_text="过敏改成对青霉素严重过敏",
    )
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
    factory = make_session_factory(db_session)

    async def edit_from_profile_page() -> None:
        async with factory() as session:
            await apply_manual_health_profile_change(
                session,
                user_id=user.id,
                request=HealthFactChangeRequest(
                    client_action_id=uuid4(),
                    expected_revision=1,
                    target_type=ProfileTargetType.HEALTH_FACT,
                    operation=ProfileOperation.UPDATE,
                    fact_type=HealthFactType.ALLERGY,
                    target_id=fact.id,
                    statement="对花生过敏",
                    assertion=FactAssertion.PRESENT,
                    temporal_status=FactTemporalStatus.CURRENT,
                ),
            )
            await session.commit()

    runner = ProfileAgentRunner(
        cast(
            "ProfileAgentGraph",
            FakeHealthProfileGraph(
                allergy_update_decision(fact.id),
                before_return=edit_from_profile_page,
            ),
        ),
        model_name="test-profile-model",
        session_factory=factory,
    )
    with pytest.raises(ProfileAgentError, match="健康档案管理失败"):
        await runner.run(make_runtime(user.id, root_run.id, parent_call.tool_call_id))

    await db_session.refresh(fact)
    assert fact.statement == "对花生过敏"
    assert fact.revision == 2


async def test_child_runner_failure_is_audited_without_profile_write(
    db_session: AsyncSession,
) -> None:
    user, profile, _message, root_run, parent_call = await create_parent_delegation(
        db_session,
        message_text="帮我把职业改成自由职业者",
    )
    fake_graph = FakeHealthProfileGraph(
        occupation_decision(),
        error=ValueError("模型返回了无效结果"),
    )
    runner = ProfileAgentRunner(
        cast("ProfileAgentGraph", fake_graph),
        model_name="test-profile-model",
        session_factory=make_session_factory(db_session),
    )

    with pytest.raises(ProfileAgentError, match="健康档案管理失败"):
        await runner.run(make_runtime(user.id, root_run.id, parent_call.tool_call_id))

    await db_session.refresh(profile)
    assert profile.occupation == "学生"
    child_run = await db_session.scalar(
        select(AgentRun).where(AgentRun.parent_run_id == root_run.id)
    )
    assert child_run is not None
    assert child_run.status == AgentRunStatus.FAILED
    assert child_run.error_type == "ValueError"
    assert child_run.error_message == "健康档案管理运行失败"


async def test_repeated_delegation_does_not_create_or_apply_twice(
    db_session: AsyncSession,
) -> None:
    user, profile, _message, root_run, parent_call = await create_parent_delegation(
        db_session,
        message_text="帮我把职业改成自由职业者",
    )
    fake_graph = FakeHealthProfileGraph(occupation_decision())
    runner = ProfileAgentRunner(
        cast("ProfileAgentGraph", fake_graph),
        model_name="test-profile-model",
        session_factory=make_session_factory(db_session),
    )
    runtime = make_runtime(user.id, root_run.id, parent_call.tool_call_id)

    await runner.run(runtime)
    with pytest.raises(DuplicateProfileRunError, match="已经委派过"):
        await runner.run(runtime)

    await db_session.refresh(profile)
    assert profile.occupation == "自由职业者"
    child_runs = list(
        await db_session.scalars(
            select(AgentRun).where(AgentRun.parent_run_id == root_run.id)
        )
    )
    changes = list(
        await db_session.scalars(
            select(HealthProfileChange).where(HealthProfileChange.user_id == user.id)
        )
    )
    assert len(child_runs) == 1
    assert len(changes) == 1
    assert fake_graph.call_count == 1


@pytest.mark.parametrize(
    ("message_text", "decision", "expected_status"),
    [
        (
            "帮我把职业改成自由职业者",
            occupation_decision(),
            ProfileChangeStatus.APPLIED,
        ),
        (
            "我体重130",
            weight_clarification_decision(),
            ProfileChangeStatus.PENDING,
        ),
    ],
)
async def test_retry_replays_completed_profile_result_without_running_child_again(
    db_session: AsyncSession,
    message_text: str,
    decision: ProfileDecision,
    expected_status: ProfileChangeStatus,
) -> None:
    user, _profile, message, root_run, parent_call = await create_parent_delegation(
        db_session,
        message_text=message_text,
    )
    fake_graph = FakeHealthProfileGraph(decision)
    runner = ProfileAgentRunner(
        cast("ProfileAgentGraph", fake_graph),
        model_name="test-profile-model",
        session_factory=make_session_factory(db_session),
    )
    first_result = await runner.run(
        make_runtime(user.id, root_run.id, parent_call.tool_call_id)
    )
    assert first_result["changes"][0]["status"] == expected_status.value

    first_change = await db_session.scalar(
        select(HealthProfileChange).where(
            HealthProfileChange.trigger_message_id == message.id
        )
    )
    assert first_change is not None
    if first_change.mode is not ProfileChangeMode.DIRECT:
        # 重试时应告诉 Core 卡片现在的状态，不能把已拒绝的卡片说成仍待处理。
        first_change.status = ProfileChangeStatus.REJECTED
    root_run.status = AgentRunStatus.FAILED
    parent_call.status = AgentToolCallStatus.FAILED

    retry_answer = Message(
        client_message_id=None,
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.GENERATING,
        content="",
    )
    db_session.add(retry_answer)
    await db_session.flush()
    retry_root = AgentRun(
        user_id=user.id,
        trigger_message_id=message.id,
        result_message_id=retry_answer.id,
        parent_run_id=None,
        parent_tool_call_id=None,
        agent_name="core_agent",
        model="test-core-model",
        status=AgentRunStatus.RUNNING,
    )
    db_session.add(retry_root)
    await db_session.flush()
    retry_call = AgentToolCall(
        agent_run_id=retry_root.id,
        tool_call_id="delegate-profile-retry",
        tool_name=PROFILE_TOOL_NAME,
        model_turn_index=1,
        tool_call_index=1,
        arguments={},
        status=AgentToolCallStatus.RUNNING,
    )
    db_session.add(retry_call)
    await db_session.commit()

    replayed = await runner.run(
        make_runtime(user.id, retry_root.id, retry_call.tool_call_id)
    )

    assert (
        replayed["changes"][0]["change_id"] == first_result["changes"][0]["change_id"]
    )
    assert replayed["changes"][0]["status"] == first_change.status.value
    assert fake_graph.call_count == 1
    child_runs = list(
        await db_session.scalars(
            select(AgentRun).where(
                AgentRun.trigger_message_id == message.id,
                AgentRun.agent_name == PROFILE_AGENT_NAME,
            )
        )
    )
    changes = list(
        await db_session.scalars(
            select(HealthProfileChange).where(
                HealthProfileChange.trigger_message_id == message.id
            )
        )
    )
    assert len(child_runs) == 1
    assert len(changes) == 1


async def test_profile_delegation_must_be_the_only_tool_in_its_round(
    db_session: AsyncSession,
) -> None:
    user, _profile, _message, root_run, parent_call = await create_parent_delegation(
        db_session,
        message_text="更新档案并搜索",
    )
    fake_graph = FakeHealthProfileGraph(occupation_decision())
    runner = ProfileAgentRunner(
        cast("ProfileAgentGraph", fake_graph),
        model_name="test-profile-model",
        session_factory=make_session_factory(db_session),
    )
    runtime = make_runtime(user.id, root_run.id, parent_call.tool_call_id)
    current_message = cast(AIMessage, runtime.state["messages"][-1])
    current_message.tool_calls.append(
        {
            "name": "web_search",
            "args": {"query": "不应执行"},
            "id": "search-1",
            "type": "tool_call",
        }
    )

    with pytest.raises(RuntimeError, match="必须独占"):
        await runner.run(runtime)

    assert fake_graph.call_count == 0
    child_run = await db_session.scalar(
        select(AgentRun).where(AgentRun.parent_run_id == root_run.id)
    )
    assert child_run is None


async def test_child_wearable_tool_is_persisted_without_custom_activity(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _profile, message, root_run, parent_call = await create_parent_delegation(
        db_session,
        message_text="看看我最近的手环数据",
    )
    child_run = AgentRun(
        user_id=user.id,
        trigger_message_id=message.id,
        result_message_id=None,
        parent_run_id=root_run.id,
        parent_tool_call_id=parent_call.id,
        agent_name=PROFILE_AGENT_NAME,
        model="test-profile-model",
        status=AgentRunStatus.RUNNING,
    )
    db_session.add(child_run)
    await db_session.flush()
    factory = make_session_factory(db_session)

    async def fake_latest(
        _session: AsyncSession,
        *,
        user_id: UUID,
        record_types: object,
    ) -> list[object]:
        assert user_id == user.id
        assert record_types is None
        return []

    monkeypatch.setattr(
        wearable_tools,
        "read_latest_wearable_observations",
        fake_latest,
    )
    wearable_tool = build_wearable_read_tool(factory)
    model = ToolLoopFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_wearable_data",
                        "args": {"request": {"view": "latest"}},
                        "id": "wearable-read-1",
                        "type": "tool_call",
                    }
                ],
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 3,
                    "total_tokens": 13,
                },
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ProfileAgentDecision",
                        "args": {"proposals": []},
                        "id": "structured-result-1",
                        "type": "tool_call",
                    }
                ],
                usage_metadata={
                    "input_tokens": 12,
                    "output_tokens": 2,
                    "total_tokens": 14,
                },
            ),
        ]
    )
    agent = build_profile_agent(
        model,
        InMemorySaver(),
        tools=(wearable_tool,),
        session_factory=factory,
    )
    stream_parts = [
        part
        async for part in agent.astream(
            {"messages": [HumanMessage(content="{}")]},
            config={
                "configurable": {"thread_id": str(child_run.id)},
                "metadata": {"stream_visibility": "internal"},
            },
            context=AgentContext(
                user_id=user.id,
                run_id=child_run.id,
                input_message_count=1,
            ),
            stream_mode=["custom", "values"],
            version="v2",
        )
    ]

    assert [part for part in stream_parts if part["type"] == "custom"] == []
    tool_calls = list(
        await db_session.scalars(
            select(AgentToolCall).where(AgentToolCall.agent_run_id == child_run.id)
        )
    )
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_name == "read_wearable_data"
    assert tool_calls[0].status == AgentToolCallStatus.COMPLETED
    assert tool_calls[0].arguments == {"request": {"view": "latest"}}


def test_wearable_tool_request_rejects_mixed_or_unbounded_queries() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="latest only"):
        WearableReadRequest(view="latest", start=now)
    with pytest.raises(ValidationError, match="range requires limit"):
        WearableReadRequest(
            view="range",
            record_types=[WearableRecordType.HEART_RATE],
            start=now,
            end=now,
        )

    tool = build_wearable_read_tool()
    assert "runtime" not in tool.args
    assert set(tool.args) == {"request"}
    properties = WearableReadRequest.model_json_schema()["properties"]
    assert all(
        "description" in properties[field]
        for field in (
            "view",
            "record_types",
            "start",
            "end",
            "observation_id",
            "limit",
        )
    )
    assert "已经同步到服务器的历史设备观测" in tool.description
