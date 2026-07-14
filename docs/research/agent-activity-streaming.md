# Agent 活动状态与可见文字流

## 调研结论

LangChain `create_agent` 是标准 ReAct 循环：模型消息包含工具调用时执行工具并再次调用模型，直到最后一条模型消息不再包含工具调用。LangGraph 的消息流会返回循环中每一次模型调用的增量，不只返回最后一轮；工具执行和整个运行则有独立的生命周期事件。

DeepSeek V4 的官方工具调用示例明确允许一条模型消息同时包含普通 `content` 和 `tool_calls`。工具轮的 `thinking/reasoning` 必须保留在内部消息链并传回后续模型请求，但不需要展示给用户。

OpenAI Codex、Claude Agent SDK 和 LangGraph 的公开实现都把运行过程、工具活动与用户最终看到的文字分成不同类型的事件。状态显示来自真实运行事件，而不是从模型生成的自然语言中猜测当前状态。

官方资料：

- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [DeepSeek Anthropic API](https://api-docs.deepseek.com/guides/anthropic_api/)
- [LangChain Agent Streaming](https://docs.langchain.com/oss/python/langchain/streaming)
- [LangGraph Event Streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming)
- [Codex App Server 事件协议](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md#turn-events)
- [Claude Agent SDK Streaming](https://code.claude.com/docs/en/agent-sdk/streaming-output)

## 当前项目实验

使用项目当前的 DeepSeek V4 Pro、Anthropic 兼容接口、`ChatAnthropic` 和 `create_agent` 进行合成请求验证时，连续两个工具轮都同时产生了 `thinking`、普通文字和工具调用，最后一轮才只产生最终回答。由此确认，禁止“普通文字与工具调用共存”不符合当前模型的真实协议。

## 项目决定

- 保留标准 `create_agent` ReAct 循环，不增加单独的最终回答模型调用。
- 每个模型轮次的普通文字都逐字发送给用户；工具调用前的简短说明也是对话的一部分。
- `thinking/reasoning` 正文只留在 Agent 内部状态，不进入 App、应用日志或业务聊天消息。
- 工具中间件发送真实的开始、完成、失败和取消事件；聊天出口只映射成 `thinking`、`searching`、`reading`、`organizing` 四种安全阶段。
- 一次 AgentRun 对应一条用户可见助手消息。多个模型轮次的可见文字用 `\n\n` 连接，数据库内容必须等于手机收到的全部 `text_delta` 拼接结果。
- PostgreSQL 的工具记录保存调用参数和规范化结果；下一轮上下文按“工具链在前、复合助手消息在后”重建。单次运行的精确原始循环继续由 LangGraph Checkpointer 保存，本轮不新增模型轮业务表。

## 已知边界

真正逐 token 展示与严格的输出后审存在冲突：流式文字一旦到达手机，后置 Auditor 无法保证在用户看到之前拦截。医疗安全模块开始设计时，需要在低延迟与输出前审核之间单独作出决定；本轮不提前实现审核策略。
