"""检查 / 清理默认知识库里的陈旧（重复）文档 chunk。

背景：sync_local_pdf_folder 只增不删——早先导入、后来从源目录移除/改名的文件，
它们的 chunk 仍残留在 Chroma 里，造成检索重复、严格指标虚低。
本脚本按 chunk_id 前缀（= 来源文档名）统计，并可定向删除指定来源的全部 chunk。

用法（在 src/backend 下，或容器内 uv run）：
    # 1) 只看：列出每个来源文档的 chunk 数（先确认哪些是陈旧重复，不改任何数据）
    uv run python -m AnTang.scripts.dedup_kb

    # 2) 删除指定来源（可多个，前缀=统计里显示的来源名）的全部 chunk（只动 Chroma）
    uv run python -m AnTang.scripts.dedup_kb --delete dc26s002 dc26s014 "Standards of Care in Diabetes-2026"

删除只清 Chroma；删完请再跑一次 backfill_es 让 ES 索引与 Chroma 重新对齐。
仅适配 chroma 向量库模式。
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter

from loguru import logger

from AnTang.settings import init_app_settings


def _prefix(chunk_id: str) -> str:
    """chunk_id = '<来源文档名>_<uuid>'，取第一个下划线前的部分作为来源名。"""
    return chunk_id.split("_", 1)[0]


async def _run(delete_prefixes: list[str]) -> None:
    await init_app_settings()

    from AnTang.services.antang.knowledge import get_default_knowledge_id
    from AnTang.services.rag.vector_stores import milvus_client

    kid = await get_default_knowledge_id()
    get_collection = getattr(milvus_client, "_get_collection_safe", None)
    collection = get_collection(kid) if callable(get_collection) else None
    if collection is None:
        logger.error(f"[dedup] Chroma 集合不存在: {kid}")
        return

    ids = collection.get().get("ids") or []
    counts = Counter(_prefix(cid) for cid in ids)

    # 模式一：只看
    if not delete_prefixes:
        logger.info(f"[dedup] 知识库 {kid} 共 {len(ids)} chunk，按来源文档统计（chunk 数 / 来源）：")
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            logger.info(f"  {n:6d}  {name}")
        logger.info("确认哪些是陈旧重复后，用 --delete <来源> [<来源> ...] 删除。")
        return

    # 模式二：定向删除
    targets = set(delete_prefixes)
    unknown = targets - set(counts)
    if unknown:
        logger.warning(f"[dedup] 这些来源在索引里不存在，将被忽略：{sorted(unknown)}")
    to_delete = [cid for cid in ids if _prefix(cid) in targets]
    if not to_delete:
        logger.warning("[dedup] 没有匹配的 chunk，未删除任何东西。")
        return

    logger.info(f"[dedup] 即将从 Chroma 删除 {len(to_delete)} 个 chunk，来源={sorted(targets & set(counts))}")
    # Chroma 删除可能一次性 id 过多，分批删
    batch = 500
    for i in range(0, len(to_delete), batch):
        collection.delete(ids=to_delete[i : i + batch])
    logger.info(f"[dedup] 完成。剩余 chunk ≈ {len(ids) - len(to_delete)}。")
    logger.info("[dedup] 请再跑一次 backfill_es 让 ES 与 Chroma 对齐：")
    logger.info("        uv run python -m AnTang.scripts.backfill_es")


if __name__ == "__main__":
    args = sys.argv[1:]
    prefixes = args[args.index("--delete") + 1 :] if "--delete" in args else []
    asyncio.run(_run(prefixes))
