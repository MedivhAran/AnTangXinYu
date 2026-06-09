# 安糖心语 Docker 部署说明

`docker/docker-compose.yml` 提供一键启动的本地完整运行环境，包含 MySQL、Redis、MinIO、后端和前端五个服务。

## 系统要求

- Docker 20.10+
- Docker Compose v2
- 内存：建议 8 GB 以上
- 磁盘：10 GB 以上

## 启动

```bash
# 1. 从模板复制一份配置文件
cp docker/docker_config.example.yaml docker/docker_config.yaml

# 2. 改配置：填入自己的模型 API Key 等
vim docker/docker_config.yaml

# 3. 在仓库根目录启动
docker compose -f docker/docker-compose.yml up --build -d
```

构建期间 `docker/docker_config.yaml` 会被复制到容器内 `/app/AnTang/config.yaml` 作为后端运行配置。`docker_config.yaml` 已在 `.gitignore` 中，不会进仓库；只有 `docker_config.example.yaml` 会被提交。

## 访问地址

| 服务         | 地址                          | 说明                  |
| ------------ | ----------------------------- | --------------------- |
| 前端         | http://localhost:8090         | Vue + Vite 开发服务   |
| 后端 API     | http://localhost:7860         | FastAPI 应用          |
| Swagger 文档 | http://localhost:7860/docs    | 自动生成的 API 文档   |
| MinIO 控制台 | http://localhost:9001         | 默认账号 minioadmin   |
| MySQL        | localhost:3306                | root / 123456         |
| Redis        | localhost:6379                | 无密码                |

## 配置要点

`docker/docker_config.yaml` 中需要确认：

1. **数据库连接**：容器内主机名使用 `mysql` / `redis` / `minio`，不是 `localhost`。
2. **模型配置**：`multi_models` 下的 `conversation_model`、`embedding`、`qwen_vl`、`text2image`、`rerank` 都需要填入可用的 `api_key` 和 `base_url`。
3. **RAG**：默认走 Milvus（`rag.vector_db.mode: standalone`），向量索引持久化到 `milvus_data` 卷。
4. **存储**：默认 MinIO，Bucket 名为 `agentchat`，由 docker-compose 中的容器自动初始化。

## 数据持久化

```text
mysql_data           # MySQL 数据卷（docker volume）
redis_data           # Redis 数据卷（docker volume）
docker/data/minio_data   # MinIO 文件
data/antang_knowledge_pdfs/  → /app/data/antang_knowledge_pdfs（只读挂载）
data/vector_db/              → (已废弃，改用 Milvus standalone 管理)
```

向量库挂载到宿主机是必要的：MySQL 中保存的知识库文件记录与 Chroma collection 必须保持一致，重建容器时不能让其中一边丢失。

## 常用命令

```bash
# 查看运行状态
docker compose -f docker/docker-compose.yml ps

# 查看后端日志
docker compose -f docker/docker-compose.yml logs -f backend

# 重新构建后端
docker compose -f docker/docker-compose.yml up -d --build backend

# 手动触发安糖知识库 PDF 导入
docker compose -f docker/docker-compose.yml exec backend \
  uv run python -m AnTang.scripts.import_antang_knowledge

# 停止全部服务
docker compose -f docker/docker-compose.yml down
```

## 常见问题

**后端 healthcheck 一直不通过**

- 后端 healthcheck 的 `start_period` 是 180 秒，首次启动会下载依赖、初始化数据库表、跑安糖知识库扫描，可能比较慢。
- 看日志确认是否卡在数据库连接或模型 API 调用：`docker compose ... logs -f backend`。

**前端连不上后端**

- 前端容器通过 `http://agentchat-backend:7860` 访问后端，依赖 docker 内部网络。`agentchat-network` 必须已创建（compose 启动时会自动建立）。
- 如果只重启了一边，建议 `docker compose down && up -d`。

**端口被占用**

```bash
# 修改 docker-compose.yml 中的端口映射
ports:
  - "17860:7860"   # 把宿主机端口换成空闲值
```

**MinIO Bucket 没有创建或没有读权限**

进入 MinIO 控制台（http://localhost:9001，账号 `minioadmin/minioadmin`），手动创建 `agentchat` bucket 并把访问策略设为 `readwrite`。

## 与宿主机部署的关系

仓库同时支持宿主机部署（`uv sync` + `npm run dev`）和 Docker 部署。**推荐使用 Docker**：

- MySQL / Redis / MinIO / Chroma 等依赖一次起齐，不需要在本机维护多个服务。
- 构建期间会自动把 `docker/docker_config.yaml` 烧进镜像，配置统一。
- 与生产环境形态最接近，避免“本机能跑、容器跑不起来”的问题。

仅在调试 Python 代码、需要热重载、或需要直连本机 IDE 调试器时才考虑宿主机模式。
