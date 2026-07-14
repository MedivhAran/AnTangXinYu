from langchain_anthropic import ChatAnthropic

from antang_api.settings import settings


def build_deepseek_model() -> ChatAnthropic:
    """创建使用 Anthropic 协议连接 DeepSeek 的聊天模型。"""

    return ChatAnthropic(
        model_name=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_anthropic_base_url,
        timeout=settings.deepseek_timeout_seconds,
        max_tokens_to_sample=settings.deepseek_max_output_tokens,
        max_retries=0,
        stop=None,
        streaming=True,
        stream_usage=True,
    )
