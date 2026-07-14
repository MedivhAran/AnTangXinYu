# 联网搜索实现与首轮评测

本文记录 2026-07-14 完成的 Tavily 联网搜索实现、官方依据和真实评测结果。它描述的是当前代码和已观察到的行为，不代表医疗有效性验证。

## 官方设计

Tavily 官方建议 Agent 先通过 Search 找到候选 URL，再用 Extract 读取需要引用的页面。Search 和 Extract 都支持相关片段、用量信息和请求编号；Extract 在传入 `query` 时可以返回相关片段。项目据此实现 `web_search` 与 `web_fetch` 两个独立工具。[Tavily Search 最佳实践](https://docs.tavily.com/documentation/best-practices/best-practices-search) [Tavily Extract API](https://docs.tavily.com/documentation/api-reference/endpoint/extract)

LangChain 的 `ModelRequest.override(tools=...)` 只改变当前模型调用能看到的工具。`create_agent` 的 `ToolNode` 仍持有创建 Agent 时注册的全部工具，因此模型若返回一个本轮没有提供、但全局注册过的工具名，执行节点仍可能找到并运行它。官方提供的执行层控制入口是 `wrap_tool_call` / `awrap_tool_call`。[LangChain 自定义中间件](https://docs.langchain.com/oss/python/langchain/middleware/custom)

DeepSeek 的 Anthropic 兼容接口明确支持 `tool_choice` 的 `none`、`auto`、`any` 和指定工具形式。当前 `langchain-anthropic` 版本需要传入 `{"type": "none"}`，不能使用字符串 `"none"`。[DeepSeek Anthropic API 兼容说明](https://api-docs.deepseek.com/guides/anthropic_api/)

## 基于真实运行得到的证据

Tavily 文档把单个相关片段描述为最多 500 字符，但真实 Search 响应出现过 717 至 793 字符的最终 `content`。官方 OpenAPI 也没有给整个 `raw_content` 声明 `maxLength`。因此，不能把文档中的单片段数字直接当成最终响应的严格长度保证。

一轮完整评测中，Extract 曾返回超过项目 10000 字符上限的内容。当前代码按约定保存失败并终止 AgentRun，没有截断、重试或换供应商。这个结果说明 10000 是项目自己的输入边界，不是 Tavily 的官方保证；后续是否调整，需要基于更多真实样本和模型输入预算决定。

DeepSeek 在一次真实运行中生成了本轮已经隐藏的 `web_search` 调用。检查 LangChain 源码后确认，仅缩小模型可见工具不足以构成执行权限。当前实现已同时在执行前检查轮次：最后一轮重新搜索会保存失败记录，并且不会调用 Tavily；工具轮数用完后会显式发送 `tool_choice: none`。

另一次并行工具运行中，WSL 与 Docker 的 UTC 墙上时钟发生约 1.4 秒的反向校正，造成结束时间早于开始时间。工具持续时间现改为由进程单调时钟计算，再从真实开始时间推导结束时间，避免系统校时制造负耗时。

## 当前项目实现

`web_search` 固定使用 `advanced`、`general`、最多五个结果和每来源一个片段；`web_fetch` 一次读取一个来自用户或本轮搜索结果的原始 URL，固定使用 `advanced`、Markdown 和最多三个相关片段。搜索与读取均不重试，超时、空结果、结构错误和超过项目长度边界都会明确失败。

工具参数、模型实际看到的规范化结果、状态、顺序和 Tavily 请求元数据保存在 `agent_tool_calls`。供应商原始响应不入库。回答只能引用本轮成功读取的页面，来源快照随助手消息保存；历史来源编号在下一轮失效，需要重新读取后才能再次引用。

模型内部推理、工具参数和网页正文不会发给 App。App 只接收安全的阶段状态、逐字回答以及后端确认过的来源，正文中的 `[S#]` 和下方来源卡片都由同一份保存结果驱动。

## 首轮真实评测

评测包含六个低血糖与 FoH 场景、一个隐私探针和一个恶意网页提示注入探针。前七个场景真实经过 HTTP、DeepSeek、Tavily 和 PostgreSQL；提示注入探针使用真实 DeepSeek 与内存中的恶意网页，避免访问真实恶意站点。

不同完整运行曾得到 7/8 和 5/8，说明当前链路还不能用一次偶然全过来证明稳定性。5/8 的一次运行中，一项聊天实际完成但被负耗时检查判失败；一项因 Extract 超过项目长度上限而明确失败；一项因模型在最后一轮重新搜索并引用未读取页面而被引用校验拦下。前两处代码边界已经按上文修正或保留，隐藏工具的执行权限缺口也已补齐。

自动检查覆盖工具顺序、轮数与并行上限、数据库状态、流式结果、引用对应关系、隐私信息不进入工具参数，以及恶意网页不能改变 Agent 行为。来源权威性、回答对原文的忠实程度和医疗建议正确性仍需人工与专业评测；当前报告明确标记 `clinical_validation_claimed=false`。

## 尚未决定的方向

当前仍使用 Core Agent 自主决定搜索、读页和回答的 ReAct 循环。真实评测已经证明模型可能违反工具顺序，即使系统提示词和模型可见工具都已限制。候选方向是把联网研究改成更固定的“搜索、选择并读取、组织回答”工作流；它会降低自由度、增加确定性，也会改变当前 Agent 的做事方式，因此需要单独讨论后再实现。
