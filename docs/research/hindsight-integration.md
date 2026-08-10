# Hindsight 陪伴记忆接入方案

更新日期：2026-08-09。实现基于官方 Hindsight `v0.8.4`。Hindsight 是安糖心语旁边的一项独立记忆服务，不是 Sub-agent，也不是 Core Agent 可以调用的工具。Compose 和当前本地环境已经使用 `online` 模式；`api/.env.example` 仍默认 `disabled`，让孤立测试不依赖外部记忆服务。

## Hindsight 在这里负责什么

Hindsight 接收一小段带角色和时间的聊天，先提取以后可能有用的事实，再让应用按照当前对话搜索相关事实。它的核心概念很少：每位用户有一个彼此隔离的 `bank`；每次提交的聊天段是一份 `document`；`retain` 负责从 document 中提取并保存事实；`recall` 负责搜索；`Observation` 是 Hindsight 把多条相关事实整理成的较长期认识。

Hindsight `v0.8.4` 默认会在 retain 后安排 Observation 整理。Recall 同时搜索原始事实和 Observation，并设置 `prefer_observations=true`：已经被一条返回的 Observation 覆盖的旧事实不再重复返回，尚未整理的新事实仍可返回。Observation 是整理结果，不会自动删除所有原始事实，也不是健康档案里的权威结论。[Retain API](https://hindsight.vectorize.io/developer/api/retain)；[Recall API](https://hindsight.vectorize.io/developer/api/recall)

本项目只让它记住用户明确表达或确认的经历、偏好、低血糖恐惧触发因素，以及用户反馈过的有效或无效陪伴方式。糖糖的回复只用来解释用户的短回答，不能被提取成用户事实。健康档案继续由现有 PostgreSQL 和健康档案流程管理，两者不混写。

## 为什么不是每轮都写

用户经常只回复“对”“还是那个”或一个单位。单独把这种消息交给 Hindsight，它不知道用户在确认什么。因此首版每累计三次成功的 Core 对话，才形成一份带上下文的 document。三次是安糖心语的初始评测参数，不是 Hindsight 官方要求。

分段采用错位边界：

```text
第一份 document：U1 A1 U2 A2 U3
第二份 document：A3 U4 A4 U5 A5 U6
第三份 document：A6 U7 A7 U8 A8 U9
```

其中 `U` 是用户消息，`A` 是助手消息。成功的 Core 对话只负责决定“累计三轮后切一次段”，真正写入的正文是同步游标之后、第三轮用户消息之前的全部已完成真实消息。因此中间出现主动助手问候、用户短回复或连续助手消息时，也会保持原顺序进入 document。第三轮 Core 回答仍留到下一段开头，让下一条“对”能看到自己在回答什么。

切段节奏只统计成功完成的顶层 Core 对话。失败或取消的助手回复可能只有残缺文字，因此不会写入；用户已经成功保存的原话仍按真实顺序保留。重试最终成功后，那次成功 Core 对话正常参与三轮计数。

## 一条新消息到来后的完整链路

用户消息仍然先写入安糖心语现有 PostgreSQL。随后，陪伴记忆代码查看这位用户上次已经同步到哪条用户消息，并从现有消息与 Core 运行记录中找出后续三次成功对话。如果刚好形成一段，就按上面的格式拼成 document，同步调用 Hindsight retain。Retain 会进入这次请求的等待时间，不放入后台队列。

Retain 成功后，主数据库只把这位用户的同步游标推进到该段最后一条用户消息，例如 U3。主数据库不再为每个聊天段增加 pending 记录，也不复制聊天正文；需要重试时直接从现有消息表重新拼出同一段。每份 document 的固定编号由“用户 + 末尾用户消息 UUID”确定，并使用 `replace` 写入。同一次请求超时或进程在更新游标前退出，再次提交只会替换同一份 document，不会重复追加。

在 `online` 模式下，Core 用当前消息前最近两条已完成真实消息和当前用户消息调用 recall；主动关怀 Agent 用本次日常问候或计划主题从同一用户 bank 召回。两条链路都搜索原始事实和 Observation，使用 `budget=low`、`max_tokens=2048`、`prefer_observations=true`，不要求返回原始聊天块、额外实体、来源事实或调试 trace。返回文本进入各自明确标为“可能过时或错误”的上下文后，才执行模型调用。

Core 看到的只是几条记忆文字，看不到 Hindsight 的 API、bank、document 编号或内部索引。提示词明确说明这些记忆可能不完整、过时或错误；当前用户刚说的话优先，其次是权威健康档案，陪伴记忆只能辅助理解和调整表达方式。

## 三种运行模式和失败行为

- `disabled`：不调用 Hindsight，方便没有启动记忆服务的本地开发和现有测试。
- `shadow`：真实 retain、推进游标并观察 Hindsight 提取结果，但不把 recall 结果交给 Core。Hindsight 失败时只记录不含正文的运行元数据，当前聊天继续；游标不前进，下次仍用相同 document 编号重试。
- `online`：同步 retain，并在每轮 Core 回复前 recall。Retain 或 recall 失败都会让当前聊天明确失败，不能静默当作“没有记忆”继续回答。

如果 retain 已成功且游标已经提交，随后 recall 或 Core 失败，游标不会倒退。用户重试当前消息时只需再次 recall，不会重复产生新 document。影子模式是唯一允许 Hindsight 故障而聊天继续的阶段，因为该阶段的记忆本来就不参与回答。

## 数据与隐私边界

安糖心语 PostgreSQL 继续永久保存完整原始消息，并且是审计和重建的依据。Hindsight 使用自己的 PostgreSQL，保存提取后的 facts、Observations、索引和任务状态。每位用户的 bank ID 只由后端根据当前登录用户生成，客户端和模型都不能指定 bank。

Hindsight 配置关闭 document 原文长期保存和 LLM trace。关闭原文保存不代表 retain 时不发送聊天：Hindsight 仍需把当次聊天段交给 DeepSeek 提取事实，只是不再把整段正文复制进 Hindsight 数据库。应用日志只记模式、耗时、成功与否和返回条数，不记录聊天、记忆正文、提示词或密钥。

删除用户数据时必须同时删除主数据库中的用户数据和对应 Hindsight bank，并按产品隐私规则处理两套备份。Observation 会减少在线返回的重复内容，但不会保证 Hindsight 存储永不增长；影子评测要持续观察每位用户的 facts、Observations 和磁盘增长，再决定是否需要额外的删除或重建规则。

## 上线与回归验收

自动化单元测试覆盖三轮错位分段、主动助手后的短回复、固定 document 编号、失败行为、不同用户隔离和主动关怀共用 bank。需要真实 Hindsight 和 DeepSeek 的语义验收不会混进普通测试套件；服务启动后显式运行：

```bash
./api/scripts/test-hindsight-acceptance
```

这条命令只创建带随机后缀的临时 bank，验证“对”等短回复、纠正与否定、助手猜测不能覆盖用户否认、相同 document 的 `replace` 幂等、两名用户隔离以及主动关怀召回，结束时删除临时 bank。它是模型语义回归，不承诺输出永远逐字相同；失败必须人工查看合成用例的召回结果，不能降低断言或静默放过。Observation 后台整理的重启恢复、延迟、内存和磁盘增长仍需部署环境的持续验收。

2026-08-09 已在当前 Docker/DeepSeek 配置上实际运行并通过上述全部用例，临时 bank 已清理；同日还通过了临时 bank 在 Hindsight API 与数据库重启后的召回检查。这些结果不代替真实对话质量评测。

当前启用 `online` 不改变原有结构：首版仍不增加记忆 Sub-agent、Core 工具、后台队列、每段 pending 表或新的 LangGraph 流程。
