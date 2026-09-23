# 协作环境维护说明

## 现在有哪些入口

| 项目 | 位置 / 状态 |
| --- | --- |
| 代码与 PR | [Gitee 仓库](https://gitee.com/medivharan/antang) |
| 开发包与预览包 | [Expo 构建页](https://expo.dev/accounts/wocky528/projects/antang/builds) |
| App 当前 API 入口 | `https://82.157.48.200:8000`，2026-09-23 公网 HTTPS、注册和流式聊天已验证 |
| 实际 Docker 服务 | 服务器 `/home/ubuntu/antang-prod` 中的五个 Compose 服务；维护者电脑上的旧容器已停止 |
| 自动检查 | Gitee `.workflow/check.yml`，推送自动触发已验证 |
| 手动出包 | `bash scripts/build-android development` 或 `preview` |

团队联调后端现在由服务器独立运行。2026-09-23 已迁移主数据库和 Hindsight 数据库；原本地数据卷和迁移备份保留，CI 继续使用临时数据库。现有安装包仍包含构建时的旧地址，需要安装新包才能连接新入口。

## Gitee 自动检查

Gitee Go 已开通，推送协作分支会自动触发 `check.yml`。在仓库“流水线”页面选择对应分支，即可查看“安糖代码检查”。

自动检查包含两项：

| 任务 | 执行内容 |
| --- | --- |
| 手机端 | Gitee 提供的 Node.js 24.13.0；安装锁定依赖、类型检查、Lint、测试、Android JS 打包 |
| 后端 | Python 3.12、uv；Ruff、Pyright、临时 PostgreSQL、数据库迁移和测试 |

Gitee 执行机没有 Docker，`scripts/check-api-gitee` 在本次临时 Ubuntu 容器内安装并启动 PostgreSQL 18，数据库时区与本地容器统一为 UTC。它把连接地址显式交给测试入口，测试结束后删除临时库和数据库实例。本地仍使用 Docker，两种环境共用 `api/scripts/test` 的建库、迁移、测试和清理流程。

CI 使用阿里云镜像下载工具和依赖。PostgreSQL 软件包验证官方签名；Python 依赖从 `api/uv.lock` 导出固定版本与哈希，并以 `--require-hashes` 校验安装，然后运行同一套检查。普通开发者继续使用 README 的一条命令即可。

自动检查流水线不配置模型、Expo 或部署密钥。也不要给整个仓库的所有流水线设置可见的 `EXPO_TOKEN`。

## 合并规则

`main` 已设为保护分支，API 回读确认 `protected: true`。仓库已有“至少 1 人审查、1 人测试”的配置，负责人均为 `medivharan`，本次保留。

默认保护规则仍允许管理员推送与合并。团队约定统一走 PR，由维护者核对**当前提交**的检查和真机结果。当前尚未配置或验证“CI 失败禁止合并”的强制门槛；等云检查可运行后，再按套餐能力启用，并用检查失败的 PR 验证。

## 给团队生成安装包

已有 Expo 项目为 `@wocky528/antang`。在 Expo 管理页为开发成员分配所需权限；无需共享账号密码。

`eas.json` 分别使用 `development`、`preview`、`production` 环境。维护者在 Expo 对应环境设置 `EXPO_PUBLIC_API_URL`；前两者连接联调后端，生产环境单独配置。这个变量会进入 APK，不能放密钥。

本地出包前提交移动端改动，在根目录执行：

```bash
bash scripts/build-android development
bash scripts/build-android preview
```

按需要选择其中一条。脚本提交云构建后会返回链接；此时只是进入队列，必须等 Expo 页面显示 **Finished** 才能分发。构建消息包含提交编号，评审时核对它。

出包由有权限的成员使用自己的 Expo 登录执行上述命令，再把生成的安装链接放进 PR。Gitee 自动检查无需 Expo 登录或签名凭据。

首次云构建如缺少签名配置，由维护者交互执行 `npm run build:preview` 完成初始化。`.easignore` 会排除本地 `.env`、后端和原生生成目录；Android 包使用 Expo 管理的签名配置。

## 更新联调后端

当前后端在 `82.157.48.200` 的 `/home/ubuntu/antang-prod`，Compose 项目名为 `antang-prod`。Nginx 在公网 `8000` 端口提供 HTTPS，API 容器只映射到服务器本机的 `8001` 端口；`80` 端口仅用于证书验证。服务器上的旧试验分支和数据卷仍保留，但旧容器已停用。检查运行状态：

```bash
ssh ubuntu@82.157.48.200
cd /home/ubuntu/antang-prod
docker compose -p antang-prod -f compose.yaml -f compose.server.yaml ps
curl --fail https://82.157.48.200:8000/health
```

发布普通 API 改动前，先备份两套数据库并检查迁移。服务器跟踪 Gitee 的 `codex/team-development` 分支；确认提交已通过评审和检查后，在服务器执行：

```bash
cd /home/ubuntu/antang-prod
git pull --ff-only
docker compose -p antang-prod -f compose.yaml -f compose.server.yaml build api
docker compose -p antang-prod -f compose.yaml -f compose.server.yaml up -d --no-build --wait api proactive-care-worker
curl --fail https://82.157.48.200:8000/health
```

服务器已验证可独立构建 API 镜像，本地 Docker 不参与日常后端运行和这类更新。Hindsight PostgreSQL 镜像使用可跨 CPU 运行的 pgvector 编译参数；修改这个镜像或数据库结构时，应单独安排迁移。CI 不自动部署联调后端。首次迁移的备份位于维护者 WSL 的 `/home/medivh/antang-backups/20260923` 和服务器的 `/home/ubuntu/antang-backups/20260923`，两处文件仅限所有者读取。

公网 IP 证书有效期约六天。`antang-certbot.timer` 每天检查两次，证书更新后自动重载 Nginx；2026-09-23 已通过续期演练。检查定时器和证书可用性：

```bash
systemctl list-timers antang-certbot.timer
sudo /opt/antang-certbot/bin/certbot renew --dry-run --no-random-sleep-on-renew
```

Expo 的开发、预览环境已改为新 HTTPS 地址。安装包内的地址不会自动改变，改地址后必须重新出包；生产环境仍单独管理。

## 验收与待接通事项

仓库配置文件存在，不代表远端功能已经启用。交付时逐项记录实际结果：

- 手机端检查与 Android JS 打包：通过，23 组 / 148 个测试；另用不含本地配置、依赖的干净源码副本完整跑通。
- 后端迁移、测试与临时数据库清理：通过，290 个测试；干净源码副本也已跑通，临时容器和网络已清理。
- 服务器联调后端：五个服务已启动；主库迁移后有 6 个用户、176 条消息和 82,170 条手环观测，Hindsight 有 5 个 bank 和 27 条记忆；公网 HTTPS 注册、流式聊天与证书续期演练通过，本地业务容器已停止。
- EAS 开发、预览环境：已确认均配置当前 API 地址；上传归档只包含移动端与非秘密示例配置。
- [开发 APK](https://expo.dev/accounts/wocky528/projects/antang/builds/788bdfc9-4753-48d4-b28f-62283a337922) 已完成，对应提交 `69bbf61`；使用 Metro 时会读取本地新的 API 地址。[新预览 APK 构建页](https://expo.dev/accounts/wocky528/projects/antang/builds/bc5a1e32-ac6f-4733-b4eb-72164894f939) 对应提交 `2bdfa77`，只在状态为 `FINISHED` 后分发。旧预览包内仍是旧 API 地址。
- Gitee 云端执行：已开通并验证推送自动触发；每个提交的两个检查任务都通过后，再进入合并评审。最新结果在仓库“流水线”页面查看。
- `main` 分支保护：已开启并回读确认；自动检查的强制合并门槛尚未验证。
- 真机安装、手环同步与通知体验：由团队成员在对应手机上验收。

## 官方参考

- [Gitee 流水线](https://help.gitee.com/enterprise/pipeline)
- [Gitee 保护分支](https://help.gitee.com/enterprise/repo/设置保护分支)
- [Gitee 默认保护规则与自定义权限](https://blog.gitee.com/2020/02/27/protected-branches/)
- [Expo 从 CI 触发构建](https://docs.expo.dev/build/building-on-ci/)
- [Expo 内部安装包分发](https://docs.expo.dev/build/internal-distribution/)
- [PostgreSQL 官方 Ubuntu 软件源](https://www.postgresql.org/download/linux/ubuntu/)
- [阿里云 PostgreSQL 镜像](https://mirrors.aliyun.com/postgresql/repos/apt/dists/)
