"""手动把本地 PDF 目录同步到安糖默认知识库。

用法：
    uv run python -m AnTang.scripts.import_antang_knowledge

FastAPI lifespan 已经会在容器启动时自动做同样的事，本脚本主要用于：
- 本地/CI 环境快速触发
- 新增 PDF 后前台观察导入结果
- 诊断某个 PDF 是否能被解析
"""

from __future__ import annotations

import asyncio

from loguru import logger

from AnTang.settings import init_app_settings


async def _run() -> None:
    await init_app_settings()
    from AnTang.services.antang.knowledge import (
        ensure_default_knowledge,
        sync_local_pdf_folder,
    )

    kb_id = await ensure_default_knowledge()
    logger.info(f"安糖默认知识库 id={kb_id}")
    report = await sync_local_pdf_folder()
    logger.info(f"同步结果: {report}")


if __name__ == "__main__":
    asyncio.run(_run())
