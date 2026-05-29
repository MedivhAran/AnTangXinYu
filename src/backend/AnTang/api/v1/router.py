from fastapi import APIRouter
from AnTang.api.v1 import (
    completion, dialog, message, history,
    user, llm, tool, upload, cgm_report, reminder,
)

api_v1_router = APIRouter(prefix="/api/v1")

# 用户对外路由：仅保留对话 / 历史 / 鉴权 / 文件上传 / 只读模型 / 只读工具配置。
# MCP Server、Agent Skill、Register MCP 系列已经收归后端开发者管理（详见
# docs / plan：mcp-skill-sharded-wave），不再暴露给前端。需要恢复时把对应
# 模块加回 imports 和 include_router 即可，路由文件本身保留未删。
api_v1_router.include_router(completion.router)
api_v1_router.include_router(dialog.router)
api_v1_router.include_router(message.router)
api_v1_router.include_router(history.router)
api_v1_router.include_router(user.router)
api_v1_router.include_router(tool.router)
api_v1_router.include_router(llm.router)
api_v1_router.include_router(upload.router)
api_v1_router.include_router(cgm_report.router)
api_v1_router.include_router(reminder.router)
