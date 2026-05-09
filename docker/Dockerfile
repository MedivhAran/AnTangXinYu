# Python 3.12 基础镜像
FROM python:3.12-slim as backend-base

# 安装系统依赖
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends -o Acquire::Retries=3 \
    gcc \
    g++ \
    curl \
    git \
    pandoc \
    texlive-xetex \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 安装 uv
RUN pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple/

# 使用国内源（可选）
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/

# 先复制依赖文件（利用缓存）
COPY src/backend/pyproject.toml ./pyproject.toml
COPY src/backend/uv.lock ./uv.lock

# 安装依赖（基于 lock，保证稳定，并启用缓存防止由于单次网络波动导致从头开始拉取）
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --index-url https://pypi.tuna.tsinghua.edu.cn/simple/

# 再复制代码（避免频繁失效缓存）
COPY src/backend/ ./

# 覆盖配置文件
COPY docker/docker_config.yaml /app/AnTang/config.yaml

# 环境变量
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# 暴露端口
EXPOSE 7860

# 启动服务 (使用 uv run 确保在虚拟环境中执行)
CMD ["uv", "run", "uvicorn", "AnTang.main:app", "--host", "0.0.0.0", "--port", "7860"]
