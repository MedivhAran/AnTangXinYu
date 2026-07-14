# Amazfit 健康数据接入调研

> 调研时间：2026-07-14  
> 当前状态：完成资料与旧代码盘点，尚未确定最终路线，等待真机验证。

## 这次要回答的问题

本轮调研关注三件事：旧项目的 Amazfit 链路究竟做了什么；现在有哪些官方或成熟的接入方式；手环数据采集与健康档案管理 Agent 应该如何划分职责。

以下内容严格区分“官方设计”“基于证据的推论”和“自定义候选方案”。候选方案还不是项目决定。

## 旧原型：代码中实际存在的方案

旧实现不在 `../AgentChat`，而是分散在两个独立项目中：

- `/home/medivh/hutao-health`：Amazfit Active 2 上的 Zepp OS 小程序；
- `/home/medivh/HAL9000`：公网中转服务和 Agent 拉取端。

手表端读取最近心率、当日步数、距离、卡路里、站立次数、体表温度、血氧和压力。打开小程序时会上传一次，用户可以点击按钮上传，页面保持打开时还会每分钟上传。数据先经蓝牙到手机 Zepp App 中的 Side Service，再由手机向公网服务器发 HTTP 请求。

公网中转只保存最后一份 JSON。HAL9000 每隔一段时间拉取该 JSON，再把它无条件放进系统提示词，同时也向模型提供一个只读查询工具。超过 30 分钟的数据会被标记为陈旧。

这套原型没有实现可靠的息屏后台采集。所谓后台 service 只是实验性的 BLE 探针，不会读取或上传真实健康指标。

代码中还确认了这些问题：

- 服务器地址和共享凭证硬编码在手表源码中，传输使用明文 HTTP；
- 同一份凭证同时负责上传和读取，而且服务端允许无凭证启动；
- 服务端只保存最后一份数据，没有用户、设备、历史、去重或防重放；
- 多项指标共用上传时间，并非每个指标真实的测量时间；
- 传感器读取失败会静默丢字段；
- Agent 拉取失败会继续使用旧文件；
- 完整健康指标会出现在日志中；
- 未经校验的单次读数直接进入每次模型上下文。

如果旧公网服务仍在运行，源码中的旧共享凭证应立即作废。该凭证不应复用到安糖心语。

## Zepp OS 官方设计

### 能读取的数据

Zepp OS 官方传感器 API 可以读取心率、血氧、睡眠、压力、步数、距离、能量、站立、PAI、部分运动数据和部分设备的体表温度。不同型号和 API_LEVEL 支持范围不同，官方要求通过设备能力检查确认。

这些接口不是 Zepp 云端完整健康档案。多数接口只提供当天、最近 24 小时或最近 7 天的数据。公开的小程序 API 中没有找到血糖、CGM、完整 HRV、Readiness 或任意时间范围健康历史的统一读取接口。

参考：

- [HeartRate](https://docs.zepp.com/docs/reference/device-app-api/newAPI/sensor/HeartRate/)
- [BloodOxygen](https://docs.zepp.com/docs/reference/device-app-api/newAPI/sensor/BloodOxygen/)
- [Sleep](https://docs.zepp.com/docs/reference/device-app-api/newAPI/sensor/Sleep/)
- [Stress](https://docs.zepp.com/docs/reference/device-app-api/newAPI/sensor/Stress/)
- [Workout](https://docs.zepp.com/docs/reference/device-app-api/newAPI/sensor/Workout/)
- [设备列表](https://docs.zepp.com/docs/reference/related-resources/device-list/)

### 后台执行能力

Zepp OS 3.0 起提供无界面的 App Service。单次 App Service 可以由 Alarm、通知或系统事件唤醒，但执行时间被限制为 600 毫秒。持续 App Service 可以在用户退出页面后继续运行，并可在重启等系统状态变化后恢复，但需要用户额外批准后台权限，也受到设备资源限制；同一时间只能运行一个持续服务实例。

后台服务可以使用心率、睡眠、血氧和压力等非高功耗传感器，不能使用加速度计、陀螺仪和定位等高功耗传感器。官方没有承诺后台服务永不停止，也没有公开手机杀死 Zepp App、蓝牙断开或系统限制后台活动时的可靠上传保证。

参考：[App Service](https://docs.zepp.com/docs/guides/framework/device/app-service/)、[start API](https://docs.zepp.com/docs/reference/device-app-api/newAPI/app-service/start/)。

### 向服务器发送数据

Zepp 官方架构是：

`手表 Device App / App Service → 蓝牙 → 手机 Zepp App 的 Side Service → HTTP → 外部服务器`

Zepp 官方文档和示例明确演示了从手表读取睡眠数据，再通过 Side Service POST 到 Web Server。因此旧原型采用的基本方向是官方支持的方向，不是自创协议；旧原型真正的问题是只完成了手动/前台上传，并且接收、鉴权、保存和错误处理不适合真实产品。

参考：[Zepp OS 整体架构](https://docs.zepp.com/docs/guides/architecture/arc/)、[官方 Post Health Data 说明](https://github.com/orgs/zepp-health/discussions/276)。

### Zepp 云端 API

Zepp Health 官方 GitHub 曾公开 Huami REST API、OAuth 和 Subscription，但仓库与 Wiki 在 2020 年后停止维护，当时还明确限制企业合作和受邀伙伴。当前没有找到面向普通开发者、持续维护并说明 2026 年可用性的 Zepp 健康历史云 API。

因此只能确认“官方历史上提供过”，不能把它作为当前可直接使用的产品能力。[历史 REST API 仓库](https://github.com/zepp-health/rest-api)

## 路线一：Health Connect 由安糖 Android 读取

### 官方设计

Google 官方明确列出 Zepp/Amazfit 可以通过 Health Connect 分享数据。手表仍需先同步到 Zepp App，Health Connect 不会直接连接手表。Google 当前列出的 Zepp 关键数据包括总步数、距离、能量、睡眠时长和阶段、运动摘要、心率、静息心率、呼吸率、血氧、VO₂ max 和运动路线；HRV、皮肤温度、详细运动分段和健康告警没有列入共享范围。[Google Health 的 Zepp 说明](https://support.google.com/googlehealth/answer/14236613?hl=en)

Health Connect 是 Android 设备上的数据层，不是 Python 后端可以直接调用的云接口。它要求 Android 9 以上并安装 Google Play 服务；Android 14 起作为系统模块提供，Android 13 及以下通过 Google Play 中的独立应用提供。[可用性](https://developer.android.com/health-and-fitness/health-connect/availability)

Android App 可以在前台读取，也可以另外申请后台读取权限。Health Connect 不会在新数据到达时主动通知 App，官方建议在应用激活时读取，并在获得后台权限后通过后台任务同步。同步需要处理新增、修改、删除、分页、去重和可能过期的 Changes Token。[读取数据](https://developer.android.com/health-and-fitness/health-connect/read-data)、[同步数据](https://developer.android.com/health-and-fitness/health-connect/sync-data)

每条记录带有 Health Connect ID、修改时间、来源应用、可选设备信息、客户端 ID/版本和记录方式。来源应用只能证明“哪一个 App 写入了记录”，不能单独证明数值一定由某块手表传感器直接测得。[数据格式与来源](https://developer.android.com/health-and-fitness/health-connect/data-format)

发布到 Google Play 时，需要申报健康功能、数据安全信息和每一种权限的具体用途，只能申请产品真正需要的最小数据范围。[发布要求](https://developer.android.com/health-and-fitness/health-connect/publish)

React Native 有持续维护的社区库 [`react-native-health-connect`](https://github.com/matinzd/react-native-health-connect)。它提供 TypeScript 接口和 Expo 配置插件，但不是 Google 官方库，需要 development build，不能在 Expo Go 中运行。安糖当前已经使用 development build，因此技术上可以试验；Expo SDK 57、具体数据类型和后台读取仍需真机验证。

### 基于证据的推论

这条路线可以移除手表小程序和自建公网中转，复用用户本来就在使用的 Zepp App。它不是实时链路，延迟来自“手表到 Zepp、Zepp 到 Health Connect、安糖 App 读取并上传”三段。它适合睡眠、运动、日常活动和趋势分析，不能在未经实测时承担紧急告警。

它还依赖 Google Play 服务，因此不能默认覆盖没有 Google 移动服务的中国大陆 Android 设备。

## 路线二：Google Health API 由安糖后端读取

### 官方设计

Google 在 2026 年 3 月发布了新的 [Google Health API](https://developers.google.com/health)，它是下一代 Fitbit Web API，支持 OAuth、服务端 REST/gRPC、统一数据格式、all-sources 数据流和 webhook。Google 自己的帮助文档说明 Zepp/Amazfit 数据可以经 Health Connect 进入 Google Health，因此存在下面这条云端候选链路：

`Amazfit → Zepp → Health Connect → Google Health → 安糖后端`

公开测试可以先使用有限测试用户。面向超过 100 个用户公开使用时，多数健康权限需要 OAuth 验证，并需要年度 CASA 第三方安全评估。Google 当前公布的评估费用约为 500～4,500 美元，耗时通常为数周。[应用验证](https://developers.google.com/health/app-verification)

该 API 仍在快速变化，2026 年的发布记录中持续出现权限、数据类型和 webhook 行为更新。[发布记录](https://developers.google.com/health/release-notes)

### 基于证据的推论

如果真机实验确认 Zepp 来源数据完整进入 Google Health 的 all-sources 数据流，这条路线可以减少安糖 Android 后台同步压力，并让服务器通过 webhook 得知数据变化。

但它增加了 Google Health App、Google 账号、OAuth、云端数据复制、年度审核和第三方安全评估。官方也没有向开发者承诺 Amazfit 每个字段的云端粒度和延迟。因此它适合做小规模技术验证，不宜在当前阶段直接成为首版基础设施。

## 路线三：Gadgetbridge 直接连接手表

### 开源项目事实

[Gadgetbridge](https://github.com/Freeyourgadget/Gadgetbridge) 是开源 Android 伴侣应用，支持部分 Amazfit/Zepp OS 设备，可同步步数、心率、HRV、睡眠、血氧、压力、温度、运动等数据，具体能力取决于型号。[Zepp OS 支持情况](https://gadgetbridge.org/basics/topics/zeppos/)

较新的 Amazfit 设备通常仍需通过厂商服务器得到配对密钥。Gadgetbridge 的主要数据出口是本地数据库和 ZIP、GPX、FIT 文件，虽然可以通过 Intent 触发同步和导出，但没有为其他 App 提供一套稳定的健康数据 API。项目使用 AGPLv3，内部数据库也会随版本迁移。

### 基于证据的推论

这条路线适合协议研究、高级用户或验证厂商未开放的数据，不适合作为 C 端首版默认入口。普通用户需要安装和维护另一个伴侣应用，项目也会承担设备协议适配、内部格式变更和开源许可证义务。

## 不再考虑 Google Fit

Google Fit Android 和 REST API 只维护到 2026 年底。Google 已明确推荐移动端新项目使用 Health Connect，云端场景使用 Google Health API。[官方迁移说明](https://developer.android.com/health-and-fitness/health-connect/migration/fit)

因此 Google Fit 只能作为旧系统兼容桥梁，不应成为安糖心语的新基础设施。

## 对健康档案管理 Agent 的影响

### 基于证据的职责边界

设备授权、分页读取、上传、去重、删除同步、单位转换、时间校验、来源保存和可复现统计都是确定性工作，应由普通应用代码完成。原始记录和转换依据也应保留在模型上下文之外，模型只按需要读取合适范围的数据或统计结果。

手表观测不等同于健康档案事实。一次心率偏高、一晚睡眠差或一个压力分数，首先只是带有时间和来源的设备观测，不能由 Agent 自动变成疾病判断、稳定生活规律或用户心理状态。

健康档案管理 Agent 更适合读取：

- 当前健康档案；
- 相关原始对话；
- 经过验证并带有来源、统计时间范围的设备趋势；
- 可能与 CGM 数据结合后得到的可复现结果。

它可以识别候选档案变化、给出依据，并根据项目已有确认规则请求用户确认。最终权限判断、字段验证、写入和审计仍由应用代码负责。这个边界也符合 FHIR Observation、Device 和 Provenance 对观测值、来源设备和处理过程的区分，但当前没有必要把每条记录完整实现为 FHIR 资源。

## 自定义候选方案

下面是基于现有证据的候选顺序，不是已经确定的设计。

### 候选 A：先验证 Health Connect，数据足够就作为首版主入口

安糖 Android 从 Health Connect 读取用户授权的数据，上传到安糖后端。后端保存有来源的观测记录，并用普通代码生成统计结果。健康档案管理 Agent 只读取需要的统计结果并提出候选变化。

收益是无需维护手表协议和 Zepp 小程序，能兼容多个向 Health Connect 写数据的品牌。代价是依赖 Google Play 服务，且同步并非实时。

### 候选 B：Zepp 小程序只补 Health Connect 缺失的能力

如果 Active 2 上的压力、体表温度或其他关键数据没有写入 Health Connect，再用官方 App Service + Side Service 链路补充这些数据，而不是重复上传所有指标。

收益是能拿到 Zepp OS 公开但 Health Connect 未共享的数据。代价是用户还要安装手表小程序，项目需要维护 Zepp 设备兼容性和后台可靠性。

### 候选 C：Google Health API 暂作实验路线

先用测试账号验证 Zepp 数据是否进入 all-sources、来源是否可区分、延迟和删除同步是否可接受。只有它明显优于 Android 本地同步，并且产品准备承担 OAuth 验证和年度安全评估时，再考虑成为正式路线。

## 下一步真机验证

在决定架构前，需要用当前 Active 2 和手机验证：

1. 当前手表是否仍是 Active 2，以及 Zepp OS/API_LEVEL；
2. 手机是否具备 Health Connect，Zepp 是否出现在其应用权限中；
3. Zepp 实际写入哪些数据类型；
4. 心率、睡眠和活动数据的粒度；
5. 一次 Zepp 同步后，Health Connect 多久能看到数据；
6. 读取时能否得到 Zepp 来源、设备型号和记录方式；
7. 项目真正需要每日总结、分钟级趋势，还是接近实时的事件；
8. 如需比较云路线，同一份 Zepp 数据能否被 Google Health API 的 all-sources 读取。

完成这些验证后，才能有证据地决定 Health Connect 是否足够、Zepp 小程序是否还需要保留，以及 Google Health API 是否值得承担额外依赖。
