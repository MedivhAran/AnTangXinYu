# 当前已知问题

## Expo SDK 57 上游 `uuid` 安全告警

- 发现时间：2026-07-11。
- 发现方式：在官方 `blank-typescript@sdk-57` 模板安装完成后执行 `npm audit --omit=dev`。
- 当前结果：10 个中等级告警，根源是 Expo CLI 原生工程工具链经 `@expo/config-plugins`、`xcode` 引入 `uuid@7.0.3`。
- 对应公告：`GHSA-w5hq-g745-h8pq`，影响 `uuid < 11.1.1` 在特定版本 UUID 生成并传入 buffer 时的边界检查。
- Expo Doctor 结果：20/20 检查通过；TypeScript 检查通过。
- 当前处理：保持告警显式开放。`npm audit fix --force` 会把 Expo 降到 46.0.21，属于破坏性版本变化，项目不执行该命令。
- 关闭条件：Expo SDK 更新依赖链后重新安装并复查；正式分发前要求生产依赖审计通过或完成基于上游源码的影响验证与明确决策。

## EAS CLI 20.5.1 废弃依赖警告

- 发现时间：2026-07-11。
- 发现方式：首次运行 `npx eas-cli@latest login`。
- 当前结果：EAS CLI 的临时依赖链报告 `inflight`、`lodash.get`、`@xmldom/xmldom@0.7.13`、多个旧版 `uuid`、`glob`、`rimraf` 和 `tar` 已废弃，其中部分警告明确涉及已公开安全问题。
- 范围：这些包来自按需下载的 EAS CLI 20.5.1，当前手机端 `package.json` 未直接声明它们。
- 当前处理：保持告警显式开放，继续使用 Expo 官方当前 EAS CLI 完成开发构建；每次构建调用固定显示实际 CLI 版本。
- 关闭条件：EAS CLI 上游更新依赖链，或项目选择并验证另一条官方构建路径。
