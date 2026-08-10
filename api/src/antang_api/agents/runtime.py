from dataclasses import dataclass
from typing import Literal, TypedDict
from uuid import UUID


class ToolResponseError(RuntimeError):
    """供应商已经响应，但返回内容无法交给模型使用。"""

    def __init__(self, message: str, *, artifact: object | None = None) -> None:
        super().__init__(message)
        self.artifact = artifact


@dataclass(frozen=True, slots=True)
class AgentContext:
    """只在一次 Agent 运行中有效的用户与运行范围。"""

    user_id: UUID
    run_id: UUID
    input_message_count: int
    # Core 模型调用必须提供已经参与精确 token 计数的完整提示词。
    # 不直接调用 Core 模型的内部 Agent 可以留空。
    rendered_system_prompt: str | None = None

    def __post_init__(self) -> None:
        if self.input_message_count < 0:
            raise ValueError("input_message_count 不能小于 0")
        if (
            self.rendered_system_prompt is not None
            and not self.rendered_system_prompt.strip()
        ):
            raise ValueError("rendered_system_prompt 不能为空白文字")


class ToolActivity(TypedDict):
    """工具中间件发给聊天流的内部生命周期事件。"""

    event: Literal["tool_activity"]
    tool_call_id: str
    tool_name: str
    status: Literal["started", "completed", "failed", "cancelled"]
