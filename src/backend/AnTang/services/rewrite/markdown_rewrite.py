import re
import os
import base64
import asyncio
from loguru import logger

from AnTang.core.models.manager import ModelManager
from AnTang.utils.file_utils import build_storage_public_url


class MarkdownRewrite:
    def __init__(self, **kwargs):

        # LLM 的配置可以放到配置文件config中
        self.client = ModelManager.get_qwen_vl_model()

    async def _get_image_dict(self, markdown_path):
        # 获取Md文件的上层目录路径
        parent_dir = os.path.dirname(markdown_path)

        images_dir = os.path.join(parent_dir, "images")
        # 将文件名与具体路径一一对应
        image_path_dict = {}
        if os.path.exists(images_dir):
            for path in os.listdir(images_dir):
                image_path_dict[path] = os.path.join(images_dir, path)
        return image_path_dict

    async def _read_markdown(self, markdown_path):
        if not os.path.exists(markdown_path):
            raise FileNotFoundError(f'Markdown 文件未找到: {markdown_path}')
        with open(markdown_path, 'r', encoding='utf-8') as file:
            return file.read()

    async def request_vl(self, image_path):
        # 将本地图片转成 base64进行解析描述
        image_type = image_path.split('.')[-1]
        base64_image = await MarkdownRewrite.encode_image(image_path)
        response = await self.client.ainvoke(
            input=[
                {
                    "role": "system",
                    "content": [{"type": "text", "text": "You are a helpful assistant."}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            # 需要注意，传入BASE64，图像格式（即image/{format}）需要与支持的图片列表中的Content Type保持一致。"f"是字符串格式化的方法。
                            # PNG图像：  f"data:image/png;base64,{base64_image}"
                            # JPEG图像： f"data:image/jpeg;base64,{base64_image}"
                            # WEBP图像： f"data:image/webp;base64,{base64_image}"
                            "image_url": {"url": f"data:image/{image_type};base64,{base64_image}"},
                        },
                        {"type": "text", "text": "图中描绘的是什么景象? 要求：1.字数不超过100字。2.直接输出图片描述文本"},
                    ],
                }
            ],
        )
        logger.debug(f"{image_path} 中的描述信息为 {response.content}")
        return response.content

    async def async_request_vl(self, image, image_path):
        result = await self.request_vl(image_path)
        return image, result

    async def get_image_description(self, image_path_dict):
        # 创建信号量，限制并发数为3
        semaphore = asyncio.Semaphore(3)

        # 预过滤：空白/近空白 PNG 极小(<3KB)，跳过避免浪费 VL 调用
        # 真正的图表/流程图通常 15KB 起步
        _MIN_IMAGE_BYTES = 3072
        filtered = {}
        skipped = 0
        for name, path in image_path_dict.items():
            try:
                if os.path.getsize(path) < _MIN_IMAGE_BYTES:
                    skipped += 1
                    continue
            except OSError:
                continue
            filtered[name] = path
        if skipped:
            logger.info(f"跳过 {skipped}/{len(image_path_dict)} 张小图，不调视觉模型")

        async def limited_request(image, image_path):
            async with semaphore:
                return await self.async_request_vl(image, image_path)

        tasks = [limited_request(image, image_path) for image, image_path in filtered.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 获得每张图片的描述信息
        # 采用的是异步调用，只需要一次请求模型的时间
        # tasks = [self.async_request_vl(image, image_path) for image, image_path in image_path_dict.items()]
        # results = await asyncio.gather(*tasks, return_exceptions=True)

        image_desc_dict = {}
        for result in results:
            if isinstance(result, Exception):
                logger.error(f'图片描述信息出现错误: {result}')
                continue

            image, desc = result
            desc = (desc or "").strip()
            # 后过滤：扔掉明显无用的描述，避免空白/参考文献/纯页面布局充当 chunk 噪声
            _USELESS = {"空白", "无任何", "无可见", "参考文献列表", "参考文献页", "医学文献页面", "无内容"}
            if desc and not any(b in desc for b in _USELESS):
                image_desc_dict[image] = desc
            else:
                image_desc_dict[image] = ""  # 空 alt，不产生噪声
        return image_desc_dict

    async def process_markdown(self, markdown_text, image_oss_dict, image_desc_dict):
        # 正则表达式匹配Markdown中的图片链接格式
        pattern = r"!\[.*?\]\((.*?)\)"

        # 替换函数，在每个匹配的图片链接前加上提示文字
        def replace_image(match):
            image_url = match.group(1)  # 提取图片的URL
            key = os.path.basename(image_url)
            image_oss_object_name = image_oss_dict.get(key)
            image_desc = image_desc_dict.get(key) or ""

            # 图片没有对应的 OSS 上传记录（如某些图未被成功抽取/上传）：
            # 不能把 None 传给 build_storage_public_url，否则 lstrip 崩溃。
            # 优雅降级为只保留描述文字（仍可被检索），去掉死链。
            if not image_oss_object_name:
                return image_desc

            return f'![{image_desc}]({build_storage_public_url(image_oss_object_name)})'

        # 使用re.sub进行替换
        result = re.sub(pattern, replace_image, markdown_text)

        return result

    async def run_rewrite(self, markdown_path, image_oss_dict):
        markdown_text = await self._read_markdown(markdown_path)

        image_path_dict = await self._get_image_dict(markdown_path)

        # 首先获取Image中的描述信息
        image_desc_dict = await self.get_image_description(image_path_dict)

        new_markdown_text = await self.process_markdown(markdown_text, image_oss_dict, image_desc_dict)

        with open(markdown_path, 'w', encoding='utf-8') as file:
            file.write(new_markdown_text)
        logger.info(f'Markdown 文档已经重写完成 !')

    @staticmethod
    async def encode_image(image_path):
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")


markdown_rewriter = MarkdownRewrite()
