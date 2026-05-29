from pydantic import BaseModel, Field
from typing import Optional

from AnTang.services.antang.policies import ANTANG_AGENT_TYPE


class DialogCreateRequest(BaseModel):
    name: str = Field(description='对话Agent名称')
    agent_type: str = Field(ANTANG_AGENT_TYPE, description="当前项目固定为安糖心语 Agent 类型")


class DialogRenameRequest(BaseModel):
    dialog_id: str = Field(description='对话的ID')
    name: str = Field(description='新的会话名称')


class DialogUpdateRequest(BaseModel):
    name: Optional[str] = Field(description='对话Agent名称')
    dialog_id: str = Field(description='对话的ID')
    agent_type: Optional[str] = Field(ANTANG_AGENT_TYPE, description="当前项目固定为安糖心语 Agent 类型")
