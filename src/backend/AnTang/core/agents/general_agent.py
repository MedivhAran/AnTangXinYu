"""通用 Agent 实现。

GeneralAgent 是普通对话 Agent 的运行核心，负责把数据库里的配置转换成可执行的 LangChain Agent：
- 根据 llm_id 选择模型；
- 把内置工具、用户 OpenAPI 工具、MCP Server、Skill Agent 统一包装成 tool；
- 通过 LangChain/LangGraph 的中间件把工具调用过程转换成前端可展示的流式事件；
- 对外提供 astream，向接口层输出 response_chunk 和 event。
"""

import copy
import time
from loguru import logger
from pydantic import BaseModel
from typing import List, Dict, Any, AsyncGenerator, Callable, NotRequired
from langgraph.runtime import Runtime
from langgraph.types import Command
from langchain_core.tools import BaseTool, tool, StructuredTool
from langchain.tools.tool_node import ToolCallRequest
from langchain.agents import create_agent, AgentState
from langgraph.config import get_stream_writer
from langchain_core.messages import (
    BaseMessage,
    ToolMessage,
    HumanMessage,
    AIMessageChunk,
)
from langchain.agents.middleware import ModelRequest, ModelResponse, AgentMiddleware

from AnTang.api.services.agent_skill import AgentSkillService
from AnTang.core.agents.skill_agent import SkillAgent
from AnTang.core.callbacks import usage_metadata_callback
from AnTang.database import AgentSkill
from AnTang.tools import AgentToolsWithName
from AnTang.api.services.llm import LLMService
from AnTang.core.models.manager import ModelManager
from AnTang.api.services.tool import ToolService
from AnTang.core.agents.mcp_agent import MCPAgent, MCPConfig
from AnTang.api.services.mcp_server import MCPService
from AnTang.tools.openapi_tool.adapter import OpenAPIToolAdapter


class StreamAgentState(AgentState):
    """LangGraph 运行时状态，就是state。

    AgentState 默认只包含 messages，这里额外加计数字段和 user_id，
    便于中间件判断模型调用轮次，以及工具侧按用户隔离数据。
    """

    model_call_count: NotRequired[int]
    user_id: NotRequired[str]


class AgentConfig(BaseModel):
    """Agent 运行配置。

    这些字段通常来自数据库里的 Agent 配置表，
    completion 接口会把查询到的配置转成这个模型后交给 GeneralAgent。
    """

    user_id: str  # 绑定的用户id
    llm_id: str  # 绑定的语言模型id
    mcp_ids: List[str]  # 绑定的MCP服务器id列表
    tool_ids: List[str]  # 绑定的工具id列表
    agent_skill_ids: List[str]  # 绑定的技能Agent id列表
    system_prompt: str  # 系统提示词
    name: str = None  # Agent的名称


class EmitEventAgentMiddleware(AgentMiddleware):
    """把模型调用和工具调用包装成前端事件的 LangChain 中间件。"""

    def __init__(self, name_resolver_func):
        super().__init__()

        # name_resolver_func 用于把工具函数名转换成“类型 + 展示名”。
        self.name_resolver_func = name_resolver_func

    @staticmethod
    def _build_tool_event_message(display_tool_name: str, tool_content: str) -> str:
        """生成给前端事件卡片看的工具结果摘要。

        图片理解的原始结果会继续作为 ToolMessage 交给模型，但前端事件只展示状态，
        避免把供模型整合用的内部观察材料直接摊给用户。
        """
        if display_tool_name == "图片理解":
            return "图片已分析完成，我会结合图片内容继续回复。"
        return tool_content

    async def aafter_model(self, state: StreamAgentState, runtime: Runtime) -> dict[str, Any] | None:
        """模型生成后决定下一步走向。

        如果最后一条 AI 消息包含 tool_calls，说明还要继续执行工具；
        如果没有 tool_calls，就让图直接跳到 end，结束本轮 ReAct。
        """
        last_message = state["messages"][-1]
        if last_message.tool_calls:
            return {"model_call_count": state["model_call_count"] + 1}

        return {"jump_to": "end"}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """包一层模型调用，统一记录异常。"""
        try:
            response = await handler(request)
            return response
        except Exception as err:
            logger.error(f"Model call error: {err}")
            raise ValueError(err)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """包一层工具调用，并把 START/END/ERROR 事件写入 custom stream。"""
        writer = get_stream_writer()

        # 先解析展示名，再发送工具开始事件，前端会把它渲染成工具执行卡片。
        tool_type, display_tool_name = self.name_resolver_func(request.tool_call["name"])

        writer(
            {
                "status": "START",
                "title": f"执行可用{tool_type}: {display_tool_name}",
                "message": f"正在调用插件工具 {display_tool_name}...",
            }
        )
        try:
            tool_result = await handler(request)
            # 工具成功后把工具返回内容也作为事件内容推给前端。
            event_message = self._build_tool_event_message(display_tool_name, str(tool_result.content))
            writer(
                {
                    "status": "END",
                    "title": f"执行可用{tool_type}: {display_tool_name}",
                    "message": event_message,
                }
            )
            return tool_result
        except Exception as err:
            # 工具失败不直接中断整轮对话，而是把错误包装成 ToolMessage 交回模型处理。
            writer(
                {
                    "status": "ERROR",
                    "title": f"执行可用{tool_type}: {display_tool_name}",
                    "message": str(err),
                }
            )
            return ToolMessage(
                content=str(err),
                name=request.tool_call["name"],
                tool_call_id=request.tool_call["id"],
            )


class GeneralAgent:
    """普通 Agent 主类。

    这个类只关心“如何把配置组装成一个能流式运行的 Agent”，
    不处理 HTTP 鉴权、历史入库、长期记忆写入等接口层职责。
    """

    def __init__(self, agent_config: AgentConfig):
        self.agent_config = agent_config

        self.conversation_model = None
        self.react_agent = None

        # 不管底层是 HTTP API、MCP server，还是另一个 Agent，
        # 对主 Agent 而言最终都统一成“一个可以调用的 tool”。
        # 这种 Agent as Tool 的方式能让主 Agent 用同一套 ReAct 流程调度不同能力。
        self.tools = []
        self.mcp_agent_as_tools = []
        self.middlewares = []
        self.skill_agent_as_tools = []

        # 保存工具函数名到展示信息的映射，供 EmitEventAgentMiddleware 生成前端事件标题。
        self.tool_metadata_map: Dict[str, Dict[str, str]] = {}

    def wrap_event(self, data: Dict[Any, Any]):
        """把内部事件包装成统一 SSE 事件结构。"""
        event = {"type": "event", "timestamp": time.time(), "data": data}
        return event

    async def init_agent(self):
        """初始化 Agent 运行所需的模型、工具和中间件。"""
        # 1. MCP Server 会被封装成一个个 MCP Agent Tool。
        self.mcp_agent_as_tools = await self.setup_mcp_agent_as_tools()

        # 2. 平台内置工具和用户自定义 OpenAPI 工具。
        self.tools = await self.setup_tools()

        # 3. 技能 Agent 也被包装成 tool，供主 Agent 调用。
        self.skill_agent_as_tools = await self.setup_agent_skill_as_tools()

        # 4. 模型和中间件最后组装进 create_agent。
        await self.setup_language_model()

        self.middlewares = await self.setup_agent_middleware()

        # 调用 LangChain 的 create_agent，生成支持流式输出的 ReAct Agent。
        self.react_agent = self.setup_react_agent()

    async def setup_agent_middleware(self):
        """注册 Agent 中间件。"""
        return [EmitEventAgentMiddleware(self.get_tool_display_name)]

    async def setup_language_model(self):
        """根据 Agent 配置选择对话模型。

        有 llm_id 时使用用户绑定模型；没有时使用系统默认对话模型。
        """
        if self.agent_config.llm_id:
            model_config = await LLMService.get_llm_by_id(self.agent_config.llm_id)
            self.conversation_model = ModelManager.get_user_model(**model_config)
        else:
            self.conversation_model = ModelManager.get_conversation_model()

    def setup_react_agent(self):
        """把模型、工具、中间件组装成 LangChain Agent。"""
        return create_agent(
            model=self.conversation_model,
            tools=self.tools + self.mcp_agent_as_tools + self.skill_agent_as_tools,
            middleware=self.middlewares,
            state_schema=StreamAgentState,
        )

    async def setup_tools(self) -> List[BaseTool]:
        def create_openapi_tool_executor(tool_adapter, tool_name):
            """为单个 OpenAPI operation 创建异步执行函数。"""

            async def _execute_wrapper(**kwargs):
                return await tool_adapter.execute(_tool_name=tool_name, **kwargs)

            return _execute_wrapper

        tools = []
        db_tools = await ToolService.get_tools_from_id(self.agent_config.tool_ids)
        for db_tool in db_tools:
            if db_tool.is_user_defined:
                # 用户自定义工具存的是 OpenAPI schema，需要先解析成 LangChain StructuredTool。
                tool_adapter = OpenAPIToolAdapter(
                    auth_config=db_tool.auth_config,
                    openapi_schema=db_tool.openapi_schema,
                )

                for openapi_tool in tool_adapter.tools:
                    # 一个 OpenAPI schema 里可能有多个 operation，每个 operation 都单独变成一个 tool。
                    tools.append(
                        StructuredTool(
                            name=openapi_tool["function"].get("name", ""),
                            description=openapi_tool["function"].get("description", ""),
                            coroutine=create_openapi_tool_executor(tool_adapter, openapi_tool["function"].get("name")),
                            args_schema=openapi_tool,
                        )
                    )

                    # OpenAPI operation 的函数名可能不友好，这里映射回用户创建工具时的展示名。
                    self.tool_metadata_map[openapi_tool["function"].get("name", "")] = {
                        "name": db_tool.display_name,
                        "type": "工具",
                    }
            else:
                # 平台内置工具已经在 AgentToolsWithName 中注册，按数据库里的工具名取出即可。
                agent_tool = AgentToolsWithName.get(db_tool.name)
                if agent_tool:
                    tools.append(agent_tool)
                self.tool_metadata_map[db_tool.name] = {
                    "name": db_tool.display_name,
                    "type": "工具",
                }

        return tools

    async def setup_agent_skill_as_tools(self) -> List[BaseTool]:
        """把绑定的 Skill Agent 包装成主 Agent 可调用的 tool。"""
        agent_skill_as_tools = []
        agent_skills = await AgentSkillService.get_agent_skills_by_ids(self.agent_config.agent_skill_ids)

        def create_skill_agent_as_tool(agent_skill: AgentSkill):
            """闭包保存 skill 配置，实际调用时再初始化 SkillAgent。"""

            @tool(agent_skill.as_tool_name, description=agent_skill.description)
            async def call_skill_agent(query: str):
                """调用技能 Agent。"""
                skill_agent = SkillAgent(agent_skill, self.agent_config.user_id)
                await skill_agent.init_skill_agent()
                messages = await skill_agent.ainvoke([HumanMessage(content=query)])
                return "\n".join([message.content for message in messages])

            return call_skill_agent  # 注意这里返回的是 tool 函数，不是函数的调用结果

        for agent_skill in agent_skills:
            # 记录展示名，前端工具事件里显示 Skill 的中文名。
            self.tool_metadata_map[agent_skill.as_tool_name] = {
                "name": agent_skill.name,  # 技能的中文/友好名称
                "type": "Skill",
            }
            agent_skill_as_tools.append(create_skill_agent_as_tool(agent_skill))

        return agent_skill_as_tools

    async def setup_mcp_agent_as_tools(self):
        """把绑定的 MCP Server 包装成 MCPAgent tool。"""
        mcp_agent_as_tools = []

        def create_mcp_agent_as_tool(mcp_agent, mcp_as_tool_name, description):
            """闭包保存 MCPAgent 实例，主 Agent 调用 tool 时转发给对应 MCPAgent。"""

            @tool(mcp_as_tool_name, description=description)
            async def call_mcp_agent(query: str):
                """
                用户想要根据这些mcp工具来完成的一些任务
                Args:
                    query: 用户询问的问题
                Returns:
                    根据该MCP Agent来完成的一些任务
                """

                messages = await mcp_agent.ainvoke([HumanMessage(content=query)])
                return "\n".join([message.content for message in messages])

            return call_mcp_agent

        for mcp_id in self.agent_config.mcp_ids:
            # 每个 mcp_id 对应一套 MCP Server 配置，初始化后作为一个工具暴露给主 Agent。
            mcp_server = await MCPService.get_mcp_server_from_id(mcp_id)
            mcp_config = MCPConfig(**mcp_server)

            mcp_agent = MCPAgent(mcp_config, self.agent_config.user_id)
            await mcp_agent.init_mcp_agent()

            tool_name = mcp_server.get("mcp_as_tool_name")
            description = mcp_server.get("description")

            # 更新元数据映射，用于前端展示 MCP 工具调用过程。
            self.tool_metadata_map[tool_name] = {
                "name": mcp_config.server_name,
                "type": "MCP",
            }

            mcp_agent_as_tools.append(create_mcp_agent_as_tool(mcp_agent, tool_name, description))
        return mcp_agent_as_tools

    async def astream(self, messages: List[BaseMessage]) -> AsyncGenerator[Dict[str, Any], None]:
        """流式调用主 Agent。

        输出统一分两类：
        - custom stream 事件：工具 START/END/ERROR 等过程事件；
        - response_chunk：模型回复文本片段，前端用于逐字流式展示。
        """
        accumulated = ""
        try:
            async for token, metadata in self.react_agent.astream(
                input={
                    "messages": copy.deepcopy(messages),
                    "model_call_count": 0,
                    "user_id": self.agent_config.user_id,
                },
                config={"callbacks": [usage_metadata_callback]},
                stream_mode=["messages", "custom"],
            ):
                if token == "custom":  # 代表langchain工具事件
                    # 累计清零，让下一阶段的 accumulated 重新从 0 开始。
                    # 注意：之前已经流给前端的 chunk 不再回收，多数对话不触发工具，影响很小。
                    accumulated = ""
                    yield self.wrap_event(metadata)
                elif isinstance(metadata[0], AIMessageChunk) and metadata[0].content:
                    # LangGraph 的 messages 流会把节点内任何 LLM 调用的 token 都吐出来，
                    # 包括工具内部嵌套调用 LLM 产生的 token。这里只放行主 agent 模型节点
                    # （create_agent 固定命名为 "model"），避免图片理解、饮食建议等工具的
                    # 内部 LLM 输出污染主对话流。
                    if metadata[1].get("langgraph_node") != "model":
                        continue
                    chunk = metadata[0].content
                    accumulated += chunk
                    # 每个 chunk 立即 yield，前端就能看到逐字效果。
                    yield {
                        "type": "response_chunk",
                        "timestamp": time.time(),
                        "data": {
                            "chunk": chunk,
                            "accumulated": accumulated,
                        },
                    }

        # 针对模型回复做兜底。
        except Exception as err:
            logger.error(f"LLM Model Error: {err}")
            yield {
                "type": "response_chunk",
                "timestamp": time.time(),
                "data": {
                    "chunk": "抱歉，我无法回答这个问题。",
                    "accumulated": accumulated,
                },
            }

    def get_tool_display_name(self, tool_name: str):
        """
        根据工具原始名称解析展示类型和展示名。

        例如：
        - "gaode_weather" -> ("Skill", "高德天气")
        - "mcp_filesystem" -> ("MCP", "文件系统")
        - "search" -> ("工具", "search")
        """
        metadata = self.tool_metadata_map.get(tool_name)

        if not metadata:
            # 没有记录元数据时直接显示原始工具名，避免事件标题为空。
            return "工具", tool_name

        friendly_name = metadata.get("name", tool_name)
        tool_type = metadata.get("type", "工具")

        return tool_type, friendly_name
