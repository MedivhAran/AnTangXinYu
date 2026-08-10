# Hindsight 部署方案

更新日期：2026-08-09。部署和 Python client 都固定为 Hindsight `v0.8.4`，对应官方源码 commit [`92f433c`](https://github.com/vectorize-io/hindsight/tree/92f433c90409636804c0797071a4abbe141f76c5)。

## 最终拓扑

Hindsight 作为独立 Docker 服务接入，并使用独立 PostgreSQL：

```text
Android
   │
   ▼
安糖心语 API ─────► 安糖心语 PostgreSQL
   │
   └──────────────► Hindsight API ─────► Hindsight PostgreSQL
                            │
                            └──────────► DeepSeek
```

Compose 增加 `hindsight` 和 `hindsight-postgres` 两个长期服务。两者都不配置宿主机 `ports`，只在 Compose 内网通信；手机不能直接访问 Hindsight，Hindsight PostgreSQL 也不能从宿主机直连。安糖心语 API 通过 `http://hindsight:8888` 和共享 API key 调用官方异步 client。

独立数据库不是 Hindsight 的强制要求，而是本项目基于现状作出的决定。中文关键词检索需要 PGroonga，向量检索需要 pgvector；如果强行共用当前 PostgreSQL 18 主库，就要更换已运行的主库镜像，并让 Hindsight 的迁移、索引和后台整理与聊天数据共享故障和资源边界。独立 PostgreSQL 17 可以单独备份、升级或重建，不影响聊天主库。[官方 PGroonga Compose](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/docker/docker-compose/pgroonga/docker-compose.yaml)

## 固定镜像和扩展

Hindsight API 使用官方 API-only 镜像：

```text
ghcr.io/vectorize-io/hindsight-api:0.8.4
digest: sha256:c4471d10ab96db4ba91b0d26fa1801d57f541fae08de5634a5e186478a2cfa86
```

Hindsight PostgreSQL 由项目内的轻量 Dockerfile 构建，固定基础镜像：

```text
groonga/pgroonga:4.0.6-debian-17
digest: sha256:a3a7a5ec99796f26544054ebab756429daf698d88aab5eea145a9d0843341c2d
pgvector: v0.8.0
```

数据库第一次初始化时创建 `pgroonga` 和 `vector` 扩展。Hindsight 的数据库数据使用独立持久卷；本地 embedding/reranker 模型使用另一持久缓存卷。镜像、client、PGroonga 和 pgvector 都固定版本，不使用 `latest`。

## 中文模型与首次启动

首版配置使用本地 `BAAI/bge-m3` embedding、`BAAI/bge-reranker-v2-m3` reranker 和 PGroonga 关键词检索，事实提取与 Observation 整理由 DeepSeek V4 Pro 完成。这是项目的中文评测起点，不是 Hindsight 强制组合。[Multilingual 配置](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/multilingual.md)

API 镜像不预装这两个 BAAI 模型，所以第一次启动需要从 Hugging Face 下载较大的模型文件，冷启动会明显更久，也需要足够磁盘空间。下载结果保存在 Compose 命名卷中，后续启动通常会复用未变化的缓存；但在线解析的 `main` 已更新时仍可能下载新的 snapshot。当前 Hindsight 配置只接受模型仓库名，没有锁定 Hugging Face snapshot revision；新的空缓存卷也会取得当时的 `main`，因此“模型品种相同”还不等于“权重逐字节可复现”。模型初始化和官方启动脚本的等待上限都设为 900 秒。首次启动应在可联网环境单独完成并验收；生产发布前需要把审核过的 snapshot 预热进镜像或受控缓存卷，并记录摘要，不能在无提示的情况下更新权重或换回英文默认模型。

## 配置和密钥

部署需要三类独立配置：Hindsight PostgreSQL 的用户名和密码；安糖心语 API 调用 Hindsight 的内部共享 API key；Hindsight 调用 DeepSeek 的 LLM key。最后一项可以与安糖心语现有 DeepSeek 凭证使用同一个值，但 Compose 中仍作为 Hindsight 服务自己的环境变量传入，避免把整个 API 环境文件暴露给记忆容器。

共享 API key 的同一个值分别配置给 Hindsight 服务端和安糖心语 client。密钥只放在本地 `.env` 或部署平台 secret 中，示例文件只保留空值；Compose、日志、测试快照和仓库都不能出现真实值。Hindsight 的 bank ID 不是鉴权手段，每次仍由安糖心语后端根据已认证用户生成。

Hindsight 服务还固定关闭 MCP、document 原文保存和 LLM trace，设置中文输出、稳定 worker ID 以及只提取陪伴记忆的 retain mission。`/health` 只用于确认 API 已完成初始化且数据库可查询，不能代替真实的中文 retain/recall 验收。[配置说明](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/configuration.md)

## 启动、持久化和故障边界

启动顺序是 Hindsight PostgreSQL 健康后启动 Hindsight API，Hindsight API 健康后再启动安糖心语 API。Hindsight API 使用内置 worker，首版不增加独立 worker、Control Plane、多个副本或 Kubernetes。稳定 worker ID 避免容器重建后把后台 Observation 任务永久留在旧 worker 名下。

`hindsight-postgres` 的数据卷和模型缓存卷不能随普通部署删除。备份时分别备份安糖心语主库与 Hindsight 数据库；恢复后用固定 document ID 和主库中的同步游标核对进度。升级 Hindsight 前先备份并停止写入，再用固定目标版本执行官方 migration，检查 `/version` 和中文冒烟样本后恢复流量。

## 部署验收

第一次可用不以“容器变绿”为准。至少要真实完成一次中文 retain、recall 和 Observation 整理，确认 `bge-m3`、reranker、PGroonga 与 pgvector 均实际生效；重复提交同一 document 能替换而不是追加；容器重启后 facts、Observations 和任务仍在；不同 bank 不能互相召回；删除 bank 后不能再召回；API 与数据库端口在宿主机不可访问；日志和 Hindsight 数据库中没有完整聊天原文或 LLM trace。

2026-07-16 的本地基础冒烟已通过：首次冷启动约七分钟，模型缓存约 3.5 GiB；Hindsight 与两套 PostgreSQL、安糖心语 API 均健康，DeepSeek 连接、PGroonga `4.0.6`、pgvector `0.8.0` 以及一条虚构中文对话的同步 retain、recall 和测试 bank 删除均成功。

2026-08-09，Compose 与当前本地环境已从 `shadow` 切到 `online`，这只是配置切换，不改变 Docker 拓扑。真实语义回归使用 `./api/scripts/test-hindsight-acceptance`，覆盖短回复、纠正/否定、助手猜测、document 幂等、多用户隔离和主动关怀召回；当前 Docker/DeepSeek 配置已实际通过全部用例并删除临时 bank，普通测试仍不依赖在线服务。同日还完成了“临时 bank 写入 → Hindsight API 与数据库重启 → 召回 → 删除”的持久性检查。Observation 后台任务的重启恢复、目标机器延迟、空闲内存和长期数据增长仍要继续观察。
