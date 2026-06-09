from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class MemoryItem(BaseModel):
    """一条记忆的标准结构，search/get/get_all 都把底层结果整形成它再返回。"""

    id: str = Field(..., description="该文本数据的唯一标识")
    memory: str = Field(
        ..., description="从文本数据中提炼出的记忆内容"
    )  # TODO 等平台侧 prompt 调整后，再更新这里
    hash: Optional[str] = Field(None, description="记忆内容的哈希值")
    # metadata 的取值可以是任意类型，不限于字符串，待完善
    metadata: Optional[Dict[str, Any]] = Field(None, description="该文本数据的附加元数据")
    score: Optional[float] = Field(None, description="与该文本数据关联的得分（向量距离）")
    created_at: Optional[str] = Field(None, description="记忆创建时间戳")
    updated_at: Optional[str] = Field(None, description="记忆更新时间戳")
