"""安糖轻量分析器。

在主 ReAct 链路前运行，用小模型生成一份短 JSON（caution_level + 4 栏备忘录），
注入 system prompt 以增强主 agent 的语境理解。
超时或 JSON 非法时返回保守 fallback，不抛异常，不中断主链路。
"""

import asyncio
import json
import re
from loguru import logger

from AnTang.schemas.antang_analyzer import CautionLevel, AnalyzerMemo, LightAnalyzerResult
from AnTang.settings import app_settings


class LightAnalyzer:

    def __init__(self):
        self._model = None

    async def _get_model(self):
        """选择分析器模型。

        - 配置了 multi_models.light_analyzer.model_name：使用该模型独立的 api_key/base_url
        - 未配置：回退到 conversation_model（主对话模型通常较慢，仅作兜底）
        """
        if self._model is not None:
            return self._model

        from AnTang.core.models.manager import ModelManager

        analyzer_cfg = app_settings.multi_models.light_analyzer

        if analyzer_cfg.model_name:
            self._model = ModelManager.get_user_model(
                model=analyzer_cfg.model_name,
                base_url=analyzer_cfg.base_url,
                api_key=analyzer_cfg.api_key,
            )
        else:
            logger.warning("[antang-analyzer] multi_models.light_analyzer.model_name 未配置，回退到 conversation_model")
            self._model = ModelManager.get_conversation_model()

        return self._model

    async def analyze(
        self,
        *,
        user_input: str,
        short_history: list,
        history_summary: str | None,
        dialog_state,
        glucose_context,
        file_url: str | None = None,
        file_name: str | None = None,
        relevant_memory: list[str] | None = None,
    ) -> LightAnalyzerResult | None:
        cfg = app_settings.antang_light_analyzer
        if not cfg.enabled:
            return None

        from AnTang.prompts.antang.light_analyzer import ANALYZER_SYSTEM_PROMPT

        user_message = self._build_analyzer_input(
            user_input=user_input,
            short_history=short_history,
            history_summary=history_summary,
            dialog_state=dialog_state,
            glucose_context=glucose_context,
            file_url=file_url,
            file_name=file_name,
            relevant_memory=relevant_memory,
        )

        last_error = None
        for attempt in range(2):
            try:
                model = await self._get_model()
                response = await asyncio.wait_for(
                    model.bind(max_tokens=cfg.max_output_tokens).ainvoke([
                        {"role": "system", "content": ANALYZER_SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ]),
                    timeout=cfg.timeout_ms / 1000.0,
                )
                return self._parse_response(response.content)
            except asyncio.TimeoutError as e:
                last_error = e
                logger.warning(f"[antang-analyzer] 超时，第 {attempt + 1} 次失败")
            except Exception as e:
                last_error = e
                logger.warning(f"[antang-analyzer] 失败，第 {attempt + 1} 次: {e}")

        logger.warning(f"[antang-analyzer] 最终 fallback，last_error={last_error}")
        return self._fallback()

    def _build_analyzer_input(self, **kwargs) -> str:
        parts = [f"【用户输入】\n{kwargs['user_input']}"]

        if kwargs.get("short_history"):
            cfg = app_settings.antang_light_analyzer
            recent = kwargs["short_history"][-cfg.max_short_history_messages:]
            history_text = "\n".join(
                f"[{getattr(m, 'type', '?')}] {str(m.content)[:200]}"
                for m in recent
            )
            parts.append(f"【最近对话】\n{history_text}")

        if kwargs.get("history_summary"):
            parts.append(f"【历史摘要】\n{kwargs['history_summary'][:500]}")

        ds = kwargs.get("dialog_state")
        if ds and ds.current_stage:
            parts.append(
                f"【上一轮状态】\n"
                f"stage={ds.current_stage}\n"
                f"emotions={ds.dominant_emotions}\n"
                f"last_question={ds.last_followup_question or ''}"
            )

        gc = kwargs.get("glucose_context")
        if gc and getattr(gc, "current_value_mmol_l", None) is not None:
            parts.append(
                f"【血糖】\n"
                f"value={gc.current_value_mmol_l} mmol/L\n"
                f"trend={getattr(gc, 'trend', None)}"
            )

        if kwargs.get("file_url"):
            parts.append(f"【附件】\n文件名={kwargs.get('file_name', '未知')}")

        if kwargs.get("relevant_memory"):
            parts.append("【相关记忆】\n" + "\n".join(kwargs["relevant_memory"][:2]))

        return "\n\n".join(parts)

    def _parse_response(self, raw: str) -> LightAnalyzerResult:
        text = raw.strip()

        if text.startswith("```"):
            lines = text.split("\n")
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1])

        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            text = match.group(0)

        data = json.loads(text)
        result = LightAnalyzerResult(**data)

        result.memo.understanding = result.memo.understanding[:80].strip()
        result.memo.core_worry = result.memo.core_worry[:50].strip()
        result.memo.reply_rhythm = result.memo.reply_rhythm[:100].strip()
        result.memo.avoid = result.memo.avoid[:60].strip()
        return result

    def _fallback(self) -> LightAnalyzerResult:
        return LightAnalyzerResult(
            caution_level=CautionLevel.careful,
            memo=AnalyzerMemo(
                understanding="本轮分析结果不可用。",
                core_worry="",
                reply_rhythm="先接住用户感受，再根据上下文自然推进。",
                avoid="不要一上来列很多建议。",
            ),
        )


light_analyzer = LightAnalyzer()
