import json
from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.runtime import AgentContext
from antang_api.database import session_factory as default_session_factory
from antang_api.health_profile.wearable_service import (
    read_latest_wearable_observations,
    read_wearable_daily_summaries,
    read_wearable_observation_detail,
    read_wearable_observations,
)
from antang_api.models import WearableRecordType

WearableReadView = Literal["latest", "range", "daily_summary", "detail"]
MAX_WEARABLE_TOOL_RESULT_CHARS = 20_000


class WearableToolResultTooLargeError(RuntimeError):
    """手环结果超过模型上下文的固定上限。"""


class WearableReadRequest(BaseModel):
    """手环工具的四种互斥查询方式。"""

    model_config = ConfigDict(extra="forbid")

    view: Annotated[
        WearableReadView,
        Field(
            description=(
                "latest 返回各类型的最近记录；range 返回时间范围内的具体记录；"
                "daily_summary 返回按日统计；detail 按 observation_id 返回一条记录。"
            )
        ),
    ]
    record_types: Annotated[
        list[WearableRecordType] | None,
        Field(description="要读取的设备数据类型；latest 可省略，表示读取所有类型。"),
    ] = None
    start: Annotated[
        datetime | None,
        Field(description="range 和 daily_summary 的含时区起始时间。"),
    ] = None
    end: Annotated[
        datetime | None,
        Field(description="range 和 daily_summary 的含时区结束时间。"),
    ] = None
    observation_id: Annotated[
        UUID | None,
        Field(description="detail 查询要读取的记录 ID。"),
    ] = None
    limit: Annotated[
        int | None,
        Field(
            ge=1,
            le=200,
            description="range 查询最多返回的记录数。",
        ),
    ] = None

    @model_validator(mode="after")
    def validate_view(self) -> "WearableReadRequest":
        if self.view == "latest":
            if any(value is not None for value in (self.start, self.end, self.observation_id, self.limit)):
                raise ValueError("latest only accepts optional record_types")
            return self

        if self.view == "detail":
            if self.observation_id is None:
                raise ValueError("detail requires observation_id")
            if any(value is not None for value in (self.record_types, self.start, self.end, self.limit)):
                raise ValueError("detail only accepts observation_id")
            return self

        if not self.record_types or self.start is None or self.end is None:
            raise ValueError(f"{self.view} requires record_types, start and end")
        if self.observation_id is not None:
            raise ValueError(f"{self.view} does not accept observation_id")
        if self.view == "range" and self.limit is None:
            raise ValueError("range requires limit")
        if self.view == "daily_summary" and self.limit is not None:
            raise ValueError("daily_summary does not accept limit")
        return self


def build_wearable_read_tool(
    session_factory: async_sessionmaker[AsyncSession] = default_session_factory,
) -> BaseTool:
    """创建只读取当前登录用户设备数据的共享工具。"""

    @tool("read_wearable_data")
    async def read_wearable_data(
        request: WearableReadRequest,
        runtime: ToolRuntime[AgentContext],
    ) -> dict[str, object]:
        """读取当前用户已经同步到服务器的历史设备观测。

        用户询问自己的活动、睡眠、心率、血氧、呼吸频率、体重或其他设备
        数据时使用。结果不是手环的实时连接，不能用于读取第三人的数据，也不
        用于回答普通健康知识问题。趋势优先使用 daily_summary；只有需要具体
        样本时才使用带明确时间范围和数量上限的 range。
        """

        async with session_factory() as session:
            if request.view == "latest":
                observations = await read_latest_wearable_observations(
                    session,
                    user_id=runtime.context.user_id,
                    record_types=request.record_types,
                )
                payload: dict[str, object] = {
                    "view": request.view,
                    "observations": [item.model_dump(mode="json") for item in observations],
                }
            elif request.view == "detail":
                observation = await read_wearable_observation_detail(
                    session,
                    user_id=runtime.context.user_id,
                    observation_id=cast(UUID, request.observation_id),
                )
                payload = {
                    "view": request.view,
                    "observation": observation.model_dump(mode="json"),
                }
            else:
                record_types = cast(list[WearableRecordType], request.record_types)
                start = cast(datetime, request.start)
                end = cast(datetime, request.end)

                if request.view == "range":
                    observations = await read_wearable_observations(
                        session,
                        user_id=runtime.context.user_id,
                        record_types=record_types,
                        start=start,
                        end=end,
                        limit=cast(int, request.limit),
                    )
                    payload = {
                        "view": request.view,
                        "observations": [item.model_dump(mode="json") for item in observations],
                    }
                else:
                    summaries = await read_wearable_daily_summaries(
                        session,
                        user_id=runtime.context.user_id,
                        record_types=record_types,
                        start=start,
                        end=end,
                    )
                    payload = {
                        "view": request.view,
                        "summaries": [item.model_dump(mode="json") for item in summaries],
                    }

        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > MAX_WEARABLE_TOOL_RESULT_CHARS:
            raise WearableToolResultTooLargeError("手环查询结果超过固定上限，请改用每日汇总或缩小查询范围")
        return payload

    return read_wearable_data
