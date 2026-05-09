from http import HTTPStatus
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

import requests
from dashscope import ImageSynthesis
from langchain.tools import tool
from loguru import logger

from AnTang.services.storage import storage_client
from AnTang.settings import app_settings

@tool(parse_docstring=True)
def text_to_image(user_prompt: str):
    """
    根据用户提供的提示词产生图片。

    Args:
        user_prompt (str): 用户的图片提示词。

    Returns:
        str: 生成的图片链接。
    """
    return _text_to_image(user_prompt)


def _download_and_store_result(image_url: str) -> str:
    url_path = urlparse(image_url).path
    unquoted_path = unquote(url_path)
    file_name = PurePosixPath(unquoted_path).parts[-1]
    oss_object_name = f"text_to_image/{file_name}"

    response = requests.get(image_url, timeout=60)
    if response.status_code != HTTPStatus.OK:
        logger.error(f"获取图片 {image_url} 失败，状态码: {response.status_code}")
        raise ValueError(f"获取图片 {image_url} 失败，状态码: {response.status_code}")

    storage_client.upload_file(oss_object_name, response.content)
    logger.info(f"图片 {file_name} 已成功上传到OSS")
    storage_base_url = app_settings.storage.active.base_url.rstrip("/")
    return f"您的图片已经生成完毕，图片链接为：![图片]({storage_base_url}/{oss_object_name})"


def _resolve_qwen_image_endpoint(base_url: str) -> str:
    normalized_url = (base_url or "https://dashscope.aliyuncs.com/api/v1").rstrip("/")

    if normalized_url.endswith("/compatible-mode/v1"):
        raise ValueError(
            "text2image.base_url 配置错误：qwen-image-2.0 不能使用 compatible-mode/v1，"
            "请改为 https://dashscope.aliyuncs.com/api/v1 或完整 generation endpoint。"
        )

    if normalized_url.endswith("/services/aigc/multimodal-generation/generation"):
        return normalized_url

    if "/services/" in normalized_url:
        return normalized_url

    if normalized_url.endswith("/api/v1"):
        return f"{normalized_url}/services/aigc/multimodal-generation/generation"

    if normalized_url.endswith("/api"):
        return f"{normalized_url}/v1/services/aigc/multimodal-generation/generation"

    return f"{normalized_url}/api/v1/services/aigc/multimodal-generation/generation"


def _generate_qwen_image(user_prompt: str) -> str:
    model_config = app_settings.multi_models.text2image
    endpoint = _resolve_qwen_image_endpoint(model_config.base_url)

    payload = {
        "model": model_config.model_name,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"text": user_prompt}
                    ],
                }
            ]
        },
        "parameters": {
            "watermark": False,
            "prompt_extend": True,
            "size": "2048*2048",
        },
    }

    response = requests.post(
        endpoint,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {model_config.api_key}",
        },
        json=payload,
        timeout=120,
    )

    try:
        rsp = response.json()
    except Exception as err:
        raise ValueError(f"文生图接口返回了不可解析的响应: {response.text}") from err

    if response.status_code != HTTPStatus.OK:
        raise ValueError(
            "图片生成暂时不可用："
            f"{rsp.get('message', response.text)}"
        )

    try:
        image_url = rsp["output"]["choices"][0]["message"]["content"][0]["image"]
    except Exception as err:
        raise ValueError(f"文生图接口返回格式异常: {rsp}") from err

    return _download_and_store_result(image_url)


def _generate_legacy_image(user_prompt: str) -> str:
    rsp = ImageSynthesis.call(
        api_key=app_settings.multi_models.text2image.api_key,
        model=app_settings.multi_models.text2image.model_name,
        prompt=user_prompt,
        n=1,
        size="1024*1024",
    )
    if rsp.status_code != HTTPStatus.OK:
        return "sync_call Failed, status_code: %s, code: %s, message: %s" % (
            rsp.status_code,
            rsp.code,
            rsp.message,
        )

    for result in rsp.output.results:
        try:
            return _download_and_store_result(result.url)
        except Exception as err:
            logger.error(f"处理图片 {result.url} 时出错: {str(err)}")
            raise ValueError(f"处理图片 {result.url} 时出错: {str(err)}") from err

    raise ValueError("旧版文生图接口未返回结果图片")


def _text_to_image(user_prompt):
    """给用户的图片描述生成一张照片"""
    model_name = app_settings.multi_models.text2image.model_name or ""
    if model_name.startswith("qwen-image-2.0"):
        return _generate_qwen_image(user_prompt)
    return _generate_legacy_image(user_prompt)
