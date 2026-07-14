import json
from typing import Any
from uuid import uuid4

import pytest

from antang_api.citations import (
    CitationValidationError,
    VisibleCitationTurn,
    validate_citations,
)
from antang_api.models import AgentToolCall, AgentToolCallStatus


def tool_record(
    tool_name: str,
    result: dict[str, Any] | str | list[str | dict[str, Any]],
    *,
    turn: int,
    index: int,
) -> AgentToolCall:
    serialized_result: str | list[str | dict[str, Any]]
    if isinstance(result, dict):
        serialized_result = json.dumps(result, ensure_ascii=False)
    else:
        serialized_result = result

    return AgentToolCall(
        agent_run_id=uuid4(),
        tool_call_id=f"call-{turn}-{index}",
        tool_name=tool_name,
        model_turn_index=turn,
        tool_call_index=index,
        arguments={},
        result=serialized_result,
        status=AgentToolCallStatus.COMPLETED,
    )


def search_record() -> AgentToolCall:
    return tool_record(
        "web_search",
        {
            "results": [
                {
                    "title": "权威页面",
                    "url": "https://example.com/search-result",
                    "snippet": "搜索摘要",
                    "score": 0.9,
                }
            ]
        },
        turn=1,
        index=1,
    )


def fetch_record(
    source_id: str,
    *,
    title: str,
    url: str,
    turn: int,
    index: int,
) -> AgentToolCall:
    return tool_record(
        "web_fetch",
        {
            "source_id": source_id,
            "title": title,
            "url": url,
            "content": "网页正文",
        },
        turn=turn,
        index=index,
    )


def visible_turn(
    content: str,
    records: list[AgentToolCall],
) -> VisibleCitationTurn:
    return VisibleCitationTurn(
        content=content,
        available_tool_call_ids=frozenset(record.tool_call_id for record in records),
    )


def test_validate_citations_returns_only_referenced_sources_in_text_order() -> None:
    first = fetch_record(
        "S1",
        title="来源一",
        url="https://example.com/one",
        turn=2,
        index=1,
    )
    second = fetch_record(
        "S2",
        title="来源二",
        url="https://example.com/two",
        turn=2,
        index=2,
    )

    content = "先引用第二个。[S2] 再引用第一个。[S1] 第二个仍可复用。[S2]"
    records = [search_record(), first, second]
    sources = validate_citations(content, [visible_turn(content, records)], records)

    assert [source.model_dump() for source in sources] == [
        {
            "source_id": "S2",
            "title": "来源二",
            "url": "https://example.com/two",
        },
        {
            "source_id": "S1",
            "title": "来源一",
            "url": "https://example.com/one",
        },
    ]


def test_validate_citations_accepts_one_langchain_text_content_block() -> None:
    record = fetch_record(
        "S1",
        title="来源",
        url="https://example.com/source",
        turn=1,
        index=1,
    )
    assert isinstance(record.result, str)
    record.result = [{"type": "text", "text": record.result}]

    content = "结论。[S1]"
    sources = validate_citations(content, [visible_turn(content, [record])], [record])

    assert sources[0].source_id == "S1"


@pytest.mark.parametrize(
    ("content", "records", "error_text"),
    [
        ("只有搜索。", [search_record()], "没有读取"),
        (
            "读取了但没引用。",
            [
                fetch_record(
                    "S1",
                    title="来源",
                    url="https://example.com/source",
                    turn=1,
                    index=1,
                )
            ],
            "没有引用",
        ),
        ("凭空引用。[S9]", [], "不存在"),
    ],
)
def test_validate_citations_rejects_missing_fetch_or_citation(
    content: str,
    records: list[AgentToolCall],
    error_text: str,
) -> None:
    with pytest.raises(CitationValidationError, match=error_text):
        validate_citations(content, [visible_turn(content, records)], records)


@pytest.mark.parametrize(
    "invalid_result",
    [
        "not-json",
        "[]",
        json.dumps(
            {
                "source_id": "S1",
                "title": "来源",
                "url": "file:///tmp/page",
                "content": "正文",
            }
        ),
        [
            {"type": "text", "text": "{}"},
            {"type": "text", "text": "{}"},
        ],
    ],
)
def test_validate_citations_rejects_malformed_fetch_result(
    invalid_result: str | list[str | dict[str, Any]],
) -> None:
    record = tool_record(
        "web_fetch",
        invalid_result,
        turn=1,
        index=1,
    )

    with pytest.raises(CitationValidationError, match="web_fetch"):
        content = "引用。[S1]"
        validate_citations(content, [visible_turn(content, [record])], [record])


def test_validate_citations_rejects_duplicate_source_ids() -> None:
    records = [
        fetch_record(
            "S1",
            title="来源一",
            url="https://example.com/one",
            turn=1,
            index=1,
        ),
        fetch_record(
            "S1",
            title="来源二",
            url="https://example.com/two",
            turn=1,
            index=2,
        ),
    ]

    with pytest.raises(CitationValidationError, match="重复来源编号"):
        content = "引用。[S1]"
        validate_citations(content, [visible_turn(content, records)], records)


def test_validate_citations_rejects_malformed_search_result() -> None:
    record = tool_record(
        "web_search",
        {
            "results": [
                {
                    "title": "来源",
                    "url": "https://example.com/source",
                    "snippet": "摘要",
                }
            ]
        },
        turn=1,
        index=1,
    )

    with pytest.raises(CitationValidationError, match="web_search"):
        content = "普通回答"
        validate_citations(content, [visible_turn(content, [record])], [record])


def test_validate_citations_rejects_source_marker_before_fetch_completes() -> None:
    record = fetch_record(
        "S1",
        title="来源",
        url="https://example.com/source",
        turn=1,
        index=1,
    )

    with pytest.raises(CitationValidationError, match="读取来源之前"):
        validate_citations(
            "我先查看 [S1]\n\n最终回答没有来源。",
            [
                VisibleCitationTurn(
                    content="我先查看 [S1]",
                    available_tool_call_ids=frozenset(),
                ),
                visible_turn("最终回答没有来源。", [record]),
            ],
            [record],
        )


def test_validate_citations_accepts_source_after_fetch_before_final_turn() -> None:
    first = fetch_record(
        "S1",
        title="来源一",
        url="https://example.com/one",
        turn=1,
        index=1,
    )
    second = fetch_record(
        "S2",
        title="来源二",
        url="https://example.com/two",
        turn=2,
        index=1,
    )
    content = "第一个结论。[S1]\n\n第二个结论。[S2]"

    sources = validate_citations(
        content,
        [
            visible_turn("第一个结论。[S1]", [first]),
            visible_turn("第二个结论。[S2]", [first, second]),
        ],
        [first, second],
    )

    assert [source.source_id for source in sources] == ["S1", "S2"]
