"""安糖心语的轻量策略规则。

本文件只保留不依赖大模型的确定性判断逻辑：
- 识别安糖 Agent 类型；
- 根据血糖值和趋势做客观生理分层。

语气/危险等级判断已交给 light_analyzer，不再做关键词硬匹配。
"""

from AnTang.services.antang.state import GlucoseContext


ANTANG_AGENT_NAME = "安糖心语"
ANTANG_AGENT_TYPE = "AnTangAgent"


def classify_glucose_zone(glucose_context: GlucoseContext | None) -> str:
    """根据血糖上下文做风险分层。

    当前只做简单阈值判断：
    - severe_low: 明确严重低血糖；
    - low: 已低于常用低血糖阈值；
    - low_warning: 接近低血糖且趋势下降；
    - normal/unknown: 其余场景。
    """
    if not glucose_context or glucose_context.current_value_mmol_l is None:
        return "unknown"

    value = glucose_context.current_value_mmol_l
    if value < 3.0:
        return "severe_low"
    if 3.0 <= value < 3.9:
        return "low"
    if 3.9 <= value <= 4.5 and glucose_context.trend == "falling":
        return "low_warning"
    return "normal"
