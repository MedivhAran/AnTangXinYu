from AnTang.settings import app_settings
from AnTang.services.memory.vector_stores.chroma import ChromaDB

class VectorStoreManager:

    @classmethod
    def get_chroma_vector(cls):
        # 与现有 RAG 的 ./vector_db 保持同一风格的"容器内相对路径"：
        # 容器里 cwd=/app，所以落到 /app/memory_db；docker-compose 把它挂到宿主机 data/memory_db。
        # 本地开发时启动目录决定写到哪，记得统一从 src/backend/ 起跑。
        return ChromaDB(
            collection_name=app_settings.default_config.get("memory_collection_name"),
            path="./memory_db",
        )

    @classmethod
    def get_milvus_vector(cls):
        pass