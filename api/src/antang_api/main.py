from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from tavily import AsyncTavilyClient

from antang_api.agents.core import build_core_agent
from antang_api.agents.tool_middleware import ToolPersistenceMiddleware
from antang_api.chat import recover_interrupted_chat_runs
from antang_api.database import engine, session_factory
from antang_api.llm import build_deepseek_model
from antang_api.log_config import configure_logging
from antang_api.routers.auth import router as auth_router
from antang_api.routers.chat import router as chat_router
from antang_api.settings import settings
from antang_api.tools import build_web_tools

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """管理数据库、Tavily、LangGraph checkpointer 和 Core Agent 的生命周期。"""

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
                chat_model = build_deepseek_model()
                core_tools = build_web_tools(tavily_client)
                tool_middleware = ToolPersistenceMiddleware(
                    session_factory,
                    max_tool_rounds=settings.agent_max_tool_rounds,
                    max_parallel_tool_calls=(settings.agent_max_parallel_tool_calls),
                )

                app.state.chat_model = chat_model
                app.state.core_tools = core_tools
                app.state.core_agent = build_core_agent(
                    chat_model,
                    checkpointer,
                    tools=core_tools,
                    middleware=(tool_middleware,),
                )
                yield

    finally:
        await engine.dispose()


app = FastAPI(
    title="Antang API",
    description="Antang API for Antang App",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(chat_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
