import logging
from typing import Dict, List, Optional

from pydantic import BaseModel

try:
    import chromadb
    from chromadb.config import Settings
except ImportError:
    raise ImportError("The 'chromadb' library is required. Please install it using 'pip install chromadb'.")

from AnTang.services.memory.vector_stores.base import VectorStoreBase

logger = logging.getLogger(__name__)


class OutputData(BaseModel):
    """向量库查询结果的统一结构。"""

    id: Optional[str]  # 记忆 ID
    score: Optional[float]  # 距离（越小越相似）
    payload: Optional[Dict]  # 元数据


class ChromaDB(VectorStoreBase):
    def __init__(
        self,
        collection_name: str,
        client: Optional[chromadb.Client] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        path: Optional[str] = None,
    ):
        """初始化 Chroma 向量库。

        Args:
            collection_name (str): 集合名称。
            client (chromadb.Client, optional): 已有的 chromadb client 实例。默认 None。
            host (str, optional): chromadb 服务端地址。默认 None。
            port (int, optional): chromadb 服务端端口。默认 None。
            path (str, optional): 本地 chromadb 数据库路径。默认 None。
        """
        if client:
            self.client = client
        else:
            self.settings = Settings(anonymized_telemetry=False)

            if host and port:
                self.settings.chroma_server_host = host
                self.settings.chroma_server_http_port = port
                self.settings.chroma_api_impl = "chromadb.api.fastapi.FastAPI"
            else:
                if path is None:
                    path = "memory_db"

            self.settings.persist_directory = path
            self.settings.is_persistent = True

            self.client = chromadb.Client(self.settings)

        self.collection_name = collection_name
        self.collection = self.create_col(collection_name)

    def _parse_output(self, data: Dict) -> List[OutputData]:
        """把 Chroma 返回的原始结果解析成 OutputData 列表。

        Args:
            data (Dict): 原始输出数据。

        Returns:
            List[OutputData]: 解析后的数据。
        """
        keys = ["ids", "distances", "metadatas"]
        values = []

        for key in keys:
            value = data.get(key, [])
            if isinstance(value, list) and value and isinstance(value[0], list):
                value = value[0]
            values.append(value)

        ids, distances, metadatas = values
        max_length = max(len(v) for v in values if isinstance(v, list) and v is not None)

        result = []
        for i in range(max_length):
            entry = OutputData(
                id=ids[i] if isinstance(ids, list) and ids and i < len(ids) else None,
                score=(distances[i] if isinstance(distances, list) and distances and i < len(distances) else None),
                payload=(metadatas[i] if isinstance(metadatas, list) and metadatas and i < len(metadatas) else None),
            )
            result.append(entry)

        return result

    def create_col(self, name: str, embedding_fn: Optional[callable] = None):
        """创建（或获取已存在的）集合。

        Args:
            name (str): 集合名称。
            embedding_fn (Optional[callable]): 要使用的 embedding 函数。默认 None。

        Returns:
            chromadb.Collection: 创建或获取到的集合。
        """
        collection = self.client.get_or_create_collection(
            name=name,
            embedding_function=embedding_fn,
        )
        return collection

    def insert(
        self,
        vectors: List[list],
        payloads: Optional[List[Dict]] = None,
        ids: Optional[List[str]] = None,
    ):
        """向集合插入向量。

        Args:
            vectors (List[list]): 要插入的向量列表。
            payloads (Optional[List[Dict]], optional): 与向量一一对应的 payload 列表。默认 None。
            ids (Optional[List[str]], optional): 与向量一一对应的 ID 列表。默认 None。
        """
        logger.info(f"Inserting {len(vectors)} vectors into collection {self.collection_name}")
        self.collection.add(ids=ids, embeddings=vectors, metadatas=payloads)

    def search(
        self, query: str, vectors: List[list], limit: int = 5, filters: Optional[Dict] = None
    ) -> List[OutputData]:
        """检索相似向量。

        Args:
            query (str): 查询文本。
            vectors (List[list]): 用于检索的查询向量。
            limit (int, optional): 返回结果数量。默认 5。
            filters (Optional[Dict], optional): 检索时应用的过滤条件。默认 None。

        Returns:
            List[OutputData]: 检索结果。
        """
        where_clause = self._generate_where_clause(filters) if filters else None
        results = self.collection.query(query_embeddings=vectors, where=where_clause, n_results=limit)
        final_results = self._parse_output(results)
        return final_results

    def delete(self, vector_id: str):
        """按 ID 删除一个向量。

        Args:
            vector_id (str): 要删除的向量 ID。
        """
        self.collection.delete(ids=vector_id)

    def update(
        self,
        vector_id: str,
        vector: Optional[List[float]] = None,
        payload: Optional[Dict] = None,
    ):
        """更新一个向量及其 payload。

        Args:
            vector_id (str): 要更新的向量 ID。
            vector (Optional[List[float]], optional): 更新后的向量。默认 None。
            payload (Optional[Dict], optional): 更新后的 payload。默认 None。
        """
        self.collection.update(ids=vector_id, embeddings=vector, metadatas=payload)

    def get(self, vector_id: str) -> OutputData:
        """按 ID 读取一个向量。

        Args:
            vector_id (str): 要读取的向量 ID。

        Returns:
            OutputData: 读取到的向量。
        """
        result = self.collection.get(ids=[vector_id])
        return self._parse_output(result)[0]

    def list_cols(self) -> List[chromadb.Collection]:
        """列出全部集合。

        Returns:
            List[chromadb.Collection]: 集合列表。
        """
        return self.client.list_collections()

    def delete_col(self):
        """删除集合。"""
        self.client.delete_collection(name=self.collection_name)

    def col_info(self) -> Dict:
        """获取集合信息。

        Returns:
            Dict: 集合信息。
        """
        return self.client.get_collection(name=self.collection_name)

    def list(self, filters: Optional[Dict] = None, limit: int = 100) -> List[OutputData]:
        """列出集合中的全部向量。

        Args:
            filters (Optional[Dict], optional): 列举时应用的过滤条件。默认 None。
            limit (int, optional): 返回向量数量。默认 100。

        Returns:
            List[OutputData]: 向量列表。
        """
        where_clause = self._generate_where_clause(filters) if filters else None
        results = self.collection.get(where=where_clause, limit=limit)
        return [self._parse_output(results)]

    def reset(self):
        """通过删除并重建集合来重置索引。"""
        logger.warning(f"Resetting index {self.collection_name}...")
        self.delete_col()
        self.collection = self.create_col(self.collection_name)

    @staticmethod
    def _generate_where_clause(where: dict[str, any]) -> dict[str, any]:
        """生成符合 ChromaDB 要求的 where 过滤子句。

        Args:
            where (dict[str, any]): 过滤条件。

        Returns:
            dict[str, any]: 格式化后的 ChromaDB where 子句。
        """
        if where is None:
            return {}
        # 显式拒绝非字符串过滤值，避免老逻辑里多 key 时静默丢弃造成跨用户串读。
        for k, v in where.items():
            if not isinstance(v, str):
                raise TypeError(f"ChromaDB filter value for {k!r} must be str, got {type(v).__name__}={v!r}")
        if len(where.keys()) <= 1:
            return where
        return {"$and": [{k: v} for k, v in where.items()]}
