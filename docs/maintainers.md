# 协作环境维护说明

## 现在有哪些入口

| 项目 | 位置 / 状态 |
| --- | --- |
| 代码与 PR | [Gitee 仓库](https://gitee.com/medivharan/antang) |
| 开发包与预览包 | [Expo 构建页](https://expo.dev/accounts/wocky528/projects/antang/builds) |
| App 当前 API 入口 | `https://106.15.194.0`，2026-09-06 健康检查正常 |
| 实际 Docker 服务 | 目前在维护者电脑运行；公网转发链路未在本次配置中更改 |
| 自动检查 | Gitee `.workflow/check.yml`，推送自动触发已验证 |
| 手动出包 | `bash scripts/build-android development` 或 `preview` |

公网地址可访问，不等于已有一套全天在线、数据独立的测试服务器。当前入口沿用现有后端，团队成员使用自己的测试账号；CI 始终使用临时数据库。维护者电脑和转发链路停机时，联调入口可能不可用。

## Gitee 自动检查

Gitee Go 已开通，推送协作分支会自动触发 `check.yml`。在仓库“流水线”页面选择对应分支，即可查看“安糖代码检查”。

自动检查包含两项：

| 任务 | 执行内容 |
| --- | --- |
| 手机端 | Gitee 提供的 Node.js 24.13.0；安装锁定依赖、类型检查、Lint、测试、Android JS 打包 |
| 后端 | Python 3.12、uv；Ruff、Pyright、临时 PostgreSQL、数据库迁移和测试 |

Gitee 执行机没有 Docker，`scripts/check-api-gitee` 在本次临时 Ubuntu 容器内安装并启动 PostgreSQL 18。它把连接地址显式交给测试入口，测试结束后删除临时库和数据库实例。本地仍使用 Docker，两种环境共用 `api/scripts/test` 的建库、迁移、测试和清理流程。

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

当前后端在维护者的 WSL Ubuntu 内，由根目录 Compose 管理。合并后端改动、确认数据库迁移影响并做好数据库备份后，在该目录执行：

```bash
docker compose up --build -d api proactive-care-worker
curl --fail http://127.0.0.1:8000/health
curl --fail https://106.15.194.0/health
```

这一步会更新正在使用的服务，由维护者选择时间执行。CI 不自动部署当前联调后端。更换 API 地址时，同时更新 Expo 的开发、预览环境；已有预览 APK 中的地址不会自动改变，需要重新出包。

## 验收与待接通事项

仓库配置文件存在，不代表远端功能已经启用。交付时逐项记录实际结果：

- 手机端检查与 Android JS 打包：通过，23 组 / 148 个测试；另用不含本地配置、依赖的干净源码副本完整跑通。
- 后端迁移、测试与临时数据库清理：通过，290 个测试；干净源码副本也已跑通，临时容器和网络已清理。
- EAS 开发、预览环境：已确认均配置当前 API 地址；上传归档只包含移动端与非秘密示例配置。
- [开发 APK](https://expo.dev/accounts/wocky528/projects/antang/builds/788bdfc9-4753-48d4-b28f-62283a337922) 与 [预览 APK](https://expo.dev/accounts/wocky528/projects/antang/builds/39f4f7a0-f716-4af1-a9e6-c43fc8a3fc27)：均为 `FINISHED`，对应提交 `69bbf61`，可在构建页下载安装。
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
