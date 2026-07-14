import asyncio
from collections.abc import Sequence

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool


async def count_input_tokens(
    model: ChatAnthropic,
    system_prompt: str,
    messages: Sequence[BaseMessage],
    tools: Sequence[BaseTool] | None = None,
) -> int:
    """通过模型服务商的接口精确计算一次请求的输入 tokens。"""

    request_messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        *messages,
    ]

    # LangChain 的官方计数方法是同步函数会阻塞事件循环，放到线程池中，
    return await asyncio.to_thread(
        model.get_num_tokens_from_messages,
        request_messages,
        tools=tools,
    )
