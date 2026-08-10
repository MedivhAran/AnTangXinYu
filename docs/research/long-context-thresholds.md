# 长上下文清理与摘要阈值调研

更新日期：2026-07-20。本文只回答三个问题：“Claude 官方是否建议只使用约 30% 的上下文”“长上下文论文究竟证明了什么”“这些证据怎样影响当前 100k 工具结果清理和 150k 对话摘要阈值”。

## 结论

当前能查到的 Anthropic 官方材料没有“上下文使用到 30% 就必须压缩”这条规则。Anthropic 对外表达的是：上下文越长，准确率和召回通常会逐渐下降，但这是因模型和任务而异的渐变，不是一个统一的硬断点。Claude 的不同产品甚至采用了明显不同的默认值：Claude Code 默认约到容量的 95% 才自动压缩；Claude Messages API 的服务端压缩默认在 150,000 输入 token 触发；旧的客户端 SDK 压缩默认是 100,000 token。这些数字只能说明各产品自己的工程默认值，不能证明“最佳比例”是 30%、50% 或 75%。[Anthropic 上下文工程文章](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、[Claude Code 上下文管理](https://code.claude.com/docs/en/claude-code-on-the-web)、[Claude Messages API Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)、[Claude SDK Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)

论文同样不支持一个通用的 30% 截止线。它们支持的是更有限、也更可靠的结论：模型能接收某个长度，不等于能稳定利用这个长度；相关信息的位置、任务难度、干扰内容和模型本身都会改变退化曲线。因此阈值必须针对“具体模型＋具体输入组织方式＋具体业务任务”评测。

对本项目最直接的新证据来自 DeepSeek V4 自己的技术报告，而不是 Claude。DeepSeek 官方目前给 `deepseek-v4-pro` 标注 1M 上下文。它在 MRCR 8-needle 检索评测中称 V4-Pro-Max 在 128K 以内较稳定，超过 128K 后开始出现可见退化。当前 100k 清理阈值处于这个区间以内，方向合理；150k 摘要阈值已经略过 128K。它不算离谱，但也不是已经被证明的最优值。如果目标是尽量不进入 DeepSeek 自己报告的退化区，候选触发点应该放在 128K 之前并预留本轮输出和新工具结果空间，而不是照搬所谓“30%”。对 1M 窗口来说，30% 是 300K，反而比当前 150K 晚很多。[DeepSeek V4 技术报告](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf)

## “Claude 只用 30%”到底有没有官方来源

### 官方事实

Anthropic 的上下文工程文章明确承认 context rot：随着 token 增多，模型从上下文中准确召回信息的能力会下降；不同模型下降速度不同，而且表现为渐变而不是统一的断崖。文章给出的原则是尽量保留最少但高价值的 token，没有给出 30% 这个数字。[Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

Anthropic 当前公开的实际默认值也不支持“官方统一推荐 30%”：

- Claude Code 的官方文档写明，默认自动压缩大约在上下文容量的 95% 触发，也允许用户自行提前触发。这是 Claude Code 的产品行为，不是模型质量保证。[Claude Code 官方文档](https://code.claude.com/docs/en/claude-code-on-the-web)
- Claude Messages API 的服务端压缩把 `150000` 作为默认输入 token 触发值，允许的最低值是 50,000。文档没有声称 150K 是研究得到的质量最优点。[Claude Messages API Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- Anthropic 的工具结果清理默认在 100,000 输入 token 触发，并保留最近 3 次工具调用；旧的客户端 SDK 摘要也默认在 100,000 触发。SDK 示例把 50K 作为更早压缩、150K 作为需要更多原文时的较晚压缩示例，但没有把 50K–150K 定义成适用于所有模型和任务的最佳范围。[Claude Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)

### 基于证据的判断

用户记得的“30%”很可能来自 LongMemEval，而不是 Claude 的窗口使用规则。LongMemEval-S 给每道题约 115K token 的多轮聊天历史；论文报告，直接让长上下文模型读取完整历史，相对只提供证据会话的 oracle 条件，准确率下降约 30%–60%。这里的 30% 是准确率损失，不是“上下文占用达到 30%”。它说明把整段长期历史直接塞给模型效果不好，支持检索相关记忆，但没有定义压缩阈值。[LongMemEval 原始论文](https://openreview.net/pdf?id=wIonk5yTDq)、[官方代码与数据说明](https://github.com/xiaowu0162/longmemeval)

另一个常见混淆是把“准确率下降约 20%–30%”说成“用到窗口 20%–30% 就必须压缩”。前者是某个模型在某个任务上的结果差，后者是通用容量规则，两者并不等价。

## 原始论文究竟发现了什么

### Lost in the Middle

这篇论文改变相关信息在输入中的位置，发现多种模型通常在信息位于开头或结尾时表现较好，位于中间时明显变差。GPT-3.5-Turbo 在多文档问答中的最坏位置相对最好位置可下降超过 20%；在开放域问答中，把检索文档从 20 个增加到 50 个，只给 GPT-3.5-Turbo 带来约 1.5%、给 Claude 1.3 带来约 1% 的提升。论文本身明确说“更多上下文是否值得”取决于下游任务。[Lost in the Middle 原始论文](https://aclanthology.org/2024.tacl-1.9/)

这说明无关或低价值内容会稀释有效信息，也说明不能只看窗口能否容纳。但论文测试的是早期模型和较小窗口，不能从中推导 DeepSeek V4 应在 30%、50% 或某个固定比例压缩。

### RULER

RULER 用 13 类检索、多跳追踪、聚合和问答任务评测 17 个长上下文模型。模型在最简单的单针检索上接近满分，但长度和任务复杂度上升后大多明显退化；虽然这些模型都宣称至少支持 32K，上到 32K 时只有约一半仍能维持论文设定的满意表现。[RULER 原始论文](https://arxiv.org/abs/2404.06654)

这证明“标称窗口”和“有效窗口”不是一回事，但也同时证明有效长度取决于模型和任务，并没有共同的百分比。

### NoLiMa

NoLiMa 刻意去掉问题与答案证据之间的字面重合，要求模型先理解关联再找到证据。在最终发表版本中，13 个被测模型都宣称至少支持 128K，但到 32K 时有 11 个模型的表现已经低于自身短上下文基线的一半；GPT-4o 从短上下文的 99.3% 降到 69.7%。[NoLiMa 原始论文](https://proceedings.mlr.press/v267/modarressi25a.html)

32K 对 128K 窗口恰好约为 25%，这可能是“只用约 30%”说法的来源之一。但论文同时包含不同标称窗口的模型，测试的是无字面匹配的关联检索，而且结果差异很大，所以它不能被改写成所有模型通用的 30% 规则。

### 即使检索正确，长度本身也可能伤害推理

EMNLP 2025 的控制实验把相关证据完整保留下来，只增加无关 token。Llama-3.1-8B 在扩展到 30K 的 1,000 道 MMLU 题中，有 970 道仍能逐 token 正确复述证据，但答题准确率相对短输入下降了 24.2%；多个任务的大量损失在 7K 内已经发生，远低于模型标称的 128K。论文因此提出先从长上下文取出证据，再用短输入完成推理。[Context Length Alone Hurts LLM Performance Despite Perfect Retrieval](https://aclanthology.org/2025.findings-emnlp.1264/)

这项结果进一步否定了“只要没到窗口上限就没问题”，但被测模型、任务和输入构造仍与本项目不同，不能直接给本项目算出一个固定阈值。

## DeepSeek V4 官方能确认什么

### 官方 API 事实

截至 2026-07-20，DeepSeek 官方价格页对 `deepseek-v4-pro` 标注：上下文长度 1M，最大输出 384K，同时支持 OpenAI Chat Completions 和 Anthropic 格式端点；Anthropic 格式的 base URL 是 `https://api.deepseek.com/anthropic`。价格按每 100 万 token 计算：缓存命中输入 0.003625 美元、缓存未命中输入 0.435 美元、输出 0.87 美元。[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)、[DeepSeek Anthropic API](https://api-docs.deepseek.com/guides/anthropic_api/)

DeepSeek 的 Chat Completions 官方参考明确说明，输入 token 与生成 token 的总和受 1M 上下文长度限制，因此 384K 最大输出不是在 1M 输入之外额外增加的空间。[Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion) Anthropic 兼容文档还明确说 `anthropic-beta` 请求头会被忽略，因此不能因为接口格式兼容，就假定 Anthropic 的服务端 compaction beta 也能在 DeepSeek 端使用。[DeepSeek Anthropic API compatibility](https://api-docs.deepseek.com/guides/anthropic_api/)

DeepSeek 当前官方 Anthropic 兼容说明没有明确列出 `/v1/messages/count_tokens` 端点。官方 token 文档提供的是离线 tokenizer 示例，并以实际响应中的 usage 为最终计费依据。因此，项目现有 `count_tokens` 调用即使已经实测可用，也应被描述为实测兼容行为，不能仅凭现有官方文档当成已承诺的接口契约。[DeepSeek Token & Token Usage](https://api-docs.deepseek.com/quick_start/token_usage)

### DeepSeek 自己的长上下文证据

DeepSeek V4 技术报告称，V4-Pro-Max 在 MRCR 8-needle 检索任务上于 128K 以内较稳定，超过 128K 后出现可见退化；图中 128K、256K、512K 和 1024K 的平均 MMR 依次约为 0.92、0.82、0.66 和 0.59。报告也给出 1M 条件下的 MRCR 和 CorpusQA 结果，说明模型在极长输入下仍有能力，但没有证明它在 1M 内保持短上下文同等质量。这个结果是模型开发方自报的基准，并且稳定区间曲线针对 Max 推理档位和检索任务，不是中文医疗多轮对话的质量保证。[DeepSeek V4 技术报告，第 38–39 页](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf)

## 对当前 100k 和 150k 的影响

### 现有方案

当前流程在 100K 时先移除较早的大型工具结果，在 150K 时才把早期对话压成摘要。先删除低价值、可重新获取的网页和工具正文，再对不可逆的信息压缩动手，这个顺序本身与 Anthropic 的“保留最少但高价值上下文”原则和 Claude Code“先清旧工具结果、再总结历史”的官方设计一致。[Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、[How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)

### 基于现有证据的判断

100K 等于 DeepSeek V4-Pro 标称 1M 窗口的 10%。这个阈值清的是旧工具结果，不是直接丢弃对话，并且位于 DeepSeek 自报的 128K 稳定区间内，作为提前减噪点是有依据的。当前“100K 触发、保留最近 3 个工具结果”也恰好与 Anthropic 官方工具清理默认值一致，但这是可参考的工程先例，不是 DeepSeek 的质量证明。它是否应更早，还取决于工具结果在总上下文中占多少；单看总 token 仍然比较粗糙。

150K 等于标称窗口的 15%，不是 30%。它与 Anthropic 当前服务端 compaction 的默认绝对值恰好相同，但项目使用的是 DeepSeek，自行组织的医疗陪伴上下文也不同，所以这只能算巧合，不能作为原始设计依据。DeepSeek 自己报告超过 128K 后开始退化，因此 150K 处在退化开始之后。它仍远离 1M 的硬上限，容量上很安全；真正不确定的是回答质量，而不是能不能发送请求。

### 自定义候选建议

如果现在只根据公开证据给一个更稳妥的候选，保留 100K 的工具结果清理是合理的；对话摘要可以先考虑在清理工具结果后仍接近 120K 时触发。120K 不是论文给出的标准答案，而是根据 DeepSeek 自报的 128K 稳定边界留少量余量，避免新工具结果和本轮生成把请求推过边界。收益是少进入已观察到退化的区间，代价是更早承担摘要遗漏信息的风险。

最终值不能靠另一篇论文替项目拍板。应该用 DeepSeek V4-Pro 的实际运行档位，构造本项目中文多轮对话，在例如 64K、96K、120K、128K、160K、256K 几个长度上测试：近期指令遵循、健康档案与当前说法冲突、跨轮指代、工具选择、医学事实保持和安全回复。然后比较“不压缩”“只清工具结果”“摘要后保留近期原文”三种方式。阈值应取业务质量开始明显下降之前的位置，而不是取标称窗口的固定百分比。

## 可以对外怎样准确表述

当前的 100K 和 150K 最初是配置化起点，没有实验依据就应直接承认是拍脑门的初始值。现在补查资料后，可以说它们获得了一部分事后证据支持：DeepSeek V4 官方报告把 128K 以内描述为较稳定，100K 清工具结果与此相符；150K 略过该边界，值得用真实长对话重新评测。不能说 Anthropic 或论文规定只能使用 30% 上下文，也不能说 150K 已经被证明是最佳值。
