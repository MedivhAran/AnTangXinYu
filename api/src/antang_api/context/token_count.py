import json
import math
import re
from collections.abc import Sequence

from langchain.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage, convert_to_openai_messages
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool


_DATA_URI_PATTERN = re.compile(
    r"data:(?:application/pdf|image/[^;]+);base64,[A-Za-z0-9+/=]+"
)


async def count_input_tokens(
    _model: BaseChatModel,
    system_prompt: str,
    messages: Sequence[BaseMessage],
    tools: Sequence[BaseTool] | None = None,
) -> int:
    """按实际 OpenAI 请求结构估算输入 tokens，用于决定何时压缩。"""

    request_messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        *messages,
    ]

    payload = json.dumps(
        {
            "messages": convert_to_openai_messages(request_messages),
            "tools": [convert_to_openai_tool(tool) for tool in tools or ()],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    media_count = len(_DATA_URI_PATTERN.findall(payload))
    payload = _DATA_URI_PATTERN.sub("[attached media]", payload)
    ascii_count = sum(character.isascii() for character in payload)
    return math.ceil(ascii_count / 4) + len(payload) - ascii_count + media_count * 4096
