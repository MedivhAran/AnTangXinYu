import yaml
from typing import Literal, Optional
from loguru import logger
from types import SimpleNamespace
from pydantic.v1 import BaseSettings, Field

from AnTang.schemas.common import AntangLightAnalyzerConfig, BootstrapConfig, MultiModels, Tools, Rag, StorageConfig, ServerConfig


class Settings(BaseSettings):
    redis: dict = {}
    mysql: dict = {}
    langfuse: dict = {}
    whitelist_paths: list = []
    default_config: dict = {}
    bootstrap: Optional[BootstrapConfig] = BootstrapConfig()

    server: Optional[ServerConfig] = ServerConfig()
    rag: Optional[Rag] = None
    tools: Optional[Tools] = None
    storage: Optional[StorageConfig] = None
    multi_models: Optional[MultiModels] = None
    antang_light_analyzer: AntangLightAnalyzerConfig = Field(default_factory=AntangLightAnalyzerConfig)


app_settings = Settings()

async def init_app_settings(file_path: str = None):
    global app_settings

    file_path = file_path or "AnTang/config.yaml"
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            if data is None:
                logger.error("YAML 文件解析为空")
                return

            # 特殊处理multi_models配置
            if "multi_models" in data:
                data["multi_models"] = MultiModels(**data["multi_models"])

            if "tools" in data:
                data["tools"] = Tools(**data["tools"])

            if "rag" in data:
                data["rag"] = Rag(**data["rag"])

            if "storage" in data:
                data["storage"] = StorageConfig(**data["storage"])

            if "server" in data:
                data["server"] = ServerConfig(**data["server"])

            if "bootstrap" in data:
                data["bootstrap"] = BootstrapConfig(**data["bootstrap"])

            if "antang_light_analyzer" in data:
                data["antang_light_analyzer"] = AntangLightAnalyzerConfig(**data["antang_light_analyzer"])

            for key, value in data.items():
                setattr(app_settings, key, value)
    except Exception as e:
        logger.error(f"Yaml file loading error: {e}")
