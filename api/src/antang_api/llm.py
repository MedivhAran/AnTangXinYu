from langchain_openai import ChatOpenAI

from antang_api.settings import settings


def _build_model(*, streaming: bool) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.hachimi_model_name,
        api_key=settings.hachimi_api_key,
        base_url=settings.hachimi_openai_base_url,
        timeout=settings.hachimi_timeout_seconds,
        max_completion_tokens=settings.hachimi_max_output_tokens,
        max_retries=0,
        streaming=streaming,
        stream_usage=streaming,
    )


def build_chat_model() -> ChatOpenAI:
    """创建用于 Core Agent 用户可见流式回复的 Gemini 模型。"""

    return _build_model(streaming=True)


def build_non_streaming_model() -> ChatOpenAI:
    """创建用于内部 Sub-agent、不会向聊天流发送 token 的 Gemini 模型。"""

    return _build_model(streaming=False)
