"""CGM(动态葡萄糖监测)报告解析 + 入库 + 画像同步。

两条上传路径共用本 service:
- 路径 A: POST /api/v1/cgm/import      (专门 CGM 板块)
- 路径 B: AnTangAgent 工具 import_cgm_report (对话附件)

整体流程都是 parse_and_store -> 解析 -> 写表 -> 触发 _sync_profile_from_report。
失败不抛异常,把 parse_status='failed' 写回记录,主对话/前端不会因此挂掉。
"""

import asyncio
import json
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pymupdf
from loguru import logger
from pydantic import BaseModel, Field

from AnTang.core.agents.structured_response_agent import StructuredResponseAgent
from AnTang.database.dao.antang_profile import AnTangProfileDao
from AnTang.database.dao.cgm_report import CGMReportDao
from AnTang.database.models.cgm_report import CGMReportTable
from AnTang.schemas.cgm_report import CGMReportExtraction, CGMReportSummary
from AnTang.services.antang.profile import AnTangProfileService
from AnTang.services.antang.state import AnTangUserProfile
from AnTang.services.storage import storage_client
from AnTang.utils.file_utils import (
    get_object_name_from_aliyun_url,
    get_save_tempfile,
)


_EXTRACT_PROMPT_TEMPLATE = """
你是一名医学数据抽取助手。下面是一份动态血糖监测(CGM)评估报告的纯文本,
请从中抽取结构化字段。严格遵循:

1. 时长字段全部换算成分钟数(整数):"20h17min" -> 1217, "3h40min" -> 220。
2. 日期一律用 YYYY-MM-DD 格式。
3. 不存在的字段留空(Optional)或空数组(daily_metrics / hourly_metrics),不要编造数字。
4. 患者姓名、手机号属于隐私,不要返回。只抽病史标签、年龄、性别。
5. 目标范围:妊娠糖尿病通常 3.5-7.8 mmol/L + TIR 参考 90%;
   2 型糖尿病通常 3.9-7.8 + TIR 参考 70%。按报告里写的实际数字为准。

【报告原文开始】
{raw_text}
【报告原文结束】
""".strip()


_PROFILE_SYNC_PROMPT_TEMPLATE = """
你是一名安糖陪伴智能体的画像维护助手。下面会给你:
(a) 用户当前的长期画像 JSON
(b) 用户**最新**一份 CGM 监测报告的关键指标

请综合这两份信息,输出**更新后的完整画像 JSON**。规则:
- `recent_risk_notes` 字段**必须重写**,只保留"基于这份最新报告"的近期风险点(3-6 条短句)。
  不要堆积上一份报告的旧风险。
- `summary` 字段重写,3-6 句中文,可以包含"最近 N 天 TIR x%, 较常规目标..."这类对比性结论。
- 其他字段(common_low_glucose_times / common_triggers / night_low_tendency 等)如果报告里有相关
  线索可以更新,无线索则保留原值。**不要编造**报告里不存在的内容。
- 如果用户当前画像里有跟报告冲突的旧信息,以报告为准更新。

【当前画像】
{profile_json}

【最新 CGM 报告指标】
{report_summary}
""".strip()


_CRITICAL_REPORT_FIELDS: tuple[str, ...] = (
    "monitoring_start_date",
    "monitoring_end_date",
    "monitoring_days",
    "target_range_low",
    "target_range_high",
    "tir_threshold_pct",
    "ehba1c",
    "mg",
    "sd",
    "cv",
    "hypo_risk_level",
    "tir_pct",
    "tir_duration_min",
    "tar_pct",
    "tar_duration_min",
    "tbr_pct",
    "tbr_duration_min",
)


class CGMReportService:
    """CGM 报告解析 + 入库 + 画像同步的唯一入口。"""

    @classmethod
    async def parse_and_store(
        cls,
        *,
        file_url: str,
        file_name: str,
        user_id: str,
    ) -> CGMReportTable:
        # 1. 先插入一条 pending 占位记录,出错时也能在 DB 看到这次失败
        placeholder = CGMReportTable(
            user_id=user_id,
            file_url=file_url,
            file_name=file_name,
            monitoring_start_date=date.today(),
            monitoring_end_date=date.today(),
            monitoring_days=0,
            parse_status="pending",
        )
        report = await CGMReportDao.create(placeholder)
        report_id = report.id

        raw_text: Optional[str] = None
        try:
            # 2. 下载 PDF 到临时文件
            raw_text = await asyncio.to_thread(cls._download_and_extract_text, file_url, file_name)

            # 3. LLM 抽字段
            extraction = await asyncio.to_thread(cls._extract_fields_with_llm, raw_text)

            # 4. 把 extraction 映射成 DB 字段,并写回(success)
            update_fields = cls._extraction_to_update_fields(extraction, raw_text)
            update_fields["parse_status"] = "success"
            update_fields["parse_error"] = None
            updated = await CGMReportDao.update_by_id(report_id, update_fields)
            if updated is None:
                # 记录被并发删了,直接返回
                return report
            report = updated

            # 5. 同步覆盖画像(失败不影响报告解析的成功状态)
            try:
                await cls._sync_profile_from_report(report=report, user_id=user_id)
            except Exception as profile_err:
                logger.warning(f"[cgm-report] 画像同步失败,但报告已入库: {profile_err}")

            return report

        except Exception as err:
            logger.exception(f"[cgm-report] parse_and_store failed report_id={report_id}: {err}")
            fail_fields = {
                "parse_status": "failed",
                "parse_error": str(err)[:1000],
            }
            if raw_text is not None:
                fail_fields["raw_text"] = raw_text
            updated = await CGMReportDao.update_by_id(report_id, fail_fields)
            return updated if updated else report

    # ---------- 内部步骤 ----------

    @staticmethod
    def _download_and_extract_text(file_url: str, file_name: str) -> str:
        """同步下载 PDF + 抽纯文本。放到 to_thread 里跑,避免阻塞事件循环。"""
        object_name = get_object_name_from_aliyun_url(file_url)
        if not object_name:
            raise ValueError(f"无法从 file_url 解析 object_name: {file_url}")

        suffix = Path(file_name or object_name).suffix or ".pdf"
        local_path = get_save_tempfile(f"cgm_report{suffix}")
        storage_client.download_file(object_name, local_path)

        try:
            doc = pymupdf.open(local_path)
            try:
                pages_text: List[str] = []
                for page in doc:
                    pages_text.append(page.get_text() or "")
                return "\n".join(pages_text).strip()
            finally:
                doc.close()
        finally:
            try:
                os.remove(local_path)
            except OSError:
                pass

    @staticmethod
    def _extract_fields_with_llm(raw_text: str) -> CGMReportExtraction:
        if not raw_text:
            raise ValueError("PDF 抽出的文本为空,无法解析")

        prompt = _EXTRACT_PROMPT_TEMPLATE.format(raw_text=raw_text)
        agent = StructuredResponseAgent(CGMReportExtraction)
        result = agent.get_structured_response(prompt)
        if not isinstance(result, CGMReportExtraction):
            raise ValueError(f"StructuredResponseAgent 输出类型异常: {type(result)}")
        return result

    @staticmethod
    def _extraction_to_update_fields(
        extraction: CGMReportExtraction, raw_text: str
    ) -> Dict[str, Any]:
        """把 LLM 输出的 schema 转成 DAO update_by_id 需要的字段 dict。

        负责所有类型转换:str -> date / datetime / Decimal。
        """

        def to_decimal(value: Optional[float]) -> Optional[Decimal]:
            if value is None:
                return None
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError):
                return None

        def to_date(value: Optional[str]) -> Optional[date]:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value).date()
            except ValueError:
                # 容错:"2024/06/11" 形式
                for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
                    try:
                        return datetime.strptime(value, fmt).date()
                    except ValueError:
                        continue
            return None

        def to_datetime(value: Optional[str]) -> Optional[datetime]:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                # 兼容只给日期的情况
                d = to_date(value)
                return datetime.combine(d, datetime.min.time()) if d else None

        start = to_date(extraction.monitoring_start_date)
        end = to_date(extraction.monitoring_end_date)
        if start is None or end is None:
            raise ValueError(
                f"监测日期解析失败: start={extraction.monitoring_start_date} "
                f"end={extraction.monitoring_end_date}"
            )

        fields: Dict[str, Any] = {
            "raw_text": raw_text,
            "report_source": extraction.report_source,
            "device_model": extraction.device_model,
            "device_serial": extraction.device_serial,
            "report_generated_at": to_datetime(extraction.report_generated_at),
            "monitoring_start_date": start,
            "monitoring_end_date": end,
            "monitoring_days": extraction.monitoring_days,
            "patient_history": extraction.patient_history,
            "patient_age": extraction.patient_age,
            "patient_gender": extraction.patient_gender,
            "target_range_low": to_decimal(extraction.target_range_low),
            "target_range_high": to_decimal(extraction.target_range_high),
            "tir_threshold_pct": to_decimal(extraction.tir_threshold_pct),
            "ehba1c": to_decimal(extraction.ehba1c),
            "mg": to_decimal(extraction.mg),
            "sd": to_decimal(extraction.sd),
            "cv": to_decimal(extraction.cv),
            "hypo_risk_level": extraction.hypo_risk_level,
            "tir_pct": to_decimal(extraction.tir_pct),
            "tir_duration_min": extraction.tir_duration_min,
            "tar_pct": to_decimal(extraction.tar_pct),
            "tar_duration_min": extraction.tar_duration_min,
            "tbr_pct": to_decimal(extraction.tbr_pct),
            "tbr_duration_min": extraction.tbr_duration_min,
            "daily_metrics": extraction.daily_metrics or [],
            "hourly_metrics": extraction.hourly_metrics or [],
        }
        return fields

    @staticmethod
    async def _sync_profile_from_report(
        *, report: CGMReportTable, user_id: str
    ) -> None:
        """把"当前画像 + 这份最新报告" 喂给 LLM,产出新画像并覆盖写。

        Reuse AnTangProfileService.get_profile / AnTangProfileDao.update_profile,
        跟主对话的 update_profile 走同一条 DAO 路径。
        """
        current_profile = await AnTangProfileService.get_profile(user_id)
        report_summary_json = json.dumps(
            cgm_report_to_dict(report, slim=True), ensure_ascii=False, indent=2
        )
        prompt = _PROFILE_SYNC_PROMPT_TEMPLATE.format(
            profile_json=json.dumps(
                current_profile.model_dump(), ensure_ascii=False, indent=2
            ),
            report_summary=report_summary_json,
        )

        updater = StructuredResponseAgent(AnTangUserProfile)
        try:
            updated_profile: AnTangUserProfile = await asyncio.to_thread(
                updater.get_structured_response, prompt
            )
        except Exception as err:
            logger.warning(f"[cgm-report] LLM 画像同步抽取失败,跳过: {err}")
            return

        if not isinstance(updated_profile, AnTangUserProfile):
            logger.warning(
                f"[cgm-report] StructuredResponseAgent 输出类型异常,跳过: {type(updated_profile)}"
            )
            return

        await AnTangProfileDao.update_profile(
            user_id=user_id,
            profile_summary=updated_profile.summary,
            profile_data=updated_profile.model_dump(),
            last_memory_excerpt=f"CGM 报告 {report.monitoring_start_date} ~ {report.monitoring_end_date}",
        )


def cgm_report_to_dict(report: CGMReportTable, *, slim: bool = True) -> Dict[str, Any]:
    """把 CGMReportTable 转成给前端/Agent 用的 dict。

    slim=True 时去掉 raw_text 大字段;Decimal/date/datetime 转成可 JSON 序列化的形式。
    """
    excludes: set[str] = set()
    if slim:
        excludes.add("raw_text")
    raw = report.to_dict(hide_fields=list(excludes))

    # to_dict 已经做了 datetime ISO 化,但 Decimal/date 需要额外处理
    def _coerce(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        return value

    return {key: _coerce(val) for key, val in raw.items()}


def format_cgm_summary_for_agent(report: CGMReportTable) -> str:
    """为对话工具(`import_cgm_report`)生成一段简明中文摘要,塞回主 Agent 上下文。"""
    period = (
        f"{report.monitoring_start_date} 至 {report.monitoring_end_date}"
        f"(共 {report.monitoring_days} 天)"
    )
    history = report.patient_history or "未标注病史"
    target = ""
    if report.target_range_low and report.target_range_high:
        target = (
            f"目标范围 {report.target_range_low}-{report.target_range_high} mmol/L"
        )
        if report.tir_threshold_pct:
            target += f",TIR 参考 ≥{report.tir_threshold_pct}%"

    lines = [
        f"已导入 CGM 报告(监测时段 {period},{history})。",
        target if target else None,
        f"核心指标:TIR={report.tir_pct}%,TAR={report.tar_pct}%,TBR={report.tbr_pct}%,"
        f"平均血糖 MG={report.mg} mmol/L,变异系数 CV={report.cv}%,"
        f"低血糖风险={report.hypo_risk_level or '未标注'}。",
    ]
    if report.daily_metrics:
        lines.append(f"含 {len(report.daily_metrics)} 天的逐日明细数据。")
    if report.hourly_metrics:
        lines.append(f"含 {len(report.hourly_metrics)} 个时段的分时段统计。")
    return "\n".join(line for line in lines if line)
