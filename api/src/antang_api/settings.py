from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从.env文件中读取配置"""

    deepseek_api_key: SecretStr = Field(min_length=1)
    deepseek_model: str = Field(min_length=1)
    deepseek_timeout_seconds: float = Field(gt=0)
    deepseek_anthropic_base_url: str = "https://api.deepseek.com/anthropic"
    deepseek_max_output_tokens: int = Field(default=4096, gt=0)
    tavily_api_key: SecretStr = Field(min_length=1)
    # Tavily 没有承诺最终字符串的总上限；以下是项目自己的初始安全边界。
    tavily_search_max_snippet_chars: int = Field(default=2_000, gt=0)
    tavily_fetch_max_content_chars: int = Field(default=10_000, gt=0)

    hindsight_mode: Literal["disabled", "shadow", "online"] = "disabled"
    hindsight_base_url: str = "http://127.0.0.1:8888"
    hindsight_api_key: SecretStr | None = None
    hindsight_timeout_seconds: float = Field(default=300, gt=0)
    hindsight_retain_user_turns: int = Field(default=3, gt=0)
    hindsight_recall_budget: Literal["low", "mid", "high"] = "low"
    hindsight_recall_max_tokens: int = Field(default=2_048, gt=0)

    database_url: str = Field(pattern=r"^postgresql\+psycopg://")
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_minutes: int = Field(gt=0)
    refresh_token_days: int = Field(gt=0)

    proactive_care_worker_poll_seconds: float = Field(default=5, gt=0)
    proactive_care_lease_minutes: int = Field(default=20, gt=0)
    proactive_care_max_attempts: int = Field(default=3, gt=0)
    proactive_care_retry_seconds: int = Field(default=60, gt=0)

    expo_push_access_token: SecretStr | None = None
    expo_push_timeout_seconds: float = Field(default=15, gt=0)
    push_delivery_worker_poll_seconds: float = Field(default=5, gt=0)
    push_delivery_lease_seconds: int = Field(default=60, gt=0)
    push_delivery_max_attempts: int = Field(default=5, gt=0)
    push_delivery_retry_seconds: int = Field(default=30, gt=0)
    push_receipt_delay_seconds: int = Field(default=900, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    # 输入达到 150000 token触发压缩
    context_compaction_trigger_tokens: int = Field(
        default=150_000,
        gt=0,
    )

    # 保留20条原始消息
    context_recent_messages_to_keep: int = Field(
        default=20,
        gt=0,
    )

    # 工具结果先于对话摘要清理，避免已经失效的大段网页内容占满上下文。
    context_tool_result_cleanup_trigger_tokens: int = Field(
        default=100_000,
        gt=0,
    )
    context_recent_tool_results_to_keep: int = Field(
        default=3,
        gt=0,
    )

    # 一轮对话中限制模型连续调用工具的次数和单次并行数量。
    agent_max_tool_rounds: int = Field(default=10, gt=0)
    agent_max_parallel_tool_calls: int = Field(default=5, gt=0)

    @property
    def langgraph_database_url(self) -> str:
        """把 SQLAlchemy 的地址转换成 psycopg 直接使用的地址"""
        sqlalchemy_prefix = "postgresql+psycopg://"
        return "postgresql://" + self.database_url.removeprefix(sqlalchemy_prefix)

    @model_validator(mode="after")
    def validate_hindsight(self) -> Self:
        if self.hindsight_mode != "disabled" and (
            self.hindsight_api_key is None
            or not self.hindsight_api_key.get_secret_value()
        ):
            raise ValueError("启用 Hindsight 时必须配置 HINDSIGHT_API_KEY")
        if self.expo_push_timeout_seconds >= self.push_delivery_lease_seconds:
            raise ValueError("Expo Push HTTP 超时必须短于发送租约")
        return self


settings = Settings()  # pyright: ignore[reportCallIssue]
