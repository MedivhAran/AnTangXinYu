from loguru import logger
from fastapi import APIRouter, Depends, Body
from AnTang.api.services.agent import AgentService
from AnTang.api.services.dialog import DialogService
from AnTang.api.services.user import UserPayload, get_login_user
from AnTang.schemas.dialog import DialogCreateRequest, DialogRenameRequest
from AnTang.api.responses.builder import resp_200, resp_500, UnifiedResponseModel
from AnTang.services.antang.policies import ANTANG_AGENT_NAME, ANTANG_AGENT_TYPE

router = APIRouter(tags=["Dialog"])


@router.get("/dialog/list", response_model=UnifiedResponseModel)
async def get_dialog(
    login_user: UserPayload = Depends(get_login_user)
):
    try:
        messages = await DialogService.get_list_dialog(user_id=login_user.user_id)
        results = []
        agent_cache = {}

        for message in messages:
            agent_id = message.get("agent_id")
            if agent_id not in agent_cache:
                agent_cache[agent_id] = await AgentService.select_agent_by_id(
                    agent_id=agent_id
                ) or {}

            message_agent = agent_cache[agent_id]
            if message_agent.get("name") != ANTANG_AGENT_NAME:
                continue

            dialog_payload = {
                **message,
                "agent_type": ANTANG_AGENT_TYPE,
                "agent_name": message_agent.get("name", ""),
                "agent_logo_url": message_agent.get("logo_url", ""),
                "last_active_time": message.get("update_time"),
            }
            results.append(dialog_payload)

        return resp_200(data=results)
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))


@router.post("/dialog", response_model=UnifiedResponseModel)
async def create_dialog(
    dialog_req: DialogCreateRequest,
    login_user: UserPayload = Depends(get_login_user)
):
    try:
        dialog = await DialogService.create_dialog(
            name=dialog_req.name,
            agent_id=dialog_req.agent_id,
            agent_type=dialog_req.agent_type,
            user_id=login_user.user_id
        )
        return resp_200(dialog)
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))


@router.put("/dialog/name", response_model=UnifiedResponseModel)
async def rename_dialog(
    dialog_req: DialogRenameRequest,
    login_user: UserPayload = Depends(get_login_user)
):
    try:
        dialog = await DialogService.update_dialog_name(
            dialog_id=dialog_req.dialog_id,
            name=dialog_req.name,
            user_id=login_user.user_id
        )
        return resp_200(dialog)
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))


@router.delete("/dialog", response_model=UnifiedResponseModel)
async def delete_dialog(
    dialog_id: str = Body(description="对话ID", embed=True),
    login_user: UserPayload = Depends(get_login_user)
):
    try:
        # 验证用户权限
        await DialogService.verify_user_permission(dialog_id, login_user.user_id)

        await DialogService.delete_dialog(dialog_id=dialog_id)
        return resp_200()
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))
