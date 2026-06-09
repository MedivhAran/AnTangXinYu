import ast
import json

from loguru import logger
from langchain_core.messages import HumanMessage, SystemMessage

from AnTang.core.models.manager import ModelManager
from AnTang.prompts.rewrite import system_query_rewrite
from AnTang.prompts.rewrite import user_query_write


def _parse_query_list(content: str, fallback: str) -> list[str]:
    """从 LLM 输出里鲁棒地解析出“改写后的查询列表”。

    兼容几种常见情况：
    - 标准 JSON 数组 ["q1","q2"]；
    - Python 风格单引号列表 ['q1','q2']（prompt 里就是这么要求的，json.loads 解不了）→ 退回 ast.literal_eval；
    - 前后夹带思考/解释文字、或外层对象 → 先抠出第一个 [ 到最后一个 ]；
    解析不出非空字符串列表时，回退 [原始 query]。
    """
    if content:
        start, end = content.find("["), content.rfind("]")
        if start != -1 and end > start:
            blob = content[start : end + 1]
            for loader in (json.loads, ast.literal_eval):  # 先 JSON，失败再按 Python 字面量解析
                try:
                    parsed = loader(blob)
                except Exception:
                    continue
                if isinstance(parsed, list):
                    queries = [str(q).strip() for q in parsed if str(q).strip()]
                    if queries:
                        return queries
    return [fallback]


class QueryRewrite:
    def __init__(self):
        self.client = ModelManager.get_conversation_model()

    async def rewrite(self, user_input):
        rewrite_prompt = user_query_write.format(user_input=user_input)
        # 改为 ainvoke：这个方法本身是 async 的，同步 invoke 会阻塞整个事件循环。
        response = await self.client.ainvoke(
            [SystemMessage(content=system_query_rewrite), HumanMessage(content=rewrite_prompt)]
        )
        queries = _parse_query_list(response.content, user_input)
        if queries == [user_input]:
            # 解析不出改写结果时，截断打出原始输出，方便区分是"格式不对"还是"空返回"
            logger.warning(f"[query-rewrite] 解析失败，回退单查询。raw前300字={response.content[:300]!r}")
        return queries


query_rewriter = QueryRewrite()
