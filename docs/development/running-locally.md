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

在根目录 `.env` 中填写两套 PostgreSQL 配置、Hindsight 内部 API key 和 Hindsight 调用 DeepSeek 的 key。Compose 会为 API 与 Hindsight 分别生成容器网络内的数据库地址；密码需要使用 URL 安全字符。Compose 默认以 `online` 模式运行 Hindsight，记忆服务故障会让当前聊天或主动关怀任务明确失败；`api/.env.example` 保持 `disabled`，只供不启动 Hindsight 的孤立开发和测试使用。

在 `api/.env` 中填写 DeepSeek API key 和至少 32 个字符的随机 `JWT_SECRET`。文件里的本机 `DATABASE_URL` 可供脱离 Compose 运行 API，其中的用户名、密码和数据库名需要与根目录 `.env` 一致；容器启动时该地址会被 Compose 覆盖。

这些真实 `.env` 文件都已被 Git 忽略。

## 启动后端

```bash
docker compose up --build
```

Compose 会依次启动两套 PostgreSQL、Hindsight 和 API。API 先执行 `alembic upgrade head`，健康后再启动复用同一镜像的 `proactive-care-worker`。Worker 每五秒领取一次到期的持久任务；日常问候默认关闭，健康事件在医疗规则完成审核前只做 shadow 处理，不会生成健康关怀消息。

另开一个 WSL 终端检查服务：

```bash
curl --fail http://127.0.0.1:8000/health
docker compose ps
```

健康接口应返回：

```json
{"status":"ok"}
```

持续查看 API 和主动关怀 Worker 日志：

```bash
docker compose logs --follow api proactive-care-worker
```

## 运行后端测试

不要直接让 `pytest` 使用 `api/.env` 中的开发数据库。部分并发测试会通过独立连接真实提交事务。
在项目根目录统一运行：

```bash
./api/scripts/test
```

这个命令创建一个随机命名的临时 PostgreSQL 数据库，升级到最新 Alembic head，再运行完整
`pytest`。无论测试成功、失败还是被中断，临时数据库都会被强制删除。需要只跑一个文件或
测试节点时，直接传入 pytest 参数：

```bash
./api/scripts/test tests/test_auth.py
```

普通测试使用假 Hindsight client，不会调用外部模型。要验收真实中文提取和召回，先启动 Compose，再显式运行：

```bash
./api/scripts/test-hindsight-acceptance
```

它使用 Compose 内网中的 Hindsight，创建并删除临时 bank，检查短回复、纠正与否定、助手猜测、重复 document、用户隔离和主动关怀召回。失败时应检查合成用例的实际召回内容，不能把失败静默当作没有记忆。

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
当前 Demo 的 Android 构建通过 `expo-build-properties` 显式允许明文 HTTP，确保
`preview` APK 能访问局域网后端；发布到真实用户前必须改成 HTTPS 并删除这个例外。

当前 App 使用 `expo-secure-store`、`expo-camera`、Health Connect 和后台任务等原生模块。原生依赖发生变化后，需要重新生成 development build：

```bash
cd /home/medivh/AnTang/apps/mobile
source ~/.nvm/nvm.sh
nvm use 22.22.2
npx eas-cli@latest build --profile development --platform android
```

安装新的 development build 后启动 Metro：

```bash
npx expo start --dev-client
```

Windows 将当前网络标记为公共网络时，防火墙通常会阻止手机访问 8000 端口。开发时优先让电脑连接手机热点，并只为可信局域网或本地子网放行该端口。手机浏览器成功打开 `/health` 后再启动 App。

## 手机 Demo 验收

登录后直接进入连续聊天。页面没有底部导航；左上角打开侧边栏，侧边栏顶部是账号和设置，下面是健康档案与按日期分组的历史消息位置。健康档案读取服务器保存的权威档案和手环观测，所有设备数值都带观测时间并明确标注为历史记录。

聊天输入区上方提供“报告解读”“健康档案”和“拍照”。拍照会进入 App 内相机并在使用前预览；报告解读会打开系统文件选择器。服务器附件上传和报告分析尚未接通，因此选中的附件只显示在输入区，发送时会明确报错，不会伪造分析结果。

一轮基础展示建议依次检查：

1. 登录页品牌、键盘顶起和错误提示没有遮挡。
2. 打开侧边栏，检查账号、设置、健康档案和历史消息定位，确认没有“新建对话”或独立“今日”入口。
3. 聊天流式状态、列表/加粗排版、联网来源折叠和失败重试正常。
4. 分别打开拍照和报告解读，检查权限、预览、取消和附件移除；当前发送附件应明确提示后端尚未接通。
5. 分别发送“我身高 169”和“我体重 100”：身高卡应能直接确认或原地改值，体重卡应能直接选择公斤、斤或填写其他内容；点“暂不写入”不会修改档案。
6. 从菜单进入健康档案，逐项编辑或清除基础信息，并新增、编辑、删除一条健康情况；遇到版本冲突时页面应刷新内容并保留编辑框，让用户重新确认。设备观测始终只读，没有某类数据时显示空状态，不补虚构数值。
7. 检查最新心率采样、睡眠阶段、步数等卡片都带观测时间，并明确标为历史记录而非实时读数。
8. 从设置进入主动关怀，确认日常问候和计划回访可以保存；心率健康关怀明确显示为评测阶段，不应声称已经发送异常提醒。

不依赖 Metro 的演示安装包使用 `preview` 配置构建为 APK。构建前先把 EAS `preview` 环境中的 `EXPO_PUBLIC_API_URL` 更新为电脑当前局域网地址：

```bash
cd /home/medivh/AnTang/apps/mobile
npx eas-cli@latest env:set preview \
  --name EXPO_PUBLIC_API_URL \
  --value http://<电脑局域网IP>:8000 \
  --visibility plaintext --non-interactive
npx eas-cli@latest build --profile preview --platform android
```

局域网地址变化后，已经安装的 APK 不会自动改地址，需要重新构建。开发阶段频繁换网络时继续使用 development build 和 Metro 更方便。

App 使用本地 Health Connect 后台模块、通知原生配置和 `react-native-safe-area-context`，新增或变更这些原生依赖后必须重新制作 development build，不能只刷新 Metro。`app.json` 引用的真实 `./google-services.json` 已配置完成。

## 主动关怀真机验收

远程通知不能用 Expo Go 验收。当前代码已经配置 `googleServicesFile`、`care-checkins-v1` 默认频道、96×96 白色透明图标和 `#20B894` 图标色。2026-08-09，Firebase Android App `com.antang.app`、真实 `google-services.json` 和 EAS FCM v1 服务账号已经配置；development build `878ff7b9-340b-4b96-a9d5-5ada34815d14` 与修复通知检查的 preview build `8b5dbce2-1308-417f-933a-d80afc602917` 均构建成功。服务账号私钥没有进入项目，preview 包固定使用当时的局域网后端 `http://192.168.1.2:8000`，网络地址变化后需要重新构建。

安装该 development APK 后，在移动端目录运行 `npx expo start --dev-client`。登录 App，在主动关怀设置中开启系统通知；Android 13 的权限窗口会在通知频道建立后出现。把 App 分别放在前台、后台和锁屏状态，用日常问候或明确计划回访验证：前台不出现重复系统横幅，主动消息直接并入聊天；后台和锁屏显示通知；点击通知打开并定位到对应消息。若页面提示“系统通知已开启，但推送暂未连接”，说明 Android 权限已经打开，但手机在 15 秒内没有取得 Expo/FCM Token；先检查 Google Play 服务和手机访问 Google/Expo 服务的网络，再点“重新连接推送”，不能把仅开启系统权限当成推送成功。配置过程参考 [Expo FCM v1 官方步骤](https://docs.expo.dev/push-notifications/fcm-credentials/) 和 [Firebase Android 设置](https://firebase.google.com/docs/android/setup)。

2026-08-10，preview APK 已在 Android 真机完成 Token 登记；一条独立的推送通道测试经 Expo 接受，并在 App 退到后台后显示为系统通知。当前家庭网络能访问 Expo，但直连 Google/Firebase 服务会超时，手机建立可用的 Google 服务网络后登记成功。该结果证明 Firebase/Expo/FCM 的基础投递链路可用，但没有验证通知点击跳转，也不等同于 Agent 生成的主动关怀消息完整通过。一次真实日常问候任务已运行，但 Agent 合理选择跳过，没有产生消息；下一次应使用真实计划回访或符合条件的日常问候继续验收前台、锁屏、冷启动、点击定位和聊天落库。

后台健康检查同样需要 development build。先在聊天页连接 Health Connect 并成功完成一次前台同步，再到“主动关怀 → 健康关怀 → 后台周期检查”显式开启权限。设置页会显示已开启、权限缺失或变更游标失效等真实状态，也可以在同一处显式关闭。游标失效后当前版本会停住，因为尚未实现不会漏记录的完整历史重建；手动同步或清空游标不能安全修复。Android 的周期任务可能被 Doze、厂商省电和 Force stop 延后，所以验收要分别覆盖前台、后台、锁屏和正常结束进程；Force stop 后不应期待系统继续调度。

健康心率当前只做 shadow。即使开启“健康关怀”，命中的心率窗口也只会在数据库任务中留下 `shadow_*` 结果，不应出现聊天消息或系统通知。普通日常问候和明确计划回访才是当前可见推送的验收对象。

手环暂时离线时，可以只读重放数据库中的历史心率：

```bash
cd /home/medivh/AnTang/api
uv run --frozen python -m antang_api.proactive_care.heart_rate_replay \
  --user-id <用户UUID> \
  --start-at 2026-06-15T00:00:00+08:00 \
  --end-at 2026-07-15T00:00:00+08:00 \
  --timezone Asia/Shanghai \
  --assume-adult \
  --assume-context-complete \
  --output heart-rate-shadow-replay.json
```

报告的 `current_snapshot` 使用当前仍存在的记录和每条记录最后一次导入时间，它不是不可变的历史还原；两个 `assume` 参数只影响“假设当时准时到达”的规则评测，不会修改年龄、手环记录或生产任务。报告固定写入已被 Git 忽略的 `api/evals/reports/`，其中包含健康数据，不要提交或发送到公共位置。
