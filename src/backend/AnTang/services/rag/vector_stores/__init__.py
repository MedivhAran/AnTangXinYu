from AnTang.services.rag.vector_stores.milvus import MilvusClient
from AnTang.services.rag.vector_stores.milvus_lite import MilvusLiteClient
from AnTang.settings import app_settings

milvus_client = None
if app_settings.rag.vector_db.get("mode") == "lite":
    milvus_client = MilvusLiteClient()
else:
    milvus_client = MilvusClient()
