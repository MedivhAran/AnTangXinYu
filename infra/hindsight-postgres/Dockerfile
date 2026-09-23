FROM groonga/pgroonga:4.0.6-debian-17@sha256:a3a7a5ec99796f26544054ebab756429daf698d88aab5eea145a9d0843341c2d

# PGroonga 自带的 Arrow 软件源不参与本镜像构建，且其旧签名会阻止 apt 更新。
# pgvector 镜像会跨机器运行，禁用默认的 -march=native。
RUN rm /etc/apt/sources.list.d/apache-arrow.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        postgresql-server-dev-17 \
    && git clone --depth 1 --branch v0.8.0 \
        https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && make -C /tmp/pgvector OPTFLAGS="" \
    && make -C /tmp/pgvector install \
    && apt-get purge -y --auto-remove \
        build-essential \
        git \
        postgresql-server-dev-17 \
    && rm -rf /tmp/pgvector /var/lib/apt/lists/*
