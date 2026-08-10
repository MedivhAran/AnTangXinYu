from langchain_anthropic import ChatAnthropic

from antang_api.settings import settings


def _build_deepseek_model(*, streaming: bool) -> ChatAnthropic:
    return ChatAnthropic(
        model_name=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_anthropic_base_url,
        timeout=settings.deepseek_timeout_seconds,
        max_tokens_to_sample=settings.deepseek_max_output_tokens,
        max_retries=0,
        stop=None,
        streaming=streaming,
        stream_usage=streaming,
    )


def build_deepseek_model() -> ChatAnthropic:
    """创建用于 Core Agent 用户可见流式回复的 DeepSeek 模型。"""

    return _build_deepseek_model(streaming=True)


def build_deepseek_non_streaming_model() -> ChatAnthropic:
    """创建用于内部 Sub-agent、不会向聊天流发送 token 的 DeepSeek 模型。"""

    return _build_deepseek_model(streaming=False)
