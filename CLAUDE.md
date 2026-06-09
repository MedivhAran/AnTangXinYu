# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. 沟通风格（中文交流）

目标是把事情讲清楚，怎么清楚怎么来。要避免的是另一种毛病：把一堆自造的技术词和代码里的名字塞进短句里，还不解释。

- 不要用"短语 + 短语 + 短语"或"短语 → 短语"这种电报式堆叠。看上去精炼，但每个短语到底什么意思都得读者自己脑补，很费劲。
- 出现英文术语或代码里的名字（比如 notification、dialog、SSE）时，用中文说清楚它指什么、在这里起什么作用，别甩一个英文名词就过去。
- 不要造生硬的、机器味的词，用平实的中文。
- 结构可以简洁，表格和分点都行；但每一项内部要把意思说完整，不能为了短牺牲清晰。

**反面例子**（这两种都不行）：
> 护栏：静默时段（别半夜推）+ 每人频率上限 + 触发后冷却
>
> 事件 vs 纯定时：建议混合——事件即时记账，心跳 tick 只做 drift + 判阈值，能扛多用户

**改成把每一项讲完整**（仍可用分点，只是每点说清楚）：
> 防打扰要加三条限制：①设一个静默时间段，比如夜里不主动发消息；②每个用户每天最多收到几条主动关怀；③刚关怀过的用户给一段冷却时间，避免短时间内反复打扰。
>
> 状态更新建议"事件触发 + 定时检查"结合：用户每聊完一轮、或有新的血糖数据存进来时，就立刻更新他的几个状态值（这叫事件触发，开销很小）；后台那个定时循环只做两件轻活——让状态值随时间慢慢回落、并检查有没有超过阈值。如果把所有计算都堆到定时循环里会很重，两者结合才扛得住很多用户。

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.