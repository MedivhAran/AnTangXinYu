from enum import Enum
from pydantic import BaseModel, Field


class CautionLevel(str, Enum):
    """影响主 agent 语气和节奏的软约束，不是医疗危险等级。"""

    normal = "normal"
    careful = "careful"
    high_attention = "high_attention"


class AnalyzerMemo(BaseModel):
    """分析器输出的 4 栏备忘录，全部是短自然语言。"""

    understanding: str = Field(default="", description="这轮主要在发生什么，1~2句，≤80字")
    core_worry: str = Field(default="", description="具体怕什么，1句，≤50字")
    reply_rhythm: str = Field(default="", description="回复节奏建议，1~2句，≤100字")
    avoid: str = Field(default="", description="本轮最该避免的坑，1句，≤60字")


class LightAnalyzerResult(BaseModel):
    caution_level: CautionLevel = CautionLevel.normal
    memo: AnalyzerMemo = Field(default_factory=AnalyzerMemo)


class DialogState(BaseModel):
    """跨轮滚动的轻量对话状态。"""

    current_stage: str | None = None  # contain / clarify / explain / suggest
    dominant_emotions: list[str] = Field(default_factory=list)
    last_followup_question: str | None = None
