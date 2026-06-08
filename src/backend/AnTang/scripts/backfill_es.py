"""把默认知识库里已存在于 Milvus 的 chunk 回填到 Elasticsearch。

背景：ES 当初是关闭的，默认知识库的 PDF 只进了向量库、没进 ES；而启动时的自动同步
（sync_local_pdf_folder）只检查向量索引、会跳过这些已索引文件，所以 ES 一直是空的。
本脚本直接读 Milvus 里已解析好的 chunk，原样镜像进 ES，避免重新解析 PDF / 重传图片。

用法：
    uv run python -m AnTang.scripts.backfill_es     # 在 src/backend 目录下执行

前提：
- 已把 rag.enable_elasticsearch 置为 True；
- ES 服务在线（默认 http://127.0.0.1:9200）；
- 若开了 IK 分词（rag.enable_ik_analyzer=True），ES 需先装好 ik 插件，否则建索引会失败。

可重复执行：每次先删掉该知识库对应的 ES 索引再重建，保证幂等。
适配 Milvus standalone 模式。
"""

from __future__ import annotations

import asyncio

from loguru import logger

from AnTang.settings import init_app_settings


async def _run() -> None:
    await init_app_settings()

    from AnTang.schemas.chunk import ChunkModel
    from AnTang.services.antang.knowledge import get_default_knowledge_id
    from AnTang.services.rag.es_client import client as es_client
    from AnTang.services.rag.vector_stores import milvus_client
    from AnTang.settings import app_settings

    if not app_settings.rag.enable_elasticsearch:
        logger.warning("[backfill-es] rag.enable_elasticsearch=False，请先开启再跑回填。")
        return

    knowledge_id = await get_default_knowledge_id()

    # 1) 从 Milvus 取出默认知识库的全部正文 chunk（跳过 is_summary 摘要条目）
    output_fields = ["chunk_id", "content", "file_id", "file_name", "update_time", "summary"]
    collection = milvus_client._get_collection_safe(knowledge_id)
    if collection is None:
        logger.error(f"[backfill-es] Milvus 集合不存在: {knowledge_id}，请确认向量库已建好。")
        return

    # Milvus 单次查询上限 16384，分页拉取全部非摘要 chunk
    BATCH = 16000
    offset = 0
    results: list[dict] = []
    try:
        while True:
            page = collection.query(
                expr="summary == ''",
                output_fields=output_fields,
                limit=BATCH,
                offset=offset,
            )
            if not page:
                break
            results.extend(page)
            offset += len(page)
            if len(page) < BATCH:
                break
    except Exception as e:
        logger.error(f"[backfill-es] 查询 Milvus 失败: {e}")
        return

    if not results:
        logger.warning("[backfill-es] Milvus 里没有可回填的 chunk，跳过。")
        return

    chunks = [
        ChunkModel(
            chunk_id=(r.get("chunk_id") or ""),
            content=r.get("content") or "",
            file_id=(r.get("file_id") or ""),
            file_name=(r.get("file_name") or ""),
            update_time=(r.get("update_time") or ""),
            knowledge_id=knowledge_id,
            summary=(r.get("summary") or ""),
        )
        for r in results
    ]
    if not chunks:
        logger.warning("[backfill-es] 过滤后没有可回填的 chunk，跳过。")
        return

    # 2) 先删掉旧 ES 索引，保证可重复执行不产生重复文档
    try:
        if es_client.client.indices.exists(index=knowledge_id):
            es_client.client.indices.delete(index=knowledge_id)
            logger.info(f"[backfill-es] 已删除旧索引 {knowledge_id}，将重建。")
    except Exception as err:
        logger.warning(f"[backfill-es] 删除旧索引失败（继续尝试写入）：{err}")

    # 3) 写入 ES（index_documents 内部会按当前 IK 配置自动建索引）
    logger.info(f"[backfill-es] 开始把 {len(chunks)} 条 chunk 写入 ES index={knowledge_id}")
    await es_client.index_documents(knowledge_id, chunks)
    logger.info(f"[backfill-es] 回填完成，共 {len(chunks)} 条。")


if __name__ == "__main__":
    asyncio.run(_run())
