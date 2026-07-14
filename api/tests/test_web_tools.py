from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool
from langchain_core.tools.structured import StructuredTool
from pydantic import BaseModel
from tavily import AsyncTavilyClient

from antang_api.agents.runtime import CoreAgentContext
from antang_api.tools.web import WebToolError, build_web_tools


def make_runtime() -> ToolRuntime[CoreAgentContext]:
    return ToolRuntime(
        state={},
        context=CoreAgentContext(
            user_id=uuid4(),
            run_id=uuid4(),
            input_message_count=1,
        ),
        config={},
        stream_writer=lambda _value: None,
        tool_call_id="tool-call-id",
        store=None,
        tools=[],
    )


def tool_coroutine(tool: BaseTool) -> Callable[..., Awaitable[object]]:
    assert isinstance(tool, StructuredTool)
    assert tool.coroutine is not None
    return cast("Callable[..., Awaitable[object]]", tool.coroutine)


def mock_client() -> AsyncMock:
    return AsyncMock(spec=AsyncTavilyClient)


def build_tools(client: AsyncMock) -> tuple[BaseTool, BaseTool]:
    return build_web_tools(cast("AsyncTavilyClient", client))


def model_properties(tool: BaseTool) -> set[str]:
    schema = tool.tool_call_schema
    json_schema = (
        schema
        if isinstance(schema, dict)
        else cast("type[BaseModel]", schema).model_json_schema()
    )
    properties = json_schema.get("properties")
    assert isinstance(properties, dict)
    return set(properties)


def test_build_web_tools_exposes_only_model_arguments() -> None:
    search_tool, fetch_tool = build_tools(mock_client())

    assert search_tool.name == "web_search"
    assert fetch_tool.name == "web_fetch"
    assert model_properties(search_tool) == {"query"}
    assert model_properties(fetch_tool) == {"url", "query"}


async def test_web_search_uses_fixed_parameters_and_normalizes_results() -> None:
    client = mock_client()
    client.search.return_value = {
        "results": [
            {
                "title": "Tavily 文档",
                "url": "https://docs.tavily.com/",
                "content": "搜索接口说明",
                "score": 0.91,
                "raw_content": "不会进入规范化结果",
            }
        ]
    }
    search_tool, _ = build_tools(client)
    runtime = make_runtime()

    output = await tool_coroutine(search_tool)(query="  Tavily API  ", runtime=runtime)

    assert output == {
        "results": [
            {
                "title": "Tavily 文档",
                "url": "https://docs.tavily.com/",
                "snippet": "搜索接口说明",
                "score": 0.91,
            }
        ],
    }
    client.search.assert_awaited_once_with(
        query="Tavily API",
        search_depth="advanced",
        topic="general",
        max_results=5,
        chunks_per_source=1,
        include_answer=False,
        include_raw_content=False,
        include_images=False,
        auto_parameters=False,
        timeout=60,
        session_id=str(runtime.context.run_id),
    )


async def test_web_fetch_uses_fixed_parameters_and_normalizes_result() -> None:
    client = mock_client()
    client.extract.return_value = {
        "results": [
            {
                "url": "https://example.com/article",
                "raw_content": "## 研究结论\n\n正文内容",
                "images": [],
            }
        ],
        "failed_results": [],
    }
    _, fetch_tool = build_tools(client)
    runtime = make_runtime()

    output = await tool_coroutine(fetch_tool)(
        url=" https://example.com/article ",
        query=" 研究结论 ",
        runtime=runtime,
    )

    assert output == {
        "url": "https://example.com/article",
        "content": "## 研究结论\n\n正文内容",
    }
    client.extract.assert_awaited_once_with(
        urls="https://example.com/article",
        query="研究结论",
        extract_depth="advanced",
        format="markdown",
        chunks_per_source=3,
        include_images=False,
        timeout=30,
        session_id=str(runtime.context.run_id),
    )


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"results": []},
        {
            "results": [
                {"title": "标题", "url": "https://example.com", "content": "内容"}
            ]
        },
    ],
)
async def test_web_search_rejects_empty_or_invalid_response(
    response: dict[str, object],
) -> None:
    client = mock_client()
    client.search.return_value = response
    search_tool, _ = build_tools(client)

    with pytest.raises(WebToolError):
        await tool_coroutine(search_tool)(query="测试", runtime=make_runtime())

    assert client.search.await_count == 1


async def test_web_fetch_rejects_failed_or_non_http_result() -> None:
    client = mock_client()
    client.extract.return_value = {
        "results": [],
        "failed_results": [{"url": "https://example.com"}],
    }
    _, fetch_tool = build_tools(client)

    with pytest.raises(WebToolError, match="无法提取"):
        await tool_coroutine(fetch_tool)(
            url="https://example.com",
            query="测试",
            runtime=make_runtime(),
        )

    assert client.extract.await_count == 1

    with pytest.raises(WebToolError, match=r"http\(s\)"):
        await tool_coroutine(fetch_tool)(
            url="file:///etc/passwd",
            query="测试",
            runtime=make_runtime(),
        )

    assert client.extract.await_count == 1


async def test_web_tools_propagate_provider_failure_without_retry() -> None:
    client = mock_client()
    client.search.side_effect = RuntimeError("provider unavailable")
    search_tool, _ = build_tools(client)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await tool_coroutine(search_tool)(query="测试", runtime=make_runtime())

    assert client.search.await_count == 1
