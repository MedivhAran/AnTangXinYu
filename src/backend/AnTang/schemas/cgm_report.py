"""CGM 报告抽字段 schema 和请求 / 响应模型。

StructuredResponseAgent 会用 CGMReportExtraction 强制 LLM 输出符合该形状的 JSON,
service 拿到后做类型转换(date / datetime / Decimal)再写表。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CGMReportExtraction(BaseModel):
    """LLM 从 PDF 纯文本里抽出的结构化字段。

    所有日期 / 时间字段都用 str 接收(ISO 格式),由 service 转成 date/datetime。
    数值字段用 float,service 转 Decimal。
    """

    # 出具方
    report_source: Optional[str] = Field(None, description="出具机构名称,如 '天津医科大学总医院',找不到留空")
    device_model: Optional[str] = Field(None, description="设备型号,如 '硅基动感Pro'")
    device_serial: Optional[str] = Field(None, description="设备序号,如 'LT23129SS2'")
    report_generated_at: Optional[str] = Field(
        None, description="报告生成时间,ISO 格式 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS"
    )

    # 监测时段(必填)
    monitoring_start_date: str = Field(..., description="监测开始日期 YYYY-MM-DD")
    monitoring_end_date: str = Field(..., description="监测结束日期 YYYY-MM-DD")
    monitoring_days: int = Field(..., description="监测天数,整数")

    # 患者元信息(只抽病史/年龄/性别,不抽姓名/手机号)
    patient_history: Optional[str] = Field(
        None,
        description="病史标签,如 '妊娠糖尿病' / '2型糖尿病'。不存在则留空,不要编造",
    )
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = Field(None, description="性别,如 '男' / '女'")

    # 目标范围(因病史而异,妊娠糖尿病 3.5-7.8 + TIR ≥90%;常规 3.9-7.8 + TIR ≥70%)
    target_range_low: float = Field(..., description="目标范围下限 mmol/L")
    target_range_high: float = Field(..., description="目标范围上限 mmol/L,通常 7.8")
    tir_threshold_pct: float = Field(..., description="TIR 达标参考值,如 90 或 70")

    # 聚合指标
    ehba1c: float = Field(..., description="预估糖化血红蛋白 %,数字部分")
    mg: float = Field(..., description="平均葡萄糖值 mmol/L")
    sd: float = Field(..., description="葡萄糖标准差 mmol/L")
    cv: float = Field(..., description="变异系数 %,数字部分")
    hypo_risk_level: str = Field(..., description="低血糖风险等级:高/中/低/最低")

    # TIR / TAR / TBR
    tir_pct: float = Field(..., description="目标范围内时间百分比")
    tir_duration_min: int = Field(..., description="目标范围内总时长换算成分钟,如 '20h17min' -> 1217")
    tar_pct: float = Field(..., description="高于目标范围百分比")
    tar_duration_min: int = Field(..., description="高于目标范围时长(分钟)")
    tbr_pct: float = Field(..., description="低于目标范围百分比")
    tbr_duration_min: int = Field(..., description="低于目标范围时长(分钟)")

    # 明细(可选,短报告没有)
    daily_metrics: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "每日明细数组,每项形如 "
            "{date: 'YYYY-MM-DD', mg: 6.08, tir_pct: 86.3, tar_pct: 13.7, "
            "tbr_pct: 0.0, sd: 1.35, cv: 22.26, lage: 5.8, mage: 2.3, modd: 1.52}。"
            "短报告没有这部分则留空数组。"
        ),
    )
    hourly_metrics: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "分时段(按小时)明细数组,每项形如 "
            "{hour_range: '00-01', tir_pct: 99.3, tar_pct: 0.7, tbr_pct: 0.0, "
            "mg: 5.48, sd: 0.83, cv: 15.21, median: 5.46, iqr: 0.75}。"
            "短报告没有这部分则留空数组。"
        ),
    )


class CGMImportRequest(BaseModel):
    """POST /api/v1/cgm/import 请求体。"""

    file_url: str = Field(..., description="先调 /upload 拿到的对象存储 URL")
    file_name: str = Field(..., description="原始文件名,用于校验后缀")


class CGMReportSummary(BaseModel):
    """对外返回的简版报告字段(隐藏 raw_text 大字段)。"""

    id: str
    user_id: str
    file_url: str
    file_name: Optional[str] = None
    report_source: Optional[str] = None
    device_model: Optional[str] = None
    monitoring_start_date: Optional[str] = None
    monitoring_end_date: Optional[str] = None
    monitoring_days: Optional[int] = None
    patient_history: Optional[str] = None
    target_range_low: Optional[float] = None
    target_range_high: Optional[float] = None
    tir_threshold_pct: Optional[float] = None
    ehba1c: Optional[float] = None
    mg: Optional[float] = None
    sd: Optional[float] = None
    cv: Optional[float] = None
    hypo_risk_level: Optional[str] = None
    tir_pct: Optional[float] = None
    tar_pct: Optional[float] = None
    tbr_pct: Optional[float] = None
    daily_metrics: List[Dict[str, Any]] = Field(default_factory=list)
    hourly_metrics: List[Dict[str, Any]] = Field(default_factory=list)
    parse_status: str
    parse_error: Optional[str] = None
    create_time: Optional[str] = None
    update_time: Optional[str] = None
