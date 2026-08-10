from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import pytest
from hindsight_client import Hindsight
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.companion_memory import CompanionMemory
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    CompanionMemoryCursor,
    Message,
    MessageRole,
    MessageStatus,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    User,
)


class FakeHindsight:
    def __init__(self) -> None:
        self.retain_calls: list[dict[str, Any]] = []
        self.recall_calls: list[dict[str, Any]] = []
        self.retain_error: Exception | None = None
        self.recall_error: Exception | None = None
        self.recall_texts: list[str] = []

    async def aretain(self, **kwargs: Any) -> SimpleNamespace:
        self.retain_calls.append(kwargs)
        if self.retain_error is not None:
            raise self.retain_error
        return SimpleNamespace(success=True, var_async=False, items_count=1)

    async def arecall(self, **kwargs: Any) -> SimpleNamespace:
        self.recall_calls.append(kwargs)
        if self.recall_error is not None:
            raise self.recall_error
        return SimpleNamespace(
            results=[SimpleNamespace(text=text) for text in self.recall_texts]
        )


def build_memory(
    client: FakeHindsight,
    mode: Literal["disabled", "shadow", "online"],
) -> CompanionMemory:
    return CompanionMemory(
        cast(Hindsight, client),
        mode=mode,
        retain_user_turns=3,
        recall_budget="low",
        recall_max_tokens=2_048,
    )


async def create_user(session: AsyncSession, prefix: str) -> User:
    username = f"{prefix[:15]}_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    session.add(user)
    await session.flush()
    return user


async def create_turn(
    session: AsyncSession,
    user_id: UUID,
    user_content: str,
    assistant_content: str,
    status: AgentRunStatus = AgentRunStatus.COMPLETED,
) -> tuple[Message, Message]:
    message_status = {
        AgentRunStatus.COMPLETED: MessageStatus.COMPLETED,
        AgentRunStatus.FAILED: MessageStatus.FAILED,
        AgentRunStatus.CANCELLED: MessageStatus.CANCELLED,
    }[status]
    user_message = Message(
        client_message_id=uuid4(),
        user_id=user_id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content=user_content,
    )
    assistant_message = Message(
        client_message_id=None,
        user_id=user_id,
        role=MessageRole.ASSISTANT,
        status=message_status,
        content=assistant_content,
        completed_at=datetime.now(timezone.utc),
    )
    session.add_all([user_message, assistant_message])
    await session.flush()
    session.add(
        AgentRun(
            user_id=user_id,
            trigger_message_id=user_message.id,
            result_message_id=assistant_message.id,
            parent_run_id=None,
            agent_name="core_agent",
            model="test-model",
            status=status,
            finished_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()
    return user_message, assistant_message


async def complete_existing_turn(
    session: AsyncSession,
    user_message: Message,
    assistant_content: str,
) -> Message:
    assistant_message = Message(
        client_message_id=None,
        user_id=user_message.user_id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content=assistant_content,
        completed_at=datetime.now(timezone.utc),
    )
    session.add(assistant_message)
    await session.flush()
    session.add(
        AgentRun(
            user_id=user_message.user_id,
            trigger_message_id=user_message.id,
            result_message_id=assistant_message.id,
            parent_run_id=None,
            agent_name="core_agent",
            model="test-model",
            status=AgentRunStatus.COMPLETED,
            finished_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()
    return assistant_message


async def create_current_message(
    session: AsyncSession,
    user_id: UUID,
    content: str,
) -> Message:
    message = Message(
        client_message_id=uuid4(),
        user_id=user_id,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETED,
        content=content,
    )
    session.add(message)
    await session.flush()
    return message


async def test_shadow_retains_offset_segments_and_advances_one_cursor(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "memory_segments")
    user_id = user.id
    user_1, _ = await create_turn(db_session, user.id, "U1", "A1")
    await create_turn(db_session, user.id, "U2", "A2")
    user_3, assistant_3 = await create_turn(db_session, user.id, "U3", "A3")
    user_4 = await create_current_message(db_session, user.id, "U4")
    await db_session.commit()
    user_1_id = user_1.id
    user_3_id = user_3.id
    assistant_3_id = assistant_3.id
    user_4_id = user_4.id
    client = FakeHindsight()
    memory = build_memory(client, "shadow")

    assert await memory.prepare_context(db_session, user_id, user_4_id) == ""
    assert (
        client.retain_calls[0]["content"]
        == "用户：U1\n助手：A1\n用户：U2\n助手：A2\n用户：U3"
    )
    assert client.retain_calls[0]["document_id"] == f"chat-segment-{user_3_id}"
    assert client.retain_calls[0]["update_mode"] == "replace"
    assert client.retain_calls[0]["retain_async"] is False
    assert client.retain_calls[0]["metadata"] == {
        "source": "antang_chat",
        "start_message_id": str(user_1_id),
        "through_message_id": str(user_3_id),
    }

    cursor = await db_session.get(CompanionMemoryCursor, user_id)
    assert cursor is not None
    assert cursor.through_message_id == user_3_id

    user_4 = await db_session.get(Message, user_4_id)
    assert user_4 is not None
    await complete_existing_turn(db_session, user_4, "A4")
    await create_turn(db_session, user_id, "U5", "A5")
    user_6, _ = await create_turn(db_session, user_id, "U6", "A6")
    user_7 = await create_current_message(db_session, user_id, "U7")
    await db_session.commit()
    user_6_id = user_6.id
    user_7_id = user_7.id

    assert await memory.prepare_context(db_session, user_id, user_7_id) == ""
    assert client.retain_calls[1]["content"] == (
        "助手：A3\n用户：U4\n助手：A4\n用户：U5\n助手：A5\n用户：U6"
    )
    assert client.retain_calls[1]["metadata"]["start_message_id"] == str(assistant_3_id)
    await db_session.refresh(cursor)
    assert cursor.through_message_id == user_6_id
    assert client.recall_calls == []


async def test_online_retain_then_recalls_with_previous_turn_and_current_message(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "memory_online")
    await create_turn(db_session, user.id, "我最近睡不好", "你最担心什么？")
    await create_turn(db_session, user.id, "夜里醒来", "醒来时会想到低血糖吗？")
    user_3, _ = await create_turn(db_session, user.id, "会", "这种担心很频繁吗？")
    current = await create_current_message(db_session, user.id, "对")
    await db_session.commit()
    user_id = user.id
    user_3_id = user_3.id
    current_id = current.id
    client = FakeHindsight()
    client.recall_texts = ["用户经常担心夜间低血糖"]

    context = await build_memory(client, "online").prepare_context(
        db_session,
        user_id,
        current_id,
    )

    assert context == "- 用户经常担心夜间低血糖"
    assert client.retain_calls[0]["document_id"] == f"chat-segment-{user_3_id}"
    assert client.recall_calls[0]["query"] == (
        "用户：会\n助手：这种担心很频繁吗？\n当前用户：对"
    )
    assert client.recall_calls[0]["types"] == [
        "world",
        "experience",
        "observation",
    ]
    assert client.recall_calls[0]["prefer_observations"] is True
    assert client.recall_calls[0]["include_chunks"] is False


@pytest.mark.parametrize("mode", ["shadow", "online"])
async def test_retain_failure_only_allows_shadow_chat_to_continue(
    db_session: AsyncSession,
    mode: Literal["shadow", "online"],
) -> None:
    user = await create_user(db_session, f"memory_retain_failure_{mode}")
    await create_turn(db_session, user.id, "U1", "A1")
    await create_turn(db_session, user.id, "U2", "A2")
    await create_turn(db_session, user.id, "U3", "A3")
    current = await create_current_message(db_session, user.id, "U4")
    await db_session.commit()
    user_id = user.id
    current_id = current.id
    client = FakeHindsight()
    client.retain_error = RuntimeError("remote retain failed")
    memory = build_memory(client, mode)

    if mode == "shadow":
        assert await memory.prepare_context(db_session, user_id, current_id) == ""
    else:
        with pytest.raises(RuntimeError, match="remote retain failed"):
            await memory.prepare_context(db_session, user_id, current_id)

    assert await db_session.get(CompanionMemoryCursor, user_id) is None


async def test_online_recall_failure_keeps_successful_retain_cursor(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "memory_recall_failure")
    await create_turn(db_session, user.id, "U1", "A1")
    await create_turn(db_session, user.id, "U2", "A2")
    user_3, _ = await create_turn(db_session, user.id, "U3", "A3")
    current = await create_current_message(db_session, user.id, "U4")
    await db_session.commit()
    user_id = user.id
    user_3_id = user_3.id
    current_id = current.id
    client = FakeHindsight()
    client.recall_error = RuntimeError("remote recall failed")

    with pytest.raises(RuntimeError, match="remote recall failed"):
        await build_memory(client, "online").prepare_context(
            db_session,
            user_id,
            current_id,
        )

    cursor = await db_session.get(CompanionMemoryCursor, user_id)
    assert cursor is not None
    assert cursor.through_message_id == user_3_id


async def test_failed_and_cancelled_assistant_messages_are_not_retained(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "memory_skipped_turns")
    await create_turn(db_session, user.id, "保留一", "回答一")
    await create_turn(
        db_session,
        user.id,
        "失败内容",
        "失败回答",
        AgentRunStatus.FAILED,
    )
    await create_turn(
        db_session,
        user.id,
        "取消内容",
        "取消回答",
        AgentRunStatus.CANCELLED,
    )
    await create_turn(db_session, user.id, "保留二", "回答二")
    await create_turn(db_session, user.id, "保留三", "回答三")
    current = await create_current_message(db_session, user.id, "当前消息")
    await db_session.commit()
    user_id = user.id
    current_id = current.id
    client = FakeHindsight()

    await build_memory(client, "shadow").prepare_context(
        db_session,
        user_id,
        current_id,
    )

    retained = client.retain_calls[0]["content"]
    assert "失败回答" not in retained
    assert "取消回答" not in retained
    assert retained == (
        "用户：保留一\n助手：回答一\n用户：失败内容\n用户：取消内容\n"
        "用户：保留二\n助手：回答二\n用户：保留三"
    )


async def test_active_message_keeps_the_meaning_of_a_short_reply(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "memory_proactive_reply")
    now = datetime.now(timezone.utc)
    task = ProactiveCareTask(
        user_id=user.id,
        kind=ProactiveCareTaskKind.ROUTINE_CHECK_IN,
        status=ProactiveCareTaskStatus.COMPLETED,
        due_at=now,
        expires_at=now + timedelta(days=365),
        finished_at=now,
    )
    proactive_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="这两天夜里还会担心低血糖吗？",
        completed_at=now,
    )
    db_session.add_all([task, proactive_message])
    await db_session.flush()
    db_session.add(
        AgentRun(
            user_id=user.id,
            trigger_message_id=None,
            trigger_care_task_id=task.id,
            result_message_id=proactive_message.id,
            parent_run_id=None,
            agent_name="proactive_care_agent",
            model="test-model",
            status=AgentRunStatus.COMPLETED,
            finished_at=now,
        )
    )
    await create_turn(db_session, user.id, "会", "最明显是在什么时候？")
    await create_turn(db_session, user.id, "睡前", "那时你通常会怎么做？")
    third_user, _ = await create_turn(
        db_session,
        user.id,
        "会反复测",
        "反复测完会安心一点吗？",
    )
    current = await create_current_message(db_session, user.id, "对")
    await db_session.commit()
    proactive_message_id = proactive_message.id
    third_user_id = third_user.id
    client = FakeHindsight()

    await build_memory(client, "shadow").prepare_context(
        db_session,
        user.id,
        current.id,
    )

    assert client.retain_calls[0]["content"] == (
        "助手：这两天夜里还会担心低血糖吗？\n"
        "用户：会\n助手：最明显是在什么时候？\n"
        "用户：睡前\n助手：那时你通常会怎么做？\n用户：会反复测"
    )
    assert client.retain_calls[0]["metadata"]["start_message_id"] == str(
        proactive_message_id
    )
    assert client.retain_calls[0]["metadata"]["through_message_id"] == str(
        third_user_id
    )


async def test_online_recall_uses_the_active_message_before_a_short_reply(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "memory_proactive_recall")
    await create_turn(db_session, user.id, "U1", "A1")
    await create_turn(db_session, user.id, "U2", "A2")
    third_user, _ = await create_turn(db_session, user.id, "U3", "A3")
    await db_session.flush()
    db_session.add(
        CompanionMemoryCursor(
            user_id=user.id,
            through_message_id=third_user.id,
        )
    )
    proactive_message = Message(
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="今天感觉比昨天好一点了吗？",
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(proactive_message)
    await db_session.flush()
    current = await create_current_message(db_session, user.id, "对")
    await db_session.commit()
    client = FakeHindsight()

    await build_memory(client, "online").prepare_context(
        db_session,
        user.id,
        current.id,
    )

    assert client.recall_calls[0]["query"].endswith(
        "助手：今天感觉比昨天好一点了吗？\n当前用户：对"
    )


async def test_proactive_recall_uses_the_same_user_bank(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "memory_proactive_context")
    previous_user, _ = await create_turn(db_session, user.id, "最近睡不好", "记得了")
    db_session.add(
        CompanionMemoryCursor(
            user_id=user.id,
            through_message_id=previous_user.id,
        )
    )
    await db_session.commit()
    user_id = user.id
    client = FakeHindsight()
    client.recall_texts = ["用户最近提过夜间睡眠不稳"]

    context = await build_memory(client, "online").recall_for_proactive(
        db_session,
        user_id,
        query="日常关怀：了解用户最近的状态",
        query_timestamp=datetime.now(timezone.utc),
    )

    assert context == "- 用户最近提过夜间睡眠不稳"
    assert client.recall_calls[0]["bank_id"] == f"antang-user-{user_id}"
    assert client.recall_calls[0]["query"] == "日常关怀：了解用户最近的状态"


@pytest.mark.parametrize("mode", ["disabled", "shadow"])
async def test_proactive_recall_is_off_outside_online_mode(
    db_session: AsyncSession,
    mode: Literal["disabled", "shadow"],
) -> None:
    user = await create_user(db_session, f"proactive_recall_{mode}")
    user_id = user.id
    await db_session.commit()
    client = FakeHindsight()

    context = await build_memory(client, mode).recall_for_proactive(
        db_session,
        user_id,
        query="日常关怀",
        query_timestamp=datetime.now(timezone.utc),
    )

    assert context == ""
    assert client.recall_calls == []


async def test_proactive_online_recall_failure_is_not_hidden(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "proactive_recall_failure")
    previous_user, _ = await create_turn(db_session, user.id, "最近睡不好", "记得了")
    user_id = user.id
    db_session.add(
        CompanionMemoryCursor(
            user_id=user_id,
            through_message_id=previous_user.id,
        )
    )
    await db_session.commit()
    client = FakeHindsight()
    client.recall_error = RuntimeError("remote recall failed")

    with pytest.raises(RuntimeError, match="remote recall failed"):
        await build_memory(client, "online").recall_for_proactive(
            db_session,
            user_id,
            query="日常关怀",
            query_timestamp=datetime.now(timezone.utc),
        )


async def test_companion_memory_never_reads_another_users_turns(
    db_session: AsyncSession,
) -> None:
    first = await create_user(db_session, "memory_first_user")
    second = await create_user(db_session, "memory_second_user")
    for number in range(1, 4):
        await create_turn(db_session, first.id, f"甲{number}", f"甲答{number}")
        await create_turn(db_session, second.id, f"乙{number}", f"乙答{number}")
    current = await create_current_message(db_session, first.id, "甲当前")
    await db_session.commit()
    first_id = first.id
    second_id = second.id
    current_id = current.id
    client = FakeHindsight()

    await build_memory(client, "shadow").prepare_context(
        db_session,
        first_id,
        current_id,
    )

    assert client.retain_calls[0]["bank_id"] == f"antang-user-{first_id}"
    assert "乙" not in client.retain_calls[0]["content"]
    assert await db_session.get(CompanionMemoryCursor, second_id) is None
