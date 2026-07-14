from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import BaseMessage, ToolMessage


CLEARED_TOOL_RESULT_CONTENT = "[较早的工具结果已从当前模型上下文中清理]"


@dataclass(frozen=True)
class ClearedToolResults:
    """仅用于本次模型输入的工具结果清理结果。"""

    messages: tuple[BaseMessage, ...]
    cleared_count: int


def clear_old_tool_results(
    messages: Sequence[BaseMessage],
    recent_tool_results_to_keep: int,
) -> ClearedToolResults:
    """用固定占位符替换较早的 ToolMessage，保留最近的真实结果。

    AIMessage 中的工具名称、调用 ID 和参数保持不变，因此模型仍能看懂
    当时执行了什么。函数只复制内存中的消息，不会修改数据库记录。
    """

    if recent_tool_results_to_keep < 1:
        raise ValueError("recent_tool_results_to_keep 必须大于 0")

    tool_result_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, ToolMessage)
    ]
    indexes_to_clear = set(tool_result_indexes[:-recent_tool_results_to_keep])

    if not indexes_to_clear:
        return ClearedToolResults(tuple(messages), 0)

    cleared_messages = [
        (
            message.model_copy(update={"content": CLEARED_TOOL_RESULT_CONTENT})
            if index in indexes_to_clear
            else message
        )
        for index, message in enumerate(messages)
    ]

    return ClearedToolResults(
        messages=tuple(cleared_messages),
        cleared_count=len(indexes_to_clear),
    )
