# 安糖心语项目协作约定

## 项目定位

- 本项目从零开始设计和实现。旧项目 `../AgentChat` 仅作为业务、历史决策和问题案例的参考。
- 新项目使用全新的数据库与初始化流程，旧数据库保留在历史项目中。
- 产品面向 C 端糖尿病患者，核心目标人群是 1 型糖尿病患者，核心领域是低血糖恐惧（FoH）陪伴。
- 产品形态是一位长期陪伴用户的 Agent。每位用户拥有一个持续存在的聊天窗口，历史消息在同一窗口中延续。
- Android 为首个移动平台，iOS 放在后续阶段。
- 最终部署方式为 Docker。

## 首版产品范围

### 保留

- App 启动后直接进入聊天页。
- 健康档案和历史对话从侧边栏进入。
- 报告、照片和文件从聊天页快捷入口发送。
- 联网搜索和天气查询。
- CGM 原始报告、解析结果和历次分析长期保存，并保留上传时间和相互关联。
- 用户自然对话中出现的健康信息由健康档案管理流程识别和处理。
- 饮食、运动和健康管理问题通过对话及相应的专业工作流处理。
- 主动陪伴由主动陪伴 Sub-agent 负责，触发依据和执行规则在对应模块开始前调研。

### 排除

- 新建会话和清空当前会话。
- 用户通过对话设置定时提醒，以及独立提醒系统。
- 发送消息时单独填写当前血糖值和趋势。
- 独立的饮食建议、运动建议和食物营养查询页面。
- 回答点赞和点踩。
- 图片生成。

## 已确定技术栈

- 首版运行时使用 Python 与 TypeScript。
- 后端使用 FastAPI、SQLAlchemy、Alembic、LangGraph 和 LangChain。
- 后端应用日志使用 Loguru。日志只记录运行元数据，聊天正文、健康信息、提示词、密码和认证凭证不得进入日志。
- Android App 使用 React Native、TypeScript 与 Expo；当前脚手架采用 Expo SDK 57 官方 `blank-typescript` 模板。
- 主数据库使用 PostgreSQL。
- 模型使用 DeepSeek V4 Pro，通过 DeepSeek 官方 Anthropic 兼容接口和 `langchain-anthropic` 接入。
- Agent Harness 使用 LangGraph；单个 Agent 的模型与工具循环使用 LangChain `create_agent`。
- 联网搜索使用 Tavily Search 与 Extract API，通过官方异步 Python SDK 接入。
- LangGraph PostgreSQL Checkpointer 保存单次 AgentRun 的流程状态，为后续暂停、恢复和故障续跑提供基础。
- 当前 Core Agent 支持最多十轮工具调用，每轮最多五个并行只读工具。首版使用单 API 进程，服务重启时将遗留的运行和工具调用明确标记为失败；复杂工作流的自动续跑在对应模块设计。
- PostgreSQL 业务表负责用户、原始聊天消息、摘要快照、工具调用、健康档案和可审计运行记录。
- LightRAG 优先评估 PostgreSQL 一体化后台：KV、文档状态、pgvector 向量和 Apache AGE 图存储。
- Qdrant 已从当前技术方案中移除。
- Docker 负责 Python 后端、PostgreSQL 和其他服务；Android App 通过 Expo 开发构建并生成 APK/AAB。

## 长期记忆与健康档案

- 长期信息分为“健康档案”和“陪伴记忆”。
- 健康档案是权威记录，使用 PostgreSQL 持久化。
- 记忆管理 Agent 识别健康档案候选变更；应用程序负责验证、授权、写入和审计。
- 陪伴记忆优先评估使用 Hindsight，内容包括用户经历、偏好、恐惧触发因素和有效的陪伴方式。
- 各 Agent 按职责获取经过筛选的健康档案和陪伴记忆，访问控制由应用程序执行。
- 用户长期档案概念上分为三类：基础个人档案、健康与低血糖档案、FoH 状态与应对模式。
- 基础个人信息和明确的健康事实发生变化时由用户确认。
- 基于可验证数据计算的血糖规律可以自动更新，同时保留数据依据和统计时间范围。
- FoH 状态与应对模式可以自动演化；用户拥有查看、纠正和删除能力。明显的心理判断或影响干预方向的推论需要在对话中轻量核对。
- CGM 中提取的明确健康事实和具有统计依据的血糖规律进入健康档案，并保留来源报告与分析时间范围。
- 三类档案的详细内容需要依据 FoH 量表、糖尿病心理照护和干预研究单独设计。

## 上下文管理架构

- PostgreSQL 永久保存全部原始聊天消息，原始消息是审计和重新处理的依据。
- PostgreSQL 保存不可变的对话摘要快照。每份摘要记录覆盖到的消息、上一份摘要、模型、提示词版本和 token usage。
- 模型输入由稳定规则、健康档案、相关陪伴记忆、较早对话摘要、相关历史原文、近期原始消息、当前有效工具结果和最新用户消息组成。
- 对话摘要只负责交流连续性。健康档案更新和 Hindsight 记忆处理读取原始消息并走各自流程。
- 摘要由一次独立的 LLM 调用生成，工具保持关闭；摘要失败会让当前运行明确失败。
- 输入 token 使用 DeepSeek Anthropic `count_tokens` 接口按真实请求格式精确计算，系统提示词和工具定义都计入预算。
- 首版自动压缩触发值为 `150000` 输入 tokens，压缩后至少保留最近 `20` 条原始消息。两项均为可配置的评测起点，真实长对话评测决定后续调整。
- 摘要边界落在完整对话之后，近期原文从用户消息开始。
- 压缩完成后重新组装上下文并再次精确计数；上下文仍达到限制时直接报告错误。
- 工具调用和模型实际看到的规范化结果保存在 `agent_tool_calls`。输入达到 `100000` tokens 时，模型上下文只保留最近 `3` 个真实工具结果，较早结果临时替换为占位符；数据库原文保持不变。工具结果清理先于对话压缩。
- Tavily 的请求编号、响应耗时和额度单独保存在工具记录中，不进入模型上下文。联网回答的来源快照随助手消息保存，历史来源编号不会在新一轮继续生效。
- 用户界面保持一个连续会话，服务器自动完成上下文压缩。

## Agent 设计

- Agent 职责分为三类：Core Agent、Sub-agents 和 Auditor Agents。
- Core Agent 直接与用户交流、调用所需工作流并组织最终回复。
- Sub-agent 的拆分依据是独立的专业上下文、工具权限、执行过程或评价标准。
- 确定性的数据读写、计算、检索和格式转换优先实现为普通代码或工具。
- 当前 Sub-agent 候选为主动陪伴、记忆管理、健康管理推理、FoH 评估与干预。
- Sub-agent 的最终数量和职责由公开医疗 Agent、成熟 Agent 框架、专业方法和项目评测共同决定。
- Auditor Agents 负责实时安全审核；具体审核位置、放弃自动回答机制和医疗安全规则需要专项调研。

## 聊天运行协议

- Android 使用 `POST /api/v1/chat/messages` 发送消息。后端通过 NDJSON 返回 `message_started`、零个或多个 `agent_activity` 与 `text_delta`，最后返回一个完成或失败事件。
- 模型每一轮产生的普通文字都属于用户可见内容，包括工具调用前的简短说明；不同模型轮的可见文字用一个空行连接，并作为一条助手消息持久化。
- `thinking/reasoning` 正文、工具参数和工具原始结果不得发送给 App。`agent_activity` 只使用应用定义的安全阶段：思考、搜索、阅读和整理。
- Android 使用 `GET /api/v1/chat/messages` 按 UUIDv7 游标读取历史消息；接口返回的消息按时间升序排列。
- 手机为每次发送生成 `client_message_id`。同一次网络提交始终复用该 ID；重复提交返回稳定的 `duplicate_client_message` 冲突代码，App 随后刷新历史。
- 每位用户同时只允许一个顶层 AgentRun。上一条仍在运行时，后端返回稳定的 `active_agent_run` 冲突代码，App 禁用发送入口。
- 客户端断开响应流时，后端取消本次运行，保存已经发送的部分文字，并将消息和 AgentRun 标记为 `cancelled`。
- 历史接口保留并返回 `generating`、`completed`、`failed` 和 `cancelled` 状态；失败与取消消息的部分文字继续可见。
- 首版 Docker 部署运行单个 API 进程。服务启动时将上次进程遗留的 `running` AgentRun 和对应 `generating` 消息明确标记为失败。
- Core Agent 的最后一轮工具调用只允许读取已经找到的网页，不能重新搜索；工具轮数用完后通过 DeepSeek Anthropic 的 `tool_choice: none` 明确关闭工具。模型请求和工具实际执行两层都检查权限，违规调用保存失败并终止当前运行。

## 协作方式

- 沟通保持自然、直接、随性，优先使用清楚的日常语言。
- 技术术语只在有助于当前讨论时使用，并立即解释其实际含义。
- 产品完成优先于教学。遇到当前开发必需的新概念时进行简短说明。
- 进入一批实现前，用户与 Codex 先确认范围、关键行为和验收条件。
- 范围确认后，Codex 直接完成该批业务代码、测试、Alembic、运行配置和文档，并连续推进到验收条件满足或出现需要用户决定的问题。
- 用户负责产品决策与最终体验验收，也可以随时指定某段代码由自己手写。
- Codex 负责运行静态检查、自动化测试、数据库升级和真实链路验证，并直接处理其中发现的问题。
- 实现过程中发现会改变已确认产品行为的新问题时，Codex 暂停对应决定，先与用户讨论。
- 代码加入适量注释或 docstring，重点解释职责和容易误解的原因。

## 开发环境执行规则

- 项目在 WSL Ubuntu 中的唯一规范路径是 `/home/medivh/AnTang`。
- Codex 桌面端保留当前任务和完整对话；所有项目命令通过 `wsl.exe -d Ubuntu -- bash -lc` 进入 Ubuntu 执行。
- Python、`uv`、Alembic、Node.js、npm、Expo、Git 和 Docker 命令均在 Ubuntu 内运行。
- Windows 负责承载 Codex 界面和调用 `wsl.exe`，不得直接运行项目虚拟环境或 `node_modules` 中的程序。
- 文件链接和工具工作区可以使用 `\\wsl.localhost\Ubuntu\home\medivh\AnTang`，代码与脚本中的项目路径使用 Linux 路径。

## 调研与设计规则

- 项目先确认一层精简的整体骨架，再按模块迭代。
- 每个模块进入实现前，先查看成熟项目、官方文档、论文或源码，共同确认适合本项目的方案，然后编码和验证。
- Agent 架构、技术栈和基础设施决策需要参考优秀开源 Agent 框架、Claude Code、Hermes Agent、公开医疗 Agent、官方文档和源码。
- 调研内容明确区分三类来源：官方设计、基于证据的推论、自定义候选方案。
- 自定义建议需要说明来源、收益、代价和适用条件。
- 当前讨论停留在用户提出的层级，具体实现细节在进入对应模块时展开。
- `AGENTS.md` 只保存长期有效的事实和规则；详细调研、实验记录和方案比较进入 `docs/research/`。
- 代码追求精简、直观、可测试，减少过度抽象和无实际需求的防御逻辑。
- 错误尽早暴露。禁止静默吞错、自动降级、静态兜底、启发式补丁和失败后继续执行。

## 当前开发阶段

- 首条端到端链路是：Android 发送消息，Python 后端调用真实模型并流式返回，PostgreSQL 保存消息，App 重新打开后继续同一会话。
- Android Expo 项目已经创建并在真机运行。
- 后端已经完成用户认证基础、聊天消息与 AgentRun 数据模型、Core Agent、PostgreSQL Checkpointer、上下文读取、精确 token 计算、对话摘要快照、自动压缩阈值、压缩后重建和二次计数。
- 首条后端链路已经完成：认证、上下文准备、DeepSeek 流式执行、消息与 AgentRun 终态、历史分页、Loguru 和启动恢复均已接通。
- Android 已经完成登录注册、SecureStore 凭证、一次 refresh、历史加载、NDJSON 流式聊天、失败取消状态、手动重试和 Agent 活动渐变状态栏。
- Docker Compose 已经接入 PostgreSQL 与单进程 API，容器会先执行 Alembic 再启动服务。
- Core Agent 已接入 Tavily `web_search` 和 `web_fetch`、工具执行审计、跨轮工具上下文、旧工具结果清理、工具循环硬上限和面向 App 的结构化活动事件。
- 联网来源已经随消息持久化，Android 可以显示正文来源标记和完整 URL 来源卡片。
- 自动化检查已经通过；真实 DeepSeek、Tavily、PostgreSQL 评测暴露的模型工具顺序与网页长度问题记录在 `docs/research/web-search-evaluation.md`。下一步是在新 development build 上完成 Android 真机验收，并单独讨论是否把联网研究改为固定工作流。

## 待研究事项

- 产品完整功能清单与分期范围。
- Sub-agent 的必要性、职责和引入标准。
- Auditor Agents、医疗安全审核和放弃自动回答机制。
- Tavily 搜索来源质量、引用正确性和医疗场景提示注入评测；天气工具的来源和实现。
- 缓存、对象存储和可观测性技术栈。
- 历史原始消息的按需检索方案。
- LightRAG PostgreSQL 后台的检索效果与 Apache AGE 性能。
- Hindsight 对陪伴记忆提取、更新、遗忘和召回的效果。
- 三类长期档案的专业内容和产品交互。
- Android 发布方式。

## 现有调研资料

- `docs/research/sub-agent-candidates.md`：Sub-agent 候选的第一轮调研。
- `docs/research/legacy-project-inventory.md`：旧项目产品功能、Agent 链路、基础设施和问题盘点。
- `docs/research/agent-activity-streaming.md`：Agent 活动事件与 App 渐变状态栏的调研和决定。
- `docs/research/web-search-evaluation.md`：Tavily 工具、引用链路和首轮真实评测记录。
- `docs/research/amazfit-integration.md`：旧 Amazfit 原型、Zepp 官方能力、Health Connect、Google Health API 和替代路线调研。
