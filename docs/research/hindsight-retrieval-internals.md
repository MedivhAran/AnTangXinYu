# Hindsight 记忆合并、Observation 与四路召回

核验版本：Hindsight `v0.8.4`，固定 commit [`92f433c`](https://github.com/vectorize-io/hindsight/tree/92f433c90409636804c0797071a4abbe141f76c5)。本文只描述官方文档和源码中实际存在的行为；最后一节才是安糖心语的接入建议。

## 先说结论

Hindsight 并不会把不断增长的聊天自动压缩成一份固定大小的数据库。它有三层不同的“控制增长”：提取时跳过没价值的话，Observation 把多条相关事实整理成较少的长期结论，Recall 每次只返回 token 预算内的一小部分。前两层减少噪声和召回重复，第三层限制 Core 一次看到多少内容；它们都不会自动淘汰数据库里的原始 facts。

例如两段聊天分别提取出：

```text
F1：用户夜间独处时担心低血糖后无人发现
F2：用户最害怕的是低血糖昏迷后没人知道
```

raw fact 层通常仍保留 F1 和 F2。后台 consolidation 可以把它们整理成一条 Observation：

```text
O1：用户的夜间低血糖恐惧主要来自独处时无人发现或救助
证据：F1、F2
```

Recall 可以优先返回 O1，并不再把 F1、F2 同时塞给 Core，但 F1、F2 仍是 O1 的证据，仍在数据库里。官方明确把 raw memory 定义为 append-only；错误、过时或重复的 fact 要通过 edit/invalidate 等 curation 操作处理，没有原生 TTL 或按容量自动淘汰。[官方 curation 说明](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/api/memories.mdx#L82-L100)

## Retain 时到底去掉了什么重复

默认 fact 提取提示词会跳过问候、感谢、“对”“好的”等填充语，只保留值得长期记住的信息，并要求尽量把**当前输入段内**相关陈述合成一条 fact。[提取规则](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/retain/fact_extraction.py#L631-L708) 这正是为什么本项目要提交有上下文的短对话段：单独的“对”没有意义，连同前文提交时才可能成为完整事实。

但 fact 提取调用只带当前文本段、时间、context 和 metadata，没有把整个 bank 的旧 facts 提供给模型。[实际 LLM 输入](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/retain/fact_extraction.py#L1143-L1198) 因此“不要重复提取”只能可靠地处理本段内部重复，不能实现跨所有历史对话的全局语义去重；数据库写入也是普通 INSERT，没有按文本或向量做冲突合并。[PostgreSQL 写入](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/db/ops_postgresql.py#L126-L160)

官方确实提供了另一种“重复保护”：同一份聊天段始终使用相同 `document_id`。完全相同的内容不会重新提取；内容变化时，只删除变化/移除的 chunk 及其 facts，再提取变化部分。[delta retain](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/retain/orchestrator.py#L1859-L1949)、[变化部分替换](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/retain/orchestrator.py#L2123-L2195) 这能防止网络重试把同一段写两遍，但两个不同 `document_id` 中出现同一个事实，raw fact 层仍可能保留两条。

## Observation 怎么生成、合并和失效

**官方设计。** Retain 成功后会异步启动 consolidation；Retain 请求先返回，Observation 稍后生成。[异步触发](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/retain.md#L217-L228) 对每批新 facts，系统先分别召回相关旧 Observations 和它们的来源 facts，再让一次 LLM 调用给出 create、update、delete，最后顺序落库。Observation 保存来源 fact IDs、证据数量和变更历史。[批处理流程](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/consolidation/consolidator.py#L1455-L1573)

Observation 有两道去重：完全相同的文本直接拦截；新建或更新后的 Observation 与同 scope 中最相似的旧 Observation 达到默认 `0.97` 余弦相似度时，再让 LLM 判断 merge 或 keep。合并时会把两边的证据 ID 合到同一条；数字、否定、人物等关键差异应保留。[精确与语义去重](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/consolidation/consolidator.py#L61-L245) 官方也明确承认较弱模型仍可能生成近似重复，这套二次判断是补救，不是数学保证。[Observation 去重说明](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/observations.mdx#L44-L63)

Observation 依赖来源 facts，不是取代它们的独立压缩档案。删除一个来源 document 或 fact 时，所有引用它的 Observations 会先被删除；其余仍存在的来源 facts 被重新标记为待 consolidation，随后异步重新生成结论，因此中间可能有短暂空窗。[删除与重建](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/observations.mdx#L209-L241)

数量方面，单条 Observation 的历史版本默认只保留 50 条，但 Observation 总数默认 `-1`，即不限；而且 `max_observations_per_scope` 只约束带 tag 的 scope，不约束无 tag 的全局 scope。[默认配置](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/config.py#L982-L1007)、[scope 上限](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/configuration.md#L1474-L1489) 所以 Observation 也不是一个开箱即用的固定容量池。

## 四路 Recall 分别怎么找

| 路径 | 实际输入和候选生成 | 它擅长什么 |
|---|---|---|
| 语义 | 把 query 转为向量，用 pgvector/HNSW 找余弦相似的 facts。HNSW 是近似搜索，源码先多取 `max(预算 × 5, 100)` 条，再裁到预算。[源码](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/search/retrieval.py#L95-L155) | 不同措辞表达同一意思，例如“夜里总怕出事”与“夜间低血糖恐惧”。 |
| 关键词 | 搜正文、context、实体和日期等文本信号，适合姓名、药名、设备名、准确数字。官方把这一路统称为 BM25，但默认 `native` 实际是 PostgreSQL `tsvector + ts_rank_cd`，不是严格 BM25；其他可选后端才提供 BM25 或中文分词。[后端列表](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/retrieval.md#L55-L78) | 必须出现某个专名或数值的查询。中文效果取决于后端和 tokenizer，不能默认认为可用。 |
| 图 | 不是直接把问题翻译成图查询。它先取约 20 个语义相近的起点，再从这些起点找共享实体的 facts、写入时预建的相似 fact 连接，以及 `causes/caused_by/enables/prevents` 因果连接；同一候选命中多类连接时分数相加。[起点与连接](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/search/link_expansion_retrieval.py#L1-L24)、[扩展与打分](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/search/link_expansion_retrieval.py#L122-L245) | 从已相关的事实顺着人物、概念、相似或因果关系找间接相关信息。普通 fact 的默认图路径是有界的直接扩展，不是无限多跳。 |
| 时序 | 仅在 query 中解析出“上周”“去年冬天”等时间表达时运行。先转换成日期窗口，找时间与窗口重叠且语义相关的 facts；再把窗口分成 8 桶，从最多 60 个候选中均匀选 10 个起点，避免全挤在某几天；最后沿时间/因果连接最多扩展 5 轮。[选点](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/search/retrieval.py#L323-L380)、[窗口与有限扩展](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/search/retrieval.py#L475-L699) | “上个月发生了什么”“去年冬天哪几次最害怕”等明确时间查询。 |

时序检索和“新近程度加分”是两件事：前者根据用户问到的时间范围产生一组候选；后者是在最终排序时给较新的记忆一个很小的通用加分。

## 四路结果怎么变成最终返回

四路先各自产生一张按相关程度排序的候选表，然后依次经过：

1. **RRF 合并。** 不比较余弦、全文检索和图分数这些不同量纲，只看同一条 memory 在每一路排第几，计算 `Σ 1 / (60 + 名次)`。同一 ID 在多路靠前会升高；不同 ID 即使文字同义也仍是两条候选。[RRF 源码](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/search/fusion.py#L29-L109)
2. **Cross-encoder 重排。** 默认最多取 RRF 前 300 条，让 reranker 同时阅读“query + 每条 memory”，重新判断真实相关性。[官方排序说明](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/retrieval.md#L266-L283)
3. **小幅调整。** 最终分数是 reranker 相关性乘以新近程度、与查询时间窗口的接近程度、Observation 证据数量三个系数；默认最大影响分别约为 ±10%、±10%、±5%，只微调，不应盖过相关性。[公式与幅度](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/retrieval.md#L288-L345)
4. **结果去重和 token 截断。** `prefer_observations=true` 时，如果已返回的 Observation 覆盖了某条来源 fact，那条 fact 不再重复出现在本次结果中；这不删除数据库记录。最后按分数从高到低装入结果，直到 `max_tokens` 用完。[prefer_observations](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/engine/memory_engine.py#L4894-L4941)、[token 截断](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/retrieval.md#L357-L381)

## Observation 为什么已经进入在线召回

**官方事实。** v0.8.4 的 `enable_observations` 和 `enable_auto_consolidation` 默认都为 `true`；Observation 不是一个需要我们额外打开才会工作的旁支。[默认值](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-api-slim/hindsight_api/config.py#L982-L1007) 官方材料的措辞并不完全一致：版本化 configuration 页的小节标题仍保留 `Experimental`，但同页两个开关默认开启，专门的 Observation 页面也把自动 consolidation 当作正常流程。[配置页](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/configuration.md#L1464-L1475) 所以不能用“它默认关闭”解释当前行为。

**基于证据的风险判断。** Observation 仍有三个需要持续观察的风险。第一，consolidation 异步运行，最新 fact 已写入时 Observation 可能还没更新，官方没有承诺适用于本项目的固定完成时延；官方给出的 retain 和 recall 典型性能也不等于 consolidation 完成保证。[性能说明](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/performance.md#L13-L44) 第二，官方记录的“过时 Observation 先用 raw facts 核对”属于 `reflect`，普通 `recall` 没有同等保证。[freshness 适用范围](https://github.com/vectorize-io/hindsight/blob/92f433c90409636804c0797071a4abbe141f76c5/hindsight-docs/versioned_docs/version-0.8/developer/observations.mdx#L141-L170) 第三，它是 LLM 生成的归纳结论，仍可能错误扩大一次情绪或处理不好否定和改口。

**安糖心语当前决定。** Core 和主动关怀的在线 recall 同时请求 `world`、`experience` 和 `observation`，并使用 `prefer_observations=true`。它优先返回已经整理好的 Observation；没有覆盖到的相关原始事实仍可返回。结果只作为“可能过时或错误”的陪伴背景，当前用户说法和权威健康档案优先级更高。真实验收已经覆盖短回复语境、否定纠正、助手猜测、多用户隔离、重复 document 和主动关怀召回；Observation 的生成延迟、错误归纳率和删除重建仍需继续评测。

关于无限增长，第一阶段不要假装已经被 Observation 解决：`max_tokens` 只限制一次返回，官方没有 raw fact 自动 eviction。后续要依据真实增长量单独决定 curation/保留规则；失效重复事实会让它退出召回但保留审计归档，而删除来源 fact 又会触发 Observation 重建。由于项目自己的 PostgreSQL 永久保存原始聊天，Hindsight 可以被视为可重建索引，但何时清理、保留哪些事实仍是产品数据生命周期决定，不能由一个随意容量阈值代替。
