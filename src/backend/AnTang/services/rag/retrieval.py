import asyncio

from AnTang.services.rag.es_client import client as es_client
from AnTang.services.rag.vector_stores import milvus_client


class MixRetrival:

    @classmethod
    async def retrival_milvus_documents(cls, query, knowledges_id, search_field):
        """从Milvus（或 Chroma）检索文档。

        原实现是 query × knowledge_id 两层同步 for 循环串行 await，每次命中都要等 embedding + search 往返。
        这里展开成任务列表用 asyncio.gather 并发，其中单次 embedding 的调用由客户端各自保证串行/复用，
        相同 query 到不同集合的 search 之间互不依赖，可以安全并发。
        """
        queries = query if isinstance(query, list) else [query]

        tasks = []
        for q in queries:
            for knowledge_id in knowledges_id:
                if search_field == "summary":
                    tasks.append(milvus_client.search_summary(q, knowledge_id))
                else:
                    tasks.append(milvus_client.search(q, knowledge_id))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks)
        documents = []
        for sub in results:
            documents.extend(sub)
        return documents

    @classmethod
    async def retrival_es_documents(cls, query, knowledges_id, search_field):
        """从Elasticsearch检索文档（并发版本）。"""
        queries = query if isinstance(query, list) else [query]

        tasks = []
        for q in queries:
            for knowledge_id in knowledges_id:
                if search_field == "summary":
                    tasks.append(es_client.search_documents_summary(q, knowledge_id))
                else:
                    tasks.append(es_client.search_documents(q, knowledge_id))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks)
        documents = []
        for sub in results:
            documents.extend(sub)
        return documents

    @classmethod
    async def mix_retrival_documents(cls, query_list, knowledges_id, search_field):
        # ES 和向量库之间也可以并发，这里一起 gather。
        es_documents, milvus_documents = await asyncio.gather(
            cls.retrival_es_documents(query_list, knowledges_id, search_field),
            cls.retrival_milvus_documents(query_list, knowledges_id, search_field),
        )
        return es_documents, milvus_documents
