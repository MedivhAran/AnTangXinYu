"""提醒 / 通知接口的请求模型。"""

from typing import List

from pydantic import BaseModel, Field


class NotificationReadRequest(BaseModel):
    notification_ids: List[str] = Field(
        default_factory=list,
        description="要标记已读的通知ID列表；为空表示把当前用户全部未读标记为已读。",
    )
