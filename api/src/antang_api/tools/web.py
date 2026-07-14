from typing import Any, TypedDict
from urllib.parse import urlsplit

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from tavily import AsyncTavilyClient

from antang_api.agents.runtime import CoreAgentContext


class WebToolError(RuntimeError):
    """Tavily 返回了无法交给模型使用的结果。"""


class SearchResult(TypedDict):
    title: str
    url: str
    snippet: str
    score: float


class SearchOutput(TypedDict):
    results: list[SearchResult]


class FetchOutput(TypedDict):
    url: str
    content: str


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WebToolError(f"Tavily 响应缺少有效的 {field}")

    return value.strip()


def _http_url(value: object, field: str = "url") -> str:
    url = _required_text(value, field)
    parsed = urlsplit(url)

    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise WebToolError(f"{field} 必须是有效的 http(s) URL")

    return url


def _response_results(response: object, endpoint: str) -> list[object]:
    if not isinstance(response, dict):
        raise WebToolError(f"Tavily {endpoint} 响应不是对象")

    results = response.get("results")

    if not isinstance(results, list):
        raise WebToolError(f"Tavily {endpoint} 响应缺少 results 数组")

    if not results:
        raise WebToolError(f"Tavily {endpoint} 没有返回结果")

    return results


def _normalize_search_response(response: object) -> SearchOutput:
    normalized_results: list[SearchResult] = []

    for result in _response_results(response, "search"):
        if not isinstance(result, dict):
            raise WebToolError("Tavily search 的结果项不是对象")

        score = result.get("score")

        if isinstance(score, bool) or not isinstance(score, int | float):
            raise WebToolError("Tavily search 的结果项缺少有效的 score")

        normalized_results.append(
            {
                "title": _required_text(result.get("title"), "title"),
                "url": _http_url(result.get("url")),
                "snippet": _required_text(result.get("content"), "content"),
                "score": float(score),
            }
        )

    if len(normalized_results) > 5:
        raise WebToolError("Tavily search 返回了超过五个结果")

    return {"results": normalized_results}


def _normalize_fetch_response(response: object) -> FetchOutput:
    if not isinstance(response, dict):
        raise WebToolError("Tavily extract 响应不是对象")

    failed_results = response.get("failed_results")

    if not isinstance(failed_results, list):
        raise WebToolError("Tavily extract 响应缺少 failed_results 数组")

    if failed_results:
        raise WebToolError("Tavily 无法提取请求的网页")

    results = _response_results(response, "extract")

    if len(results) != 1 or not isinstance(results[0], dict):
        raise WebToolError("Tavily extract 必须只返回一个网页结果")

    result: dict[str, Any] = results[0]

    return {
        "url": _http_url(result.get("url")),
        "content": _required_text(result.get("raw_content"), "raw_content"),
    }


def build_web_tools(client: AsyncTavilyClient) -> tuple[BaseTool, BaseTool]:
    """创建联网搜索和单页提取工具。"""

    @tool("web_search")
    async def web_search(
        query: str,
        runtime: ToolRuntime[CoreAgentContext],
    ) -> SearchOutput:
        """搜索公开网页，返回最多五个标题、URL 和相关内容片段。"""

        normalized_query = _required_text(query, "query")
        response = await client.search(
            query=normalized_query,
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
        return _normalize_search_response(response)

    @tool("web_fetch")
    async def web_fetch(
        url: str,
        query: str,
        runtime: ToolRuntime[CoreAgentContext],
    ) -> FetchOutput:
        """读取一个 http(s) 网页，按 query 返回最多三个相关 Markdown 片段。"""

        normalized_url = _http_url(url)
        normalized_query = _required_text(query, "query")
        response = await client.extract(
            urls=normalized_url,
            query=normalized_query,
            extract_depth="advanced",
            format="markdown",
            chunks_per_source=3,
            include_images=False,
            timeout=30,
            session_id=str(runtime.context.run_id),
        )
        return _normalize_fetch_response(response)

    return web_search, web_fetch
