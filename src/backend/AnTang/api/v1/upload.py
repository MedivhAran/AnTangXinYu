from loguru import logger
from fastapi import APIRouter, UploadFile, File, Depends

from AnTang.api.services.user import UserPayload, get_login_user
from AnTang.api.responses.builder import UnifiedResponseModel, resp_200, resp_500
from AnTang.services.storage import storage_client
from AnTang.utils.file_utils import build_storage_public_url, get_object_storage_base_path

router = APIRouter(tags=["Upload"])


@router.post("/upload", description="上传文件的接口", response_model=UnifiedResponseModel)
async def upload_file(
    *,
    file: UploadFile = File(description="支持常见的Pdf、Docx、Txt、Jpg等文件"),
    login_user: UserPayload = Depends(get_login_user),
):
    try:
        file_content = await file.read()

        oss_object_name = get_object_storage_base_path(file.filename)
        file_url = build_storage_public_url(oss_object_name)
        storage_client.upload_file(
            oss_object_name,
            file_content,
            content_type=file.content_type,
        )

        return resp_200(file_url)
    except Exception as err:
        logger.error(f"上传文件{file.filename}出错：{err}")
        return resp_500(message=str(err))
