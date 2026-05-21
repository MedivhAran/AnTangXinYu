"""安糖心语的长期画像与长期记忆服务。

主对话循环里三件事的接通状态：
- recall_context 已通：每轮对话开始时召回画像 + 向量记忆，拼进 system prompt
- store_turn_memory 已通：每轮对话结束后 fire-and-forget 写入向量库
- update_profile 已通：每轮对话结束后 fire-and-forget 用 LLM 抽事实更新画像
"""

import asyncio
import json
from typing import Iterable

from AnTang.core.agents.structured_response_agent import StructuredResponseAgent
from AnTang.database.dao.antang_profile import AnTangProfileDao
from AnTang.services.antang.policies import ANTANG_AGENT_TYPE
from AnTang.services.antang.state import (
    AnTangMemoryContext,
    AnTangUserProfile,
)
from AnTang.services.memory.client import memory_client


PROFILE_UPDATE_PROMPT = """
你需要维护一个低血糖陪伴型智能体的用户长期画像。
请根据旧画像和本轮新增信息，输出更新后的结构化画像。

要求：
- 只保留对后续支持真正有用的信息。
- 如果信息不明确，不要编造。
- 常见字段尽量用简短中文短语。
- `summary` 需要是 3 到 6 句的中文摘要，适合前端直接展示。

【旧画像】
{profile_json}

【本轮用户输入】
{user_input}

【本轮助手回复】
{assistant_response}

【状态评估】
{assessment_reason}

【能力输出摘要】
{capability_summary}
""".strip()


class AnTangProfileService:
    """安糖用户画像服务。

    画像数据存在 MySQL 的 antang_profile 表，长期记忆存在 memory_client 背后的向量库。
    这层负责把两类信息合并成 Agent 可用的 AnTangMemoryContext。
    """

    @classmethod
    async def get_profile(cls, user_id: str) -> AnTangUserProfile:
        """读取用户显式画像。

        如果数据库里没有画像，或历史画像字段不完整，就返回空画像，避免阻断对话。
        """
        record = await AnTangProfileDao.get_by_user_id(user_id)
        if not record or not record.profile_data:
            return AnTangUserProfile()

        try:
            return AnTangUserProfile(**record.profile_data)
        except Exception:
            return AnTangUserProfile(summary=record.profile_summary or "")

    @classmethod
    async def recall_context(cls, *, user_id: str, query: str) -> AnTangMemoryContext:
        """召回本轮对话可用的长期上下文。

        返回值同时包含结构化画像和向量记忆片段：
        - profile/profile_summary 用于稳定描述用户长期特征；
        - recalled_memories 用于补充最近或语义相关的事实。

        任何召回失败（embedding/Chroma/MySQL 出错）都吞掉返回空 context，避免拖垮主对话。
        如需抑制离题召回，可给 memory_client.search 加 threshold（距离上限，越小越严，参考值 0.5~0.8）。
        """
        try:
            profile_record = await AnTangProfileDao.get_by_user_id(user_id)
            profile = await cls.get_profile(user_id)
            memory_result = await memory_client.search(
                query=query,
                user_id=user_id,
                agent_id=ANTANG_AGENT_TYPE,
                limit=5,
            )
        except Exception as err:
            from loguru import logger
            logger.warning(f"[antang-memory] recall_context 失败，回退空上下文: {err}")
            return AnTangMemoryContext()

        recalled_memories = [
            item.get("memory", "")
            for item in memory_result.get("results", [])
            if item.get("memory")
        ]
        return AnTangMemoryContext(
            profile=profile,
            profile_summary=profile_record.profile_summary if profile_record else profile.summary,
            recalled_memories=recalled_memories,
            last_memory_excerpt=profile_record.last_memory_excerpt if profile_record else None,
        )

    @classmethod
    async def store_turn_memory(
        cls,
        *,
        user_id: str,
        user_input: str,
        assistant_response: str,
    ) -> None:
        """把一轮用户-助手对话写入安糖专用长期记忆空间。"""
        await memory_client.add(
            messages=[
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": assistant_response},
            ],
            user_id=user_id,
            agent_id=ANTANG_AGENT_TYPE,
        )

    @classmethod
    async def update_profile(
        cls,
        *,
        user_id: str,
        user_input: str,
        assistant_response: str,
        assessment_reason: str = "",
        capability_outputs: Iterable[str] = (),
        last_memory_excerpt: str | None = None,
    ) -> AnTangUserProfile:
        """根据本轮对话更新结构化用户画像。

        StructuredResponseAgent 会强制模型输出 AnTangUserProfile 结构；
        如果模型更新失败，则保留当前画像，避免因为画像维护影响正常聊天。

        assessment_reason 是 light_analyzer 对本轮上下文的简短判断（通常用
        memo.understanding 字段），帮助 LLM 决定哪些信息值得沉淀到长期画像。
        """
        current_profile = await cls.get_profile(user_id)
        prompt = PROFILE_UPDATE_PROMPT.format(
            profile_json=json.dumps(current_profile.model_dump(), ensure_ascii=False, indent=2),
            user_input=user_input,
            assistant_response=assistant_response,
            assessment_reason=assessment_reason or "无",
            capability_summary="\n".join(capability_outputs) or "无",
        )
        updater = StructuredResponseAgent(AnTangUserProfile)
        try:
            # StructuredResponseAgent 是同步封装，放到线程池避免阻塞异步接口。
            updated_profile = await asyncio.to_thread(updater.get_structured_response, prompt)
        except Exception:
            updated_profile = current_profile

        await AnTangProfileDao.update_profile(
            user_id=user_id,
            profile_summary=updated_profile.summary,
            profile_data=updated_profile.model_dump(),
            last_memory_excerpt=last_memory_excerpt,
        )
        return updated_profile
