from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    DECIMAL,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlmodel import Field

from AnTang.database.models.base import SQLModelSerializable


class CGMReportTable(SQLModelSerializable, table=True):
    """CGM(动态葡萄糖监测)评估报告。

    每份用户上传的 PDF 报告独立一行。允许同一用户多份并存,通过
    monitoring_end_date 取最新。不去重(用户自己删错误上传)。
    """

    __tablename__ = "cgm_report"

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: str = Field(
        sa_column=Column(String(64), nullable=False, index=True),
        description="报告所属用户ID",
    )

    # 报告原始引用
    file_url: str = Field(
        sa_column=Column(String(512), nullable=False),
        description="上传后对象存储里的 URL",
    )
    file_name: Optional[str] = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
        description="原始文件名",
    )

    # 报告出具方
    report_source: Optional[str] = Field(
        default=None,
        sa_column=Column(String(128), nullable=True),
        description="出具机构,如 '天津医科大学总医院'",
    )
    device_model: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
        description="设备型号,如 '硅基动感Pro'",
    )
    device_serial: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
        description="设备序号",
    )
    report_generated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True),
        description="报告生成时间",
    )

    # 监测时段(必填,核心索引字段)
    monitoring_start_date: date = Field(
        sa_column=Column(Date, nullable=False),
        description="监测开始日期",
    )
    monitoring_end_date: date = Field(
        sa_column=Column(Date, nullable=False),
        description="监测结束日期",
    )
    monitoring_days: int = Field(
        sa_column=Column(SmallInteger, nullable=False),
        description="监测天数",
    )

    # 患者元信息(不存姓名/手机)
    patient_history: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
        description="病史标签",
    )
    patient_age: Optional[int] = Field(
        default=None,
        sa_column=Column(SmallInteger, nullable=True),
    )
    patient_gender: Optional[str] = Field(
        default=None,
        sa_column=Column(String(8), nullable=True),
    )

    # 目标范围(因病史而异)
    target_range_low: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(DECIMAL(4, 2), nullable=True),
        description="目标范围下限 mmol/L,妊娠糖尿病 3.5 / 常规 3.9",
    )
    target_range_high: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(DECIMAL(4, 2), nullable=True),
        description="目标范围上限 mmol/L,通常 7.8",
    )
    tir_threshold_pct: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(DECIMAL(4, 1), nullable=True),
        description="TIR 达标参考值,妊娠糖尿病 90 / 常规 70",
    )

    # 聚合指标
    ehba1c: Optional[Decimal] = Field(
        default=None, sa_column=Column(DECIMAL(4, 2), nullable=True)
    )
    mg: Optional[Decimal] = Field(
        default=None, sa_column=Column(DECIMAL(4, 2), nullable=True)
    )
    sd: Optional[Decimal] = Field(
        default=None, sa_column=Column(DECIMAL(4, 2), nullable=True)
    )
    cv: Optional[Decimal] = Field(
        default=None, sa_column=Column(DECIMAL(5, 2), nullable=True)
    )
    hypo_risk_level: Optional[str] = Field(
        default=None,
        sa_column=Column(String(16), nullable=True),
        description="低血糖风险等级 高/中/低/最低",
    )

    # TIR / TAR / TBR
    tir_pct: Optional[Decimal] = Field(
        default=None, sa_column=Column(DECIMAL(5, 2), nullable=True)
    )
    tir_duration_min: Optional[int] = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
    tar_pct: Optional[Decimal] = Field(
        default=None, sa_column=Column(DECIMAL(5, 2), nullable=True)
    )
    tar_duration_min: Optional[int] = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
    tbr_pct: Optional[Decimal] = Field(
        default=None, sa_column=Column(DECIMAL(5, 2), nullable=True)
    )
    tbr_duration_min: Optional[int] = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )

    # 明细 JSON(短报告可能为空 list)
    daily_metrics: List[Dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
        description="每日明细列表,每项含 date/mg/tir/tar/tbr/sd/cv 等",
    )
    hourly_metrics: List[Dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
        description="分时段明细列表(可选,长报告才有)",
    )

    # 备份与状态
    raw_text: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="PDF 抽出的纯文本,便于重抽或排错",
    )
    parse_status: str = Field(
        default="pending",
        sa_column=Column(String(16), nullable=False),
        description="解析状态 pending/success/failed",
    )
    parse_error: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="解析失败时的错误信息",
    )

    create_time: Optional[datetime] = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
        ),
    )
    update_time: Optional[datetime] = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        ),
    )
