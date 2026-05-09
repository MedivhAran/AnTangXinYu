# 安糖心语 项目架构说明

本文基于当前仓库代码做静态梳理，帮助开发者快速理解项目的运行结构、模块边界和主要数据流，也可以作为给 AI 看的说明文档，帮助AI快速理解项目架构。

## 1. 项目定位

这是一个前后端分离的中文智能体项目，产品形态是“安糖心语”：面向 T1D/低血糖恐惧情绪演示场景的陪伴型 Agent。

整体分两层：

```text
安糖心语产品层（AnTangAgent + 安糖会话 UI + 安全/血糖/图片/知识库等专属能力）
  运行在
通用 Agent 底座（Agent / Tool / LLM / MCP / Skill / Memory / RAG / Storage）
  之上
```

用户侧产品入口只有安糖会话界面；Agent、工具、MCP Server、模型等底座能力通过管理页面暴露给开发者。知识库不作为用户能力出现，只作为安糖 Agent 的内置系统资料源。

## 2. 技术栈

后端：

- FastAPI：HTTP API、SSE 流式对话、MCP Proxy。
- Python 3.12+。
- SQLModel / SQLAlchemy：数据库模型与 DAO。
- MySQL：用户、Agent、会话、历史、工具、MCP、知识库元数据等关系数据。
- Redis：MCP Proxy session 与运行时辅助状态。
- LangChain / LangGraph：Agent 执行、ReAct 工具调用、中间件事件流。
- ChromaDB：当前 Docker 配置下的默认 RAG 向量库。
- MinIO / OSS：上传文件、头像、PDF 转换中间产物等对象存储。

前端：

- Vue 3 + Vite。
- TypeScript。
- Pinia：用户状态、聊天历史、会话列表等状态管理。
- Element Plus：基础 UI 组件。
- `@microsoft/fetch-event-source`：SSE 流式对话。
- `md-editor-v3`：渲染模型 Markdown 回复。

部署：

- Docker / Docker Compose。
- 默认启动 `mysql`、`redis`、`minio`、`backend`、`frontend`。

## 3. 仓库层次

```text
AgentChat/
├── ARCHITECTURE.md                  # 当前架构说明
├── AGENTS.md                        # 本项目给 coding agent 的协作说明
├── data/
│   ├── antang_knowledge_pdfs/        # 安糖系统内置 PDF 资料目录，容器只读挂载
│   └── vector_db/                    # Chroma 持久化目录，容器读写挂载
├── docker/
│   ├── Dockerfile                    # 后端镜像
│   ├── Dockerfile.frontend           # 前端镜像
│   ├── docker-compose.yml            # 本地完整服务编排
│   └── docker_config.yaml            # 容器内后端配置，构建时复制为 AnTang/config.yaml
├── docs/
│   └── development/                  # 开发说明，含安糖知识库说明
├── scripts/                          # 本地辅助脚本
├── src/backend/
│   ├── pyproject.toml
│   └── AnTang/
│       ├── main.py                   # FastAPI app / lifespan / middleware
│       ├── settings.py               # YAML 配置加载
│       ├── api/                      # HTTP 路由、响应封装、业务 service
│       ├── auth/                     # JWT 鉴权
│       ├── core/                     # Agent 运行核心、模型管理、回调
│       ├── database/                 # SQLModel 模型、DAO、初始化逻辑
│       ├── mcp_proxy/                # OpenAPI 转 MCP 与 MCP 代理协议实现
│       ├── mcp_servers/              # 内置/示例 MCP Server
│       ├── middleware/               # TraceID、白名单
│       ├── prompts/                  # 通用 prompt
│       ├── schemas/                  # Pydantic schema
│       ├── scripts/                  # 后端可执行脚本
│       ├── services/                 # RAG、安糖、MCP、Memory、Storage、Sandbox 等服务
│       └── tools/                    # 内置工具与 OpenAPI 工具适配
└── src/frontend/
    ├── package.json
    └── src/
        ├── apis/                     # REST / SSE API 封装
        ├── assets/                   # 静态资源
        ├── components/               # 通用组件
        ├── pages/                    # 页面
        ├── router/                   # Vue Router
        ├── store/                    # Pinia Store
        ├── type.ts                   # 前端类型定义
        └── utils/                    # 请求封装与工具函数
```

## 4. 运行时总览

整体可以理解为五层：

```text
浏览器 / 用户
  ↓
Vue 前端
  - 页面路由
  - 登录态
  - 会话 UI
  - SSE 消费
  - 管理页面
  ↓ HTTP / SSE
FastAPI 后端
  - 鉴权与中间件
  - API 路由
  - 业务 service
  - Agent 编排
  - MCP Proxy
  ↓
Agent / 服务能力层
  - GeneralAgent
  - AnTangAgent
  - RAG
  - Tool / MCP / Skill
  - Memory
  - Storage
  ↓
基础设施
  - MySQL
  - Redis
  - MinIO / OSS
  - Chroma / Milvus / ES
  - 外部模型与工具 API
```

当前最重要的主链路是：

```text
ChatPage.vue
  → POST /api/v1/completion
  → DialogService.get_dialog_runtime_config()
  → 实例化 AnTangAgent
  → 组装历史、总结、用户输入、血糖上下文、图片上下文
  → LangChain/LangGraph Agent 流式运行
  → SSE: response_chunk + event
  → 前端逐段渲染回复和工具事件卡片
  → 保存 history / 更新 dialog summary
```

产品运行态只有安糖心语，`completion` 接口固定走 `AnTangAgent`；`GeneralAgent` 仅作为 `AnTangAgent` 的运行底座保留。

## 5. 后端启动过程

入口文件是 `src/backend/AnTang/main.py`。

启动顺序：

```text
create_app()
  → 注册 CORS / TraceID / Whitelist middleware
  → 注册 AuthJWT 配置与异常处理
  → FastAPI lifespan 启动
      → init_app_settings()
      → init_agentchat_system()
          → 确保 MySQL database/table
          → 初始化或更新默认 LLM
          → 确保系统级“安糖心语”Agent
          → 同步系统 MCP 配置
      → 创建 Redis client
      → 创建 MCP SessionManager
      → include_router(api router + mcp proxy router)
      → ensure_default_knowledge()
      → 后台 create_task(sync_local_pdf_folder())
      → 应用开始响应 /health 和业务请求
```

几个关键点：

- `init_app_settings()` 默认读取 `AnTang/config.yaml`。Docker 构建时会把 `docker/docker_config.yaml` 复制到容器内这个位置。
- `init_agentchat_system()` 是幂等启动入口，不只首次初始化；已有系统启动时也会更新默认 LLM、安糖 Agent、MCP 元数据。
- 安糖默认知识库创建是 startup 同步等待的，PDF 解析和向量化是后台任务，不阻塞 healthcheck。
- shutdown 时只记录安糖知识库后台任务是否完成，不主动阻塞退出。

## 6. API 路由结构

顶层路由：

```text
AnTang.main
  → AnTang.api.router.router
      → /api/v1/*
      → /mcp/*
```

当前 `/api/v1` 注册的模块在 `src/backend/AnTang/api/v1/router.py`：

```text
/completion                  对话 SSE 主入口
/dialog/*                    会话创建、列表、删除
/history                     会话历史
/message/*                   点赞/点踩
/agent                       Agent 创建、查询、更新、删除
/user/*                      用户注册、登录、头像
/tool/*                      工具管理
/llm/*                       模型配置
/mcp_server*                 MCP Server 管理
/mcp_user_config*            MCP 用户配置
/upload                      文件上传
/agent_skill/*               Skill Agent 管理
/register_mcp/*              OpenAPI → MCP Server 对话式生成
/register_mcp_completion     生成任务 SSE 入口
/register_task               HITL 审批与生成任务记录
```

知识库相关 HTTP 接口（`/api/v1/knowledge/*`、`/api/v1/knowledge_file/*`）不对外注册。底层 service、DAO、model 和 RAG 实现保留，供安糖内置系统知识库复用。

## 7. 后端分层职责

```text
api/v1/
  负责 HTTP 参数、鉴权依赖、响应模型、SSE 包装。

api/services/
  负责业务编排，例如 AgentService、DialogService、HistoryService。
  这一层通常会调用 DAO，也会调用 core/agents 或 services。

core/agents/
  负责把数据库配置变成可运行 Agent。
  GeneralAgent 是普通 Agent 核心，AnTangAgent 在其上叠加安糖能力。

core/models/
  负责模型实例化。
  ModelManager 从 app_settings 或用户模型配置生成 ChatModel、Embedding client 等。

services/
  负责领域能力与基础设施能力。
  包括 antang、rag、mcp、memory、storage、rewrite、sandbox、convert_files 等。

database/dao/
  数据访问层。

database/models/
  SQLModel 表结构。

schemas/
  请求、响应、工具、RAG、MCP 等 Pydantic 类型。

tools/
  平台内置工具与 OpenAPI 工具适配。
```

## 8. GeneralAgent

`src/backend/AnTang/core/agents/general_agent.py` 是普通 Agent 的运行核心。

它的输入是 `AgentConfig`，来源通常是数据库里的 `agent` 表：

```text
AgentConfig
  - user_id
  - llm_id
  - mcp_ids
  - knowledge_ids      # 由安糖内置知识库占用；GeneralAgent 自身不再使用
  - tool_ids
  - agent_skill_ids
  - system_prompt
  - enable_memory
  - name
```

初始化过程：

```text
GeneralAgent.init_agent()
  → setup_mcp_agent_as_tools()
      把绑定的 MCP Server 包装成可调用 tool
  → setup_tools()
      加载平台内置工具或用户 OpenAPI 工具
  → setup_agent_skill_as_tools()
      把 Skill Agent 包装成 tool
  → setup_language_model()
      按 llm_id 或默认配置创建对话模型
  → setup_agent_middleware()
      注册工具事件中间件
  → setup_react_agent()
      create_agent(model, tools, middleware)
```

流式输出：

```text
LangGraph stream_mode=["messages", "custom"]

messages 通道：
  AIMessageChunk → response_chunk → 前端逐字渲染

custom 通道：
  工具 START / END / ERROR → event → 前端事件卡片
```

工具事件由 `EmitEventAgentMiddleware` 负责包装。它通过 `tool_metadata_map` 把内部函数名翻译成前端展示名，例如：

```text
retrieve_diabetes_knowledge → 糖尿病知识库
lookup_weather              → 天气查询
某个 MCP as tool             → MCP 服务名
```

## 9. AnTangAgent

`src/backend/AnTang/core/agents/antang_agent.py` 继承 `GeneralAgent`。

它不是重写整套 Agent 运行框架，而是在通用 ReAct Agent 之前和工具列表中加入安糖专属逻辑：

```text
AnTangAgent
  复用 GeneralAgent:
    - 模型选择
    - 内置工具
    - MCP as tool
    - Skill as tool
    - LangGraph streaming
    - 工具事件中间件

  增加安糖专属能力:
    - 轻量分析器（小快模型生成上下文备忘录）
    - 血糖分层（确定性规则）
    - 跨轮 DialogState 滚动
    - 安糖提示词组装
    - 图片理解
    - 糖尿病知识库检索
    - 饮食建议
    - 运动建议
    - 时间、天气、联网搜索、文生图等工具
```

每轮对话的入口是 `AnTangAgent.astream()`：

```text
接口层传入:
  user_input
  short_history
  history_summary
  glucose_context
  file_url / file_name
  previous_dialog_state        # 由 completion.py 从最近 history events 加载

AnTangAgent:
  → 缓存本轮 glucose / image 上下文
  → classify_glucose_zone(glucose_context)              # 确定性规则
  → light_analyzer.analyze(...)                         # 小快模型生成 4 栏备忘录
  → build_turn_system_prompt_v2(分析结果 + 血糖分层)    # 注入 system prompt
  → build_turn_user_message()
  → super().astream(messages)
  → 累积 assistant 文本，更新 DialogState
  → yield hidden event(antang_dialog_state) → 随 history events 落库
```

血糖分层仍由 `services/antang/policies.py` 的 `classify_glucose_zone()` 负责，是确定性规则：

```text
value < 3.0                         → severe_low
3.0 <= value < 3.9                  → low
3.9 <= value <= 4.5 且 trend falling → low_warning
其他                                → normal / unknown
```

`severe_low` 不再向前端推安全卡片，而是通过 system prompt 让主回复在自然语气中表达紧急提醒。

### 9.1 轻量分析器

`services/antang/light_analyzer.py` 是在主 ReAct 链路**之前**插入的小模型分析步骤：

```text
输入: 用户原话 + 最近 N 条历史 + 历史摘要 + 上一轮 DialogState + 血糖/附件/记忆
输出: LightAnalyzerResult {
        caution_level:  normal | careful | high_attention,
        memo: {
          understanding,    # 这轮在发生什么（≤80字）
          core_worry,       # 具体怕什么（≤50字）
          reply_rhythm,     # 回复节奏建议（≤100字）
          avoid             # 本轮要避开的坑（≤60字）
        }
      }
```

设计要点：

- **不是用户可见的回复**，只是注入主 agent 的 system prompt 增强语境理解
- **超时/JSON 非法时返回保守 fallback**，不抛异常，不中断主链路
- **模型独立配置**：`multi_models.light_analyzer` 与 `conversation_model` 等其他模型节并列，推荐填 flash/turbo 类亚秒级返回的小模型
- **行为开关单独成节**：`antang_light_analyzer.{enabled, timeout_ms, max_output_tokens, max_short_history_messages, show_internal_trace}`

DialogState 是跨轮滚动的轻量状态（current_stage / dominant_emotions / last_followup_question），通过 hidden history event 落库，不新增表。下一轮由 `completion.py` 从最近 events 反向扫一次取出，作为 `previous_dialog_state` 传给 `astream()`。

旧版基于关键词的硬匹配（`assess_safety` / `build_safety_notice` / `DANGER_HELP_KEYWORDS`）已删除。语境是否谨慎、要不要先接住情绪，全部交给分析器判断。

## 10. 安糖工具集

AnTangAgent 在 `_build_antang_tools()` 中注册以下工具：

```text
get_current_beijing_time()
  获取当前北京时间。

analyze_uploaded_image()
  分析本轮上传图片，复用 AnTangVisionService，结果在本轮缓存。

retrieve_diabetes_knowledge(query)
  检索安糖内置糖尿病知识库。

web_search(query)
  Tavily 联网搜索，不限健康话题。

lookup_weather(city)
  高德天气查询，主要辅助运动/外出建议。

get_diet_support(query)
  调用模型生成一段饮食建议依据，供主助手整合。

get_exercise_support(query)
  调用模型生成一段运动建议依据，供主助手整合。

text_to_image(prompt)
  调用文生图工具。
```

这些工具大多不是直接面向用户输出最终答案，而是给主 Agent 提供可整合的中间依据。最终语气和回复节奏仍由安糖系统提示词控制。

## 11. 对话链路

后端主入口是 `src/backend/AnTang/api/v1/completion.py`。

完整链路：

```text
1. 前端调用 sendMessage()
   文件：src/frontend/src/apis/chat.ts
   方法：fetchEventSource('/api/v1/completion')

2. completion(req, login_user)
   文件：src/backend/AnTang/api/v1/completion.py

3. 读取运行时配置
   DialogService.get_dialog_runtime_config(req.dialog_id)
     → dialog
     → agent

4. 实例化 Agent
   chat_agent = AnTangAgent(agent_config)
   chat_agent.init_agent()

5. 读取上下文
   HistoryService.get_short_term_messages()
   DialogService.get_dialog_history_summary()
   _load_dialog_state_from_history()      # 从最近 events 反向扫一次取上一轮 DialogState

6. 保存用户消息
   HistoryService.save_chat_history(role="user", events=[attachment/glucose_context])

7. SSE 流式运行
   AnTangAgent.astream(..., previous_dialog_state=...)
     → light_analyzer 先跑一遍生成备忘录（不可见）
     → response_chunk
     → event（含 hidden antang_dialog_state，本轮结束时 yield）

8. 收尾
   AnTangAgent.finalize_turn() 当前为空钩子
   保存 assistant history（hidden event 一起进 history.events）
   DialogService.update_dialog_summary()
```

前端接收逻辑在 `src/frontend/src/pages/conversation/chatPage/chatPage.vue`：

- `response_chunk`：累加到最后一条 AI 消息。
- `event`：更新工具/安全事件卡片。
- `error`：显示错误消息。
- `heartbeat`：忽略。

## 12. 会话、历史和总结

会话主表：

- `dialog`：会话 ID、绑定 Agent、Agent 类型、用户、会话摘要。
- `history`：用户/助手消息、事件列表、token_usage。

历史上下文分两部分：

```text
短期上下文:
  HistoryService.get_short_term_messages()
  从 summary_last_time 之后读取消息，转成 LangChain Message。

长期压缩摘要:
  DialogService.update_dialog_summary()
  对较早的成对 user/assistant 消息做模型总结，写回 dialog.summary。
```

长期记忆：

- 底层由 `services/memory/client.py` 支撑，`memory_history` 表记录变更。
- 当前 `completion` 接口固定走 `AnTangAgent`，不会写入长期 memory。
- `AnTangAgent.finalize_turn()` 是保留钩子，后续若接入长期记忆，在这里实现。

## 13. 安糖内置知识库

当前知识库已经收缩为安糖 Agent 的固有资料源，不再是用户侧可创建、可上传、可绑定的通用能力。

核心文件：

```text
src/backend/AnTang/services/antang/knowledge.py
src/backend/AnTang/scripts/import_antang_knowledge.py
src/backend/AnTang/services/antang/capabilities.py
src/backend/AnTang/services/rag/*
src/backend/AnTang/api/services/knowledge.py
src/backend/AnTang/api/services/knowledge_file.py
```

用户不可见的部分：

```text
前端没有 /knowledge 页面
前端没有 knowledge API 封装
Agent 编辑器不再展示 knowledge_ids
/api/v1/knowledge/* 未注册
/api/v1/knowledge_file/* 未注册
GeneralAgent 不再注册 retrieval_knowledge 工具
```

保留的底层：

```text
knowledge / knowledge_file 数据表
KnowledgeDao / KnowledgeFileDao
KnowledgeService / KnowledgeFileService
RagHandler
doc_parser
vector_stores
```

这样做的原因是：产品层不需要用户自建知识库，但安糖 RAG 仍需要一套可替换的资料导入和检索基础设施。

## 14. 安糖知识库导入链路

默认 PDF 目录：

```text
data/antang_knowledge_pdfs/
```

容器挂载：

```text
../data/antang_knowledge_pdfs:/app/data/antang_knowledge_pdfs:ro
../data/vector_db:/app/vector_db
```

默认知识库：

```text
name: 安糖默认知识库
owner: SystemUser ("0")
desc: 安糖 Agent 内置糖尿病/低血糖系统资料源，由系统管理。
```

启动同步逻辑：

```text
FastAPI lifespan
  → ensure_default_knowledge()
      如果同名知识库存在，接管为 SystemUser
      如果不存在，创建默认知识库
  → 后台 sync_local_pdf_folder()
      扫描 data/antang_knowledge_pdfs/*.pdf
      读取 knowledge_file 表
      检查 Chroma collection 是否存在/为空
      检查每个 file_id 是否已有向量条目
      新文件 → create_knowledge_file()
      已有 DB 记录但缺向量 → 重建该 PDF 索引
      已有 DB 记录且向量存在且 status=success → skip
```

这个补索引逻辑很重要。因为 MySQL 和 Chroma 的持久化生命周期可能不同：数据库里可能有文件记录，但向量库 collection 已丢失或只写了一半。当前实现不会只相信 `knowledge_file.status`，而会继续检查向量库中的实际条目数。

手动导入脚本：

```bash
cd src/backend
uv run python -m AnTang.scripts.import_antang_knowledge
```

Docker 环境中：

```bash
docker compose -f docker/docker-compose.yml exec backend \
  uv run python -m AnTang.scripts.import_antang_knowledge
```

## 15. RAG 实现

当前 RAG 主控是 `src/backend/AnTang/services/rag/handler.py`。

导入链路：

```text
PDF / 文档文件
  → doc_parser.parse_doc_into_chunks()
      → pdf_parser / markdown_parser / text_parser / docx_parser / pptx_parser / ...
  → ChunkModel[]
  → RagHandler.index_milvus_documents()
      → vector_stores 当前实际为 ChromaClient
      → get_embedding()
      → collection.add()
  → 可选 RagHandler.index_es_documents()
```

PDF 解析：

```text
pdf_parser.convert_markdown()
  → pymupdf4llm.to_markdown(write_images=True)
  → 上传图片到 MinIO / OSS
  → markdown_rewriter.run_rewrite()
  → 上传 Markdown 中间产物
  → markdown_parser.parse_into_chunks()
```

检索链路：

```text
retrieve_diabetes_knowledge(query)
  → AnTangCapabilityService.retrieve_knowledge()
  → resolve_knowledge_ids()
      只返回安糖默认知识库 ID
  → RagHandler.retrieve_ranked_documents()
      → query_rewrite()
      → MixRetrival.retrival_milvus_documents(..., search_field="content")
      → Reranker.rerank_documents()
      → min_score / top_k 过滤
      → 拼接命中文本
```

当前 Docker 配置：

```text
rag.enable_summary: False
rag.enable_elasticsearch: False
rag.retrival.top_k: 5
rag.retrival.min_score: 0.01
rag.vector_db.mode: chroma
```

因为安糖检索当前走 content 字段，`enable_summary` 关闭可以显著减少 PDF 导入阶段的 LLM 调用。

## 16. 前端结构

前端入口：

```text
src/frontend/src/main.ts
  → createApp(App)
  → createPinia()
  → router
  → ElementPlus
```

路由核心：

```text
/login
/register
/
  → /conversation
  → /conversation/chatPage
  → /configuration
  → /agent
  → /agent/editor
  → /mcp-server
  → /mcp-server/chat
  → /tool
  → /agent-skill
  → /model
  → /model/editor
  → /profile
/:catchAll(.*)
```

根路径 `/` 默认重定向到 `/conversation`。

前端主产品视图：

```text
pages/index.vue
  外层布局与登录用户状态。

pages/conversation/conversation.vue
  会话列表与聊天子路由容器。

pages/conversation/chatPage/chatPage.vue
  实际聊天界面：
    - 文本输入
    - 文件上传
    - 安糖会话下的血糖值/趋势/时间输入
    - SSE 消费
    - response_chunk 渲染
    - event 工具卡片渲染
```

状态管理（`src/frontend/src/store/`）：

```text
user                 token、用户信息、登录态
history_chat_msg     当前 dialogId、聊天消息、事件信息、agentType
history_list         历史会话列表
agent_card           Agent 卡片状态
```

## 17. 前端到后端的数据格式

聊天请求由 `src/frontend/src/apis/chat.ts` 发起：

```text
Chat
  dialogId
  userInput
  fileUrl?
  fileName?
  glucoseContext?
    currentValueMmolL
    trend
    measuredAt
    source
```

发送到后端时转换成后端字段：

```json
{
  "dialog_id": "...",
  "user_input": "...",
  "file_url": "...",
  "file_name": "...",
  "glucose_context": {
    "current_value_mmol_l": 3.8,
    "trend": "falling",
    "measured_at": "...",
    "source": "manual"
  }
}
```

后端 SSE 事件主要有两类：

```text
response_chunk
  data.chunk
  data.accumulated

event
  data.status: START / END / ERROR
  data.title
  data.message
  data.tags?
  data.details?
```

安糖安全事件也是 `event`，例如：

```text
title: 安全判断
event_type: safety_assessment
tags: ["安全", risk_level]
details: AnTangSafetyAssessment
```

## 18. 数据层

核心数据可以分为几类：

用户与权限：

```text
user
role
user_role
```

对话与 Agent：

```text
agent
dialog
history
message
memory_history
antang_profile
```

能力配置：

```text
tool
llm
agent_skill
mcp_server
mcp_user_config
mcp_agent
```

知识库底层：

```text
knowledge
knowledge_file
```

这些表仍存在，仅作为安糖内置知识库的数据底座。

MCP 生成相关：

```text
register_mcp
register_mcp_tool
register_task
```

以上表用于 OpenAPI → MCP Server 的对话式生成流程（HITL 审批、生成任务记录）。

## 19. MCP 与工具体系

项目里有三类工具来源：

```text
1. 平台内置工具
   tools/*
   例如天气、联网搜索、文生图、文件转换、邮件、Arxiv 等。

2. 用户自定义 OpenAPI 工具
   tools/openapi_tool/adapter.py
   把 OpenAPI schema 中的 operation 转成 LangChain StructuredTool。

3. MCP Server
   services/mcp/*
   mcp_proxy/*
   core/agents/mcp_agent.py
   MCP Server 可以被包装成主 Agent 可调用的 tool。
```

MCP Proxy 暴露两种传输形式：

```text
/mcp/{server_key}/sse
/mcp/{server_key}
```

OpenAPI 到 MCP 的生成链路主要在：

```text
api/v1/register_mcp.py
api/v1/register_mcp_completion.py
mcp_proxy/register_mcp.py
mcp_proxy/agent.py
```

这条链路支持对话式生成、校验、HITL approve/reject 和保存注册结果。

## 20. Storage

存储抽象在 `src/backend/AnTang/services/storage`。

当前 Docker 配置：

```text
storage.mode: minio
minio.endpoint: minio:9000
minio.bucket_name: agentchat
minio.base_url: http://127.0.0.1:9000/agentchat
```

典型使用场景：

- 用户上传文件：`/api/v1/upload`。
- 前端头像、Agent logo、工具 logo。
- PDF 解析过程中生成的图片和 Markdown 中间文件。
- 图片理解场景的用户附件 URL。

## 21. Docker 拓扑

默认命令：

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

服务：

```text
mysql
  MySQL 8.0
  端口 3306
  volume: mysql_data

redis
  Redis 7
  端口 6379
  volume: redis_data

minio
  对象存储
  端口 9000 / 9001
  volume: docker/data/minio_data

backend
  FastAPI / uvicorn
  端口 7860
  依赖 mysql / redis / minio healthy
  挂载:
    ../data/antang_knowledge_pdfs:/app/data/antang_knowledge_pdfs:ro
    ../data/vector_db:/app/vector_db

frontend
  Vite 开发服务
  端口 8090
  依赖 backend healthy
```

这里最容易踩坑的是向量库持久化。Chroma 实际写入容器内 `./vector_db`，所以必须挂载到宿主机 `data/vector_db`，否则重建容器后 MySQL 里还显示 PDF 已导入，但向量 collection 可能已经消失。

## 22. 当前产品形态与平台底座的边界

产品侧（安糖心语对外的能力）：

```text
安糖心语 Agent
安糖会话 UI
血糖上下文输入
图片上传和理解
内置糖尿病知识库检索
安全判断事件
饮食/运动/天气/搜索辅助能力
```

平台底座（开发者管理界面与基础能力）：

```text
Agent 创建与编辑
Tool 管理
LLM 配置
MCP Server 管理
OpenAPI → MCP Server 对话式生成
Agent Skill
Memory 底层
RAG 底层
Storage / SSE / 鉴权
```

## 23. 当前架构的几个重要判断

1. 安糖不是一个完全独立的后端服务。

它复用了 AgentChat 的 Agent、工具、模型、历史、存储、SSE、MCP 等底座，只在 `AnTangAgent` 和 `services/antang/*` 中放置产品差异化逻辑。

2. 知识库现在是系统资料源，不是用户能力。

`knowledge` / `knowledge_file` 表仍然存在，但它们现在服务于安糖默认知识库。用户侧 API 和 UI 下线后，普通用户不会再污染安糖检索。

3. 普通 Agent 和安糖 Agent 的边界清晰。

`GeneralAgent` 负责通用能力编排。`AnTangAgent` 继承它，但只额外处理安糖上下文、风险判断、提示词和专用工具。

4. 语境理解走轻量分析器，不再依赖关键词硬匹配。

`policies.py` 现在只剩 `classify_glucose_zone()` 这一条确定性规则，处理客观生理边界。"用户是否在情绪化、是否引用他人、是否在分析虚构文本" 这类语境判断由 `services/antang/light_analyzer.py` 的小模型分析器给出。分析器超时或失败时返回保守 fallback，不中断主链路。模型身份在 `multi_models.light_analyzer` 单独配置，与 `conversation_model` 等其他模型节并列。

5. RAG 的工程基础已经可替换。

安糖侧只依赖 `retrieve_diabetes_knowledge(query)` 这个工具契约和 `AnTangCapabilityService.retrieve_knowledge()`。后续组内同学替换 RAG 时，理想情况下只需替换 `services/rag/*` 或 `AnTangCapabilityService.retrieve_knowledge()` 内部，不必改前端和 Agent 工具契约。

## 24. 后续最值得维护的扩展点

安糖 Agent 体验：

```text
src/backend/AnTang/core/agents/antang_agent.py
src/backend/AnTang/services/antang/prompts.py
src/backend/AnTang/services/antang/policies.py
src/backend/AnTang/services/antang/capabilities.py
```

本地 RAG / PDF 知识：

```text
src/backend/AnTang/services/antang/knowledge.py
src/backend/AnTang/services/rag/*
data/antang_knowledge_pdfs/
data/vector_db/
```

对话 UI：

```text
src/frontend/src/pages/conversation/chatPage/chatPage.vue
src/frontend/src/apis/chat.ts
src/frontend/src/store/history_chat_msg/index.ts
```

平台工具与 MCP：

```text
src/backend/AnTang/tools/*
src/backend/AnTang/services/mcp/*
src/backend/AnTang/mcp_proxy/*
src/backend/AnTang/core/agents/mcp_agent.py
```

移动端适配前的产品协议：

```text
POST /api/v1/completion
GET /api/v1/dialog/list
POST /api/v1/dialog
GET /api/v1/history
POST /api/v1/upload
```

如果后续做 React Native，优先稳定这些接口和 SSE 事件结构，而不是直接复刻当前 Web 页面实现。

## 25. 一句话总结

当前项目是一个“安糖心语产品层 + AgentChat 智能体平台底座”的混合架构：前端以 Vue 会话界面承载产品体验，后端以 FastAPI 管理 API、会话和基础设施，以 LangChain/LangGraph 执行 Agent，以 MySQL/Redis/MinIO/Chroma 保存状态与知识；安糖的差异化集中在 `AnTangAgent`、`services/antang/*` 和内置系统知识库上。
