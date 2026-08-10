from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.chat import prepare_chat_run, recover_interrupted_chat_runs
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    AgentToolCall,
    AgentToolCallStatus,
    Message,
    MessageStatus,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    User,
)


async def test_startup_marks_interrupted_run_and_message_failed(
    db_session: AsyncSession,
) -> None:
    username = f"restart_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(ProactiveCareSettings(user_id=user.id))
    await db_session.flush()
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "服务重启测试",
    )
    assistant_message = await db_session.get(
        Message,
        prepared_run.assistant_message_id,
    )
    assert assistant_message is not None
    assistant_message.content = "已经生成的部分文字"
    tool_call = AgentToolCall(
        agent_run_id=prepared_run.run_id,
        tool_call_id="interrupted-tool-call",
        tool_name="web_search",
        model_turn_index=1,
        tool_call_index=1,
        arguments={"query": "服务重启测试"},
        status=AgentToolCallStatus.RUNNING,
    )
    db_session.add(tool_call)

    now = datetime.now(timezone.utc)
    care_task = ProactiveCareTask(
        user_id=user.id,
        kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        status=ProactiveCareTaskStatus.RUNNING,
        due_at=now,
        expires_at=now + timedelta(hours=12),
        lease_token=uuid4(),
        lease_expires_at=now + timedelta(minutes=20),
    )
    db_session.add(care_task)
    await db_session.flush()
    care_run = AgentRun(
        user_id=user.id,
        trigger_message_id=None,
        trigger_care_task_id=care_task.id,
        result_message_id=None,
        parent_run_id=None,
        agent_name="proactive_care_agent",
        model="deepseek-v4-pro",
        status=AgentRunStatus.RUNNING,
    )
    db_session.add(care_run)
    await db_session.flush()
    care_tool_call = AgentToolCall(
        agent_run_id=care_run.id,
        tool_call_id="care-tool-call",
        tool_name="proactive-care-test-tool",
        model_turn_index=1,
        tool_call_index=1,
        arguments={},
        status=AgentToolCallStatus.RUNNING,
    )
    db_session.add(care_tool_call)
    await db_session.commit()

    await recover_interrupted_chat_runs(db_session)

    run = await db_session.get(AgentRun, prepared_run.run_id)
    message = await db_session.get(Message, prepared_run.assistant_message_id)
    recovered_tool_call = await db_session.get(AgentToolCall, tool_call.id)
    assert run is not None
    assert run.status == AgentRunStatus.FAILED
    assert run.error_type == "ServerRestarted"
    assert run.finished_at is not None
    assert message is not None
    assert message.status == MessageStatus.FAILED
    assert message.content == "已经生成的部分文字"
    assert message.completed_at is not None
    assert recovered_tool_call is not None
    assert recovered_tool_call.status == AgentToolCallStatus.FAILED
    assert recovered_tool_call.error_type == "ServerRestarted"
    assert recovered_tool_call.finished_at is not None
    await db_session.refresh(care_run)
    await db_session.refresh(care_tool_call)
    assert care_run.status == AgentRunStatus.RUNNING
    assert care_tool_call.status == AgentToolCallStatus.RUNNING
