# 安糖心语

面向 1 型糖尿病用户的长期陪伴 App，关注低血糖恐惧。Android 使用 Expo / React Native，后端使用 FastAPI、PostgreSQL 和 LangGraph。

## 第一次参与开发

先准备 **Git 和 Node.js 24**。Windows 开发使用 WSL Ubuntu，项目和命令都放在 Ubuntu 内；不要混用 Windows 的 Node 或 Python。

```bash
git clone https://gitee.com/medivharan/antang.git AnTang
cd AnTang/apps/mobile
npm ci
cp .env.example .env
```

示例配置已经填好当前联调地址。`EXPO_PUBLIC_API_URL` 是**安糖后端**的地址，和模型服务地址不是一回事；调试自己的后端时再修改。

安装维护者提供的开发 APK，然后运行：

```bash
npm run dev
```

手机与开发电脑需要能互相访问。修改页面和 TypeScript 逻辑通常会直接刷新；增加原生库或权限后需要重新安装开发包。完整步骤见 [开发说明](docs/development.md)。

## 提交前检查

在仓库根目录执行对应的一条命令：

| 改了哪里 | 命令 | 需要准备 |
| --- | --- | --- |
| 手机端 | `bash scripts/check mobile` | Node.js 24 |
| 后端 | `bash scripts/check api` | uv、Python 3.12、Docker Compose |
| 两边都有 | `bash scripts/check all` | 上面两套环境 |

脚本安装锁定依赖，再执行检查和测试。后端自动检查使用临时数据库，不需要模型密钥，不会连接团队的业务数据库。

## 协作怎么走

在自己的分支修改 → 运行检查 → 提交 PR → 需要体验时生成测试 APK → 评审与真机验收 → 合并。

PR 写清楚“解决什么问题、怎么验证、有没有迁移或配置变化”。涉及手环权限、数据同步、通知等手机行为时，要写实际设备上的结果。编译通过不能代替真机验证。

## 安装包与维护

- **[开发包](https://expo.dev/accounts/wocky528/projects/antang/builds/788bdfc9-4753-48d4-b28f-62283a337922)**：开发者安装，连接自己电脑上的开发服务。
- **[预览包](https://expo.dev/accounts/wocky528/projects/antang/builds/39f4f7a0-f716-4af1-a9e6-c43fc8a3fc27)**：给评审和测试人员安装，不需要电脑持续运行前端服务；仍需连接后端。
- 安装包在 [Expo 项目构建页](https://expo.dev/accounts/wocky528/projects/antang/builds) 获取，部分操作需要项目成员权限。

以上两包于 2026-09-06 编译成功，对应提交 `69bbf61`。后续原生改动需要新包，安装前核对构建页的提交编号。

[维护说明](docs/maintainers.md) 包含 Gitee 流水线、云构建和测试环境。

## 目录

| 路径 | 内容 |
| --- | --- |
| `apps/mobile/` | Android App 与移动端测试 |
| `api/` | 后端、数据库迁移与后端测试 |
| `scripts/` | 团队共用的检查和出包入口 |
| `.workflow/` | Gitee 自动检查配置 |
| `docs/` | 开发与维护说明 |
| `AGENTS.md` | 已确认的产品范围和项目约定 |
