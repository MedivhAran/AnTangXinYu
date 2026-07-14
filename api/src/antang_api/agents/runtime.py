from dataclasses import dataclass
from typing import Literal, TypedDict
from uuid import UUID


class ToolResponseError(RuntimeError):
    """供应商已经响应，但返回内容无法交给模型使用。"""

    def __init__(self, message: str, *, artifact: object | None = None) -> None:
        super().__init__(message)
        self.artifact = artifact


@dataclass(frozen=True, slots=True)
class CoreAgentContext:
    """只在一次 Core Agent 运行中有效的应用上下文。"""

    user_id: UUID
    run_id: UUID
    input_message_count: int

    def __post_init__(self) -> None:
        if self.input_message_count < 0:
            raise ValueError("input_message_count 不能小于 0")


class ToolActivity(TypedDict):
    """工具中间件发给聊天流的内部生命周期事件。"""

    event: Literal["tool_activity"]
    tool_call_id: str
    tool_name: str
    status: Literal["started", "completed", "failed", "cancelled"]
