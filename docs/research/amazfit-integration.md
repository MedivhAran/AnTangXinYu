# Amazfit 健康数据接入调研

> 调研时间：2026-07-14  
> 当前状态：Amazfit Active 2 已通过 Zepp 向 Health Connect 写入数据；安糖 Android 已完成权限、原生调用和 Zepp 真实读数的真机验收。本文保留仍会影响实现的依据与边界，不记录已经失效的排查过程。

## 当前结论

首版采用这条链路：

`Active 2 → Zepp → Health Connect → 安糖 Android → PostgreSQL`

Android 只读取 Zepp 来源，首次同步最近 30 天，之后在用户手动触发或 App 回到前台时同步新增、修改和删除。它适合活动、睡眠和生理趋势分析，不是实时链路，也不能承担低血糖告警。

已经验收的是 Expo 57 原生构建、Health Connect 权限、原生模块调用和 Zepp 真实数据读取。同步延迟、设备型号元数据、权限撤销后的完整行为，以及 Changes Token 失效后的历史对账仍是后续问题，不阻塞首版使用。

## 旧原型提供的教训

旧方案由 `/home/medivh/hutao-health` 的 Zepp OS 小程序和 `/home/medivh/HAL9000` 的公网中转组成。手表页面打开时经 Zepp Side Service 上传数据，服务器只保存最后一份 JSON，Agent 再定时拉取并放进系统提示词。所谓后台 service 只是实验性 BLE 探针，没有可靠采集真实指标。

这条原型验证了 Zepp OS 的基本传输方向，但不能直接复用：共享凭证和服务器地址硬编码、明文 HTTP、没有用户与历史记录、测量时间不准确、失败会静默使用旧数据、健康正文进入日志、单次读数未经校验就进入每轮模型上下文。旧凭证如仍有效，应作废且不得复用。

## 官方路线与公开证据

### Zepp OS

Zepp OS 公开传感器 API 可读取心率、血氧、睡眠、压力、步数、距离、能量、PAI、部分运动和部分设备的体表温度，实际范围取决于型号和 API level。多数接口只提供当天或近期数据，并不是完整的云端健康档案。[传感器文档](https://docs.zepp.com/docs/reference/device-app-api/newAPI/sensor/HeartRate/)、[设备列表](https://docs.zepp.com/docs/reference/related-resources/device-list/)

官方支持 `手表 Device App / App Service → Zepp Side Service → HTTP → 外部服务器`。持续后台服务需要额外授权并受设备资源限制，官方没有承诺手机杀进程、蓝牙断开等情况下始终可靠。[Zepp OS 架构](https://docs.zepp.com/docs/guides/architecture/arc/)、[App Service](https://docs.zepp.com/docs/guides/framework/device/app-service/)、[官方健康数据示例](https://github.com/orgs/zepp-health/discussions/276)

Zepp 曾公开 REST API、OAuth 和订阅接口，但相关仓库在 2020 年后停止维护，当前没有找到面向普通开发者、持续维护的健康历史云 API。[历史 REST API](https://github.com/zepp-health/rest-api)

因此 Zepp OS 小程序只保留为后续候选：若关键数据没有进入 Health Connect，再用它补缺，不重复上传已有指标。

### Health Connect

Health Connect 是 Android 设备上的数据层，不是后端云 API。它要求 Android 9 以上和 Google Play 服务；Android 14 起是系统模块，较早版本由 Google Play 安装。[可用性](https://developer.android.com/health-and-fitness/health-connect/availability)

Google Play 的兼容应用集合明确包含 Zepp。Health Connect 不直接连接手表，数据仍先进入 Zepp App；具体字段、粒度和延迟以当前手机真机读取为准。[兼容应用](https://play.google.com/store/apps/collection/promotion_all__health_connect?hl=en_US)

官方同步合同中与本项目直接相关的内容是：

- Changes Token 只覆盖创建 token 后的新增、修改和删除，应按数据类型分别维护；
- 所有分页处理和服务器上传成功后才能推进 token；
- 删除事件只带记录 ID，因此服务器必须保存来源 ID；
- App 回到前台是建议的同步时点，后台读取需要额外权限；
- Health Connect 不推送实时新记录，不能用它承诺实时告警。

依据见 [读取数据](https://developer.android.com/health-and-fitness/health-connect/read-data) 与 [同步数据](https://developer.android.com/health-and-fitness/health-connect/sync-data)。

每条记录可带 Health Connect ID、修改时间、来源 App、设备信息和记录方式。来源 App 只能证明谁写入了记录，不能单独证明数值来自某块手表传感器。[数据格式与来源](https://developer.android.com/health-and-fitness/health-connect/data-format)

用户可以撤销单项权限。撤销应显示为“停止同步”，不能表现成数值为零；撤销不会自动删除已上传到 PostgreSQL 的副本，产品仍需提供导入数据删除能力。[删除说明](https://support.google.com/android/answer/12201232)

步数、距离等累计量存在多个来源时应使用 Health Connect Aggregate API 和来源优先级，不能直接把原始记录相加。[聚合读取](https://developer.android.com/health-and-fitness/health-connect/aggregate-data)

### 其他路线

- Google Health API 可以由服务器读取云端数据并使用 webhook，但会引入 Google Health App、Google 账号、OAuth、云端复制和公开发布审核，当前只作为实验候选。[官方 API](https://developers.google.com/health)、[应用验证](https://developers.google.com/health/app-verification)
- Gadgetbridge 能直接连接部分 Amazfit 设备，但普通用户需要更换伴侣应用，数据接口与设备支持不稳定，并带来 AGPLv3 义务，不适合作为 C 端首版默认入口。[项目源码](https://github.com/Freeyourgadget/Gadgetbridge)、[Zepp OS 支持](https://gadgetbridge.org/basics/topics/zeppos/)
- Google Fit API 只维护到 2026 年底，Google 已要求新移动项目迁往 Health Connect，因此不再考虑。[迁移说明](https://developer.android.com/health-and-fitness/health-connect/migration/fit)

## Expo 57 接入事实

项目精确锁定 `react-native-health-connect@3.5.3` 和 `expo-health-connect@0.1.1`。后者不只修改 Manifest，还通过 Expo 原生模块的 Activity 生命周期监听器设置 Health Connect 权限代理，因此没有再维护一份重复的项目配置插件。[Expo 插件源码](https://github.com/matinzd/expo-health-connect)、[主库安装说明](https://github.com/matinzd/react-native-health-connect/tree/v3.5.3#installation)

当前版本有两个已经由源码和真机确认的调用合同缺口：

1. 公开 TypeScript `GetChangesRequest` 使用 `dataOriginFilter`，Android 原生代码实际读取 `dataOriginFilters`。项目在唯一原生边界传复数键，并用测试固定；升级依赖时必须重新核对并删除兼容处理。
2. 没有下一页时，Health Connect 和 Kotlin 桥接层返回 `null`，公开 TypeScript 类型却只声明可省略的 `string`。项目在原生边界把 `null` 规范化为“没有下一页”，非空游标原样保留，重复非空游标明确失败。[ReadRecordsResponse](https://developer.android.com/reference/kotlin/androidx/health/connect/client/response/ReadRecordsResponse)

这些处理针对精确版本中已经确认的不一致，不是对未知错误的兜底。

## Active 2 真机结果

2026 年 7 月 14 日，用户在 Health Connect 中确认 Zepp 写入以下十类数据：

- 活动：步数、锻炼、距离、爬升高度；
- 身体测量：体重；
- 生命体征：呼吸频率、静息心率、心率、血氧饱和度；
- 睡眠。

心率以连续的一分钟区间写入，检查样本包含该分钟内一个 Zepp 心率值，可用于分钟级趋势，但不是原始 PPG 波形。睡眠是带浅睡、深睡和 REM 阶段的完整会话。

本次未看到 HRV、皮肤温度、压力、Readiness、PAI、活动能量或 VO₂ max；这只能说明它们未出现在已检查的数据中，不能推断设备没有测量。体重也不能直接标为 Active 2 传感器观测，因为手表本身不测体重，必须保留记录方式和设备元数据。

2026 年 7 月 16 日，安糖 development build 已进一步完成权限、原生模块调用和 Zepp 真实读数进入应用的验收，因此当前链路不再处于“等待真机数据”的状态。

## 健康档案边界

健康档案是产品总称，内部仍区分三种性质：

1. Zepp/Health Connect 提供的带时间与来源的设备观测；
2. 由确定性程序计算、能够复现的统计结果；
3. 经过管理的长期档案结论。

同步、分页、去重、删除、单位与时间校验、来源保存和统计计算都由普通代码完成。Agent 只能按当前用户和时间范围读取观测或统计，不能修改原始记录，也不能把一次异常读数直接写成疾病判断或长期规律。详细职责与确认规则见 `docs/research/health-profile-manager-design.md`。

## 项目实现决定

Android 严格限定 Zepp 包来源，为每种数据类型保存独立 Changes Token。首次建立 token 后读取最近 30 天，再消费建立 token 后出现的变化；后续逐页同步。只有服务器完整接收一页后才保存下一 token。删除事件形成墓碑，防止旧分页把已删除记录重新创建。

首版只在用户手动触发和 App 回到前台时同步，不申请后台读取权限，不声称实时监测。权限不足、原生返回结构异常、相同版本内容冲突和 token 失效都明确报错。

Changes Token 失效后不能简单清空 token 再读 30 天：失效期间的删除不会出现在新快照中，30 天以前的数据也无法对账。当前实现停止该类型同步；完整恢复需要另行设计服务器快照对账和无法覆盖的历史边界。

## 已验收与未决边界

已验收：Expo 57 clean prebuild、EAS Android 原生编译、权限请求、原生模块调用、Zepp 来源真实读数，以及前台/手动同步的主链路。

仍待验证或设计：实际同步延迟、设备型号元数据、权限撤销后的端到端表现、导入数据删除、Changes Token 失效后的完整历史重建，以及是否值得用 Zepp OS 或 Google Health API 补充缺失能力。这些事项不改变 Health Connect 已作为首版主入口的决定。
