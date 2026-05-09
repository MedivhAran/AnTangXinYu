# encoding=utf-8
import json
import os.path
import tempfile
import logging
from urllib.parse import unquote, urlparse

import aiofiles
from uuid import uuid4

from AnTang.settings import app_settings
from AnTang.utils.date_utils import get_beijing_date_str

def format_file_size(size_bytes):
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    while size_bytes >= 1024 and unit_index < len(units)-1:
        size_bytes /= 1024
        unit_index += 1
    return f"{round(size_bytes, 2)}{units[unit_index]}"

def load_file_to_obj(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error(f"Error loading scene prompts: {e}")
        return {}

def get_object_storage_base_path(file_name):
    beijing_time = get_beijing_date_str()
    file_type = get_file_type(file_name)

    # 改成文件唯一文件名称
    new_file_name = reset_file_name(file_name)

    # 2024-10-26/png/企业文档_a12xk25jn34kn5.png
    return f"files/{beijing_time}/{file_type}/{new_file_name}"


def build_storage_public_url(object_name: str) -> str:
    base_url = app_settings.storage.active.base_url.rstrip("/")
    return f"{base_url}/{object_name.lstrip('/')}"


def normalize_storage_public_url(url_or_object_name: str) -> str:
    if not url_or_object_name:
        return url_or_object_name

    if "://" not in url_or_object_name:
        return build_storage_public_url(url_or_object_name)

    parsed_url = urlparse(url_or_object_name)
    base_url = getattr(app_settings.storage.active, "base_url", "")
    if not base_url:
        return url_or_object_name

    parsed_base = urlparse(base_url)
    if (
        parsed_url.scheme != parsed_base.scheme
        or parsed_url.netloc != parsed_base.netloc
    ):
        return url_or_object_name

    object_name = get_object_name_from_aliyun_url(url_or_object_name)
    if not object_name:
        return url_or_object_name

    return build_storage_public_url(object_name)

def get_file_type(file_name):
    return file_name.split(".")[-1]

def reset_file_name(file_name):
    file_type = get_file_type(file_name)

    return f"{"_".join(file_name.split(".")[:-1])}_{str(uuid4().hex)[:10]}.{file_type}"

async def save_upload_file(upload_file):
    # 创建临时文件夹
    temp = tempfile.mkdtemp()
    file_name = os.path.basename(upload_file.file_name)
    file_path = os.path.join(temp, file_name)
    async with aiofiles.open(file_path, 'wb') as file:
        content = await upload_file.read()
        await file.write(content)
    return file_path

def get_object_name_from_aliyun_url(url: str) -> str:
    """从存储访问 URL 中提取 object name，兼容 OSS 和 MinIO。"""
    if not url:
        return ""

    if "://" not in url:
        return url.lstrip("/")

    parsed_url = urlparse(url)
    object_name = parsed_url.path.strip("/")

    base_url = getattr(app_settings.storage.active, "base_url", "")
    if base_url:
        base_parsed = urlparse(base_url)
        base_path = base_parsed.path.strip("/")
        if base_path and object_name.startswith(f"{base_path}/"):
            object_name = object_name[len(base_path) + 1:]
        elif object_name == base_path:
            object_name = ""

    bucket_name = getattr(app_settings.storage.active, "bucket_name", "")
    if bucket_name and object_name.startswith(f"{bucket_name}/"):
        object_name = object_name[len(bucket_name) + 1:]

    return unquote(object_name)

def get_save_tempfile(file_name):
    # 创建临时文件夹
    temp = tempfile.mkdtemp()
    file_name = os.path.basename(file_name)
    file_path = os.path.join(temp, file_name)
    return file_path

def get_images_dir(images_dir: str="images"):
    # 创建临时文件夹
    temp = tempfile.mkdtemp()
    file_path = os.path.join(temp, images_dir)
    return file_path

def get_markdown_dir():
    return get_images_dir("markdown")

# 只对PDF ---> Markdown使用
def get_convert_markdown_images_dir():
    # 创建临时文件夹
    temp = tempfile.mkdtemp()
    images_path = os.path.join(temp, "images")
    return temp, images_path

def generate_unique_filename(file_name: str, file_suffix: str=None) -> str:
    file_name = os.path.basename(file_name)
    if file_suffix:
        return f"{file_name.split(".")[0]}_{uuid4().hex}.{file_suffix}"
    else:
        return f"{file_name.split(".")[0]}_{uuid4().hex}.{file_name.split(".")[-1]}"

async def get_oss_object_name(file_path, knowledge_id):
    file_name = os.path.basename(file_path)
    file_suffix = file_name.split('.')[-1]

    object_name = f"/{knowledge_id}/{os.path.splitext(file_name)}_{uuid4().hex}_{file_suffix}"
    return object_name

async def read_upload_file(file_path):
    async with aiofiles.open(file_path, 'r') as file:
        content = await file.read()
    return content
