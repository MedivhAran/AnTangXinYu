# 协作环境维护说明

## 代码、检查与安装包

| 项目 | 入口 |
| --- | --- |
| 代码与 PR | [GitHub AnTangXinYu](https://github.com/MedivhAran/AnTangXinYu)，默认分支 `master` |
| 自动检查 | `.github/workflows/check.yml`：手机端检查和后端检查 |
| App 云构建 | [Expo 构建页](https://expo.dev/accounts/wocky528/projects/antang/builds) |
| 联调 API | `https://82.157.48.200:8000` |
| 服务器项目 | `/home/ubuntu/antang-prod`，Compose 项目名 `antang-prod` |

GitHub 检查在 PR 和 `master` 推送时运行。手机任务安装锁定的 npm 依赖，执行类型检查、Lint、测试和 Android JS 打包。后端任务使用独立 PostgreSQL 服务，执行 Ruff、Pyright、迁移和测试。测试入口创建并删除临时数据库，不连接业务库；CI 不配置模型、Expo 和部署密钥。

合并前核对当前提交的检查结果、代码评审和需要的真机结果。检查运行成功只证明自动检查覆盖的行为；Health Connect 权限、同步、原生模块及通知仍须在安装包上验收。

## 构建 Android 安装包

Expo 项目是 `@wocky528/antang`。`development`、`preview`、`production` 环境分别配置 `EXPO_PUBLIC_API_URL`；联调预览包应指向上表中的 HTTPS API。该地址会编入安装包。原生权限、模块或依赖变化后必须重新构建 APK，普通 JS 更新可以使用开发包中的 Metro。

提交手机端代码后，在项目根目录运行以下其中一条：

```bash
bash scripts/build-android development
bash scripts/build-android preview
```

出包脚本提交云构建并附上 Git 提交编号。只有 Expo 页面显示 `FINISHED`、核对地址与 Android 签名后，才分发安装链接。开发包连接 Metro；预览 APK 独立运行。团队成员使用自己的 Expo 项目权限，不共享登录凭据。`EXPO_PUBLIC_API_URL` 不是密钥。

## 更新服务器

服务器生产目录跟踪 GitHub `master`。Nginx 在公网 `8000` 端口提供 HTTPS，API 容器映射到服务器本机 `8001`。发布前确认目标提交通过检查，备份主数据库和 Hindsight 数据库，并检查是否有数据库迁移。

```bash
ssh ubuntu@82.157.48.200
cd /home/ubuntu/antang-prod
git remote -v
git status --short
git pull --ff-only origin master
docker compose -p antang-prod -f compose.yaml -f compose.server.yaml build api
docker compose -p antang-prod -f compose.yaml -f compose.server.yaml up -d --no-build --wait api proactive-care-worker
curl --fail https://82.157.48.200:8000/health
```

发布后核对容器状态、数据库迁移和主要 API 请求。服务端代码从 GitHub 拉取并在服务器构建；App APK 由 Expo 构建，手机安装新包后才能使用新的 Health Connect 历史权限和本地导入功能。旧包内的 API 地址也不会自动改变。

服务器上的旧试验目录如确认没有运行的服务、进程和需要保留的未迁移文件，可以单独清理。不要因为目录已经停用就删除其数据库卷；保留备份，清理范围只限已确认的旧目录。

证书由 `antang-certbot.timer` 定期续期。查看状态：

```bash
systemctl list-timers antang-certbot.timer
```

## 官方参考

- [GitHub Actions 工作流](https://docs.github.com/en/actions/writing-workflows)
- [Expo 构建](https://docs.expo.dev/build/introduction/)
- [Expo 内部分发](https://docs.expo.dev/build/internal-distribution/)
