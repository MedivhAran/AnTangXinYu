from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator


class ModelConfig(BaseModel):
    model_name: str = ""
    api_key: str = ""
    base_url: str = ""
    # 透传给 OpenAI 兼容 API 的 extra_body，用于关闭 DeepSeek 思考模式等厂商特殊参数。
    extra_body: Optional[dict] = None


class MultiModels(BaseModel):
    class Config:
        # 允许从dict额外属性创建模型
        extra = "allow"

    conversation_model: ModelConfig = Field(default_factory=ModelConfig)
    qwen_vl: ModelConfig = Field(default_factory=ModelConfig)
    text2image: ModelConfig = Field(default_factory=ModelConfig)
    embedding: ModelConfig = Field(default_factory=ModelConfig)
    rerank: ModelConfig = Field(default_factory=ModelConfig)
    light_analyzer: ModelConfig = Field(default_factory=ModelConfig)


class Tools(BaseModel):
    class Config:
        extra = "allow"

    weather: dict = Field(default_factory=dict)
    tavily: dict = Field(default_factory=dict)
    google: dict = Field(default_factory=dict)
    delivery: dict = Field(default_factory=dict)
    bocha: dict = Field(default_factory=dict)


class Rag(BaseModel):
    class Config:
        extra = "allow"

    enable_elasticsearch: bool = Field(default=False)
    enable_summary: bool = Field(default=False)
    enable_ik_analyzer: bool = Field(default=False)
    retrival: dict = Field(default_factory=dict)
    split: dict = Field(default_factory=dict)
    elasticsearch: dict = Field(default_factory=dict)
    vector_db: dict = Field(default_factory=dict)


class BootstrapConfig(BaseModel):
    """控制启动时的系统数据初始化，避免每次启动都跑重型同步。"""

    init_default_tools: bool = True
    init_system_mcp: bool = True
    refresh_system_mcp_on_startup: bool = False
    # 系统 Skill 自动 seed：扫描 src/backend/AnTang/skills/ 下目录写入 agent_skill 表。
    init_system_skills: bool = True
    # 启动时强制覆盖已存在的同名系统 Skill（开发调试用，默认关）。
    refresh_system_skills_on_startup: bool = False


class OSSConfig(BaseModel):
    access_key_id: str
    access_key_secret: str
    endpoint: str
    bucket_name: str
    base_url: str


class MinioConfig(BaseModel):
    access_key_id: str
    access_key_secret: str
    endpoint: str
    bucket_name: str
    base_url: str


class StorageConfig(BaseModel):
    mode: Literal["oss", "minio"]
    oss: Optional[OSSConfig] = None
    minio: Optional[MinioConfig] = None

    @model_validator(mode="after")
    def validate_storage(self):
        if self.mode == "oss" and not self.oss:
            raise ValueError("mode=oss 时必须提供 aliyun_oss")
        if self.mode == "minio" and not self.minio:
            raise ValueError("mode=minio 时必须提供 minio")
        return self

    @property
    def active(self):
        return self.oss if self.mode == "oss" else self.minio


class ServerConfig(BaseModel):
    name: str = "AgentChat"
    version: str = "2.5.0"
    host: str = "127.0.0.1"
    port: int = 7860
    env: str = "dev"


class AntangLightAnalyzerConfig(BaseModel):
    """安糖轻量分析器的行为开关。模型身份信息位于 multi_models.light_analyzer。"""

    enabled: bool = True
    timeout_ms: int = 3000
    max_output_tokens: int = 220
    max_short_history_messages: int = 6
    show_internal_trace: bool = False


class ReminderConfig(BaseModel):
    """心跳提醒系统的行为开关。生成提醒文案所用模型沿用 multi_models.light_analyzer。"""

    enabled: bool = True
    check_interval_seconds: int = 30
    generate_timeout_ms: int = 10000
    max_output_tokens: int = 200


class ProactiveCareConfig(BaseModel):
    """主动关怀开关。默认关闭，因为它会在用户没主动询问时主动发消息，部署时确认无误再打开。
    具体的计量阈值/加分/衰减等调参常量在 services/antang/proactive_care.py 里。"""

    enabled: bool = True
    scan_interval_seconds: int = 300  # 关怀扫描的降频间隔（心跳每 30s 一跳，这里约 5 分钟扫一次）
