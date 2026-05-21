from typing import Any, Dict, List, Optional

from sqlmodel import delete, select

from AnTang.database.models.cgm_report import CGMReportTable
from AnTang.database.session import async_session_getter


class CGMReportDao:

    @classmethod
    async def create(cls, report: CGMReportTable) -> CGMReportTable:
        async with async_session_getter() as session:
            session.add(report)
            await session.commit()
            await session.refresh(report)
            return report

    @classmethod
    async def update_by_id(cls, report_id: str, fields: Dict[str, Any]) -> Optional[CGMReportTable]:
        """整片字段覆盖。仅更新传入的 key。"""
        if not fields:
            return await cls.get_by_id(report_id)

        async with async_session_getter() as session:
            statement = select(CGMReportTable).where(CGMReportTable.id == report_id)
            result = await session.exec(statement)
            report = result.first()
            if not report:
                return None
            for key, value in fields.items():
                setattr(report, key, value)
            session.add(report)
            await session.commit()
            await session.refresh(report)
            return report

    @classmethod
    async def get_by_id(cls, report_id: str) -> Optional[CGMReportTable]:
        async with async_session_getter() as session:
            statement = select(CGMReportTable).where(CGMReportTable.id == report_id)
            result = await session.exec(statement)
            return result.first()

    @classmethod
    async def get_latest_by_user(cls, user_id: str) -> Optional[CGMReportTable]:
        """取用户最近一份成功解析的报告(按监测结束日期降序)。"""
        async with async_session_getter() as session:
            statement = (
                select(CGMReportTable)
                .where(CGMReportTable.user_id == user_id)
                .where(CGMReportTable.parse_status == "success")
                .order_by(CGMReportTable.monitoring_end_date.desc())
                .limit(1)
            )
            result = await session.exec(statement)
            return result.first()

    @classmethod
    async def list_by_user(
        cls,
        user_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> List[CGMReportTable]:
        """按上传时间倒序列出用户全部报告(含失败的)。"""
        async with async_session_getter() as session:
            statement = (
                select(CGMReportTable)
                .where(CGMReportTable.user_id == user_id)
                .order_by(CGMReportTable.create_time.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.exec(statement)
            return list(result.all())

    @classmethod
    async def delete_by_id(cls, report_id: str, user_id: str) -> bool:
        """按 id + user_id 双重校验删除,防越权。返回是否真的删了一行。"""
        async with async_session_getter() as session:
            statement = (
                select(CGMReportTable)
                .where(CGMReportTable.id == report_id)
                .where(CGMReportTable.user_id == user_id)
            )
            result = await session.exec(statement)
            report = result.first()
            if not report:
                return False
            await session.exec(
                delete(CGMReportTable).where(CGMReportTable.id == report_id)
            )
            await session.commit()
            return True
