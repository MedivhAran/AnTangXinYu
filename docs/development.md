# 开发说明

## 先选你要做的事

只改手机端，先用团队后端和开发 APK，不必先搭数据库。修改后端时，再配置 Python、uv 和 Docker。项目有 Health Connect 原生模块，手机端使用开发 APK。

Windows 使用 WSL Ubuntu。以下命令都在 Ubuntu 终端运行；先确认 `node --version` 是 24.x，`command -v node` 指向 Linux 路径。

## 开始修改手机端

完成 README 的安装步骤后，从最新 `master` 新建自己的分支。分支名描述这次工作，例如 `feature/wearable-import`。

示例配置已填好当前联调 API 地址。从 [README 的开发包链接](../README.md#安装包与维护) 安装 APK；后续原生代码有变化时向维护者取得新包。安装后在 `apps/mobile` 执行：

```bash
npm run dev
```

在开发 APK 中打开电脑提供的开发服务。手机需要能够访问该电脑；WSL 用户要同时检查 Windows 防火墙和 WSL 网络。若二维码地址不可达，先解决网络连通，不要通过改业务代码处理连接问题。

改完后，在仓库根目录执行 `bash scripts/check mobile`。这会检查类型、Lint、单元测试，并实际生成 Android JS bundle。日常快速检查可在 `apps/mobile` 执行 `npm run check`。

## 什么时候需要重新编译 APK

只改 TypeScript、页面或普通资源，通常继续使用原开发包即可。添加原生依赖、修改权限、Expo 插件或本地 Android 模块后，需要新开发包。

有 Expo 项目权限的成员，在 `apps/mobile` 执行 `npm run build:development`。首次使用先执行 `npx --yes eas-cli@20.5.1 login`；没有项目权限时，让维护者构建并提供安装链接。

开发包与预览包目前使用同一 Android 包名，切换时安装对应的包，不会作为两个独立 App 并存。

也可以本地编译：准备 JDK 17、Android SDK，并让 Ubuntu 内的 `adb devices` 看到手机后，执行：

```bash
npx expo run:android --device
```

如果已经生成过 `android/`，此次又修改了插件或权限，先执行 `npx expo prebuild --platform android` 再构建。原生目录由配置生成，不把修改只留在被 Git 忽略的 `android/` 内。

## 只检查后端代码

安装 uv，并准备独立的测试 PostgreSQL。在仓库根目录执行：

```bash
bash scripts/check api
```

它安装锁定依赖，运行 Ruff、Pyright，再通过 `api/scripts/test` 完成迁移和测试。默认使用临时 Docker PostgreSQL；已有独立测试实例时，可设置 `ANTANG_CHECK_DATABASE_URL`，脚本只创建和删除其中的临时测试数据库。无论本地是否配置了真实模型密钥，这条命令都使用明确的测试配置。

只重跑某个后端测试时，先完成依赖安装，再执行：

```bash
bash scripts/test-api tests/test_auth.py
```

不要直接把全量 `pytest` 指向团队或个人正在使用的业务数据库。

## 在自己电脑运行后端

需要实际测试聊天时，从根目录复制 `.env.example` 为 `.env`，并复制 `api/.env.example` 为 `api/.env`。

根目录 `.env` 填 PostgreSQL、Hindsight 数据库及 API 密钥；`api/.env` 填模型地址、模型密钥、Tavily 密钥和至少 32 字符的 JWT 密钥。两份配置中的数据库密码要保持一致。密钥由维护者按需提供或自行申请，不提交到 Git。

```bash
docker compose up --build -d
curl --fail http://127.0.0.1:8000/health
```

首次启动 Hindsight 需要下载模型，会比后续启动慢。Docker 中的 API 启动前会执行数据库迁移。查看状态用 `docker compose ps`；停止服务用 `docker compose stop`，它会保留数据。

手机连接本地后端时，填写手机能访问的电脑地址，而不是手机自己的 `localhost`。WSL 的端口需要对手机可达。若 API 运行在 Docker 中，修改后端源码后需要重新构建对应镜像。

## 提交 PR

写清楚问题、改动效果、检查结果和必要的真机结果。需要评审体验时提供预览 APK 链接及提交编号。新增一种设备接入方式前先确认范围，不直接替换已验收的设备链路。

每次更新提交后重新检查。只有测试通过、评审通过且该批真机验收完成，才进入合并。
