import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api import chat
from antang_api.chat import prepare_chat_run
from antang_api.context.builder import ChatContext
from antang_api.context.preparation import PreparedChatContext
from antang_api.main import app
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    Message,
    MessageAttachment,
    MessageAttachmentKind,
    MessageRole,
    MessageStatus,
)


class RouteFakeAgent:
    """为聊天路由返回一次完整的 LangGraph v2 响应。"""

    async def astream(
        self,
        _input: dict[str, Any],
        **_kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        yield {
            "type": "messages",
            "data": (
                AIMessageChunk(content="我在这里。"),
                {
                    "langgraph_node": "model",
                    "langgraph_step": 0,
                    "stream_visibility": "user",
                },
            ),
        }
        yield {
            "type": "values",
            "data": {
                "messages": [
                    HumanMessage(content="有点担心"),
                    AIMessage(
                        content="我在这里。",
                        usage_metadata={
                            "input_tokens": 20,
                            "output_tokens": 5,
                            "total_tokens": 25,
                        },
                    ),
                ]
            },
        }


class RouteFakeCompanionMemory:
    async def prepare_context(self, *_args: Any) -> str:
        return ""


async def register(client: AsyncClient, prefix: str) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "username": f"{prefix}_{uuid4().hex[:12]}",
            "password": "correct-password",
        },
    )
    assert response.status_code == 201
    return response.json()


def auth_headers(auth: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


async def test_send_message_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/chat/messages",
        json={"client_message_id": str(uuid4()), "content": "你好"},
    )

    assert response.status_code == 401


async def test_upload_report_attachment(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    auth = await register(client, "route_attachment")

    response = await client.post(
        "/api/v1/chat/attachments",
        headers=auth_headers(auth),
        data={"kind": "report"},
        files={"file": ("血糖报告.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "血糖报告.pdf"
    assert body["kind"] == "report"
    attachment = await db_session.get(MessageAttachment, UUID(body["id"]))
    assert attachment is not None
    assert attachment.kind == MessageAttachmentKind.REPORT
    assert attachment.data == b"%PDF-1.4"

    content = await client.get(
        f"/api/v1/chat/attachments/{body['id']}/content",
        headers=auth_headers(auth),
    )
    assert content.status_code == 200
    assert content.headers["content-type"] == "application/pdf"
    assert content.content == b"%PDF-1.4"


async def test_send_message_rejects_content_over_limit(client: AsyncClient) -> None:
    auth = await register(client, "route_content_limit")

    response = await client.post(
        "/api/v1/chat/messages",
        headers=auth_headers(auth),
        json={
            "client_message_id": str(uuid4()),
            "content": "a" * 2001,
        },
    )

    assert response.status_code == 422


async def test_send_message_streams_ndjson_and_saves_answer(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = await register(client, "route_stream")
    app.state.chat_model = object()
    app.state.core_agent = RouteFakeAgent()
    app.state.core_tools = ()
    app.state.companion_memory = RouteFakeCompanionMemory()
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(
            return_value=PreparedChatContext(
                context=ChatContext(
                    messages=(HumanMessage(content="有点担心"),),
                    summary_id=None,
                    summary_through_message_id=None,
                ),
                input_tokens=20,
                was_compacted=False,
                cleared_tool_result_count=0,
            )
        ),
    )

    response = await client.post(
        "/api/v1/chat/messages",
        headers=auth_headers(auth),
        json={
            "client_message_id": str(uuid4()),
            "content": "有点担心",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == [
        "message_started",
        "agent_activity",
        "text_delta",
        "message_completed",
    ]
    assert events[1]["phase"] == "thinking"
    assert events[2]["delta"] == "我在这里。"
    assert events[3]["input_tokens"] == 20
    assert events[3]["sources"] == []

    history = await client.get(
        "/api/v1/chat/messages",
        headers=auth_headers(auth),
    )
    assert history.status_code == 200
    messages = history.json()["messages"]
    assert [message["content"] for message in messages] == [
        "有点担心",
        "我在这里。",
    ]
    assert all(UUID(message["id"]) for message in messages)
    assert all(message["sources"] == [] for message in messages)

    saved_answer = await db_session.get(
        Message, UUID(events[0]["assistant_message_id"])
    )
    assert saved_answer is not None
    assert saved_answer.status == MessageStatus.COMPLETED


async def test_send_message_returns_stable_duplicate_and_active_codes(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    auth = await register(client, "route_conflict")
    user_id = UUID(auth["user"]["id"])
    app.state.chat_model = object()
    app.state.core_agent = object()
    app.state.core_tools = ()
    client_message_id = uuid4()
    await prepare_chat_run(
        db_session,
        user_id,
        client_message_id,
        "仍在运行",
    )

    duplicate = await client.post(
        "/api/v1/chat/messages",
        headers=auth_headers(auth),
        json={
            "client_message_id": str(client_message_id),
            "content": "重复提交",
        },
    )
    active = await client.post(
        "/api/v1/chat/messages",
        headers=auth_headers(auth),
        json={
            "client_message_id": str(uuid4()),
            "content": "并发提交",
        },
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "duplicate_client_message"
    assert active.status_code == 409
    assert active.json()["code"] == "active_agent_run"


async def test_retry_failed_message_streams_a_new_answer_without_new_user_message(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = await register(client, "route_retry")
    user_id = UUID(auth["user"]["id"])
    original = await prepare_chat_run(
        db_session,
        user_id,
        uuid4(),
        "有点担心",
    )
    failed_message = await db_session.get(Message, original.assistant_message_id)
    failed_run = await db_session.get(AgentRun, original.run_id)
    assert failed_message is not None
    assert failed_run is not None
    failed_message.status = MessageStatus.FAILED
    failed_message.content = "没有生成完"
    failed_run.status = AgentRunStatus.FAILED
    await db_session.commit()

    app.state.chat_model = object()
    app.state.core_agent = RouteFakeAgent()
    app.state.core_tools = ()
    app.state.companion_memory = RouteFakeCompanionMemory()
    monkeypatch.setattr(
        chat,
        "prepare_chat_context",
        AsyncMock(
            return_value=PreparedChatContext(
                context=ChatContext(
                    messages=(HumanMessage(content="有点担心"),),
                    summary_id=None,
                    summary_through_message_id=None,
                ),
                input_tokens=20,
                was_compacted=False,
                cleared_tool_result_count=0,
            )
        ),
    )

    response = await client.post(
        f"/api/v1/chat/messages/{original.assistant_message_id}/retry",
        headers=auth_headers(auth),
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0]["type"] == "message_started"
    assert events[0]["user_message_id"] == str(original.user_message_id)
    assert events[0]["assistant_message_id"] != str(original.assistant_message_id)
    history = await client.get(
        "/api/v1/chat/messages",
        headers=auth_headers(auth),
    )
    assert [message["content"] for message in history.json()["messages"]] == [
        "有点担心",
        "我在这里。",
    ]


async def test_retry_message_returns_stable_not_found_and_conflict_codes(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    auth = await register(client, "route_retry_errors")
    missing = await client.post(
        f"/api/v1/chat/messages/{uuid4()}/retry",
        headers=auth_headers(auth),
    )

    completed_message = Message(
        client_message_id=None,
        user_id=UUID(auth["user"]["id"]),
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="已经完成",
    )
    db_session.add(completed_message)
    await db_session.commit()
    conflict = await client.post(
        f"/api/v1/chat/messages/{completed_message.id}/retry",
        headers=auth_headers(auth),
    )

    assert missing.status_code == 404
    assert missing.json()["code"] == "retry_message_not_found"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "chat_retry_not_allowed"


async def test_message_history_uses_before_cursor_and_includes_all_statuses(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    auth = await register(client, "route_history")
    user_id = UUID(auth["user"]["id"])
    now = datetime.now(timezone.utc)
    message_specs = [
        (MessageRole.USER, MessageStatus.COMPLETED, "一", []),
        (MessageRole.ASSISTANT, MessageStatus.GENERATING, "二", []),
        (
            MessageRole.ASSISTANT,
            MessageStatus.COMPLETED,
            "三[S1]",
            [
                {
                    "source_id": "S1",
                    "title": "来源",
                    "url": "https://example.com/source",
                }
            ],
        ),
        (MessageRole.ASSISTANT, MessageStatus.FAILED, "四", []),
        (MessageRole.ASSISTANT, MessageStatus.CANCELLED, "五", []),
    ]
    saved_messages: list[Message] = []

    for role, message_status, content, sources in message_specs:
        message = Message(
            client_message_id=uuid4() if role == MessageRole.USER else None,
            user_id=user_id,
            role=role,
            status=message_status,
            content=content,
            sources=sources,
            completed_at=(None if message_status == MessageStatus.GENERATING else now),
        )
        db_session.add(message)
        await db_session.flush()
        saved_messages.append(message)

    await db_session.commit()

    first_page = await client.get(
        "/api/v1/chat/messages?limit=2",
        headers=auth_headers(auth),
    )
    first_body = first_page.json()
    assert first_page.status_code == 200
    assert [message["content"] for message in first_body["messages"]] == [
        "四",
        "五",
    ]
    assert first_body["next_before"] == str(saved_messages[3].id)

    second_page = await client.get(
        "/api/v1/chat/messages",
        headers=auth_headers(auth),
        params={"limit": 2, "before": first_body["next_before"]},
    )
    second_body = second_page.json()
    assert [message["content"] for message in second_body["messages"]] == [
        "二",
        "三[S1]",
    ]
    assert second_body["messages"][1]["sources"] == [
        {
            "source_id": "S1",
            "title": "来源",
            "url": "https://example.com/source",
        }
    ]

    third_page = await client.get(
        "/api/v1/chat/messages",
        headers=auth_headers(auth),
        params={"limit": 2, "before": second_body["next_before"]},
    )
    assert [message["content"] for message in third_page.json()["messages"]] == ["一"]
    assert third_page.json()["next_before"] is None

    invalid_limit = await client.get(
        "/api/v1/chat/messages?limit=101",
        headers=auth_headers(auth),
    )
    assert invalid_limit.status_code == 422
