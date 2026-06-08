import asyncio
import logging
import warnings
from contextlib import asynccontextmanager
from fastapi import FastAPI
from loguru import logger
from AnTang.auth import AuthJWT
from AnTang.auth.exceptions import AuthJWTException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from AnTang.api.JWT import Settings as AuthJwtSettings
from AnTang.middleware.trace_id_middleware import TraceIDMiddleware
from AnTang.middleware.white_list_middleware import WhitelistMiddleware
from AnTang.settings import init_app_settings
from AnTang.settings import app_settings

warnings.filterwarnings("ignore")



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
        from AnTang.services.lock import try_acquire_leader

        if not await try_acquire_leader("antang:kb_sync:once", ttl=1800):
            logger.info("[antang-kb] 其他 worker 已持有同步锁，本 worker 跳过")
            return
        try:
            logger.info("[antang-kb] 后台同步任务启动 (Leader)")
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


async def _bootstrap_reminder_heartbeat(app: FastAPI) -> None:
    """启动心跳提醒循环。

    - 先把上次异常退出残留的 firing 状态复位为 pending（崩溃恢复）。
    - 与知识库后台同步一样，用 call_soon 把循环的 create_task 延后到 startup
      返回事件循环之后，避免拖慢容器 healthcheck。
    """
    if not app_settings.reminder.enabled:
        logger.info("[reminder] 已禁用，跳过心跳循环")
        return

    from AnTang.database.dao.reminder import ReminderDao
    from AnTang.services.antang.reminder import reminder_heartbeat_loop

    try:
        recovered = await ReminderDao.reset_stale_firing()
        if recovered:
            logger.info(f"[reminder] 启动复位 {recovered} 条残留 firing 提醒")
    except Exception as err:
        logger.warning(f"[reminder] 启动复位 firing 失败: {err}")

    app.state.reminder_heartbeat_task = None

    def _schedule_heartbeat() -> None:
        app.state.reminder_heartbeat_task = asyncio.create_task(reminder_heartbeat_loop())

    asyncio.get_running_loop().call_soon(_schedule_heartbeat)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_config()

    await register_router(app)
    await _bootstrap_antang_knowledge(app)
    await _bootstrap_reminder_heartbeat(app)
    print_logo()

    yield

    sync_task = getattr(app.state, "antang_kb_sync_task", None)
    if sync_task:
        logger.info(f"[antang-kb] shutdown 时后台同步任务完成状态: {sync_task.done()}")

    heartbeat_task = getattr(app.state, "reminder_heartbeat_task", None)
    if heartbeat_task:
        heartbeat_task.cancel()


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
