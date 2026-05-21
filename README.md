# 安糖心语 AnTang

面向 1 型糖尿病（T1D）/低血糖恐惧情绪的中文陪伴型 Agent，前后端分离。

## 项目定位

详细架构见 [docs/development/项目理解指南.md](docs/development/项目理解指南.md)。


## 第一种部署方式：Docker（强烈强烈推荐）

首先需要电脑装好 Docker Desktop（Windows 装 Docker 时会自动开 WSL2，跟着提示走就行）。

```bash

# 第一步： 进入项目目录
cd AgentChat

# 第二步： 改配置：把 docker/docker_config.example.yaml 复制一份，命名为 docker_config.yaml，然后把里面的 API key 换成自己的
#    重点是 multi_models 下各模型的 api_key 和 base_url
#    详细看文件里的注释

# 3. 一条命令起全部服务（MySQL + Redis + MinIO + 后端 + 前端）
docker compose -f docker/docker-compose.yml up --build -d

# 4. 等 1~3 分钟首次构建 + 启动，然后浏览器打开
#    前端：http://localhost:8090
#    Swagger 文档：http://localhost:7860/docs
```

### 常用维护命令

```bash
# 看后端日志（实时滚）
docker compose -f docker/docker-compose.yml logs -f backend

# 看所有服务状态
docker compose -f docker/docker-compose.yml ps

# 改了后端代码，重新构建后端
docker compose -f docker/docker-compose.yml up -d --build backend

# 停止全部服务（数据保留）
docker compose -f docker/docker-compose.yml down

# 进后端容器跑脚本（比如手动重导知识库 PDF）
docker compose -f docker/docker-compose.yml exec backend \
  uv run python -m AnTang.scripts.import_antang_knowledge

# 彻底重置（连数据库、向量库一起清掉，慎用）
docker compose -f docker/docker-compose.yml down -v
rm -rf data/vector_db docker/data/minio_data
```

### 第一次启动可能遇到的问题

| 现象                         | 原因 / 处理                                                    |
| ---------------------------- | -------------------------------------------------------------- |
| 构建很慢，卡在装依赖         | 首次会拉 Python + Node 全套依赖，正常 5~15 分钟                |
| 前端打开后接口 401           | 还没注册账号，去 `/register` 页面注册一个                      |
| 安糖会话刚启动，知识库答得差 | PDF 索引在后台异步构建，等 1~2 分钟刷新页面再问                |
| 端口被占用                   | 改 `docker/docker-compose.yml` 里的端口映射，比如 `17860:7860` |
| 模型调用 401 / 403           | `docker_config.yaml` 里的 API key 没改成自己的                 |

---

## 第二种部署方式：宿主机部署（强烈 *不* 推荐，时间太多的同学可以试试）

需要本机自己起好 MySQL / Redis / MinIO，并装 Python 3.12+。

```bash
# 1. 装依赖（uv 管理，比 pip 快很多）
cd src/backend
uv sync

# 2. 改配置：把 connection 字段改成本机地址
vim AnTang/config.yaml
# mysql.endpoint:  mysql+pymysql://root:xxx@localhost:3306/agentchat
# redis.endpoint:  redis://localhost:6379
# storage.minio.endpoint: localhost:9000

# 3. 启动（带热重载）
uv run uvicorn AnTang.main:app --host 0.0.0.0 --port 7860 --reload
```

启动后：
- API：http://localhost:7860
- Swagger：http://localhost:7860/docs
- 健康探针：http://localhost:7860/health

依赖管理细节：新增包改 `pyproject.toml` 后跑 `uv lock`，`requirements.txt` 仅做兜底。

---

## 配置文件位置

| 部署方式 | 配置文件                                                  |
| -------- | --------------------------------------------------------- |
| Docker   | `docker/docker_config.yaml` → 构建时复制到容器内 `config.yaml` |
| 宿主机   | `src/backend/AnTang/config.yaml`                          |

需要填入的关键字段：

- `mysql.endpoint` / `redis.endpoint` / `storage.minio.endpoint`
- `multi_models.*.api_key`、`multi_models.*.base_url`（对话、Embedding、Vision、Rerank、文生图）
- `tools.tavily.api_key`、`tools.weather.api_key` 等可选工具

## 安糖系统知识库

PDF 放在仓库根的 `data/antang_knowledge_pdfs/`。后端启动后会自动同步并补建索引，无需手动操作。详细机制见 [docs/development/antang_knowledge.md](../../docs/development/antang_knowledge.md)。

## 测试

`AnTang/test/` 下是模块级手工冒烟脚本，不是 pytest 套件。