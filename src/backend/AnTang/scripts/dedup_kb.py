"""检查 / 清理默认知识库里的陈旧（重复）文档 chunk。

背景：sync_local_pdf_folder 只增不删——早先导入、后来从源目录移除/改名的文件，
它们的 chunk 仍残留在向量库里，造成检索重复、严格指标虚低。
本脚本按 chunk_id 前缀（= 来源文档名）统计，并可定向删除指定来源的全部 chunk。

用法（在 src/backend 下，或容器内 uv run）：
    # 1) 只看：列出每个来源文档的 chunk 数（先确认哪些是陈旧重复，不改任何数据）
    uv run python -m AnTang.scripts.dedup_kb

    # 2) 删除指定来源（可多个，前缀=统计里显示的来源名）的全部 chunk
    uv run python -m AnTang.scripts.dedup_kb --delete dc26s002 dc26s014 "Standards of Care in Diabetes-2026"

删除只清向量库；删完请再跑一次 backfill_es 让 ES 索引与向量库重新对齐。
适配 Milvus standalone 模式。
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
    if kid not in milvus_client.get_all_collections():
        logger.error(f"[dedup] Milvus 集合不存在: {kid}")
        return

    # 从 Milvus 拉取所有 chunk_id
    output_fields = ["chunk_id", "file_name"]
    collection = milvus_client._get_collection_safe(kid)
    if collection is None:
        logger.error(f"[dedup] 无法获取集合: {kid}")
        return

    # Milvus 单次查询上限 16384，分页拉取全部
    BATCH = 16000
    offset = 0
    ids: list[str] = []
    try:
        while True:
            page = collection.query(expr="", output_fields=output_fields, limit=BATCH, offset=offset)
            if not page:
                break
            ids.extend(r["chunk_id"] for r in page if r.get("chunk_id"))
            offset += len(page)
            if len(page) < BATCH:
                break
    except Exception as e:
        logger.error(f"[dedup] 查询 Milvus 失败: {e}")
        return
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
    to_delete_ids = [cid for cid in ids if _prefix(cid) in targets]
    if not to_delete_ids:
        logger.warning("[dedup] 没有匹配的 chunk，未删除任何东西。")
        return

    logger.info(f"[dedup] 即将从 Milvus 删除 {len(to_delete_ids)} 个 chunk，来源={sorted(targets & set(counts))}")
    # Milvus delete 支持按 id 列表删除
    batch = 500
    for i in range(0, len(to_delete_ids), batch):
        batch_ids = to_delete_ids[i : i + batch]
        expr = f"chunk_id in {batch_ids}"
        try:
            collection.delete(expr)
        except Exception as e:
            logger.error(f"[dedup] 批量删除失败 (offset={i}): {e}")
            return
        logger.info(f"[dedup] 已删除第 {i // batch + 1} 批 ({len(batch_ids)} 条)")
    collection.flush()
    logger.info(f"[dedup] 完成。剩余 chunk ≈ {len(ids) - len(to_delete_ids)}。")
    logger.info("[dedup] 请再跑一次 backfill_es 让 ES 与向量库对齐：")
    logger.info("        uv run python -m AnTang.scripts.backfill_es")


if __name__ == "__main__":
    args = sys.argv[1:]
    prefixes = args[args.index("--delete") + 1 :] if "--delete" in args else []
    asyncio.run(_run(prefixes))
