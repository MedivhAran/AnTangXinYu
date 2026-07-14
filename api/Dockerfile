# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.19 AS uv
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

RUN groupadd --system antang \
    && useradd --system --gid antang --home-dir /app antang \
    && mkdir --parents /app \
    && chown antang:antang /app

WORKDIR /app

# 先安装锁文件中的第三方依赖，让业务源码变化时可以复用这一层缓存。
COPY --chown=antang:antang pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=antang:antang src ./src
COPY --chown=antang:antang migrations ./migrations
COPY --chown=antang:antang alembic.ini ./
RUN uv sync --frozen --no-dev

USER antang

EXPOSE 8000

# 迁移成功后才启动唯一的 API 进程；任一步失败都会让容器退出。
CMD ["sh", "-c", ".venv/bin/alembic upgrade head && exec .venv/bin/uvicorn antang_api.main:app --host 0.0.0.0 --port 8000 --workers 1"]
