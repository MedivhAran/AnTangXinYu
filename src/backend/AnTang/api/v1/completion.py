import json
import re
import time
from fastapi import APIRouter, Depends
from loguru import logger

from AnTang.core.agents.antang_agent import AnTangAgent
from AnTang.core.agents.general_agent import AgentConfig
from AnTang.api.services.history import HistoryService
from AnTang.api.services.dialog import DialogService
from AnTang.database.dao.history import HistoryDao
from AnTang.schemas.antang_analyzer import DialogState
from AnTang.api.responses.streaming import WatchedStreamingResponse
from AnTang.api.services.user import UserPayload, get_login_user
from AnTang.schemas.completion import CompletionReq
from AnTang.utils.common import count_tokens_usage
from AnTang.utils.contexts import set_user_id_context, set_agent_name_context

router = APIRouter(tags=["Completion"])


async def _load_dialog_state_from_history(dialog_id: str) -> DialogState | None:
    """从最近的 history events 中取最后一条 antang_dialog_state hidden event。"""
    try:
        recent_records = await HistoryDao.select_history_from_time(dialog_id, k=20)
        for record in reversed(recent_records):
            for event in reversed(record.events or []):
                event_data = event.get("data") or {}
                if event_data.get("event_type") == "antang_dialog_state":
                    return DialogState(**event_data.get("details", {}))
    except Exception:
        pass
    return None


IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*]\((https?://[^)\s]+)\)")


def _extract_main_chat_tool_result(event: dict) -> str | None:
    """把需要进入主聊天的工具结果提取成回复片段。"""
    if event.get("type") != "event":
        return None

    event_data = event.get("data") or {}
    title = str(event_data.get("title") or "")
    message = str(event_data.get("message") or "").strip()
    status = event_data.get("status")

    # 图片生成工具的结果应作为助手主回复展示，而不是只留在工具事件里。
    if status == "END" and "图片生成" in title and IMAGE_MARKDOWN_RE.search(message):
        return f"\n\n{message}\n\n"

    return None


def _hide_main_chat_tool_result_in_event(event: dict) -> dict:
    """图片已进入主回复时，事件卡片只保留状态说明，避免重复显示大图。"""
    event_data = event.get("data") or {}
    return {
        **event,
        "data": {
            **event_data,
            "message": "图片已经生成完毕，已在主回复中展示。",
        },
    }


def _build_response_chunk(chunk: str, accumulated: str) -> dict:
    return {
        "type": "response_chunk",
        "timestamp": time.time(),
        "data": {
            "chunk": chunk,
            "accumulated": accumulated,
        },
    }


# 最顶层的对话接口，负责处理输入输出和Agent的调用，Agent的具体实现细节在各自的类里实现，这里不关心。
@router.post("/completion", description="对话接口")
async def completion(*, req: CompletionReq, login_user: UserPayload = Depends(get_login_user)):
    """
    实时对话接口（SSE流式）
    """

    # 根据 dialog_id 异步加载 runtime_config
    runtime_config = await DialogService.get_dialog_runtime_config(req.dialog_id)

    # 从 runtime_config 中获取 agent_config，并注入 user_id
    db_config = runtime_config["agent"]
    agent_config = AgentConfig(**db_config)
    agent_config.user_id = login_user.user_id

    # 设置上下文信息
    set_user_id_context(login_user.user_id)
    set_agent_name_context(agent_config.name)

    chat_agent = AnTangAgent(agent_config)
    await chat_agent.init_agent()

    # 输入处理
    raw_input = req.user_input

    # 获取截止到上次压缩之后的历史消息原文，作为短期记忆
    short_history = await HistoryService.get_short_term_messages(req.dialog_id, login_user.user_id)

    # 获取上次压缩的历史消息摘要，作为压缩记忆
    history_summary = await DialogService.get_dialog_history_summary(req.dialog_id)

    # 事件 & 流式响应
    assistant_events: list = []

    previous_dialog_state = await _load_dialog_state_from_history(req.dialog_id)

    # 定义函数还没执行！负责流式生成回复、收集事件，并在结束后落库。
    async def stream():
        response_content = ""
        try:
            async for event in chat_agent.astream(
                user_input=raw_input,
                short_history=short_history,
                history_summary=history_summary,
                glucose_context=req.glucose_context,
                file_url=req.file_url,
                file_name=req.file_name,
                previous_dialog_state=previous_dialog_state,
                dialog_id=req.dialog_id,
            ):
                main_chat_chunk = None
                if event.get("type") == "response_chunk":
                    chunk = event["data"].get("chunk", "")
                    response_content += chunk
                else:
                    # hidden event 只落库，不推给前端
                    is_hidden = (event.get("data") or {}).get("hidden", False)
                    main_chat_chunk = _extract_main_chat_tool_result(event)
                    if main_chat_chunk:
                        event = _hide_main_chat_tool_result_in_event(event)
                    assistant_events.append(event)
                    if is_hidden:
                        continue

                yield f"data: {json.dumps(event)}\n\n"
                if main_chat_chunk:
                    response_content += main_chat_chunk
                    yield f"data: {json.dumps(_build_response_chunk(main_chat_chunk, response_content))}\n\n"

        finally:
            try:
                await chat_agent.finalize_turn(
                    user_input=raw_input,
                    assistant_response=response_content,
                )
                # ↑ 内部 asyncio.create_task 后台进行长期记忆写入，立即返回

            except Exception as err:
                logger.warning(f"Finalize completion turn failed: {err}")

            await HistoryService.save_chat_history(
                role="assistant",
                content=response_content,
                events=assistant_events,
                dialog_id=req.dialog_id,
                token_usage=count_tokens_usage(response_content),
            )
            # ↑ 存到mysql 的 history 表

            # 如果token累积超过阈值就进行上下文压缩
            await DialogService.update_dialog_summary(
                dialog_id=req.dialog_id,
                user_id=login_user.user_id,
            )

    # 在推流给前端之前，先将用户的文件和血糖数据入库
    user_events = []
    if req.file_url:
        user_events.append(
            {
                "type": "attachment",
                "timestamp": time.time(),
                "data": {
                    "file_url": req.file_url,
                    "file_name": req.file_name or "",
                },
            }
        )
    if req.glucose_context:
        user_events.append(
            {
                "type": "glucose_context",
                "timestamp": time.time(),
                "data": req.glucose_context.model_dump(),
            }
        )
    # 用户的文字输入和事件入库
    await HistoryService.save_chat_history(
        role="user",
        content=raw_input,
        events=user_events,
        dialog_id=req.dialog_id,
        token_usage=count_tokens_usage(raw_input),
    )

    # 将上方的 async def stream() 包装为监控式流式响应返回给前端。
    return WatchedStreamingResponse(content=stream(), media_type="text/event-stream")
