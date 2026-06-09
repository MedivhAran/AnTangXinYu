from loguru import logger
from typing import Optional
from AnTang.services.rag.retrieval import MixRetrival
from AnTang.services.rewrite.query_write import query_rewriter
from AnTang.services.rag.es_client import client as es_client
from AnTang.services.rag.vector_stores import milvus_client
from AnTang.services.rag.rerank import Reranker
from AnTang.settings import app_settings


def _rrf_fuse(ranked_lists, k: int = 60, limit: int = 30):
    """Reciprocal Rank Fusion：按"排名"而非原始分融合多路召回结果。

    ES 的 BM25 分数无上界、向量的余弦相似度在 0~1，两者量纲不一致，直接比大小不合理。
    RRF 只看每个 chunk 在各路里的名次：融合分 = Σ 1/(k + rank)，天然规避量纲问题。

    Args:
        ranked_lists: 每个元素是一路"已按相关度从高到低排好序"的 SearchModel 列表。
        k: RRF 常数，越大越削弱头部名次的优势，经验值 60。
        limit: 最多返回多少条（默认 30，给 reranker 足够候选池）。

    Returns:
        list: 按融合分降序、并按 chunk_id 去重后的结果。
    """
    scores: dict = {}
    representative: dict = {}
    for ranked in ranked_lists:
        seen_in_list = set()  # 同一路内同 chunk 只按最好名次计一次
        rank = 0
        for doc in ranked:
            if not doc.chunk_id or doc.chunk_id in seen_in_list:
                continue
            seen_in_list.add(doc.chunk_id)
            scores[doc.chunk_id] = scores.get(doc.chunk_id, 0.0) + 1.0 / (k + rank)
            representative.setdefault(doc.chunk_id, doc)
            rank += 1
    fused = sorted(representative.values(), key=lambda d: scores[d.chunk_id], reverse=True)
    return fused[:limit]


class RagHandler:

    @classmethod
    async def query_rewrite(cls, query):
        query_list = await query_rewriter.rewrite(query)
        return query_list

    @classmethod
    async def index_milvus_documents(cls, collection_name, chunks):
        return await milvus_client.insert(collection_name, chunks)

    @classmethod
    async def index_es_documents(cls, index_name, chunks):
        await es_client.index_documents(index_name, chunks)

    @classmethod
    async def mix_retrival_documents(cls, query_list, knowledges_id, search_field="summary"):

        if app_settings.rag.enable_elasticsearch:
            es_documents, milvus_documents = await MixRetrival.mix_retrival_documents(
                query_list, knowledges_id, search_field
            )
            # 两路各自按自身分数排成有序列表，再用 RRF 按名次融合，回避 BM25 与余弦的量纲不一致。
            ranked_lists = [
                sorted(es_documents, key=lambda d: d.score, reverse=True),
                sorted(milvus_documents, key=lambda d: d.score, reverse=True),
            ]
        else:
            milvus_documents = await MixRetrival.retrival_milvus_documents(query_list, knowledges_id, search_field)
            ranked_lists = [sorted(milvus_documents, key=lambda d: d.score, reverse=True)]

        # 融合 + 按 chunk_id 去重，最多返回 30 条（给 reranker 足够候选池）
        return _rrf_fuse(ranked_lists)

    @classmethod
    async def retrieve_ranked_documents(
        cls,
        query,
        collection_names,
        index_names=None,
        min_score: Optional[float] = None,
        top_k: Optional[int] = None,
        needs_query_rewrite: bool = True,
    ):
        """
        处理 RAG 流程：查询重写、文档检索、重排序、结果过滤和拼接。

        Args:
            query (str): 用户查询。
            collection_names (list[str]): 向量知识库 集合ID。
            index_names (list[str]): ES关键词库 集合ID。
            min_score (float): 文档最低分数阈值，默认为配置中的值。
            top_k (int): 召回文档的个数。
            needs_query_rewrite (bool): 是否需要开启Query重写，默认开启

        Returns:
            str: 拼接后的最终结果。
        """
        result = await cls._rank_for_query(
            query,
            collection_names,
            min_score=min_score,
            top_k=top_k,
            needs_query_rewrite=needs_query_rewrite,
        )
        final = result["final"]
        if not final:
            return "No relevant documents found."
        # 拼接最终结果
        return "\n".join(item["content"] for item in final)

    @classmethod
    async def _rank_for_query(cls, query, collection_names, *, min_score=None, top_k=None, needs_query_rewrite=True):
        """检索→精排→过滤的核心，返回结构化分阶段结果（带 chunk_id），供生产拼接与评估共用。

        Returns:
            dict: {
              "candidates": list[SearchModel],  # 粗召回(RRF)后、精排前，用于算召回天花板
              "final": list[dict],              # 精排+过滤+top_k 后，每项含 chunk_id/score/content
            }
        """
        if min_score is None:
            min_score = app_settings.rag.retrival.get("min_score")
        if top_k is None:
            top_k = app_settings.rag.retrival.get("top_k")

        rewritten_queries = await cls.query_rewrite(query) if needs_query_rewrite else [query]
        candidates = await cls.mix_retrival_documents(rewritten_queries, collection_names, "content")

        # 精排用原始 query；rerank 只吃 content，靠返回的 index 映射回原候选取回 chunk_id
        reranked_docs = await Reranker.rerank_documents(query, [doc.content for doc in candidates])

        actual_top_k = top_k if top_k is not None else 0
        docs_to_process = reranked_docs if len(reranked_docs) <= actual_top_k else reranked_docs[:actual_top_k]
        final = []
        for doc in docs_to_process:
            if min_score is not None and doc.score >= min_score:
                src = candidates[doc.index] if 0 <= doc.index < len(candidates) else None
                final.append(
                    {
                        "chunk_id": src.chunk_id if src else "",
                        "score": doc.score,
                        "content": doc.content,
                    }
                )
        return {"candidates": candidates, "final": final}



    @classmethod
    async def delete_documents_es_milvus(cls, file_id, knowledge_id):
        if app_settings.rag.enable_elasticsearch:
            await es_client.delete_documents(file_id, knowledge_id)
        await milvus_client.delete_by_file_id(file_id, knowledge_id)
