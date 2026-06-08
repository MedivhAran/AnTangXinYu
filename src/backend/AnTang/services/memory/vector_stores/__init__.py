from AnTang.settings import app_settings
from AnTang.services.memory.vector_stores.milvus import MilvusDB, MetricType


class VectorStoreManager:

    @classmethod
    def get_milvus_vector(cls):
        vector_cfg = app_settings.rag.vector_db
        host = vector_cfg.get("host", "127.0.0.1")
        port = str(vector_cfg.get("port", "19530"))
        url = f"http://{host}:{port}"

        collection_name = app_settings.default_config.get("memory_collection_name", "memory")
        embedding_dims = app_settings.default_config.get("memory_embedding_dims", 1024)

        return MilvusDB(
            url=url,
            token="",
            collection_name=collection_name,
            embedding_model_dims=embedding_dims,
            metric_type=MetricType.COSINE,
            db_name="",
        )
