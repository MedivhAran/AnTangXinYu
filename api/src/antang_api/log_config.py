import sys

from loguru import logger


def configure_logging() -> None:
    """配置应用日志；业务代码只绑定经过筛选的运行元数据。"""

    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | {message} | {extra}"
        ),
        backtrace=False,
        diagnose=False,
        catch=False,
    )
