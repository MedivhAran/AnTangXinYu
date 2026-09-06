# 协作环境维护说明

## 现在有哪些入口

| 项目 | 位置 / 状态 |
| --- | --- |
| 代码与 PR | [Gitee 仓库](https://gitee.com/medivharan/antang) |
| 开发包与预览包 | [Expo 构建页](https://expo.dev/accounts/wocky528/projects/antang/builds) |
| App 当前 API 入口 | `https://106.15.194.0`，2026-09-06 健康检查正常 |
| 实际 Docker 服务 | 目前在维护者电脑运行；公网转发链路未在本次配置中更改 |
| 自动检查 | `.workflow/check.yml`；本地验证结果见下方验收记录 |
| 手动出包 | `.workflow/android-preview.yml`，或 `bash scripts/build-android preview` |

公网地址可访问，不等于已有一套全天在线、数据独立的测试服务器。当前入口沿用现有后端，团队成员使用自己的测试账号；CI 始终使用临时数据库。维护者电脑和转发链路停机时，联调入口可能不可用。

## 开通 Gitee 自动检查

仓库管理员打开“流水线”，开通 Gitee Go，并导入本仓库 `.workflow/check.yml`。配置按 Gitee 官方 API 返回的流水线示例和 `custom-build@custom` 插件字段编写。需要由命令行代为配置时，先在 Ubuntu 执行 `npx --yes @gitee/gitee-cli@0.3.0 auth login`；令牌只填在本机终端，不发送到聊天或提交到仓库。

自动检查包含两项：

| 任务 | 执行内容 |
| --- | --- |
| 手机端 | Node.js 24；安装锁定依赖、类型检查、Lint、测试、Android JS 打包 |
| 后端 | Python 3.12、uv；Ruff、Pyright、临时 PostgreSQL、数据库迁移和测试 |

先手动执行一次，再确认 Push 和目标为 `main` 的 PR 更新会触发。执行环境必须支持 Node 24、Python 3.12、Docker 和 Compose；配置会在缺少 Docker 时明确失败，不会跳过数据库测试。Gitee 账号权限、额度及实际执行机能力需要在首次云端运行中确认。

如果当前云执行机不提供 Docker，需要为这项任务配置一台**独立的检查执行机**。不要把维护者正在运行业务数据库的电脑作为执行外部 PR 的宿主机。

自动检查流水线不配置模型、Expo 或部署密钥。也不要给整个仓库的所有流水线设置可见的 `EXPO_TOKEN`。

## 配置合并规则

把 `main` 设为保护分支，限制直接推送，合并由维护者负责。开启当前套餐支持的评审、测试通过要求，并用一个故意检查失败的 PR 验证是否确实阻止合并。

分支保护不自动等于“CI 必须通过”。如果当前套餐不能把流水线结果设为强制门槛，维护者必须核对**当前提交**的检查结果，不能把旧提交的绿色结果当作已通过。

## 给团队生成安装包

已有 Expo 项目为 `@wocky528/antang`。在 Expo 管理页为开发成员分配所需权限；无需共享账号密码。

`eas.json` 分别使用 `development`、`preview`、`production` 环境。维护者在 Expo 对应环境设置 `EXPO_PUBLIC_API_URL`；前两者连接联调后端，生产环境单独配置。这个变量会进入 APK，不能放密钥。

本地出包前提交移动端改动，在根目录执行：

```bash
bash scripts/build-android development
bash scripts/build-android preview
```

按需要选择其中一条。脚本提交云构建后会返回链接；此时只是进入队列，必须等 Expo 页面显示 **Finished** 才能分发。构建消息包含提交编号，评审时核对它。

Gitee 手动出包使用 `.workflow/android-preview.yml`。只允许维护者运行，在**这条手动流水线**中配置 `EXPO_TOKEN` 密钥，选择已审查的分支执行。它仅提交 EAS 任务，后续构建结果在 Expo 查看。不要自动为外部 Fork PR 执行带凭据的出包流程。

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

- 本地手机端检查与 Android JS 打包：通过，23 组 / 148 个测试。
- 本地后端迁移、测试与临时数据库清理：通过，290 个测试，临时容器和网络已清理。
- EAS 开发、预览环境：已确认均配置了当前 API 地址；上传归档已检查，只包含移动端与非秘密示例配置。新安装包等待本次构建结果。
- Gitee 流水线开通与云端执行：管理 API 返回未登录，尚未验证。
- `main` 分支保护：API 确认当前未开启，需要管理员启用并验证合并门槛。
- 真机安装、手环同步与通知体验：由团队成员在对应手机上验收。

## 官方参考

- [Gitee 流水线](https://help.gitee.com/enterprise/pipeline)
- [Gitee 保护分支](https://help.gitee.com/enterprise/repo/设置保护分支)
- [Expo 从 CI 触发构建](https://docs.expo.dev/build/building-on-ci/)
- [Expo 内部安装包分发](https://docs.expo.dev/build/internal-distribution/)
