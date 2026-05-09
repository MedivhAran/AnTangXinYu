"""安糖心语图片理解服务。

图片处理分两步：
1. 使用视觉模型把本地图片描述成自然语言；
2. 使用普通对话模型把描述整理成 AnTangVisionAnalysis 结构。

这样做的好处是，最终给 Agent 工具使用的是稳定字段，而不是不可控的长文本。
"""

import base64
import json
import re
from pathlib import Path

from loguru import logger

from AnTang.core.models.manager import ModelManager
from AnTang.services.antang.prompts import build_vision_structuring_prompt
from AnTang.services.antang.state import AnTangVisionAnalysis
from AnTang.services.storage import storage_client
from AnTang.utils.file_utils import get_object_name_from_aliyun_url, get_save_tempfile

VISION_DESCRIPTION_PROMPT = """
请分析这张图片。

要求：
- 先判断这更像是餐食照片、血糖相关界面/截图，还是其他日常图片。
- 识别关键物体、食物、设备、文本界面线索。
- 如果是餐食，说明可能的主要食物和它们与补糖/维持血糖的关系。
- 如果是血糖相关界面，说明你看到的关键线索，但不要假装精确读到了看不清的数值。
- 用自然中文输出 5 到 8 句，不要使用 Markdown。
""".strip()


class AnTangVisionService:
    """图片分析服务。

    对外只暴露 analyze_image，内部负责下载临时文件、调用模型、解析结构化结果和清理文件。
    """

    @classmethod
    async def analyze_image(cls, *, file_url: str, file_name: str | None = None) -> AnTangVisionAnalysis:
        """
        分析图片的顶层入口
        """
        file_path = None
        stage = "prepare"
        description = ""
        try:
            # 先把对象存储 URL 转成 object name，再下载到临时文件供视觉模型读取。
            stage = "resolve_object"
            object_name = get_object_name_from_aliyun_url(file_url)
            suffix = Path(file_name or object_name).suffix or ".png"
            file_path = get_save_tempfile(f"antang_image{suffix}")
            stage = "download_image"
            storage_client.download_file(object_name, file_path)

            # 第一步：视觉模型直接看图，输出自然语言描述。
            stage = "describe_image"
            description = await cls._describe_local_image(file_path)

            # 第二步：把描述转成固定字段，方便 Agent 后续稳定使用。
            stage = "structure_analysis"
            return await cls._structure_analysis(description)
        except Exception as err:
            logger.exception(f"AnTang vision analysis fallback at stage={stage}: {err}")
            if description:
                # 如果已经拿到视觉描述，但结构化失败，则用规则兜出一个可用摘要。
                return cls._build_best_effort_analysis(description)
            return AnTangVisionAnalysis(
                scene_type="unknown",
                summary="本轮已收到图片，但暂时无法稳定完成场景分析，将按文字上下文继续支持。",
                recommended_follow_up="如果你愿意，可以再用一句话补充这张图里最重要的信息。",
            )
        finally:
            # 临时图片只服务本轮分析，结束后立即清理。
            if file_path and Path(file_path).exists():
                Path(file_path).unlink(missing_ok=True)

    @classmethod
    async def _describe_local_image(cls, image_path: str) -> str:
        """
        调用视觉理解模型，描述本地图片
        """
        image_bytes = Path(image_path).read_bytes()
        image_type = cls._get_image_mime_subtype(image_path)
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        model = ModelManager.get_qwen_vl_model()
        # Qwen VL 接收 data URL，这里直接把临时文件转成 base64 图片输入。
        response = await model.ainvoke(
            [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": "你是一个擅长分析医疗生活场景图片的助手。"}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/{image_type};base64,{base64_image}"},
                        },
                        {
                            "type": "text",
                            "text": VISION_DESCRIPTION_PROMPT,
                        },
                    ],
                },
            ]
        )
        description = cls._coerce_text_content(response.content)
        if not description:
            raise ValueError("vision model returned empty description")
        return description

    @classmethod
    async def _structure_analysis(cls, description: str) -> AnTangVisionAnalysis:
        """
        对图片描述进行结构化分析，提取出场景类型、关键线索、风险提示等信息
        """
        model = ModelManager.get_conversation_model()
        response = await model.ainvoke(build_vision_structuring_prompt(description))
        response_text = cls._coerce_text_content(response.content)
        # 先提取 JSON，再做字段归一化，最后交给 Pydantic 校验。
        payload = cls._extract_json_payload(response_text)
        normalized_payload = cls._normalize_analysis_payload(payload)
        return AnTangVisionAnalysis.model_validate(normalized_payload)

    @classmethod
    def _coerce_text_content(cls, content) -> str:
        """兼容不同模型返回格式，把响应内容统一压成纯文本。"""
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()

        if content is None:
            return ""
        return str(content).strip()

    @classmethod
    def _extract_json_payload(cls, response_text: str) -> dict:
        """从模型输出中提取 JSON 对象。

        即使提示词要求只输出 JSON，模型偶尔也会包 Markdown 代码块，
        所以这里做一次宽容解析。
        """
        text = response_text.strip()
        if not text:
            raise ValueError("structured vision response is empty")

        fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fenced_match:
            text = fenced_match.group(1)
        elif not text.startswith("{"):
            object_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if object_match:
                text = object_match.group(0)

        return json.loads(text)

    @classmethod
    def _normalize_analysis_payload(cls, payload: dict) -> dict:
        """把模型字段做归一化，降低结构化输出的小偏差影响。"""
        normalized = dict(payload or {})
        normalized["scene_type"] = cls._normalize_scene_type(normalized.get("scene_type"))
        normalized["summary"] = str(normalized.get("summary") or "").strip()
        normalized["dietary_risk_hint"] = str(normalized.get("dietary_risk_hint") or "").strip()
        normalized["recommended_follow_up"] = str(normalized.get("recommended_follow_up") or "").strip()
        normalized["detected_items"] = cls._normalize_string_list(normalized.get("detected_items"))
        normalized["glucose_related_hints"] = cls._normalize_string_list(normalized.get("glucose_related_hints"))
        return normalized

    @classmethod
    def _normalize_scene_type(cls, raw_scene_type) -> str:
        """把中英文场景别名统一到 AnTangVisionAnalysis 支持的枚举值。"""
        value = str(raw_scene_type or "").strip().lower()
        mapping = {
            "meal": "meal",
            "food": "meal",
            "diet": "meal",
            "餐食": "meal",
            "饮食": "meal",
            "meal_photo": "meal",
            "glucose_related": "glucose_related",
            "glucose": "glucose_related",
            "blood_glucose": "glucose_related",
            "血糖": "glucose_related",
            "血糖相关": "glucose_related",
            "general": "general",
            "daily": "general",
            "日常": "general",
            "unknown": "unknown",
            "其他": "unknown",
        }
        return mapping.get(value, "unknown")

    @classmethod
    def _normalize_string_list(cls, value) -> list[str]:
        """把模型可能返回的字符串或列表统一成字符串列表。"""
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @classmethod
    def _build_best_effort_analysis(cls, description: str) -> AnTangVisionAnalysis:
        """结构化失败时的降级结果。

        这里仍尽量保留视觉模型已经看到的信息，避免用户上传图片后完全没有反馈。
        """
        summary = cls._build_summary_from_description(description)
        scene_type = cls._guess_scene_type(description)
        dietary_risk_hint = ""
        if scene_type == "meal":
            dietary_risk_hint = "从当前图像描述看更像一顿正餐。若当时正在处理低血糖，通常还需要结合更快吸收的糖类。"

        return AnTangVisionAnalysis(
            scene_type=scene_type,
            summary=summary,
            dietary_risk_hint=dietary_risk_hint,
            recommended_follow_up="如果你愿意，可以告诉我这张图里你最希望我重点判断的部分。",
        )

    @classmethod
    def _build_summary_from_description(cls, description: str) -> str:
        """从自然语言描述里截取前两句作为简短摘要。"""
        cleaned = re.sub(r"\s+", " ", description).strip()
        if not cleaned:
            return "本轮已收到图片，但暂时无法稳定完成场景分析，将按文字上下文继续支持。"

        sentence_candidates = re.split(r"(?<=[。！？])\s*", cleaned)
        sentences = [sentence.strip() for sentence in sentence_candidates if sentence.strip()]
        summary = "".join(sentences[:2]).strip()
        if summary:
            return summary
        return cleaned[:120]

    @classmethod
    def _guess_scene_type(cls, description: str) -> str:
        """根据关键词粗略判断图片场景，用于结构化失败后的兜底分析。"""
        text = description.lower()
        meal_keywords = ["饭", "菜", "餐", "米饭", "面", "粥", "晚饭", "午饭", "早餐", "食物", "餐盘"]
        glucose_keywords = ["血糖", "mmol", "传感器", "曲线", "读数", "胰岛素", "监测", "截图", "数值"]
        if any(keyword in text for keyword in glucose_keywords):
            return "glucose_related"
        if any(keyword in text for keyword in meal_keywords):
            return "meal"
        return "general"

    @classmethod
    def _get_image_mime_subtype(cls, image_path: str) -> str:
        """根据文件后缀生成 data URL 需要的 MIME subtype。"""
        suffix = Path(image_path).suffix.lower().lstrip(".")
        mime_map = {
            "jpg": "jpeg",
            "jpeg": "jpeg",
            "png": "png",
            "webp": "webp",
            "gif": "gif",
            "bmp": "bmp",
        }
        return mime_map.get(suffix, suffix or "png")
