from fastapi import APIRouter
from AnTang.api.v1 import (
    completion, dialog, message, agent, history,
    user, llm, tool, mcp_server, mcp_user_config,
    upload, agent_skill,
    register_mcp, register_mcp_completion, register_task
)

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(completion.router)
api_v1_router.include_router(dialog.router)
api_v1_router.include_router(message.router)
api_v1_router.include_router(agent.router)
api_v1_router.include_router(history.router)
api_v1_router.include_router(user.router)
api_v1_router.include_router(tool.router)
api_v1_router.include_router(llm.router)
api_v1_router.include_router(mcp_server.router)
api_v1_router.include_router(mcp_user_config.router)
api_v1_router.include_router(upload.router)
api_v1_router.include_router(agent_skill.router)
api_v1_router.include_router(register_task.router)
api_v1_router.include_router(register_mcp.router)
api_v1_router.include_router(register_mcp_completion.router)
