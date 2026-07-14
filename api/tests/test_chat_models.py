from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    Message,
    MessageRole,
    MessageStatus,
    User,
)


async def test_message_and_agent_run_lifecycle(db_session: AsyncSession) -> None:
    """聊天消息和 Core Agent 运行记录能够完整写入 PostgreSQL。"""

    username = f"chat_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    user_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="我今天有点担心低血糖。",
        completed_at=datetime.now(timezone.utc),
    )
    assistant_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.GENERATING,
        content="",
    )
    db_session.add_all([user_message, assistant_message])
    await db_session.flush()

    run = AgentRun(
        user_id=user.id,
        trigger_message_id=user_message.id,
        result_message_id=assistant_message.id,
        parent_run_id=None,
        agent_name="core",
        model="deepseek-v4-pro",
        status=AgentRunStatus.RUNNING,
    )
    db_session.add(run)
    await db_session.flush()

    # PostgreSQL 18 为消息和运行记录生成按时间排列的 UUIDv7。
    assert user_message.id.version == 7
    assert assistant_message.id.version == 7
    assert run.id.version == 7

    finished_at = datetime.now(timezone.utc)
    assistant_message.content = "我在这里。我们可以一起看看是什么让你担心。"
    assistant_message.status = MessageStatus.COMPLETED
    assistant_message.completed_at = finished_at
    run.status = AgentRunStatus.COMPLETED
    run.input_tokens = 120
    run.output_tokens = 32
    run.finished_at = finished_at
    await db_session.commit()

    saved_run = await db_session.get(AgentRun, run.id)
    saved_message = await db_session.get(Message, assistant_message.id)

    assert saved_run is not None
    assert saved_run.status == AgentRunStatus.COMPLETED
    assert saved_run.trigger_message_id == user_message.id
    assert saved_run.result_message_id == assistant_message.id
    assert saved_message is not None
    assert saved_message.status == MessageStatus.COMPLETED
    assert saved_message.content == "我在这里。我们可以一起看看是什么让你担心。"


async def test_deleting_user_cascades_chat_data(db_session: AsyncSession) -> None:
    """删除用户时，数据库会同步删除其消息和 Agent 运行记录。"""

    username = f"cascade_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()

    user_message = Message(
        user_id=user.id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content="测试消息",
        completed_at=datetime.now(timezone.utc),
    )
    assistant_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.GENERATING,
        content="",
    )
    db_session.add_all([user_message, assistant_message])
    await db_session.flush()

    run = AgentRun(
        user_id=user.id,
        trigger_message_id=user_message.id,
        result_message_id=assistant_message.id,
        parent_run_id=None,
        agent_name="core",
        model="deepseek-v4-pro",
        status=AgentRunStatus.RUNNING,
    )
    db_session.add(run)
    await db_session.commit()

    user_id = user.id
    message_ids = [user_message.id, assistant_message.id]
    run_id = run.id

    await db_session.delete(user)
    await db_session.commit()

    remaining_messages = await db_session.scalars(
        select(Message).where(Message.id.in_(message_ids))
    )
    remaining_run_id = await db_session.scalar(
        select(AgentRun.id).where(AgentRun.id == run_id)
    )

    assert await db_session.get(User, user_id) is None
    assert list(remaining_messages) == []
    assert remaining_run_id is None
