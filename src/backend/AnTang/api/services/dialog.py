from loguru import logger

from AnTang.api.services.agent import AgentService
from AnTang.core.callbacks import usage_metadata_callback
from AnTang.core.models.manager import ModelManager
from AnTang.database.dao.dialog import DialogDao
from AnTang.database.dao.history import HistoryDao
from AnTang.database.models.user import AdminUser
from AnTang.prompts.completion import GENERATE_CHAT_SUMMARY
from AnTang.services.antang.policies import ANTANG_AGENT_TYPE


class DialogService:

    @classmethod
    async def create_dialog(cls, name: str, user_id: str):
        """Create a new dialog bound to the system 安糖心语 agent"""
        try:
            dialog = await DialogDao.create_dialog(
                name=name, agent_type=ANTANG_AGENT_TYPE, user_id=user_id
            )
            return dialog.to_dict()
        except Exception as err:
            raise ValueError(f"Add Dialog Appear Error: {err}")

    @classmethod
    async def select_dialog(cls, dialog_id: str):
        """Select dialog by dialog_id"""
        try:
            results = await DialogDao.select_dialog_by_id(dialog_id=dialog_id)
            return [res.to_dict() for res in results]
        except Exception as err:
            raise ValueError(f"Select Dialog Appear Error: {err}")

    @classmethod
    async def get_list_dialog(cls, user_id: str):
        """Get all dialogs for a user"""
        try:
            results = await DialogDao.get_dialog_by_user(user_id=user_id)
            return [res.to_dict() for res in results]
        except Exception as err:
            raise ValueError(f"Get List Dialog Appear Error: {err}")

    @classmethod
    async def get_agent_by_dialog_id(cls, dialog_id: str):
        """Get agent information by dialog_id"""
        try:
            runtime_config = await cls.get_dialog_runtime_config(dialog_id)
            return runtime_config["agent"]
        except Exception as err:
            raise ValueError(f"Select Dialog Appear Error: {err}")

    @classmethod
    async def get_dialog_runtime_config(cls, dialog_id: str):
        """Get merged dialog and agent runtime config by dialog_id"""
        try:
            dialog = await DialogDao.get_agent_by_dialog_id(dialog_id=dialog_id)
            if not dialog:
                raise ValueError("对话不存在")

            agent = await AgentService.get_antang_agent()
            if not agent:
                raise ValueError("安糖心语智能体不存在")

            return {
                "dialog": dialog.to_dict(),
                "agent": agent,
            }
        except Exception as err:
            raise ValueError(f"Get Dialog Runtime Config Error: {err}")

    @classmethod
    async def touch_dialog_last_active(cls, dialog_id: str):
        """Touch dialog last active time without mutating create_time."""
        try:
            await DialogDao.touch_dialog_last_active(dialog_id=dialog_id)
        except Exception as err:
            raise ValueError(f"Touch Dialog Last Active Time Appear Error: {err}")

    @classmethod
    async def update_dialog_time(cls, dialog_id: str):
        """Backward-compatible alias for touching dialog last active time."""
        await cls.touch_dialog_last_active(dialog_id=dialog_id)

    @classmethod
    async def update_dialog_name(cls, dialog_id: str, name: str, user_id: str):
        """Update dialog name after verifying ownership."""
        try:
            dialog = await DialogDao.select_dialog_by_id(dialog_id)
            if not dialog:
                raise ValueError("对话不存在")
            if user_id not in (AdminUser, dialog.user_id):
                raise ValueError("没有权限访问")

            clean_name = name.strip()
            if not clean_name:
                raise ValueError("会话名称不能为空")

            updated_dialog = await DialogDao.update_dialog_name(dialog_id=dialog_id, name=clean_name)
            return updated_dialog.to_dict()
        except Exception as err:
            raise ValueError(f"Update Dialog Name Appear Error: {err}")

    @classmethod
    async def delete_dialog(cls, dialog_id: str):
        """Delete dialog and its history"""
        try:
            await DialogDao.delete_dialog_by_id(dialog_id=dialog_id)
            await HistoryDao.delete_history_by_dialog_id(dialog_id=dialog_id)
        except Exception as err:
            raise ValueError(f"Delete Dialog Appear Error: {err}")

    @classmethod
    async def verify_user_permission(cls, dialog_id: str, user_id: str):
        """Verify user has permission to access dialog"""
        dialog = await DialogDao.get_agent_by_dialog_id(dialog_id=dialog_id)
        if user_id not in (AdminUser, dialog.user_id):
            raise ValueError(f"没有权限访问")

    @classmethod
    async def get_dialog_history_summary(cls, dialog_id):
        dialog = await DialogDao.select_dialog_by_id(dialog_id)
        return dialog.summary

    @classmethod
    async def update_dialog_summary(cls, dialog_id: str, user_id: str, cutoff_tokens: int = 3000):
        """上下文压缩总结。"""

        # 1. 从mysql数据库取全部消息
        messages = await HistoryDao.select_history_from_time(dialog_id=dialog_id, k=10000)
        dialog = await DialogDao.select_dialog_by_id(dialog_id)

        if dialog.user_id != user_id:
            raise ValueError(f"无权限访问 {dialog_id} 数据")
        current_summary = dialog.summary
        summary_last_time = dialog.summary_last_time

        # 2. 按 summary_last_time 筛选出 上次总结之后的新消息
        if not messages:
            return None
        if summary_last_time:
            incremental_messages = [m for m in messages if m.create_time and m.create_time > summary_last_time]
        else:
            incremental_messages = messages

        if not incremental_messages:
            return None

        # 3. 将新消息按user/assistant两两分组
        pairs = []
        i = 0
        while i < len(incremental_messages) - 1:
            if incremental_messages[i].role == "user" and incremental_messages[i + 1].role == "assistant":
                pairs.append((incremental_messages[i], incremental_messages[i + 1]))
                i += 2
            else:
                i += 1

        if not pairs:
            return None

        # 至少保留一对
        # 如果只有一对，并且 token 超过 cutoff → 不总结
        if len(pairs) == 1:
            pair_tokens = sum((m.token_usage or len(m.content) // 4) for m in pairs[0])
            if pair_tokens > cutoff_tokens:
                return None

        # 4. 从后往前累计 token，保留最近cutoff_tokens(默认3000)内的消息不总结，保留为原始上下文
        total_tokens = 0
        kept_pairs = []

        for pair in reversed(pairs):
            pair_tokens = sum((m.token_usage or len(m.content) // 4) for m in pair)

            if total_tokens + pair_tokens > cutoff_tokens:
                break

            kept_pairs.append(pair)
            total_tokens += pair_tokens

        kept_pairs = list(reversed(kept_pairs))

        # 切分messages
        cutoff_index = len(pairs) - len(kept_pairs)
        old_pairs = pairs[:cutoff_index]

        # 保证 new 至少有一对
        if not kept_pairs:
            # 把最后一对强行放入 new
            kept_pairs = [pairs[-1]]
            old_pairs = pairs[:-1]

        if not old_pairs:
            return None

        # 构造 summary 输入
        def format_messages(msgs):
            texts = []
            for m in msgs:
                role = "User" if m.role == "user" else "Assistant"
                texts.append(f"{role}: {m.content}")
            return "\n".join(texts)

        old_messages = [m for pair in old_pairs for m in pair]

        summary_input = f"""
        【已有总结】
        {current_summary or "（暂无）"}

        【新增需要总结的对话】
        {format_messages(old_messages)}
        """

        # 调用模型总结对话
        messages_prompt = GENERATE_CHAT_SUMMARY.format(summary_input=summary_input)
        summary = await cls._generate_messages_summary(messages_prompt)

        # 更新 summary_last_time
        # 用“最后一个参与总结的消息时间”
        last_time = max((m.create_time for m in old_messages if m.create_time), default=None)

        await DialogDao.update_dialog_summary(dialog_id, summary, last_time)
        return None

    @classmethod
    async def _generate_messages_summary(cls, messages_prompt):
        conversation_model = ModelManager.get_conversation_model()
        response = await conversation_model.ainvoke(
            input=messages_prompt, config={"callbacks": [usage_metadata_callback]}
        )
        logger.info(f"当前会话进行压缩总结信息: {response.content}")
        return response.content

    @classmethod
    def split_messages_by_token(cls, messages, cutoff_tokens: int):
        if not messages or cutoff_tokens <= 0:
            return [], []

        # user、assistant两两分组
        pairs = []
        i = 0
        while i < len(messages) - 1:
            if messages[i].role == "user" and messages[i + 1].role == "assistant":
                pairs.append((messages[i], messages[i + 1]))
                i += 2
            else:
                i += 1

        if not pairs:
            return [], []

        # 从后往前累计 token
        total_tokens = 0
        kept_pairs = []

        for pair in reversed(pairs):
            pair_tokens = sum((m.token_usage or len(m.content) // 4) for m in pair)

            if total_tokens + pair_tokens > cutoff_tokens:
                break

            kept_pairs.append(pair)
            total_tokens += pair_tokens

        kept_pairs = list(reversed(kept_pairs))

        # 切分
        cutoff_index = len(pairs) - len(kept_pairs)
        old_pairs = pairs[:cutoff_index]

        if not old_pairs or not kept_pairs:
            return [], []

        # 展平成 messages
        old_messages = [m for pair in old_pairs for m in pair]
        new_messages = [m for pair in kept_pairs for m in pair]

        return old_messages, new_messages
