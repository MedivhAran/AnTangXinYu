"""安糖 Agent 的内置默认知识库。

知识库已经从用户侧功能收缩为安糖 Agent 的系统资料源：
- 用户不可见、不可创建、不可绑定；
- 安糖 Agent 默认检索这里；
- 其他 Agent 完全不碰。

本模块只负责：
1. 保证存在一条名为「安糖默认知识库」、owner=SystemUser 的 `KnowledgeTable` 记录；
2. 把 `DEFAULT_PDF_DIR` 里的 PDF 幂等导入这个默认知识库。

底层 RAG（解析、向量化、检索）仍然复用 `KnowledgeFileService`/`RagHandler`，
换引擎时不用改这里。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from AnTang.database.models.user import SystemUser

DEFAULT_KB_NAME = "安糖默认知识库"
DEFAULT_KB_DESC = "安糖 Agent 内置糖尿病/低血糖系统资料源，由系统管理。"

_DEFAULT_PDF_DIR_ENV = "ANTANG_KNOWLEDGE_PDF_DIR"
_DEFAULT_PDF_DIR_FALLBACK = Path("data/antang_knowledge_pdfs")


def _pdf_dir() -> Path:
    override = os.environ.get(_DEFAULT_PDF_DIR_ENV)
    return Path(override) if override else _DEFAULT_PDF_DIR_FALLBACK


_cached_default_id: Optional[str] = None
_ensure_lock = asyncio.Lock()


async def ensure_default_knowledge() -> str:
    """保证默认知识库存在，返回其 ID。幂等、并发安全。"""
    from AnTang.api.services.knowledge import KnowledgeService
    from AnTang.database.dao.knowledge import KnowledgeDao

    global _cached_default_id
    if _cached_default_id:
        return _cached_default_id

    async with _ensure_lock:
        if _cached_default_id:
            return _cached_default_id

        # KnowledgeTable.name 是全局唯一，不能只按 SystemUser 过滤；
        # 否则历史数据里若已有同名知识库，创建时会撞唯一索引。
        existing = await KnowledgeDao.get_knowledge_by_name(DEFAULT_KB_NAME)
        if existing:
            if existing.user_id != SystemUser or existing.description != DEFAULT_KB_DESC:
                await KnowledgeDao.update_knowledge_system_metadata(
                    existing.id,
                    SystemUser,
                    DEFAULT_KB_DESC,
                )
                logger.info(f"[antang-kb] 接管已有默认知识库 id={existing.id}")
            _cached_default_id = existing.id
            return _cached_default_id

        await KnowledgeService.create_knowledge(
            DEFAULT_KB_NAME, DEFAULT_KB_DESC, SystemUser
        )
        created = await KnowledgeDao.get_knowledge_by_name(DEFAULT_KB_NAME)
        if not created:
            raise RuntimeError("创建安糖默认知识库后仍查不到记录，请检查数据库。")
        _cached_default_id = created.id
        logger.info(f"[antang-kb] 创建安糖默认知识库 id={_cached_default_id}")
        return _cached_default_id


async def get_default_knowledge_id() -> str:
    """安糖检索入口使用的唯一获取方法。"""
    return await ensure_default_knowledge()


@dataclass
class SyncReport:
    imported: int = 0
    reindexed: int = 0
    skipped: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"imported={self.imported} reindexed={self.reindexed} "
            f"skipped={self.skipped} failed={self.failed}"
        )


async def _vector_document_count(knowledge_id: str) -> int:
    """返回默认知识库当前向量条目数，collection 不存在时视为 0。"""
    try:
        from AnTang.services.rag.vector_stores import milvus_client

        get_collection_count = getattr(milvus_client, "get_collection_count", None)
        if callable(get_collection_count):
            return int(get_collection_count(knowledge_id) or 0)

        get_collection = getattr(milvus_client, "_get_collection_safe", None)
        if callable(get_collection):
            collection = get_collection(knowledge_id)
            if collection is not None and hasattr(collection, "num_entities"):
                return int(collection.num_entities or 0)
    except Exception as err:
        logger.warning(f"[antang-kb] 检查向量索引数量失败，将尝试重建: {err}")
    return 0


async def _vector_file_document_count(knowledge_id: str, file_id: str) -> int:
    """返回单个 PDF 对应的向量条目数，用于补齐半截索引。"""
    try:
        from AnTang.services.rag.vector_stores import milvus_client

        get_file_document_count = getattr(milvus_client, "get_file_document_count", None)
        if callable(get_file_document_count):
            return int(get_file_document_count(knowledge_id, file_id) or 0)
    except Exception as err:
        logger.warning(f"[antang-kb] 检查文件向量索引数量失败，将尝试重建: {err}")
    return 0


async def _reindex_existing_pdf(
    knowledge_id: str,
    knowledge_file,
    pdf_path: Path,
    *,
    clear_existing_vectors: bool = False,
) -> None:
    """复用已有 knowledge_file 记录重建向量索引，避免数据库重复插入文件行。

    对图片版书籍，优先使用 ocr_books.py 预生成的 .md 缓存，
    避免逐页调用 VL 模型（省大量 token / 时间）。
    """
    from AnTang.api.services.knowledge_file import KnowledgeFileService
    from AnTang.database.models.knowledge_file import Status
    from AnTang.services.rag.parser import doc_parser
    from AnTang.services.rag.handler import RagHandler
    from AnTang.settings import app_settings

    await KnowledgeFileService.update_parsing_status(knowledge_file.id, Status.process)
    try:
        if clear_existing_vectors:
            await RagHandler.delete_documents_es_milvus(knowledge_file.id, knowledge_id)

        # 优先使用预先 OCR 好的 markdown（ocr_books.py 产物），不走 VL 模型
        md_dir = _pdf_dir().parent / "antang_knowledge_md"
        source_path: str = str(pdf_path)
        for candidate in (md_dir.glob("*.md")):
            stem = pdf_path.stem
            if stem in candidate.name or candidate.stem in stem:
                logger.info(f"[antang-kb] {pdf_path.name} → 使用预生成 markdown {candidate.name}")
                source_path = str(candidate)
                break

        logger.info(f"[antang-kb] 开始重建 {pdf_path.name} 的向量索引")
        chunks = await doc_parser.parse_doc_into_chunks(
            knowledge_file.id,
            source_path,
            knowledge_id,
        )
        logger.info(f"[antang-kb] {pdf_path.name} 解析完成，chunk_count={len(chunks)}")
        indexed = await RagHandler.index_milvus_documents(knowledge_id, chunks)
        if not indexed:
            raise RuntimeError("向量索引写入失败")
        if app_settings.rag.enable_elasticsearch:
            await RagHandler.index_es_documents(knowledge_id, chunks)
        await KnowledgeFileService.update_parsing_status(knowledge_file.id, Status.success)
    except Exception:
        await KnowledgeFileService.update_parsing_status(knowledge_file.id, Status.fail)
        raise


async def sync_local_pdf_folder() -> SyncReport:
    """扫描 DEFAULT_PDF_DIR，把新 PDF 幂等导入默认知识库。

    - 已存在的同名文件直接跳过；
    - 单个文件解析失败不影响后续文件；
    - 目录不存在或为空时只打日志，不抛异常（容器首次启动合理路径）。
    """
    report = SyncReport()
    pdf_dir = _pdf_dir()

    if not pdf_dir.exists():
        logger.info(f"[antang-kb] PDF 目录不存在，跳过同步: {pdf_dir}")
        return report

    from AnTang.api.services.knowledge_file import KnowledgeFileService
    from AnTang.database.dao.knowledge_file import KnowledgeFileDao
    from AnTang.database.models.knowledge_file import Status

    knowledge_id = await ensure_default_knowledge()
    existing_files = await KnowledgeFileDao.select_knowledge_file(knowledge_id)
    existing_by_name = {f.file_name: f for f in existing_files}

    pdf_paths = sorted(p for p in pdf_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdf_paths:
        logger.info(f"[antang-kb] PDF 目录为空: {pdf_dir}")
        return report

    vector_count = await _vector_document_count(knowledge_id)
    needs_reindex_existing = bool(existing_by_name) and vector_count == 0
    if needs_reindex_existing:
        logger.warning(
            f"[antang-kb] 数据库已有 {len(existing_by_name)} 个文件记录，"
            f"但向量 collection 为空或不存在，将复用记录重建索引。"
        )

    logger.info(f"[antang-kb] 开始同步 {len(pdf_paths)} 个 PDF 到默认知识库 {knowledge_id}")

    for pdf_path in pdf_paths:
        file_name = pdf_path.name
        existing_file = existing_by_name.get(file_name)
        if existing_file:
            file_vector_count = 0
            if not needs_reindex_existing:
                file_vector_count = await _vector_file_document_count(knowledge_id, existing_file.id)
            if file_vector_count > 0 and existing_file.status == Status.success:
                report.skipped += 1
                continue
            if not needs_reindex_existing:
                logger.warning(
                    f"[antang-kb] {file_name} 已有数据库记录但缺少向量索引或状态异常，"
                    f"file_vector_count={file_vector_count}, status={existing_file.status}，将重建。"
                )

            try:
                await _reindex_existing_pdf(
                    knowledge_id,
                    existing_file,
                    pdf_path,
                    clear_existing_vectors=file_vector_count > 0,
                )
                report.reindexed += 1
                logger.info(f"[antang-kb] 重建 {file_name} 向量索引成功")
            except Exception as err:
                report.failed += 1
                logger.exception(f"[antang-kb] 重建 {file_name} 向量索引失败: {err}")
            continue

        try:
            file_size = pdf_path.stat().st_size
            await KnowledgeFileService.create_knowledge_file(
                file_name=file_name,
                file_path=str(pdf_path),
                knowledge_id=knowledge_id,
                user_id=SystemUser,
                oss_url=str(pdf_path),
                file_size_bytes=file_size,
            )
            report.imported += 1
            logger.info(f"[antang-kb] 导入 {file_name} 成功")
        except Exception as err:
            report.failed += 1
            logger.exception(f"[antang-kb] 导入 {file_name} 失败: {err}")

    logger.info(f"[antang-kb] 同步完成: {report}")
    return report
