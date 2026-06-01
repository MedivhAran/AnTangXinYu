"""安糖心语专用 Agent。

这个类继承通用 GeneralAgent，只在通用 ReAct Agent 之外追加安糖场景需要的能力：
- 每轮对话前运行轻量分析器，生成语境备忘录注入 system prompt；
- 根据血糖分层补充安全提醒；
- 跨轮维护轻量 DialogState 并通过 hidden event 持久化；
- 暴露图片理解、糖尿病知识检索、饮食/运动建议等安糖工具。
"""

import asyncio
import json
from datetime import datetime
from typing import AsyncGenerator, Sequence

import pytz
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool
from loguru import logger

from AnTang.core.agents.general_agent import AgentConfig, GeneralAgent
from AnTang.database.dao.cgm_report import CGMReportDao
from AnTang.database.dao.reminder import ReminderDao
from AnTang.database.models.reminder import ReminderTable
from AnTang.schemas.antang_analyzer import DialogState, LightAnalyzerResult
from AnTang.services.antang.capabilities import AnTangCapabilityService
from AnTang.services.antang.cgm_report import (
    CGMReportService,
    cgm_report_to_dict,
    format_cgm_summary_for_agent,
)
from AnTang.services.antang.light_analyzer import light_analyzer
from AnTang.services.antang.policies import classify_glucose_zone
from AnTang.services.antang.profile import AnTangProfileService
from AnTang.services.antang.prompts import (
    build_turn_system_prompt_v2,
    build_turn_user_message,
    DEFAULT_ANTANG_SYSTEM_PROMPT,
)
from AnTang.services.antang.state import (
    AnTangMemoryContext,
    AnTangVisionAnalysis,
    GlucoseContext,
)
from AnTang.services.antang.vision import AnTangVisionService
from AnTang.settings import app_settings
from AnTang.utils.helpers import build_completion_system_prompt


def _format_beijing_datetime() -> str:
    """返回给工具使用的北京时间字符串，避免模型自己猜当前日期。"""
    now = datetime.now(pytz.timezone("Asia/Shanghai"))
    weekday_map = {
        0: "周一",
        1: "周二",
        2: "周三",
        3: "周四",
        4: "周五",
        5: "周六",
        6: "周日",
    }
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')} " f"{weekday_map[now.weekday()]}（Asia/Shanghai）"


class AnTangAgent(GeneralAgent):
    """安糖心语 Agent 主类。

    通用工具、MCP、Skill、模型初始化仍复用 GeneralAgent；
    安糖特有逻辑集中在本类，便于和普通 Agent 行为隔离。
    """

    def __init__(self, agent_config: AgentConfig):
        super().__init__(agent_config)

        # 以下字段只缓存"当前这一轮"的上下文，工具闭包会从这里读取用户上传的血糖/图片信息。
        self.current_glucose_context: GlucoseContext | None = None
        self.current_file_url: str | None = None
        self.current_file_name: str | None = None
        self.current_vision_analysis: AnTangVisionAnalysis | None = None
        # 本轮会话 ID，供 create_reminder 工具知道提醒落进哪个会话。
        self.current_dialog_id: str | None = None
        # light_analyzer 对本轮上下文的理解，finalize_turn 把它作为 update_profile 的判断依据。
        self.last_analyzer_understanding: str = ""

    async def setup_tools(self) -> list[BaseTool]:
        """在通用工具基础上追加安糖专用工具。"""
        tools = await super().setup_tools()
        tools.extend(self._build_antang_tools())
        return tools

    def _build_antang_tools(self) -> list[BaseTool]:
        """构造安糖工具列表。

        这些工具以闭包方式定义，可以直接访问 self.current_* 中保存的本轮上下文。
        这样模型调用工具时不需要把血糖、图片 URL 等内部字段再暴露到工具参数里。
        """

        @tool(parse_docstring=True)
        async def get_current_beijing_time() -> str:
            """
            获取当前北京时间。

            当用户询问现在几点、今天日期、星期几、当前时间等事实问题时使用。

            Returns:
                str: 当前北京时间与星期信息。
            """
            return f"当前北京时间：{_format_beijing_datetime()}"

        @tool(parse_docstring=True)
        async def analyze_uploaded_image() -> str:
            """
            分析本轮用户上传的图片内容。

            当用户提到"这张图""这个截图""这顿饭""图片里是什么"等，并且本轮确实上传了图片时使用。

            Returns:
                str: 图片内容总结、识别出的关键内容、以及与血糖/饮食相关的线索。
            """
            # 图片分析成本较高，统一通过 _ensure_vision_analysis 做懒加载和缓存。
            analysis = await self._ensure_vision_analysis()
            if not analysis:
                return "本轮没有可分析的图片，或图片分析暂时不可用。"

            details = [f"图片整体看起来是：{analysis.summary}"]
            if analysis.detected_items:
                details.append("画面里比较明确的内容有：" + "、".join(analysis.detected_items))
            if analysis.glucose_related_hints:
                details.append("和血糖有关的线索是：" + "；".join(analysis.glucose_related_hints))
            if analysis.dietary_risk_hint:
                details.append("饮食和血糖提醒：" + analysis.dietary_risk_hint)
            return "\n".join(details)

        @tool(parse_docstring=True)
        async def retrieve_diabetes_knowledge(query: str) -> str:
            """
            检索当前用户的糖尿病/低血糖相关知识资料。

            当用户在问原理、处理方式、预防建议、饮食依据、运动注意事项等需要领域知识支撑的问题时使用。

            Args:
                query: 需要检索的具体问题。

            Returns:
                str: 检索到的知识内容；如果没有命中则返回未命中的说明。
            """
            knowledge_support = await AnTangCapabilityService.retrieve_knowledge(query=query)
            return knowledge_support or "没有命中直接相关的糖尿病知识资料。"

        @tool(parse_docstring=True)
        async def web_search(query: str) -> str:
            """
            联网搜索任意主题的公开信息。

            适用于回答模型本身不掌握或可能过时的事实型问题，覆盖范围不限于健康，例如：
            乐队/人物/作品介绍、新闻事件、城市资讯、最新指南、最新研究、产品/机构信息等。
            只要用户的问题需要外部互联网知识来支撑回答，都可以使用本工具。

            Args:
                query: 需要搜索的主题或具体问题。

            Returns:
                str: 搜索结果摘要；如果不可用则返回失败说明。
            """
            return await AnTangCapabilityService.web_search(query) or "联网搜索没有返回可用结果。"

        @tool(parse_docstring=True)
        async def lookup_weather(city: str) -> str:
            """
            查询指定城市天气。

            当用户要结合天气判断是否适合外出、运动、散步、跑步等场景时使用。

            Args:
                city: 要查询天气的城市名。

            Returns:
                str: 天气查询结果；如果不可用则返回失败说明。
            """
            return await AnTangCapabilityService.weather_lookup(city) or "天气查询没有返回可用结果。"

        @tool(parse_docstring=True)
        async def get_diet_support(query: str) -> str:
            """
            生成与当前问题相关的饮食建议。

            当用户在问"这顿饭能不能吃""要不要加餐""这个适不适合补糖"等饮食问题时使用。

            Args:
                query: 用户当前最关心的饮食问题。

            Returns:
                str: 面向当前场景的简洁饮食建议。
            """
            vision_analysis = (
                await self._ensure_vision_analysis() if self.current_file_url else self.current_vision_analysis
            )
            return await AnTangCapabilityService.diet_advice(
                model=self.conversation_model,
                user_input=query,
                glucose_context=self.current_glucose_context,
                vision_analysis=vision_analysis,
                glucose_zone=classify_glucose_zone(self.current_glucose_context),
                focus=query,
            )

        @tool(parse_docstring=True)
        async def get_exercise_support(query: str) -> str:
            """
            生成与当前问题相关的运动建议。

            当用户在问"现在能不能运动""适合散步还是休息""运动前后要注意什么"等问题时使用。

            Args:
                query: 用户当前最关心的运动问题。

            Returns:
                str: 面向当前场景的简洁运动建议。
            """
            return await AnTangCapabilityService.exercise_advice(
                model=self.conversation_model,
                user_input=query,
                glucose_context=self.current_glucose_context,
                glucose_zone=classify_glucose_zone(self.current_glucose_context),
                focus=query,
            )

        @tool(parse_docstring=True)
        async def text_to_image(prompt: str) -> str:
            """
            根据用户提供的提示词产生图片。

            当用户询问"帮我画一张图""生成一张图片""根据这个描述画图"等需要生成图片的场景时使用。

            Args:
                prompt: 用户的图片提示词。

            Returns:
                str: 生成的图片链接；如果不可用则返回失败说明。
            """
            return await AnTangCapabilityService.text_to_image(prompt) or "图片生成没有返回可用结果。"

        @tool(parse_docstring=True)
        async def import_cgm_report() -> str:
            """
            将本轮用户上传的 CGM(动态葡萄糖监测)评估报告 PDF 解析入库,并自动更新用户长期画像。

            **何时使用**:用户本轮上传了 PDF 附件,且文件名或对话内容暗示是血糖/葡萄糖/CGM/动态/
            评估报告。本工具会从对象存储下载 PDF,抽取 TIR / TAR / TBR / MG / SD / CV 等核心数值,
            写入 cgm_report 表并基于这份新报告刷新用户画像的"近期风险"。

            **何时不使用**:用户没传文件、或文件不是 PDF、或 PDF 明显不是 CGM 报告
            (例如普通说明书、化验单)。

            Returns:
                str: 一段简明中文摘要,说明导入成功与否、监测时段、核心 TIR/TAR/TBR 数值。
                  失败时返回失败原因,主对话可据此告知用户。
            """
            if not self.current_file_url:
                return "本轮对话没有上传文件,无法导入 CGM 报告。"
            if not self.current_file_name or not self.current_file_name.lower().endswith(".pdf"):
                return "本轮上传的文件不是 PDF,无法当作 CGM 报告解析。"
            report = await CGMReportService.parse_and_store(
                file_url=self.current_file_url,
                file_name=self.current_file_name,
                user_id=self.agent_config.user_id,
            )
            if report.parse_status != "success":
                return f"CGM 报告导入失败:{report.parse_error or '未知错误'}"
            return format_cgm_summary_for_agent(report)

        @tool(parse_docstring=True)
        async def get_latest_cgm_report() -> str:
            """
            获取当前用户最近一份成功解析的 CGM 报告数据。

            **何时使用**:用户询问"我最近的报告/血糖控制怎么样""上次监测情况""TIR 达标了吗"
            等需要看历史血糖监测数据的问题。

            Returns:
                str: 报告关键字段的 JSON 字符串(含 monitoring 时段、TIR/TAR/TBR、MG/SD/CV、
                  患者病史和目标范围)。若用户没有任何报告则返回提示。可以把此结果作为后续 CGM
                  解读 Skill 的输入。
            """
            report = await CGMReportDao.get_latest_by_user(self.agent_config.user_id)
            if not report:
                return "用户尚未上传过 CGM 报告。"
            return json.dumps(cgm_report_to_dict(report, slim=True), ensure_ascii=False)

        _RECURRENCE_DESC = {"once": "仅一次", "hourly": "每小时", "daily": "每天", "weekly": "每周"}

        @tool(parse_docstring=True)
        async def create_reminder(
            fire_time: str,
            content: str,
            recurrence: str = "once",
            repeat_count: int | None = None,
        ) -> str:
            """创建一个定时提醒，到点后我会主动给用户发消息提醒。

            当用户要求"提醒我做某事""到点叫我""每天/每周定时提醒"等时使用。设置前先调用
            get_current_beijing_time 获取当前时间，把"明天早上8点""半小时后"这类相对时间
            换算成绝对时间再传入。

            Args:
                fire_time: 首次触发的绝对北京时间，格式严格为 'YYYY-MM-DD HH:MM:SS'。
                content: 提醒事项内容，简洁描述要提醒用户做什么，如"测空腹血糖"。
                recurrence: 重复方式，可选 once(仅一次)/hourly(每小时)/daily(每天)/weekly(每周)，默认 once。
                repeat_count: 重复次数上限，仅对重复提醒有效；不填表示一直重复。

            Returns:
                str: 创建结果说明（含触发时间）；失败时返回失败原因。
            """
            if not self.current_dialog_id:
                return "无法创建提醒：当前会话上下文缺失。"
            recurrence = (recurrence or "once").lower()
            if recurrence not in _RECURRENCE_DESC:
                return f"不支持的重复方式：{recurrence}。只支持 once/hourly/daily/weekly。"
            try:
                fire_dt = datetime.strptime(fire_time.strip(), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return "时间格式不对，请用 'YYYY-MM-DD HH:MM:SS' 这种绝对北京时间。"
            now = datetime.now(pytz.timezone("Asia/Shanghai")).replace(tzinfo=None)
            if fire_dt <= now:
                return "提醒时间必须晚于当前时间，请重新确认时间。"

            reminder = ReminderTable(
                user_id=self.agent_config.user_id,
                dialog_id=self.current_dialog_id,
                content=content.strip(),
                recurrence=recurrence,
                repeat_count=repeat_count,
                next_fire_at=fire_dt,
            )
            await ReminderDao.create(reminder)
            return (
                f"已设置提醒：{content}（{_RECURRENCE_DESC[recurrence]}，下次 {fire_time}）。" f"到点我会主动提醒你。"
            )

        @tool(parse_docstring=True)
        async def list_reminders() -> str:
            """查看当前用户已设置、尚未结束的定时提醒列表。

            当用户问"我有哪些提醒""我设了什么闹钟""帮我看看待办提醒"等时使用。

            Returns:
                str: 提醒列表（含编号、内容、下次触发时间、重复方式）；没有则提示。
            """
            reminders = await ReminderDao.list_active_by_user(self.agent_config.user_id)
            if not reminders:
                return "你当前没有设置任何提醒。"
            lines = [
                f"[{r.id}] {r.content}"
                f"（{_RECURRENCE_DESC.get(r.recurrence, r.recurrence)}，"
                f"下次 {r.next_fire_at.strftime('%Y-%m-%d %H:%M:%S')}）"
                for r in reminders
            ]
            return "你当前的提醒：\n" + "\n".join(lines)

        @tool(parse_docstring=True)
        async def cancel_reminder(reminder_id: str) -> str:
            """取消一条已设置的定时提醒。

            当用户要求"取消提醒""别再提醒我了""删掉那个闹钟"时使用。通常先用
            list_reminders 拿到提醒编号，再调用本工具。

            Args:
                reminder_id: 要取消的提醒编号（来自 list_reminders）。

            Returns:
                str: 取消结果说明。
            """
            ok = await ReminderDao.cancel(reminder_id.strip(), self.agent_config.user_id)
            return "已取消该提醒。" if ok else "没找到这条提醒，或它已经结束/取消了。"

        tools = [
            get_current_beijing_time,
            analyze_uploaded_image,
            retrieve_diabetes_knowledge,
            web_search,
            lookup_weather,
            get_diet_support,
            get_exercise_support,
            text_to_image,
            import_cgm_report,
            get_latest_cgm_report,
            create_reminder,
            list_reminders,
            cancel_reminder,
        ]

        # 这份映射用于 GeneralAgent 的工具事件展示，把函数名翻译成前端更友好的名称。
        self.tool_metadata_map.update(
            {
                "get_current_beijing_time": {"name": "北京时间", "type": "安糖能力"},
                "analyze_uploaded_image": {"name": "图片理解", "type": "安糖能力"},
                "retrieve_diabetes_knowledge": {"name": "糖尿病知识库", "type": "安糖能力"},
                "web_search": {"name": "联网搜索", "type": "安糖能力"},
                "lookup_weather": {"name": "天气查询", "type": "安糖能力"},
                "get_diet_support": {"name": "饮食建议", "type": "安糖能力"},
                "get_exercise_support": {"name": "运动建议", "type": "安糖能力"},
                "text_to_image": {"name": "图片生成", "type": "安糖能力"},
                "import_cgm_report": {"name": "导入 CGM 报告", "type": "安糖能力"},
                "get_latest_cgm_report": {"name": "查看最近 CGM 报告", "type": "安糖能力"},
                "create_reminder": {"name": "设置提醒", "type": "安糖能力"},
                "list_reminders": {"name": "查看提醒", "type": "安糖能力"},
                "cancel_reminder": {"name": "取消提醒", "type": "安糖能力"},
            }
        )
        return tools

    async def _ensure_vision_analysis(self) -> AnTangVisionAnalysis | None:
        """确保本轮图片只分析一次。"""
        if self.current_vision_analysis:
            return self.current_vision_analysis
        if not self.current_file_url:
            return None

        self.current_vision_analysis = await AnTangVisionService.analyze_image(
            file_url=self.current_file_url,
            file_name=self.current_file_name,
        )
        return self.current_vision_analysis

    async def astream(
        self,
        *,
        user_input: str,
        short_history: Sequence[BaseMessage],
        history_summary: str | None,
        glucose_context: GlucoseContext | None,
        file_url: str | None = None,
        file_name: str | None = None,
        previous_dialog_state: DialogState | None = None,
        dialog_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """安糖每轮对话的流式入口。"""

        self.current_glucose_context = glucose_context
        self.current_file_url = file_url
        self.current_file_name = file_name
        self.current_vision_analysis = None
        self.current_dialog_id = dialog_id

        glucose_zone = classify_glucose_zone(glucose_context)

        # 长期记忆召回与轻量分析器并行：embedding+chroma 检索（~几百 ms）
        # 一般会比 qwen-turbo 分析器（~2s）先完成，所以下面的 await recall_task 通常是零等待。
        recall_task = (
            asyncio.create_task(
                AnTangProfileService.recall_context(user_id=self.agent_config.user_id, query=user_input)
            )
            if self.agent_config.user_id
            else None
        )  # 长期记忆召回，~500ms

        # 跑轻量分析器，分析用户的语境、恐惧、风险度，并且生成本轮的语境备忘录，把结果拼到 system prompt 使用
        analyzer_result = await light_analyzer.analyze(  # 分析器运行，1-2s
            user_input=user_input,
            short_history=list(short_history),
            history_summary=history_summary,
            dialog_state=previous_dialog_state,
            glucose_context=glucose_context,
            file_url=file_url,
            file_name=file_name,
            relevant_memory=[],
        )
        # 缓存本轮 understanding，供 finalize_turn 里 update_profile 当判断依据使用。
        self.last_analyzer_understanding = analyzer_result.memo.understanding

        memory_ctx = await recall_task if recall_task else None  # 一般已经准备好了
        long_term_memory = self._format_long_term_memory_block(memory_ctx)

        turn_prompt = build_turn_system_prompt_v2(
            base_prompt=DEFAULT_ANTANG_SYSTEM_PROMPT,
            analyzer_result=analyzer_result,
            glucose_zone=glucose_zone,
        )
        system_prompt = build_completion_system_prompt(turn_prompt, history_summary, long_term_memory)

        messages = [
            SystemMessage(content=system_prompt),
            *short_history,
            HumanMessage(
                content=build_turn_user_message(
                    user_input=user_input,
                    glucose_context=glucose_context,
                    file_url=file_url,
                    file_name=file_name,
                )
            ),
        ]

        response_chunks: list[str] = []

        # 真正调 ReAct
        async for event in super().astream(messages):
            # 当模型决定执行工具时，general_agent会抛出type: "event" 的通知（例如工具开始调用）。一旦产生了这种中间事件，说明之前模型输出的文本只是“前置思考流程”，而不是最终回答。因此执行 response_chunks.clear() 将其清空。
            if event.get("type") == "event":
                response_chunks.clear()
            chunk = self._extract_response_text(event)
            if chunk:
                response_chunks.append(chunk)
            yield event

        response_content = "".join(response_chunks).strip()

        new_dialog_state = self._update_dialog_state(
            previous=previous_dialog_state,
            analyzer_result=analyzer_result,
            assistant_response=response_content,
        )

        yield self.wrap_event(
            {
                "status": "END",
                "title": "antang_dialog_state",
                "message": "hidden state update",
                "event_type": "antang_dialog_state",
                "hidden": True,
                "details": new_dialog_state.model_dump(),
            }
        )

        if app_settings.antang_light_analyzer.show_internal_trace and analyzer_result is not None:
            memo = analyzer_result.memo
            yield self.wrap_event(
                {
                    "status": "END",
                    "title": "本轮分析备忘录",
                    "message": (
                        f"语境理解：{memo.understanding or '—'}\n"
                        f"核心担心：{memo.core_worry or '—'}\n"
                        f"回复节奏：{memo.reply_rhythm or '—'}\n"
                        f"避免事项：{memo.avoid or '—'}\n"
                        f"谨慎度：{analyzer_result.caution_level.value}"
                    ),
                    "event_type": "antang_analyzer_trace",
                    "details": analyzer_result.model_dump(),
                }
            )

    def _extract_response_text(self, event: dict) -> str:
        """从 super().astream 的事件中提取 assistant 可见文本。"""
        if event.get("type") == "response_chunk":
            return event.get("data", {}).get("chunk", "")
        return ""

    def _update_dialog_state(
        self,
        *,
        previous: DialogState | None,
        analyzer_result: LightAnalyzerResult | None,
        assistant_response: str,
    ) -> DialogState:
        """从分析器 memo 和主回复内容推断本轮结束后的 DialogState。"""
        prev = previous or DialogState()

        if analyzer_result is None:
            return prev

        memo = analyzer_result.memo

        rhythm = memo.reply_rhythm
        if "先接住" in rhythm or "接住" in rhythm:
            stage = "contain"
        elif "追问" in rhythm or "问一句" in rhythm or "澄清" in rhythm:
            stage = "clarify"
        elif "解释" in rhythm or "说明" in rhythm or "原理" in rhythm:
            stage = "explain"
        elif "建议" in rhythm or "小动作" in rhythm:
            stage = "suggest"
        else:
            stage = prev.current_stage

        emotions = []
        combined = f"{memo.core_worry} {memo.understanding}"
        emotion_keywords = {
            "害怕": "fear",
            "后怕": "fear",
            "恐惧": "fear",
            "焦虑": "anxiety",
            "担心": "anxiety",
            "紧张": "anxiety",
            "委屈": "sadness",
            "难过": "sadness",
            "沮丧": "sadness",
            "烦躁": "frustration",
            "生气": "frustration",
            "放心": "relief",
            "松了一口气": "relief",
        }
        for kw, tag in emotion_keywords.items():
            if kw in combined and tag not in emotions:
                emotions.append(tag)
        if not emotions:
            emotions = prev.dominant_emotions

        last_q = prev.last_followup_question
        stripped = assistant_response.strip()
        if "？" in stripped or "?" in stripped:
            normalized = stripped.replace("?", "？")
            parts = [p.strip() for p in normalized.split("？") if p.strip()]
            if parts:
                last_q = parts[-1][-60:] + "？"

        return DialogState(
            current_stage=stage,
            dominant_emotions=emotions[:2],
            last_followup_question=last_q,
        )

    async def finalize_turn(
        self,
        *,
        user_input: str,
        assistant_response: str,
    ) -> None:
        """把本轮对话写入长期记忆 + 更新结构化画像。

        两条线都走 fire-and-forget，不阻塞 SSE 关闭：
        - store_turn_memory：LLM 抽事实 + 合并去重 + embedding，3~10 秒。
        - update_profile：LLM 把"旧画像 + 本轮对话"合成新画像，2~5 秒。
        """
        if not self.agent_config.user_id or not assistant_response.strip():
            return
        asyncio.create_task(self._store_turn_memory_safely(user_input, assistant_response))
        asyncio.create_task(
            self._update_profile_safely(
                user_input=user_input,
                assistant_response=assistant_response,
                assessment_reason=self.last_analyzer_understanding,
            )
        )

    async def _store_turn_memory_safely(self, user_input: str, assistant_response: str) -> None:
        try:
            await asyncio.wait_for(
                AnTangProfileService.store_turn_memory(
                    user_id=self.agent_config.user_id,
                    user_input=user_input,
                    assistant_response=assistant_response,
                ),
                timeout=30.0,
            )
        except Exception as err:
            logger.warning(f"[antang-memory] 写入长期记忆失败: {err}")

    async def _update_profile_safely(
        self,
        *,
        user_input: str,
        assistant_response: str,
        assessment_reason: str,
    ) -> None:
        try:
            await asyncio.wait_for(
                AnTangProfileService.update_profile(
                    user_id=self.agent_config.user_id,
                    user_input=user_input,
                    assistant_response=assistant_response,
                    assessment_reason=assessment_reason,
                ),
                timeout=30.0,
            )
        except Exception as err:
            logger.warning(f"[antang-profile] 更新结构化画像失败: {err}")

    @staticmethod
    def _format_long_term_memory_block(ctx: AnTangMemoryContext | None) -> str | None:
        """把长期上下文渲染成给主 LLM 的文本块；没有内容时返回 None 让 system prompt 不加这一段。"""
        if ctx is None:
            return None
        parts: list[str] = []
        if ctx.profile_summary:
            parts.append(f"用户画像：{ctx.profile_summary}")
        if ctx.recalled_memories:
            bullets = "\n".join(f"- {m}" for m in ctx.recalled_memories)
            parts.append(f"相关历史片段：\n{bullets}")
        return "\n\n".join(parts) if parts else None
