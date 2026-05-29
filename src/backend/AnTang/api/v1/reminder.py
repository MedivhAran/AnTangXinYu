from datetime import datetime

from fastapi import APIRouter, Depends
from loguru import logger

from AnTang.api.responses.builder import UnifiedResponseModel, resp_200, resp_500
from AnTang.api.services.user import UserPayload, get_login_user
from AnTang.database.dao.notification import NotificationDao
from AnTang.database.dao.reminder import ReminderDao
from AnTang.schemas.reminder import NotificationReadRequest

router = APIRouter(tags=["Reminder"])


@router.get("/reminder/list", summary="列出当前用户未结束的提醒", response_model=UnifiedResponseModel)
async def list_reminders(login_user: UserPayload = Depends(get_login_user)):
    try:
        reminders = await ReminderDao.list_active_by_user(login_user.user_id)
        return resp_200(data=[r.to_dict() for r in reminders])
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))


@router.delete("/reminder/{reminder_id}", summary="取消一条提醒", response_model=UnifiedResponseModel)
async def cancel_reminder(reminder_id: str, login_user: UserPayload = Depends(get_login_user)):
    try:
        ok = await ReminderDao.cancel(reminder_id, login_user.user_id)
        return resp_200(data={"cancelled": ok})
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))


@router.get("/notification/inbox", summary="拉取通知（前端轮询未读 feed）", response_model=UnifiedResponseModel)
async def notification_inbox(
    login_user: UserPayload = Depends(get_login_user),
    since: str | None = None,
):
    """前端定时轮询。since 为上次拉取到的最大 create_time（ISO 字符串）；
    不传则只返回当前未读。"""
    try:
        since_dt = None
        if since:
            try:
                # to_dict 输出带 +08:00，回传后去掉 tzinfo 与库内 naive 北京墙钟对齐比较。
                since_dt = datetime.fromisoformat(since).replace(tzinfo=None)
            except ValueError:
                since_dt = None
        notifications = await NotificationDao.list_since(login_user.user_id, since_dt)
        return resp_200(data=[n.to_dict() for n in notifications])
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))


@router.post("/notification/read", summary="标记通知已读", response_model=UnifiedResponseModel)
async def notification_read(
    req: NotificationReadRequest,
    login_user: UserPayload = Depends(get_login_user),
):
    try:
        count = await NotificationDao.mark_read(login_user.user_id, req.notification_ids)
        return resp_200(data={"marked": count})
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))
