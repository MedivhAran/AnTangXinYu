# 安糖内置知识库

安糖知识库已经收缩为系统内置资料源，不再对用户开放创建、上传、绑定入口。

## 本地 PDF 目录

默认目录：

```bash
data/antang_knowledge_pdfs
```

部署时可通过环境变量覆盖：

```bash
ANTANG_KNOWLEDGE_PDF_DIR=/path/to/pdfs
```

后端容器已将仓库里的 `data/antang_knowledge_pdfs` 只读挂载到：

```bash
/app/data/antang_knowledge_pdfs
```

Chroma 向量索引持久化在：

```bash
data/vector_db
```

该目录会挂载到容器内的 `/app/vector_db`，避免后端镜像重建后 MySQL 仍有文件记录、但 Chroma collection 丢失。

## 启动导入

FastAPI 启动时会先保证「安糖默认知识库」这条系统记录存在，然后在后台异步扫描 PDF 目录并导入新增 PDF。

## 手动补录

新增 PDF 后，也可以在后端容器中手动触发：

```bash
docker compose -f docker/docker-compose.yml exec backend \
  uv run python -m AnTang.scripts.import_antang_knowledge
```

导入按文件名幂等跳过已登记 PDF；如果数据库已有文件记录但向量 collection 为空或不存在，会复用已有文件记录重建索引。单个文件失败只记录日志，不中断整批同步。
