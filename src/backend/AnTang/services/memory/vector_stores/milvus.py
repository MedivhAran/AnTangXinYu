import logging
from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel

from AnTang.services.memory.vector_stores.base import VectorStoreBase

try:
    import pymilvus  # noqa: F401
except ImportError:
    raise ImportError("The 'pymilvus' library is required. Please install it using 'pip install pymilvus'.")

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient

logger = logging.getLogger(__name__)


class OutputData(BaseModel):
    """向量库查询结果的统一结构。"""

    id: Optional[str]  # 记忆 ID
    score: Optional[float]  # 距离
    payload: Optional[Dict]  # 元数据


class MetricType(str, Enum):
    """
    milvus/zilliz 服务端使用的距离度量常量。
    """

    def __str__(self) -> str:
        return str(self.value)

    L2 = "L2"
    IP = "IP"
    COSINE = "COSINE"
    HAMMING = "HAMMING"
    JACCARD = "JACCARD"

class MilvusDB(VectorStoreBase):
    """Milvus/Zilliz 向量库后端（当前安糖默认用 Chroma，此后端预留未启用）。"""

    def __init__(
        self,
        url: str,
        token: str,
        collection_name: str,
        embedding_model_dims: int,
        metric_type: MetricType,
        db_name: str,
    ) -> None:
        """初始化 MilvusDB 数据库。

        Args:
            url (str): Milvus/Zilliz 服务端的完整 URL。
            token (str): Zilliz 服务端的 token/api_key；本地部署时默认 None。
            collection_name (str): 集合名称（默认 mem0）。
            embedding_model_dims (int): embedding 模型的维度（默认 1536）。
            metric_type (MetricType): 相似度检索使用的度量类型（默认 L2）。
            db_name (str): 数据库名称（默认 ""）。
        """
        self.collection_name = collection_name
        self.embedding_model_dims = embedding_model_dims
        self.metric_type = metric_type
        self.client = MilvusClient(uri=url, token=token, db_name=db_name)
        self.create_col(
            collection_name=self.collection_name,
            vector_size=self.embedding_model_dims,
            metric_type=self.metric_type,
        )

    def create_col(
        self,
        collection_name: str,
        vector_size: str,
        metric_type: MetricType = MetricType.COSINE,
    ) -> None:
        """创建一个 index_type 为 AUTOINDEX 的新集合。

        Args:
            collection_name (str): 集合名称（默认 mem0）。
            vector_size (str): embedding 模型的维度（默认 1536）。
            metric_type (MetricType, optional): 相似度检索的度量类型。默认 MetricType.COSINE。
        """

        if self.client.has_collection(collection_name):
            logger.info(f"Collection {collection_name} already exists. Skipping creation.")
        else:
            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=512),
                FieldSchema(name="vectors", dtype=DataType.FLOAT_VECTOR, dim=vector_size),
                FieldSchema(name="metadata", dtype=DataType.JSON),
            ]

            schema = CollectionSchema(fields, enable_dynamic_field=True)

            index = self.client.prepare_index_params(
                field_name="vectors", metric_type=metric_type, index_type="AUTOINDEX", index_name="vector_index"
            )
            self.client.create_collection(collection_name=collection_name, schema=schema, index_params=index)

    def insert(self, ids, vectors, payloads, **kwargs: Optional[dict[str, any]]):
        """向集合插入向量。

        Args:
            vectors (List[List[float]]): 要插入的向量列表。
            payloads (List[Dict], optional): 与向量一一对应的 payload 列表。
            ids (List[str], optional): 与向量一一对应的 ID 列表。
        """
        for idx, embedding, metadata in zip(ids, vectors, payloads):
            data = {"id": idx, "vectors": embedding, "metadata": metadata}
            self.client.insert(collection_name=self.collection_name, data=data, **kwargs)

    def _create_filter(self, filters: dict):
        """组装可高效查询的过滤表达式。

        Args:
            filters (dict): 过滤条件 [user_id, agent_id, run_id]

        Returns:
            str: 格式化后的过滤表达式。
        """
        operands = []
        for key, value in filters.items():
            if isinstance(value, str):
                operands.append(f'(metadata["{key}"] == "{value}")')
            else:
                operands.append(f'(metadata["{key}"] == {value})')

        return " and ".join(operands)

    def _parse_output(self, data: list):
        """把 Milvus 返回的原始结果解析成 OutputData 列表。

        Args:
            data (Dict): 原始输出数据。

        Returns:
            List[OutputData]: 解析后的数据。
        """
        memory = []

        for value in data:
            uid, score, metadata = (
                value.get("id"),
                value.get("distance"),
                value.get("entity", {}).get("metadata"),
            )

            memory_obj = OutputData(id=uid, score=score, payload=metadata)
            memory.append(memory_obj)

        return memory

    def search(self, query: str, vectors: list, limit: int = 5, filters: dict = None) -> list:
        """检索相似向量。

        Args:
            query (str): 查询文本。
            vectors (List[float]): 查询向量。
            limit (int, optional): 返回结果数量。默认 5。
            filters (Dict, optional): 检索时应用的过滤条件。默认 None。

        Returns:
            list: 检索结果。
        """
        query_filter = self._create_filter(filters) if filters else None
        hits = self.client.search(
            collection_name=self.collection_name,
            data=[vectors],
            limit=limit,
            filter=query_filter,
            output_fields=["*"],
        )
        result = self._parse_output(data=hits[0])
        return result

    def delete(self, vector_id):
        """按 ID 删除一个向量。

        Args:
            vector_id (str): 要删除的向量 ID。
        """
        self.client.delete(collection_name=self.collection_name, ids=vector_id)

    def update(self, vector_id=None, vector=None, payload=None):
        """更新一个向量及其 payload。

        Args:
            vector_id (str): 要更新的向量 ID。
            vector (List[float], optional): 更新后的向量。
            payload (Dict, optional): 更新后的 payload。
        """
        schema = {"id": vector_id, "vectors": vector, "metadata": payload}
        self.client.upsert(collection_name=self.collection_name, data=schema)

    def get(self, vector_id):
        """按 ID 读取一个向量。

        Args:
            vector_id (str): 要读取的向量 ID。

        Returns:
            OutputData: 读取到的向量。
        """
        result = self.client.get(collection_name=self.collection_name, ids=vector_id)
        output = OutputData(
            id=result[0].get("id", None),
            score=None,
            payload=result[0].get("metadata", None),
        )
        return output

    def list_cols(self):
        """列出全部集合。

        Returns:
            List[str]: 集合名称列表。
        """
        return self.client.list_collections()

    def delete_col(self):
        """删除集合。"""
        return self.client.drop_collection(collection_name=self.collection_name)

    def col_info(self):
        """获取集合信息。

        Returns:
            Dict[str, Any]: 集合信息。
        """
        return self.client.get_collection_stats(collection_name=self.collection_name)

    def list(self, filters: dict = None, limit: int = 100) -> list:
        """列出集合中的全部向量。

        Args:
            filters (Dict, optional): 列举时应用的过滤条件。
            limit (int, optional): 返回向量数量。默认 100。

        Returns:
            List[OutputData]: 向量列表。
        """
        query_filter = self._create_filter(filters) if filters else None
        result = self.client.query(collection_name=self.collection_name, filter=query_filter, limit=limit)
        memories = []
        for data in result:
            obj = OutputData(id=data.get("id"), score=None, payload=data.get("metadata"))
            memories.append(obj)
        return [memories]

    def reset(self):
        """通过删除并重建集合来重置索引。"""
        logger.warning(f"Resetting index {self.collection_name}...")
        self.delete_col()
        self.create_col(self.collection_name, self.embedding_model_dims, self.metric_type)
