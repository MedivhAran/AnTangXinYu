from typing import List, Optional
from pydantic import BaseModel

from langchain.tools import BaseTool
from langchain.agents import create_agent
from langgraph.config import get_stream_writer
from langgraph.prebuilt.tool_node import ToolCallRequest
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain.agents.middleware import AgentState, wrap_tool_call, before_agent

from AnTang.core.models.manager import ModelManager
from AnTang.prompts.completion import CALL_END_PROMPT
from AnTang.services.mcp.manager import MCPManager
from AnTang.settings import app_settings
from AnTang.utils.convert import convert_mcp_config


class MCPConfig(BaseModel):
    url: str
    type: str = "sse"
    tools: List[str] = []
    server_name: str
    mcp_server_id: str


class MCPAgent:
    def __init__(self, mcp_config: MCPConfig, user_id: str):
        self.mcp_config = mcp_config
        self.mcp_manager = MCPManager([convert_mcp_config(mcp_config.model_dump())])

        self.user_id = user_id
        self.mcp_tools: List[BaseTool] = []

        self.conversation_model = None

        self.react_agent = None
        self.middlewares = None

    async def init_mcp_agent(self):
        if self.mcp_config:
            self.mcp_tools = await self.setup_mcp_tools()

        await self.setup_language_model()

        self.middlewares = await self.setup_agent_middlewares()

        self.react_agent = self.setup_react_agent()

    async def emit_event(self, event):
        writer = get_stream_writer()
        writer(event)

    async def setup_language_model(self):
        self.conversation_model = ModelManager.get_conversation_model()

    async def setup_mcp_tools(self):
        mcp_tools = await self.mcp_manager.get_mcp_tools()
        return mcp_tools

    async def setup_agent_middlewares(self):

        @wrap_tool_call
        async def add_tool_call_args(request: ToolCallRequest, handler):
            await self.emit_event(
                {
                    "status": "START",
                    "title": f"Sub-Agent - {self.mcp_config.server_name}执行可用工具: {request.tool_call["name"]}",
                    "messages": f"正在调用工具 {request.tool_call["name"]}...",
                }
            )

            # MCP Server 鉴权凭证统一从后端配置文件读取（config.yaml 的 mcp_credentials 段）。
            # 顶层 key 是 mcp_server.json 中的 server_name。无配置则视为不需要密钥。
            mcp_config = app_settings.mcp_credentials.get(self.mcp_config.server_name, {})
            request.tool_call["args"].update(mcp_config)

            tool_result = await handler(request)

            await self.emit_event(
                {
                    "status": "END",
                    "title": f"Sub-Agent - {self.mcp_config.server_name}执行可用工具: {request.tool_call["name"]}",
                    "messages": f"{tool_result}",
                }
            )
            return tool_result

        return [add_tool_call_args]

    def setup_react_agent(self):
        return create_agent(
            model=self.conversation_model,
            tools=self.mcp_tools,
            middleware=self.middlewares,
            system_prompt=CALL_END_PROMPT,
        )

    async def ainvoke(self, messages: List[BaseMessage]) -> List[BaseMessage] | str:
        """非流式版本"""
        result = await self.react_agent.ainvoke({"messages": messages})
        messages = []

        for message in result["messages"][:-1]:
            if not isinstance(message, HumanMessage) and not isinstance(message, SystemMessage):
                messages.append(message)
        return messages
