from collections.abc import AsyncIterator, Sequence
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import BaseTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.core import CoreAgentGraph
from antang_api.chat import (
    ActiveAgentRunError,
    ChatRetryError,
    DuplicateClientMessageError,
    PreparedChatRun,
    prepare_chat_retry,
    prepare_chat_run,
    stream_chat_run,
)
from antang_api.companion_memory import CompanionMemory
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


def _stream_response(
    http_request: Request,
    session: AsyncSession,
    user: User,
    prepared_run: PreparedChatRun,
) -> StreamingResponse:
    model = cast(ChatAnthropic, http_request.app.state.chat_model)
    agent = cast(CoreAgentGraph, http_request.app.state.core_agent)
    tools = cast(Sequence[BaseTool], http_request.app.state.core_tools)
    companion_memory = cast(
        CompanionMemory,
        http_request.app.state.companion_memory,
    )

    async def ndjson_stream() -> AsyncIterator[str]:
        async for event in stream_chat_run(
            session=session,
            model=model,
            agent=agent,
            tools=tools,
            companion_memory=companion_memory,
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


@router.post("/messages", response_model=None)
async def send_message(
    request: SendMessageRequest,
    http_request: Request,  # FastAPI 原生的请求对象（包含 app 引用）
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse | JSONResponse:
    """保存用户消息，并以 NDJSON 持续返回 Core Agent 的执行事件。"""

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

    return _stream_response(http_request, session, user, prepared_run)


@router.post("/messages/{failed_assistant_message_id}/retry", response_model=None)
async def retry_message(
    failed_assistant_message_id: UUID,
    http_request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse | JSONResponse:
    """重试失败或取消的回答，不创建第二条用户消息。"""

    try:
        prepared_run = await prepare_chat_retry(
            session,
            user.id,
            failed_assistant_message_id,
        )
    except ChatRetryError as error:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if error.code == "retry_message_not_found"
            else status.HTTP_409_CONFLICT
        )
        return JSONResponse(
            status_code=status_code,
            content={"code": error.code, "message": str(error)},
        )
    except ActiveAgentRunError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "active_agent_run",
                "message": "上一条消息仍在处理中",
            },
        )

    return _stream_response(http_request, session, user, prepared_run)


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
        messages=[
            ChatMessageResponse.model_validate(message) for message in reversed(page)
        ],
        next_before=next_before,
    )


@router.get(
    "/messages/{message_id}/window",
    response_model=MessageHistoryResponse,
)
async def get_message_window(
    message_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MessageHistoryResponse:
    """返回目标消息前后各最多 25 条消息，供旧通知准确定位。"""

    target = await session.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.user_id == user.id,
        )
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到聊天消息",
        )

    previous = list(
        reversed(
            list(
                await session.scalars(
                    select(Message)
                    .where(
                        Message.user_id == user.id,
                        Message.id < target.id,
                    )
                    .order_by(Message.id.desc())
                    .limit(25)
                )
            )
        )
    )
    following = list(
        await session.scalars(
            select(Message)
            .where(
                Message.user_id == user.id,
                Message.id > target.id,
            )
            .order_by(Message.id)
            .limit(25)
        )
    )
    return MessageHistoryResponse(
        messages=[
            ChatMessageResponse.model_validate(message)
            for message in (*previous, target, *following)
        ],
        next_before=None,
    )
