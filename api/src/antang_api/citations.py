import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import AgentToolCall, AgentToolCallStatus
from antang_api.schemas.chat import ChatSource

CITATION_PATTERN = re.compile(r"\[S(\d+)\]")
VISIBLE_TURN_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class VisibleCitationTurn:
    """一段用户可见模型文字，以及它产生时已经完成的工具调用。"""

    content: str
    available_tool_call_ids: frozenset[str]


class CitationValidationError(RuntimeError):
    """联网工具记录或回答中的来源引用不满足固定规则。"""


class _SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str
    url: str
    snippet: str
    score: float

    @field_validator("title", "snippet")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("搜索结果文字必须非空且已规范化")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            value != value.strip()
            or parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
        ):
            raise ValueError("搜索结果 URL 必须是已规范化的 http(s) URL")
        return value


class _SearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    results: list[_SearchResult]


class _FetchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: str
    title: str
    url: str
    content: str


def _json_object_from_tool_result(
    result: str | list[str | dict[str, Any]] | None,
    *,
    tool_name: str,
) -> dict[str, Any]:
    """严格还原 LangChain 存入 JSONB 的模型可见工具结果。"""

    serialized: str
    if isinstance(result, str):
        serialized = result
    elif isinstance(result, list) and len(result) == 1:
        block = result[0]
        if isinstance(block, str):
            serialized = block
        elif (
            isinstance(block, dict)
            and set(block) == {"type", "text"}
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            serialized = block["text"]
        else:
            raise CitationValidationError(f"{tool_name} 的工具结果不是受支持的文本块")
    else:
        raise CitationValidationError(f"{tool_name} 的工具结果不是 JSON 文本")

    try:
        parsed = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise CitationValidationError(f"{tool_name} 的工具结果不是有效 JSON") from error

    if not isinstance(parsed, dict):
        raise CitationValidationError(f"{tool_name} 的工具结果必须是 JSON 对象")

    return parsed


def _validate_search_result(record: AgentToolCall) -> None:
    parsed = _json_object_from_tool_result(record.result, tool_name="web_search")
    try:
        output = _SearchOutput.model_validate(parsed)
    except ValidationError as error:
        raise CitationValidationError("web_search 的工具结果结构无效") from error

    if not output.results:
        raise CitationValidationError("成功的 web_search 工具记录没有搜索结果")


def _source_from_fetch_result(record: AgentToolCall) -> ChatSource:
    parsed = _json_object_from_tool_result(record.result, tool_name="web_fetch")
    try:
        output = _FetchOutput.model_validate(parsed)
        if not output.content.strip():
            raise ValueError("content 为空")
        return ChatSource(
            source_id=output.source_id,
            title=output.title,
            url=output.url,
        )
    except (ValidationError, ValueError) as error:
        raise CitationValidationError("web_fetch 的工具结果结构无效") from error


def validate_citations(
    content: str,
    visible_turns: Sequence[VisibleCitationTurn],
    tool_calls: Sequence[AgentToolCall],
) -> list[ChatSource]:
    """校验回答引用，并返回按正文首次出现排序的来源快照。"""

    if not visible_turns or content != VISIBLE_TURN_SEPARATOR.join(
        turn.content for turn in visible_turns
    ):
        raise CitationValidationError("可见模型轮次与完整回答不一致")

    completed_searches = [call for call in tool_calls if call.tool_name == "web_search"]
    completed_fetches = [call for call in tool_calls if call.tool_name == "web_fetch"]

    for search in completed_searches:
        _validate_search_result(search)

    sources_by_id: dict[str, tuple[ChatSource, str]] = {}
    for fetch in completed_fetches:
        source = _source_from_fetch_result(fetch)
        if source.source_id in sources_by_id:
            raise CitationValidationError(
                f"web_fetch 出现重复来源编号：{source.source_id}"
            )
        sources_by_id[source.source_id] = (source, fetch.tool_call_id)

    if completed_searches and not completed_fetches:
        raise CitationValidationError("Agent 搜索了网页，但没有读取任何可引用页面")

    citations: list[tuple[str, frozenset[str]]] = []
    for turn in visible_turns:
        citations.extend(
            (f"S{match.group(1)}", turn.available_tool_call_ids)
            for match in CITATION_PATTERN.finditer(turn.content)
        )

    if completed_fetches and not citations:
        raise CitationValidationError("Agent 读取了网页，但可见回答没有引用来源")

    ordered_sources: list[ChatSource] = []
    seen: set[str] = set()
    for source_id, available_tool_call_ids in citations:
        source_record = sources_by_id.get(source_id)
        if source_record is None:
            raise CitationValidationError(
                f"回答引用了本次运行中不存在的来源：{source_id}"
            )
        source, tool_call_id = source_record
        if tool_call_id not in available_tool_call_ids:
            raise CitationValidationError(f"回答在读取来源之前就引用了它：{source_id}")
        if source_id not in seen:
            ordered_sources.append(source)
            seen.add(source_id)

    return ordered_sources


async def load_and_validate_citations(
    session: AsyncSession,
    *,
    agent_run_id: UUID,
    content: str,
    visible_turns: Sequence[VisibleCitationTurn],
) -> list[ChatSource]:
    """读取本次运行成功的联网工具记录并校验最终回答。"""

    tool_calls = list(
        await session.scalars(
            select(AgentToolCall)
            .where(
                AgentToolCall.agent_run_id == agent_run_id,
                AgentToolCall.status == AgentToolCallStatus.COMPLETED,
                AgentToolCall.tool_name.in_(("web_search", "web_fetch")),
            )
            .order_by(
                AgentToolCall.model_turn_index,
                AgentToolCall.tool_call_index,
            )
        )
    )
    return validate_citations(content, visible_turns, tool_calls)
