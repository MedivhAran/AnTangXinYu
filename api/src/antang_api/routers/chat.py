from collections.abc import AsyncIterator, Sequence
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import BaseTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.core import CoreAgentGraph
from antang_api.chat import (
    ActiveAgentRunError,
    DuplicateClientMessageError,
    prepare_chat_run,
    stream_chat_run,
)
from antang_api.database import get_session
from antang_api.models import Message, User
from antang_api.routers.auth import get_current_user
from antang_api.schemas.chat import (
    ChatMessageResponse,
    MessageHistoryResponse,
    SendMessageRequest,
    encode_stream_event,
)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.post("/messages", response_model=None)
async def send_message(
    request: SendMessageRequest,
    http_request: Request,  # FastAPI 原生的请求对象（包含 app 引用）
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse | JSONResponse:
    """保存用户消息，并以 NDJSON 持续返回 Core Agent 的执行事件。"""

    model = cast(ChatAnthropic, http_request.app.state.chat_model)
    agent = cast(CoreAgentGraph, http_request.app.state.core_agent)
    tools = cast(Sequence[BaseTool], http_request.app.state.core_tools)

    try:
        prepared_run = await prepare_chat_run(
            session=session,
            user_id=user.id,
            client_message_id=request.client_message_id,
            content=request.content,
        )

    except DuplicateClientMessageError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "duplicate_client_message",
                "message": "这条消息已经提交",
            },
        )
    except ActiveAgentRunError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "active_agent_run",
                "message": "上一条消息仍在处理中",
            },
        )

    async def ndjson_stream() -> AsyncIterator[str]:
        async for event in stream_chat_run(
            session=session,
            model=model,
            agent=agent,
            tools=tools,
            user_id=user.id,
            prepared_run=prepared_run,
        ):
            yield encode_stream_event(event)

    return StreamingResponse(
        ndjson_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/messages", response_model=MessageHistoryResponse)
async def get_message_history(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    before: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessageHistoryResponse:
    """使用消息 UUIDv7 游标读取一页历史，并按时间升序返回。"""

    query = select(Message).where(Message.user_id == user.id)

    if before is not None:
        query = query.where(Message.id < before)

    newest_first = list(
        await session.scalars(
            query.order_by(Message.id.desc()).limit(limit + 1),
        )
    )
    has_more = len(newest_first) > limit
    page = newest_first[:limit]
    next_before = page[-1].id if has_more else None

    return MessageHistoryResponse(
        messages=[ChatMessageResponse.model_validate(message) for message in reversed(page)],
        next_before=next_before,
    )
