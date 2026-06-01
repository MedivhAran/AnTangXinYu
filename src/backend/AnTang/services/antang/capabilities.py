"""安糖心语的能力封装。

这里不直接处理 HTTP 请求，也不负责最终对话生成；
它只把 RAG、联网搜索、天气、文生图、饮食/运动建议等能力包装成可复用方法，
供 AnTangAgent 中的工具调用。
"""

import asyncio

from langchain_core.language_models import BaseChatModel

from AnTang.core.models.manager import ModelManager
from AnTang.services.antang.knowledge import get_default_knowledge_id
from AnTang.services.antang.policies import glucose_zone_to_risk_level
from AnTang.services.antang.state import AnTangVisionAnalysis, GlucoseContext
from AnTang.services.rag.handler import RagHandler
from AnTang.tools.get_weather.action import get_weather
from AnTang.tools.web_search.tavily_search.action import tavily_search
from AnTang.tools.text2image.action import text_to_image


# 饮食/运动建议工具不是直接面向用户输出完整回复，而是给主助手提供一段可整合的依据。
DIET_ADVICE_PROMPT = """
你是安糖心语内部的饮食建议工具。请直接给主助手一个简洁、可靠的结论，帮助它回复用户。

要求：
- 先判断“适不适合、为什么、下一步怎么做”。
- 如果信息不足，要明确说明不确定性。
- 不要输出表格，不要长篇营养学解释。

【用户问题】
{user_input}

【血糖上下文】
{glucose_context}

【风险等级】
{risk_level}

【血糖分层】
{glucose_zone}

【图片分析】
{vision_summary}

【聚焦点】
{focus}
""".strip()

EXERCISE_ADVICE_PROMPT = """
你是安糖心语内部的运动建议工具。请直接给主助手一个简洁、可靠的结论，帮助它回复用户。

要求：
- 先回答“现在适不适合动、能做什么、要避免什么”。
- 如果信息不足，要明确说明不确定性。
- 不要输出表格，不要长篇讲原理。
- 控制在 3 到 5 句话。

【用户问题】
{user_input}

【血糖上下文】
{glucose_context}

【风险等级】
{risk_level}

【血糖分层】
{glucose_zone}

【聚焦点】
{focus}
""".strip()


def _render_glucose_context(glucose_context: GlucoseContext | None) -> str:
    """把结构化血糖上下文压缩成适合塞进工具提示词的短文本。"""
    if not glucose_context:
        return "未提供"

    items = []
    if glucose_context.current_value_mmol_l is not None:
        items.append(f"血糖 {glucose_context.current_value_mmol_l} mmol/L")
    items.append(f"趋势 {glucose_context.trend}")
    if glucose_context.measured_at:
        items.append(f"测量时间 {glucose_context.measured_at}")
    return "；".join(items)


class AnTangCapabilityService:
    """安糖能力服务。

    方法基本保持无状态，依赖调用方传入 user_id、知识库 ID、血糖上下文等信息。
    这样同一套能力既能被 Agent 工具使用，也方便后续被接口或脚本复用。
    """

    @classmethod
    async def retrieve_knowledge(cls, *, query: str) -> str | None:
        """检索安糖内置默认知识库（全体用户共用，不按用户隔离）。

        命中则返回可拼入回复的文本；未命中、或没有默认知识库时返回 None。
        """
        default_id = await get_default_knowledge_id()
        if not default_id:
            return None

        # RagHandler 屏蔽 ES/Milvus/rerank 细节，调用方只关心最终可拼入回复的文本。
        knowledge_message = await RagHandler.retrieve_ranked_documents(query, [default_id])
        if not knowledge_message or knowledge_message == "No relevant documents found.":
            return None
        return knowledge_message

    @classmethod
    async def web_search(cls, query: str) -> str | None:
        """联网搜索近期公开信息。

        tavily_search 是同步 Tool，这里放到线程池，避免阻塞 FastAPI 的事件循环。
        """
        if not query.strip():
            return None
        try:
            return await asyncio.to_thread(
                tavily_search.invoke,
                {
                    "query": query,
                    "topic": "general",
                    "max_results": 5,
                    "time_range": "month",
                },
            )
        except Exception as err:
            return f"联网搜索暂时不可用：{err}"

    @classmethod
    async def weather_lookup(cls, city: str) -> str | None:
        """查询天气，主要用于运动/外出场景的辅助判断。"""
        if not city.strip():
            return None
        try:
            return await asyncio.to_thread(
                get_weather.invoke,
                {"city": city},
            )
        except Exception as err:
            return f"天气查询暂时不可用：{err}"

    @classmethod
    async def text_to_image(cls, prompt: str) -> str | None:
        """调用文生图工具，返回生成结果链接或失败说明。"""
        if not prompt.strip():
            return None
        try:
            return await asyncio.to_thread(
                text_to_image.invoke,
                {"user_prompt": prompt},
            )
        except Exception as err:
            return f"图片生成暂时不可用：{err}"

    @classmethod
    async def diet_advice(
        cls,
        *,
        model: BaseChatModel | None,
        user_input: str,
        glucose_context: GlucoseContext | None,
        vision_analysis: AnTangVisionAnalysis | None,
        glucose_zone: str,
        focus: str,
    ) -> str:
        """根据当前血糖、图片和风险分层生成饮食建议依据。

        风险等级直接由 glucose_zone 派生（severe_low→urgent，low/low_warning→caution，其余 normal），
        不再由调用方传入。
        """
        tool_model = model or ModelManager.get_conversation_model()
        prompt = DIET_ADVICE_PROMPT.format(
            user_input=user_input,
            glucose_context=_render_glucose_context(glucose_context),
            risk_level=glucose_zone_to_risk_level(glucose_zone),
            glucose_zone=glucose_zone,
            vision_summary=vision_analysis.summary if vision_analysis else "无",
            focus=focus or "当前饮食是否适合作为补糖或维持血糖的选择",
        )
        response = await tool_model.ainvoke(prompt)
        return response.content

    @classmethod
    async def exercise_advice(
        cls,
        *,
        model: BaseChatModel | None,
        user_input: str,
        glucose_context: GlucoseContext | None,
        glucose_zone: str,
        focus: str,
    ) -> str:
        """根据当前血糖和风险分层生成运动建议依据。

        风险等级直接由 glucose_zone 派生，不再由调用方传入。
        """
        tool_model = model or ModelManager.get_conversation_model()
        prompt = EXERCISE_ADVICE_PROMPT.format(
            user_input=user_input,
            glucose_context=_render_glucose_context(glucose_context),
            risk_level=glucose_zone_to_risk_level(glucose_zone),
            glucose_zone=glucose_zone,
            focus=focus or "当前是否适合运动，以及运动前后需要注意什么",
        )
        response = await tool_model.ainvoke(prompt)
        return response.content
