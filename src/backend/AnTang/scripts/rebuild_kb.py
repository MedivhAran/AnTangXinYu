"""安糖默认知识库【全量重建】。

清空 Chroma collection + ES 索引 + 旧文件记录，然后重新灌入：
- 清洗后的文本指南 PDF（pymupdf4llm 解析 → cleaner 去参考文献/样板）；
- OCR 散文书 markdown（data/antang_knowledge_md/*.md）。

排除 4 个图片版 PDF：食物成分表（走结构化查询工具）+ 3 本散文书（走各自 OCR 的 .md）。

依赖：MySQL / Elasticsearch / MinIO / Chroma 均需就绪（PDF 解析会向 MinIO 传图），
因此通常在后端容器内执行：
    docker compose exec backend uv run python -m AnTang.scripts.rebuild_kb
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import tempfile

from loguru import logger

from AnTang.settings import app_settings, init_app_settings

# 注意：knowledge / database 等模块在导入时就会用配置建数据库 engine，
# 必须等 init_app_settings() 之后再 import（沿用 import_antang_knowledge 的约定）。

# 图片版 PDF 不走 PDF 解析：食物表 → 结构化工具；3 本书 → 各自 OCR 的 markdown
EXCLUDE_PDF_KEYWORDS = ["中国食物成分表", "认知疗法", "当事人中心治疗", "糖尿病结构化教育实用教程"]


def _excluded(name: str) -> bool:
    return any(k in name for k in EXCLUDE_PDF_KEYWORDS)


async def _drop_indexes(kb_id: str) -> None:
    """清空向量库与 ES 索引（不存在则忽略）。"""
    from AnTang.services.rag.vector_stores import milvus_client

    try:
        await milvus_client.delete_collection(kb_id)
        logger.info(f"已删除 Chroma collection {kb_id}")
    except Exception as e:
        logger.warning(f"删除 Chroma collection 失败（可能本就不存在）：{e}")

    if app_settings.rag.enable_elasticsearch:
        try:
            from AnTang.services.rag.es_client import client as es_client

            es_client.client.indices.delete(index=kb_id, ignore_unavailable=True)
            logger.info(f"已删除 ES 索引 {kb_id}")
        except Exception as e:
            logger.warning(f"删除 ES 索引失败：{e}")


async def _clear_db_records(kb_id: str) -> None:
    """删除该知识库旧的文件记录，避免重复导入。"""
    from AnTang.database.dao.knowledge_file import KnowledgeFileDao

    existing = await KnowledgeFileDao.select_knowledge_file(kb_id)
    for f in existing:
        try:
            await KnowledgeFileDao.delete_knowledge_file(f.id)
        except Exception as e:
            logger.warning(f"删除文件记录 {f.file_name} 失败：{e}")
    logger.info(f"已清空 {len(existing)} 条旧文件记录")


async def _ingest(file_name: str, file_path: str, kb_id: str) -> None:
    from AnTang.api.services.knowledge_file import KnowledgeFileService
    from AnTang.database.models.user import SystemUser

    await KnowledgeFileService.create_knowledge_file(
        file_name=file_name,
        file_path=file_path,
        knowledge_id=kb_id,
        user_id=SystemUser,
        oss_url=str(file_path),
        file_size_bytes=os.path.getsize(file_path),
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        default=None,
        help="只重灌文件名包含该关键字的文档（不清空整库），用于补救个别失败文件，如 --only 胰岛素泵",
    )
    args = parser.parse_args()

    await init_app_settings()
    # init 之后再导入，避免 database engine 在配置就绪前被创建
    from AnTang.services.antang.knowledge import _pdf_dir, ensure_default_knowledge
    from AnTang.database.dao.knowledge_file import KnowledgeFileDao
    from AnTang.services.rag.handler import RagHandler
    from AnTang.services.rag.vector_stores import milvus_client

    kb_id = await ensure_default_knowledge()
    logger.info(f"默认知识库 id={kb_id}")

    pdf_dir = _pdf_dir()
    md_dir = pdf_dir.parent / "antang_knowledge_md"
    # 统一成 (文件名, 路径, 是否markdown) 列表
    sources = [(p.name, str(p), False) for p in sorted(pdf_dir.glob("*.pdf")) if not _excluded(p.name)]
    sources += [(m.name, str(m), True) for m in (sorted(md_dir.glob("*.md")) if md_dir.exists() else [])]

    if args.only:
        sources = [s for s in sources if args.only in s[0]]
        logger.warning(f"==== 定向补灌：{len(sources)} 个文件（含'{args.only}'），不清空整库 ====")
        existing = {f.file_name: f for f in await KnowledgeFileDao.select_knowledge_file(kb_id)}
        for name, _, _ in sources:
            f = existing.get(name)
            if f:  # 先删旧残块与记录，避免重复
                await RagHandler.delete_documents_es_milvus(f.id, kb_id)
                await KnowledgeFileDao.delete_knowledge_file(f.id)
                logger.info(f"已清除旧块/记录：{name}")
    else:
        logger.warning("==== 全量重建：清空 Chroma + ES + 文件记录 ====")
        await _drop_indexes(kb_id)
        await _clear_db_records(kb_id)

    logger.info(f"将灌入 {len(sources)} 个文档")
    ok = fail = 0
    # markdown 喂临时副本：markdown 解析器读完会删源文件，临时副本可保护原始 OCR 产物
    tmp = tempfile.mkdtemp(prefix="kb_md_")
    try:
        for name, path, is_md in sources:
            ingest_path = path
            if is_md:
                ingest_path = os.path.join(tmp, name)
                shutil.copy(path, ingest_path)
            kind = "md" if is_md else "pdf"
            try:
                await _ingest(name, ingest_path, kb_id)
                ok += 1
                logger.info(f"[{kind}] 灌入成功 {name}")
            except Exception as e:
                fail += 1
                logger.exception(f"[{kind}] 灌入失败 {name}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    count = milvus_client.get_collection_count(kb_id)
    logger.info(f"==== 完成：成功 {ok} 失败 {fail}，Chroma 向量条目 {count} ====")


if __name__ == "__main__":
    asyncio.run(main())
