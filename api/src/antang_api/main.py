from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from hindsight_client import Hindsight
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from tavily import AsyncTavilyClient

from antang_api.agents.core import build_core_agent
from antang_api.agents.health_profile import build_profile_agent
from antang_api.agents.tool_middleware import ToolPersistenceMiddleware
from antang_api.chat import recover_interrupted_chat_runs
from antang_api.companion_memory import CompanionMemory
from antang_api.database import engine, session_factory
from antang_api.llm import (
    build_chat_model,
    build_non_streaming_model,
)
from antang_api.log_config import configure_logging
from antang_api.routers.auth import router as auth_router
from antang_api.routers.chat import router as chat_router
from antang_api.routers.health_profile import router as health_profile_router
from antang_api.routers.proactive_care import router as proactive_care_router
from antang_api.settings import settings
from antang_api.tools import (
    build_care_plan_tool,
    build_profile_tool,
    build_wearable_read_tool,
    build_web_tools,
)

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """管理数据库、外部客户端、checkpointer 和两类 Agent 的生命周期。"""

    hindsight_client = Hindsight(
        base_url=settings.hindsight_base_url,
        api_key=(
            settings.hindsight_api_key.get_secret_value()
            if settings.hindsight_api_key is not None
            else None
        ),
        timeout=settings.hindsight_timeout_seconds,
        user_agent="antang-api",
    )

    try:
        async with session_factory() as session:
            await recover_interrupted_chat_runs(session)

        async with AsyncPostgresSaver.from_conn_string(
            settings.langgraph_database_url
        ) as checkpointer:
            await checkpointer.setup()

            async with AsyncTavilyClient(
                api_key=settings.tavily_api_key.get_secret_value(),
                client_name="antang-api",
            ) as tavily_client:
                chat_model = build_chat_model()
                health_profile_model = build_non_streaming_model()

                wearable_tool = build_wearable_read_tool(session_factory)
                health_profile_agent = build_profile_agent(
                    health_profile_model,
                    checkpointer,
                    tools=(wearable_tool,),
                    session_factory=session_factory,
                )
                health_profile_delegate = build_profile_tool(
                    health_profile_agent,
                    model_name=settings.hachimi_model_name,
                    session_factory=session_factory,
                )
                care_plan_tool = build_care_plan_tool(session_factory)
                web_tools = build_web_tools(
                    tavily_client,
                    search_max_snippet_chars=(settings.tavily_search_max_snippet_chars),
                    fetch_max_content_chars=settings.tavily_fetch_max_content_chars,
                )
                core_tools = (
                    *web_tools,
                    wearable_tool,
                    health_profile_delegate,
                    care_plan_tool,
                )
                tool_middleware = ToolPersistenceMiddleware(
                    session_factory,
                    max_tool_rounds=settings.agent_max_tool_rounds,
                    max_parallel_tool_calls=(settings.agent_max_parallel_tool_calls),
                )

                app.state.chat_model = chat_model
                app.state.core_tools = core_tools
                app.state.companion_memory = CompanionMemory(
                    hindsight_client,
                    mode=settings.hindsight_mode,
                    retain_user_turns=settings.hindsight_retain_user_turns,
                    recall_budget=settings.hindsight_recall_budget,
                    recall_max_tokens=settings.hindsight_recall_max_tokens,
                )
                app.state.core_agent = build_core_agent(
                    chat_model,
                    checkpointer,
                    tools=core_tools,
                    middleware=(tool_middleware,),
                )
                yield

    finally:
        await hindsight_client.aclose()
        await engine.dispose()


app = FastAPI(
    title="Antang API",
    description="Antang API for Antang App",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(health_profile_router)
app.include_router(proactive_care_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
