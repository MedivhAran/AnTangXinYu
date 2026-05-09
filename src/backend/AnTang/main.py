import asyncio
import logging
import warnings
import redis.asyncio as aioredis
from contextlib import asynccontextmanager
from fastapi import FastAPI
from loguru import logger
from AnTang.auth import AuthJWT
from AnTang.auth.exceptions import AuthJWTException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from AnTang.api.JWT import Settings as AuthJwtSettings
from AnTang.mcp_proxy.session.manager import SessionManager
from AnTang.middleware.trace_id_middleware import TraceIDMiddleware
from AnTang.middleware.white_list_middleware import WhitelistMiddleware
from AnTang.settings import init_app_settings
from AnTang.settings import app_settings

warnings.filterwarnings("ignore")
logging.getLogger("chromadb").setLevel(logging.WARNING)


async def register_router(app: FastAPI):
    from AnTang.api.router import router

    app.include_router(router)

    # 健康探针
    @app.get("/health")
    def check_health():
        return {"status": "OK"}


def register_middleware(app: FastAPI):
    origins = [
        "*",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Trace ID的中间件操作
    app.add_middleware(TraceIDMiddleware)

    # 注册白名单中间件
    app.add_middleware(WhitelistMiddleware)

    return app


async def init_config():
    await init_app_settings()

    from AnTang.database.init_data import init_agentchat_system

    await init_agentchat_system()


def print_logo():
    from pyfiglet import Figlet

    f = Figlet(font="slant")
    print(f.renderText("Agent Chat"))


async def _bootstrap_antang_knowledge(app: FastAPI) -> None:
    """启动时保证安糖默认知识库存在，并把本地 PDF 在后台异步索引。

    - `ensure_default_knowledge()` 很快（只查/写一行 DB），在 startup 里 await。
    - `sync_local_pdf_folder()` 可能要分钟级解析 PDF，交给 asyncio 后台任务，
      让 FastAPI 立即进入可响应状态；容器 healthcheck 不会被拖慢。
    """
    from AnTang.services.antang.knowledge import (
        ensure_default_knowledge,
        sync_local_pdf_folder,
    )

    try:
        await ensure_default_knowledge()
    except Exception as err:
        logger.exception(f"[antang-kb] 初始化默认知识库失败: {err}")
        return

    async def _background_sync() -> None:
        try:
            logger.info("[antang-kb] 后台同步任务启动")
            report = await sync_local_pdf_folder()
            logger.info(f"[antang-kb] 后台同步任务完成: {report}")
        except Exception as err:
            logger.exception(f"[antang-kb] 后台同步 PDF 失败: {err}")

    # 关键：不要在 startup 生命周期里立刻跑重任务。
    # 通过 call_soon 把 create_task 延后到本次 startup 返回事件循环之后，
    # 避免容器 healthcheck 在应用监听端口前超时失败。
    app.state.antang_kb_sync_task = None

    def _schedule_sync_task() -> None:
        app.state.antang_kb_sync_task = asyncio.create_task(_background_sync())

    asyncio.get_running_loop().call_soon(_schedule_sync_task)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_config()

    redis_client = aioredis.from_url(app_settings.redis.get("endpoint"), decode_responses=True)
    app.state.session_manager = SessionManager(redis_client)

    await register_router(app)
    await _bootstrap_antang_knowledge(app)
    print_logo()

    yield

    sync_task = getattr(app.state, "antang_kb_sync_task", None)
    if sync_task:
        logger.info(f"[antang-kb] shutdown 时后台同步任务完成状态: {sync_task.done()}")

    await redis_client.close()


def create_app():
    app = FastAPI(title=app_settings.server.name, version=app_settings.server.version, lifespan=lifespan)

    app = register_middleware(app)

    # 配置 AuthJWT
    @AuthJWT.load_config
    def get_config():
        return AuthJwtSettings()

    # 处理 AuthJWT 异常
    @app.exception_handler(AuthJWTException)
    def authjwt_exception_handler(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("AnTang.main:app", host="0.0.0.0", port=7860)
