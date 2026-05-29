from AnTang.database.dao.agent import AgentDao
from AnTang.services.antang.policies import ANTANG_AGENT_NAME


class AgentService:

    @staticmethod
    def _to_dict_list(results):
        """
        将查询结果列表转换为字典列表，空列表返回 []
        """
        return [res.to_dict() for res in results] if results else []

    @classmethod
    async def get_agent(cls):
        """
        查询所有 Agent
        返回字典列表
        """
        results = await AgentDao.get_agent()
        return cls._to_dict_list(results)

    @classmethod
    async def get_antang_agent(cls):
        """
        获取唯一的系统级安糖心语 Agent
        返回字典或 None
        """
        agent = await AgentDao.select_agent_by_name(ANTANG_AGENT_NAME)
        return agent.to_dict() if agent else None
