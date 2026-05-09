"""安糖心语的数据状态模型。

这些 Pydantic 模型在接口、Agent、能力服务之间传递结构化信息：
- GlucoseContext 来自前端输入；
- Safety/State/Execution 模型用于描述本轮风险和调度；
- Profile/Memory 模型用于长期画像和长期记忆；
- VisionAnalysis 模型用于图片理解结果。
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 以下 Literal 用来约束模型输出和内部状态，避免 LLM 或接口传入任意字符串。
EmotionTag = Literal["平静", "焦虑", "恐惧", "无助", "委屈", "烦躁", "内疚", "疲惫"]
GlucoseZone = Literal["severe_low", "low", "low_warning", "normal", "unknown"]
RiskLevel = Literal["urgent", "caution", "normal"]
Trend = Literal["falling", "stable", "rising", "unknown"]
VisionSceneType = Literal["meal", "glucose_related", "general", "unknown"]
CapabilityName = Literal[
    "memory_recall",
    "vision_analysis",
    "knowledge_retrieval",
    "web_search",
    "weather_lookup",
    "diet_advice",
    "exercise_advice",
]


class GlucoseContext(BaseModel):
    """前端传入的血糖上下文。

    Phase 1 中主要来自用户手动输入，后续如果接入设备或移动端，
    可以通过 source 区分数据来源。
    """

    current_value_mmol_l: Optional[float] = Field(None, description="当前血糖值，单位 mmol/L")
    trend: Trend = Field("unknown", description="血糖变化趋势")
    measured_at: Optional[str] = Field(None, description="测量时间")
    source: str = Field("manual", description="来源，Phase 1 固定为手动输入")

    @field_validator("current_value_mmol_l")
    @classmethod
    def validate_glucose_value(cls, value: Optional[float]):
        """限制血糖值范围，避免明显脏数据进入安全分层逻辑。"""
        if value is None:
            return value
        if value <= 0 or value > 40:
            raise ValueError("血糖值必须在 0 到 40 mmol/L 之间")
        return round(value, 1)


class AnTangIntentLevels(BaseModel):
    """用户意图/情绪强度的简单量化结果。"""

    panic_level: int = Field(0, ge=0, le=3, description="惊慌强度")
    helplessness_level: int = Field(0, ge=0, le=3, description="无助强度")
    emergency_level: int = Field(0, ge=0, le=3, description="危险紧急度")


class AnTangIntentExtraction(BaseModel):
    """从用户文本中抽取的意图和情绪信息。"""

    primary_intent: str = Field("情绪支持", description="用户当前最主要的诉求")
    emotion_tags: list[EmotionTag] = Field(default_factory=list, description="当前情绪标签")
    levels: AnTangIntentLevels = Field(default_factory=AnTangIntentLevels)
    explicit_help_request: bool = Field(False, description="是否明确在求助")
    mentions_physical_symptoms: bool = Field(False, description="是否提到身体症状")
    notes: str = Field("", description="简要解释")


class AnTangStateAssessment(BaseModel):
    """综合状态评估结果。

    这是更完整的评估模型，预留给后续“意图抽取 + 能力调度 + 画像更新”流程使用。
    当前主流程主要使用更轻量的 AnTangSafetyAssessment。
    """

    glucose_context: Optional[GlucoseContext] = None
    extracted_intent: AnTangIntentExtraction = Field(default_factory=AnTangIntentExtraction)
    glucose_zone: GlucoseZone = Field("unknown")
    risk_level: RiskLevel = Field("normal")
    need_safety_notice: bool = Field(False)
    needs_knowledge: bool = Field(False)
    reason: str = Field("", description="风险判断依据")
    knowledge_support: Optional[str] = Field(None, description="检索到的知识支持内容")


class AnTangSafetyAssessment(BaseModel):
    """本轮对话的轻量安全评估。

    该模型由 policies.assess_safety 生成，用来决定是否推送安全事件、
    是否切换 urgent 提示词，以及饮食/运动工具应采用的风险语境。
    """

    glucose_context: Optional[GlucoseContext] = None
    glucose_zone: GlucoseZone = Field("unknown")
    risk_level: RiskLevel = Field("normal")
    has_danger_signal: bool = Field(False, description="文本中是否出现明显危险求助信号")
    reason: str = Field("", description="本轮安全判断依据")
    needs_immediate_action: bool = Field(False, description="是否需要优先输出明确安全建议")


class AnTangInterventionPlan(BaseModel):
    """干预计划草稿。

    预留给后续把回复拆成“共情、解释、建议、鼓励”等结构化步骤时使用。
    """

    risk_level: RiskLevel = Field("normal")
    safety_notice: Optional[str] = Field(None)
    empathy: str = Field("")
    objective_explanation: str = Field("")
    knowledge_support: Optional[str] = Field(None)
    behavior_suggestion: str = Field("")
    positive_reinforcement: str = Field("")


class AnTangUserProfile(BaseModel):
    """安糖长期用户画像。

    只保留对后续陪伴和低血糖支持有帮助的信息，不记录无关闲聊细节。
    """

    common_low_glucose_times: list[str] = Field(default_factory=list, description="常见低血糖时段")
    common_triggers: list[str] = Field(default_factory=list, description="常见诱因")
    night_low_tendency: str = Field("", description="夜间低血糖倾向")
    dietary_preferences: list[str] = Field(default_factory=list, description="饮食/补糖偏好")
    exercise_patterns: list[str] = Field(default_factory=list, description="运动习惯与敏感性")
    emotional_patterns: list[str] = Field(default_factory=list, description="常见情绪模式")
    soothing_preferences: list[str] = Field(default_factory=list, description="更有效的安抚方式")
    recent_risk_notes: list[str] = Field(default_factory=list, description="近期重点风险提醒")
    summary: str = Field("", description="画像摘要")


class AnTangMemoryContext(BaseModel):
    """一轮对话可用的长期上下文。"""

    profile: AnTangUserProfile = Field(default_factory=AnTangUserProfile)
    profile_summary: str = Field("", description="显式用户画像摘要")
    recalled_memories: list[str] = Field(default_factory=list, description="命中的长期记忆片段")
    last_memory_excerpt: Optional[str] = Field(None, description="最近一次命中的记忆摘要")


class AnTangVisionAnalysis(BaseModel):
    """图片理解后的结构化结果。

    由 vision.py 将视觉模型描述二次整理得到，供图片分析工具和饮食建议工具使用。
    """

    scene_type: VisionSceneType = Field("unknown")
    summary: str = Field("", description="图像整体理解摘要")
    detected_items: list[str] = Field(default_factory=list, description="识别出的关键内容")
    glucose_related_hints: list[str] = Field(default_factory=list, description="血糖相关线索")
    dietary_risk_hint: str = Field("", description="饮食相关风险提示")
    recommended_follow_up: str = Field("", description="建议下一步追问或行动")


class AnTangExecutionPlan(BaseModel):
    """能力调度计划。

    预留给后续由模型先规划“本轮要不要检索、看图、查天气”等步骤。
    """

    reasoning: str = Field("", description="本轮调度原因")
    capability_order: list[CapabilityName] = Field(default_factory=list, description="本轮执行能力顺序")
    knowledge_query: str = Field("", description="知识检索问题")
    web_query: str = Field("", description="联网补充搜索词")
    weather_city: str = Field("", description="天气查询城市")
    diet_focus: str = Field("", description="饮食建议聚焦点")
    exercise_focus: str = Field("", description="运动建议聚焦点")


class AnTangCapabilityResult(BaseModel):
    """单个能力执行后的摘要结果。"""

    capability: CapabilityName
    summary: str = Field("", description="能力执行摘要")
    evidence: str = Field("", description="供最终回复整合的证据")
    tags: list[str] = Field(default_factory=list, description="展示标签")
