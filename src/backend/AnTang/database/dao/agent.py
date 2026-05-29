from AnTang.database.models.agent import AgentTable
from sqlmodel import select, update, desc

from AnTang.database.session import session_getter


class AgentDao:

    @classmethod
    async def create_agent(cls, agent: AgentTable):
        with session_getter() as session:
            session.add(agent)
            session.commit()
            session.refresh(agent)
            return agent

    @classmethod
    async def get_agent(cls):
        with session_getter() as session:
            statement = select(AgentTable).order_by(desc(AgentTable.create_time))
            result = session.exec(statement).all()
            return result

    @classmethod
    async def select_agent_by_name(cls, name: str):
        with session_getter() as session:
            statement = select(AgentTable).where(AgentTable.name == name)
            result = session.exec(statement).first()
            return result

    @classmethod
    async def update_agent_by_id(cls, agent_id: str, update_values: dict):
        with session_getter() as session:
            statement = update(AgentTable).where(AgentTable.id == agent_id).values(**update_values)
            session.exec(statement)
            session.commit()
