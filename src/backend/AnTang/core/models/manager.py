from typing import Any, Dict, Tuple

from openai import AsyncOpenAI
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from AnTang.core.models.embedding import EmbeddingModel
from AnTang.settings import app_settings


class ModelManager:
    """模型实例工厂。

    各个 `get_*_model` 原本每次调用都会新建一个 `ChatOpenAI` / `AsyncOpenAI`：
    热路径上每轮对话会反复创建多个客户端，浪费连接和 tokenizer 初始化。
    这里按 (model_name, api_key, base_url) 做进程内缓存，确保同一份配置只建一次。
    配置通过 YAML 启动时加载且运行中不会变，缓存长期持有是安全的。
    """

    _cached: Dict[Tuple[Any, ...], Any] = {}

    @classmethod
    def _get_or_create_chat_openai(
        cls,
        kind: str,
        model_name: str,
        api_key: str,
        base_url: str,
        extra_body: dict | None = None,
    ) -> ChatOpenAI:
        # extra_body 来自 yaml，启动时一次性加载、运行不变，所以不纳入 cache key。
        key = ("chat", kind, model_name, api_key, base_url)
        cached = cls._cached.get(key)
        if cached is not None:
            return cached
        kwargs: dict = dict(
            stream_usage=True,
            model=model_name,
            api_key=api_key,
            base_url=base_url,
        )
        if extra_body:
            kwargs["extra_body"] = extra_body
        instance = ChatOpenAI(**kwargs)
        cls._cached[key] = instance
        return instance

    @classmethod
    def get_conversation_model(cls, **kwargs) -> BaseChatModel:
        conversation_model = app_settings.multi_models.conversation_model
        return cls._get_or_create_chat_openai(
            "conversation",
            conversation_model.model_name,
            conversation_model.api_key,
            conversation_model.base_url,
            extra_body=conversation_model.extra_body,
        )

    @classmethod
    def get_qwen_vl_model(cls) -> BaseChatModel:
        qwen_vl_model = app_settings.multi_models.qwen_vl
        return cls._get_or_create_chat_openai(
            "qwen_vl",
            qwen_vl_model.model_name,
            qwen_vl_model.api_key,
            qwen_vl_model.base_url,
        )

    @classmethod
    def get_user_model(cls, **kwargs) -> BaseChatModel:
        # 用户绑定的自定义模型也可能被多次请求复用，按配置三元组做缓存。
        # db 的 llm 表不含 extra_body 字段。当 db 模型的 (model_name, base_url) 恰好
        # 与 yaml conversation_model 相同（典型情况：项目仅有一个对话模型，db 与 yaml
        # 各存一份），自动继承 yaml 中的 extra_body，避免 deepseek 思考模式等厂商参数
        # 在这条路径上丢失。
        model_name = kwargs.get("model")
        base_url = kwargs.get("base_url")
        cm = app_settings.multi_models.conversation_model
        extra_body = None
        if cm.extra_body and model_name == cm.model_name and base_url == cm.base_url:
            extra_body = cm.extra_body
        return cls._get_or_create_chat_openai(
            "user",
            model_name,
            kwargs.get("api_key"),
            base_url,
            extra_body=extra_body,
        )

    @classmethod
    def get_embedding_openai_model(cls) -> AsyncOpenAI:
        """以ChatOpenAI的形式输出"""
        embedding_model = app_settings.multi_models.embedding
        key = ("async_openai", embedding_model.base_url, embedding_model.api_key)
        cached = cls._cached.get(key)
        if cached is not None:
            return cached
        instance = AsyncOpenAI(base_url=embedding_model.base_url, api_key=embedding_model.api_key)
        cls._cached[key] = instance
        return instance

    @classmethod
    def get_embedding_model(cls) -> EmbeddingModel:
        embedding_model = app_settings.multi_models.embedding

        return EmbeddingModel(
            model=embedding_model.model_name, base_url=embedding_model.base_url, api_key=embedding_model.api_key
        )
