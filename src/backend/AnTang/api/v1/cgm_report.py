from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from AnTang.api.responses.builder import UnifiedResponseModel, resp_200, resp_500
from AnTang.api.services.user import UserPayload, get_login_user
from AnTang.database.dao.cgm_report import CGMReportDao
from AnTang.schemas.cgm_report import CGMImportRequest
from AnTang.services.antang.cgm_report import CGMReportService, cgm_report_to_dict


router = APIRouter(prefix="/cgm", tags=["CGM-Report"])


@router.post("/import", summary="导入并解析 CGM 评估报告", response_model=UnifiedResponseModel)
async def import_cgm_report(
    req: CGMImportRequest,
    login_user: UserPayload = Depends(get_login_user),
):
    """前端先调 /upload 拿 file_url,再调本接口。

    同步返回解析结果(成功 / 失败),解析失败也是 200,内层 parse_status='failed'。
    """
    try:
        if not req.file_name.lower().endswith(".pdf"):
            return resp_500(message="只支持 PDF 文件作为 CGM 报告")
        report = await CGMReportService.parse_and_store(
            file_url=req.file_url,
            file_name=req.file_name,
            user_id=login_user.user_id,
        )
        return resp_200(data=cgm_report_to_dict(report, slim=True))
    except Exception as err:
        logger.exception(f"[cgm-report] /cgm/import 失败: {err}")
        return resp_500(message=str(err))


@router.get("/reports", summary="列出当前用户历史 CGM 报告", response_model=UnifiedResponseModel)
async def list_cgm_reports(
    login_user: UserPayload = Depends(get_login_user),
    limit: int = 20,
    offset: int = 0,
):
    try:
        reports = await CGMReportDao.list_by_user(
            login_user.user_id, limit=limit, offset=offset
        )
        return resp_200(data=[cgm_report_to_dict(r, slim=True) for r in reports])
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))


@router.get(
    "/reports/{report_id}", summary="获取单份 CGM 报告详情", response_model=UnifiedResponseModel
)
async def get_cgm_report(
    report_id: str,
    login_user: UserPayload = Depends(get_login_user),
):
    try:
        report = await CGMReportDao.get_by_id(report_id)
        if not report:
            raise HTTPException(status_code=404, detail="报告不存在")
        if report.user_id != login_user.user_id:
            raise HTTPException(status_code=403, detail="无权访问该报告")
        return resp_200(data=cgm_report_to_dict(report, slim=False))
    except HTTPException:
        raise
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))


@router.delete(
    "/reports/{report_id}",
    summary="删除某份 CGM 报告(仅删自己上传的)",
    response_model=UnifiedResponseModel,
)
async def delete_cgm_report(
    report_id: str,
    login_user: UserPayload = Depends(get_login_user),
):
    try:
        deleted = await CGMReportDao.delete_by_id(report_id, login_user.user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="报告不存在或无权删除")
        return resp_200(data={"deleted": True})
    except HTTPException:
        raise
    except Exception as err:
        logger.error(err)
        return resp_500(message=str(err))
