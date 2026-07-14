# 本地运行

项目代码、依赖和 Docker 命令统一在 Ubuntu WSL 中执行。Codex 从 Windows 调用命令时使用下面的形式：

```powershell
wsl.exe -d Ubuntu -- bash -lc "cd /home/medivh/AnTang && <命令>"
```

进入 WSL 终端后，先切换到项目目录：

```bash
cd /home/medivh/AnTang
```

## 准备环境变量

首次运行时复制示例文件：

```bash
cp .env.example .env
cp api/.env.example api/.env
cp apps/mobile/.env.example apps/mobile/.env
```

在根目录 `.env` 中填写 PostgreSQL 密码。Compose 会使用根目录的三个 `POSTGRES_*` 变量创建数据库，并为 API 生成容器网络内的 `DATABASE_URL`。开发密码需要使用 URL 安全字符，因为它会成为数据库连接地址的一部分。

在 `api/.env` 中填写 DeepSeek API key 和至少 32 个字符的随机 `JWT_SECRET`。文件里的本机 `DATABASE_URL` 可供脱离 Compose 运行 API，其中的用户名、密码和数据库名需要与根目录 `.env` 一致；容器启动时该地址会被 Compose 覆盖。

这些真实 `.env` 文件都已被 Git 忽略。

## 启动后端

```bash
docker compose up --build
```

Compose 会等待 PostgreSQL 健康，随后启动 API 容器。API 容器先执行 `alembic upgrade head`，迁移成功后启动一个 Uvicorn 进程。

另开一个 WSL 终端检查服务：

```bash
curl --fail http://127.0.0.1:8000/health
docker compose ps
```

健康接口应返回：

```json
{"status":"ok"}
```

持续查看 API 日志：

```bash
docker compose logs --follow api
```

停止服务并保留 PostgreSQL 数据：

```bash
docker compose down
```

需要同时删除本地数据库卷时，明确执行：

```bash
docker compose down --volumes
```

## Android 真机连接

手机与电脑连接同一个局域网，在 WSL 中查看 Windows 主机的局域网 IPv4 地址：

```bash
powershell.exe -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { \$_.IPAddress -notlike '127.*' -and \$_.PrefixOrigin -ne 'WellKnown' } | Select-Object InterfaceAlias,IPAddress"
```

将 `apps/mobile/.env` 配置为实际地址，例如：

```dotenv
EXPO_PUBLIC_API_URL=http://192.168.1.100:8000
```

用手机浏览器访问 `http://<电脑局域网IP>:8000/health` 可以先确认网络连通性。Windows 防火墙需要允许 TCP 8000 入站。

`EXPO_PUBLIC_API_URL` 会随 App 一起发布，属于公开配置。API key、数据库密码和 JWT 密钥只能保存在服务器环境中。

正式部署必须通过 HTTPS 暴露 API。明文 HTTP 只用于同一局域网内的本地开发。

当前 App 使用 `expo-secure-store` 和 `expo-crypto` 原生模块。依赖发生变化后，需要重新生成 development build：

```bash
cd /home/medivh/AnTang/apps/mobile
source ~/.nvm/nvm.sh
nvm use 24
npx eas-cli@latest build --profile development --platform android
```

安装新的 development build 后启动 Metro：

```bash
npx expo start --dev-client
```

Windows 将当前网络标记为公共网络时，防火墙通常会阻止手机访问 8000 端口。开发时优先让电脑连接手机热点，并只为可信局域网或本地子网放行该端口。手机浏览器成功打开 `/health` 后再启动 App。
