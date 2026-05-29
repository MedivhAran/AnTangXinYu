# 安糖心语 项目架构说明

本文基于当前仓库代码做静态梳理，帮助开发者快速理解项目的运行结构、模块边界和主要数据流，也可以作为给 AI 看的说明文档，帮助AI快速理解项目架构。

## 1. 项目定位

这是一个前后端分离的中文智能体项目，产品形态是“安糖心语”：面向 1 型糖尿病 / 妊娠糖尿病 / 低血糖恐惧情绪用户的陪伴型 Agent。合作方提供硅基仿生 CGM 设备评估报告与患者场景库，AI 需要能解析这些医学数据并在长期对话中提供个性化支持。

整体分两层：

```text
安糖心语产品层（AnTangAgent + 安糖会话 UI + 长期画像 + CGM 报告链路 + 安糖工具集 + 内置知识库）
  运行在
通用 Agent 底座（GeneralAgent + Sub-Agent-as-Tool 抽象 + MCP / Skill / Memory / RAG / Storage）
  之上
```

**当前产品形态是「聊天为主，管理后端化」**：
- 用户侧只剩聊天界面。Agent 编辑器等管理页面仍存在但**未在前端导航暴露**，只能直接输入 URL 进入，留作开发者调试用。
- MCP Server 和 Agent Skill **不再开放给用户创建**。开发者在后端代码里维护（`config/mcp_server.json` 列系统 MCP，`skills/` 目录放系统 Skill），启动时自动 seed 进数据库并自动绑定到「安糖心语」Agent。
- 知识库不作为用户能力出现，只作为安糖 Agent 的内置系统资料源。
- CGM 评估报告通过专门上传板块或对话附件两条路径共用同一个 service 解析入库。

## 2. 技术栈

后端：

- FastAPI：HTTP API、SSE 流式对话。
- Python 3.12+ / uv 包管理。
- SQLModel / SQLAlchemy：数据库模型与 DAO。
- MySQL 8：用户、Agent、会话、历史、长期画像、CGM 报告、工具、MCP、知识库元数据等关系数据。
- Redis 7：运行时辅助状态、长期记忆 mem0 后端。
- LangChain / LangGraph：Agent 执行、ReAct 工具调用、中间件事件流、Structured Output。
- ChromaDB：当前 Docker 配置下的默认向量库（同时承担 RAG 知识库与长期记忆向量化）。
- mem0：长期对话记忆抽事实 + 合并去重，底层 Chroma 持久化。
- pymupdf / pymupdf4llm：PDF 文字层抽取（CGM 报告解析、知识库 markdown 转换）。
- MinIO / 阿里云 OSS：上传文件、头像、PDF 转换中间产物等对象存储。

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
├── ARCHITECTURE.md                   # 旧版,已被 docs/development/ARCHITECTURE.md 取代
├── AGENTS.md / CLAUDE.md             # 给 coding agent 的协作说明
├── CGM/                              # 合作方提供的 CGM 报告 PDF 样本(开发测试用)
├── data/
│   ├── antang_knowledge_pdfs/        # 安糖系统内置医学 PDF 资料目录,容器只读挂载
│   └── vector_db/                    # Chroma 持久化目录,容器读写挂载
├── docker/
│   ├── Dockerfile / Dockerfile.frontend
│   ├── docker-compose.yml            # mysql + redis + minio + backend + frontend
│   ├── docker_config.example.yaml    # 配置模板,仓库里维护它
│   └── docker_config.yaml            # 真实配置(含 API key),.gitignore 屏蔽
├── docs/
│   └── development/                  # 开发文档,含本架构说明 + 项目理解指南
├── src/backend/
│   ├── pyproject.toml                # uv 依赖,关键: langchain / langgraph / pymupdf / mem0 / chroma
│   └── AnTang/
│       ├── main.py                   # FastAPI app / lifespan(初始化 + 知识库后台同步)
│       ├── settings.py               # YAML 配置加载,Pydantic 强类型
│       ├── api/
│       │   ├── v1/                   # 已挂载: completion/dialog/message/agent/history/user/llm/tool/upload/cgm_report
│       │   │                         # 已删除/未挂载: agent_skill/mcp_server/mcp_user_config/register_mcp* (用户管理路径)
│       │   ├── services/             # 业务 service (AgentService/DialogService/HistoryService 等)
│       │   ├── responses/            # 统一响应封装
│       │   └── router.py             # 顶层 API router (只挂载 api_v1_router,之前的 /mcp/* proxy 已删)
│       ├── auth/                     # JWT 鉴权
│       ├── core/
│       │   ├── agents/               # GeneralAgent + AnTangAgent + SkillAgent + MCPAgent + StructuredResponseAgent
│       │   ├── models/               # ModelManager (主对话 / 视觉 / embedding / rerank / light_analyzer)
│       │   └── callbacks/            # usage_metadata 统计
│       ├── config/                   # 配置 JSON: mcp_server.json (3 个系统 MCP) + tool.json + avatars.json
│       ├── database/
│       │   ├── models/               # SQLModel 表: agent/dialog/history/antang_profile/cgm_report/agent_skill 等
│       │   ├── dao/                  # 异步 DAO
│       │   └── init_data.py          # 启动时 seed: 系统 MCP / 系统 Skill / 安糖心语 Agent
│       ├── skills/                   # 系统 Skill 源码,启动时 seed 进 agent_skill 表
│       │   ├── README.md
│       │   └── cgm_interpretation/   # CGM 报告解读 Skill (SKILL.md + reference + scripts)
│       ├── mcp_servers/              # 标准独立 MCP Server 示例 (lark_mcp / weather / arxiv,与运行时无关)
│       ├── middleware/               # TraceID、白名单
│       ├── prompts/                  # 通用 prompt (completion / mcp 注册等)
│       ├── schemas/                  # Pydantic schema (cgm_report / agent_skill / mcp / common 等)
│       ├── services/
│       │   ├── antang/               # 安糖业务: prompts / policies / light_analyzer / profile / cgm_report / knowledge / capabilities / vision / state
│       │   ├── mcp/                  # MCP runtime manager
│       │   ├── memory/               # mem0 长期记忆 client
│       │   ├── rag/                  # 知识库 RAG handler / doc_parser / vector_stores
│       │   ├── storage/              # OSS / MinIO 抽象
│       │   ├── rewrite/              # 知识库导入时的 markdown 改写
│       │   ├── sandbox/              # 工具沙箱
│       │   └── convert_files/        # PDF / DOCX 转换
│       └── tools/                    # 内置工具: get_weather / arxiv / delivery / web_search / image2text / text2image / 文档转换 等
└── src/frontend/
    ├── package.json
    └── src/
        ├── apis/                     # 已删除: mcp-server/agent-skill/mcp-chat. 保留: chat/agent/llm/tool/history/auth/file
        ├── assets/                   # 已删除: mcp.svg / skill.svg
        ├── components/               # 已删除: dialog/create_agent/ (孤儿)
        ├── pages/                    # 已删除: mcp-server/ + agent-skill/
        ├── router/index.ts           # 已删除: /mcp-server, /agent-skill 路由
        ├── store/                    # Pinia: user / history_chat_msg / history_list / agent_card
        └── utils/                    # axios 封装 + utils
```

> 注:上方目录树里多次提到「已删除」「未挂载」,是因为产品方向从「用户可自建 MCP/Skill」收敛为「后端开发者维护」,做了一轮大清理。完整清理范围见 `docs/development/项目理解指南.md` 的"Skill 系统"和"MCP 完全后端化"章节。

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

启动顺序（首次启动 vs 增量启动逻辑见后文）：

```text
create_app()
  → 注册 CORS / TraceID / Whitelist middleware
  → 注册 AuthJWT 配置与异常处理
  → FastAPI lifespan 启动
      → init_app_settings()                       # 读 config.yaml → 强类型 Pydantic
      → init_agentchat_system()                   # 见下方详细步骤
      → register_router(app)                      # include /api/v1/* + /health
      → _bootstrap_antang_knowledge(app)
          → ensure_default_knowledge()            # 同步,确保默认知识库 record
          → asyncio.create_task(_background_sync) # 异步同步 PDF 入向量库,不阻塞 health
      → print_logo()
      → 应用开始响应 /health 和业务请求
```

> **历史变更**：旧版 main.py 还会在这里实例化 `SessionManager(redis_client)` 给 MCP Proxy SSE 用,以及 `include_router(/mcp/*)` proxy 路由。整套 OpenAPI→MCP 注册功能已删除,这两步也跟着去掉了。Redis 仍然在 docker-compose 里跑,因为 mem0 长期记忆内部可能用到。

`init_agentchat_system()`（`database/init_data.py`）的核心逻辑是**首次初始化 vs 增量更新**两套路径：

```text
init_agentchat_system()
  → init_database()                                # SQLModel.metadata.create_all
  → 查询 agent 表
      ├─ 空 → 首次初始化序列:
      │   - _init_default_tools()                  # config/tool.json → tool 表
      │   - _init_default_llms()                   # config.yaml conversation_model → llm 表
      │   - upload_user_avatars_storage()          # 默认头像传 MinIO
      │   - _init_system_mcp_server()              # config/mcp_server.json → mcp_server 表(SystemUser)
      │   - _init_system_skills()                  # skills/ 目录扫描 → agent_skill 表(SystemUser)
      │   - _ensure_antang_agent()                 # 创建"安糖心语" Agent,自动绑 SystemUser 的所有 MCP/Skill
      │
      └─ 非空 → 增量更新序列:
          - _update_exist_llm()                    # 对比 config 与 DB 里的 llm 配置
          - _update_mcp_server_into_mysql(True)    # 仅当 bootstrap.refresh_system_mcp_on_startup=true
          - _init_system_skills()                  # 仅当 bootstrap.init_system_skills=true,内部按 refresh flag 决定覆盖
          - _ensure_antang_agent()                 # 始终最后跑,绑最新的 MCP/Skill ID 列表
```

### 关键初始化机制

**`_init_system_skills`（skills 目录 → DB seed）**

新增于产品收敛后的"Skill 后端化"改造。逻辑：

1. 扫描 `src/backend/AnTang/skills/` 下所有子目录（用 `Path(__file__)` 推算,与 cwd 解耦）
2. 每个子目录读 `SKILL.md`,正则抠 frontmatter 拿 `name` / `description`
3. 递归把目录树构造成 `AgentSkillFolder` JSON 结构（含 reference / scripts 子目录）
4. `as_tool_name` 派生为 `<folder_name>_skill`（snake_case 强制）
5. 按 `(name, user_id=SystemUser)` 查重：不存在则 create；存在且 `refresh_system_skills_on_startup=true` 则覆盖；否则跳过

**`_init_system_mcp_server`（mcp_server.json → DB seed）**

逻辑类似 Skill seed,但只在首次初始化跑（不存在 `agent_skill` seed 的"增量补建"逻辑）。`refresh_system_mcp_on_startup` 用来强制重抓 MCP 工具元信息。

**`_ensure_antang_agent`（始终在启动序列末尾跑）**

每次启动都对比代码里的 `DEFAULT_ANTANG_SYSTEM_PROMPT` / `mcp_ids` / `agent_skill_ids` 跟 DB 里的"安糖心语"记录,有差异就 UPDATE。`mcp_ids` 和 `agent_skill_ids` 不再硬编码为 `[]`,而是**从 DB 实时查询所有 SystemUser 的 MCP / Skill**：

```python
"mcp_ids":         [s["mcp_server_id"] for s in await MCPService.get_all_servers(SystemUser)],
"agent_skill_ids": [s.id for s in await AgentSkillDao.get_agent_skills(SystemUser)],
```

这意味着：**新增系统 MCP / Skill 后,只要重启服务,安糖心语 Agent 就自动绑定上**,不需要手动改任何配置。

> **重要不变量**：`_ensure_antang_agent` 是 Prompt 的 single source of truth。改 `services/antang/prompts.py` 的 `DEFAULT_ANTANG_SYSTEM_PROMPT` + 重启即可,但**直接 UPDATE DB 里的 `agent.system_prompt` 会在下次启动时被代码值覆盖**。

### 配置加载

- `init_app_settings()` 默认读取 `AnTang/config.yaml`。Docker 构建时把 `docker/docker_config.yaml` 复制为容器内的 `AnTang/config.yaml`。
- 顶层 Pydantic 模型在 `settings.py` 里,关键字段：`multi_models` / `tools` / `rag` / `storage` / `bootstrap` / `mcp_credentials` / `antang_light_analyzer`。
- `bootstrap` 字段控制启动 seed 行为：`init_default_tools` / `init_system_mcp` / `refresh_system_mcp_on_startup` / `init_system_skills` / `refresh_system_skills_on_startup`。
- `mcp_credentials` 字段是后端化 MCP 后新加的：顶层 key 是 `mcp_server.json` 里的 server_name,值是 tool 调用时合并到 args 的字典（替代旧的 `mcp_user_config` 表）。

### shutdown

shutdown 时只记录安糖知识库后台同步任务是否完成,不主动阻塞退出。Redis client 也由 mem0 库自己管理生命周期,不再由 main.py 显式关闭。

## 6. API 路由结构

顶层路由（`api/router.py`）只挂载 `/api/v1/*`,旧的 `/mcp/*` MCP Proxy 路由已删：

```text
AnTang.main
  → AnTang.api.router.router
      → /api/v1/*
```

当前 `/api/v1` 注册的模块在 `src/backend/AnTang/api/v1/router.py`,**仅 10 个**：

```text
/completion/stream           对话 SSE 主入口
/dialog/*                    会话创建、列表、删除
/history                     会话历史
/message/*                   点赞 / 点踩
/agent/*                     Agent 查询(管理类创建/更新通过此入口,但前端导航不可达)
/user/*                      用户注册、登录、头像、个人资料
/llm/*                       模型只读查询
/tool/*                      工具只读查询
/upload                      文件上传(支持 PDF / 图片 / docx 等所有类型)
/cgm/*                       CGM 评估报告(import / reports 列表 / 单份详情 / 删除)
```

**已经从用户路由里下线的模块**（路由文件保留但未在 router.py 挂载,便于回滚）：

```text
/agent_skill/*               Skill 管理 → 后端 skills/ 目录维护
/mcp_server*                 MCP Server 管理 → 后端 config/mcp_server.json 维护
/mcp_user_config*            MCP 用户配置 → 改走 docker_config.yaml 的 mcp_credentials 段
```

**已经从代码里删除的模块**（OpenAPI → MCP 自动注册的整套子系统）：

```text
/register_mcp/*              OpenAPI 描述转 MCP Server
/register_mcp_completion     生成任务 SSE
/register_task               HITL 审批
+ database/models/register_* + database/dao/register_* + prompts/register_mcp.py
+ mcp_proxy/ 整个包(json_rpc / agent / session 等)
+ api/mcp_proxy/ 整个包(SSE / Streamable HTTP 路由)
```

> 数据库表 `register_mcp` / `register_task` / `register_mcp_tool` 因为没做 migration 还在 MySQL 里,但是空表,代码不再读写,不影响运行。

知识库相关 HTTP 接口（`/api/v1/knowledge/*`、`/api/v1/knowledge_file/*`）从开始就不对外注册,底层 service / DAO / model 和 RAG 实现保留,供安糖内置系统知识库复用。

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

## 8. GeneralAgent —— "Sub-Agent as Tool" 抽象的核心

`src/backend/AnTang/core/agents/general_agent.py` 是普通 Agent 的运行核心。整个项目对外能力分三种来源:本地工具(`tools/`)、MCP Server(远程协议)、Skill(子 Agent 子对话),它们通过 `GeneralAgent` 被**统一抽象成 LangChain `BaseTool`**,塞给 ReAct 引擎统一调度。

### 输入配置

输入是 `AgentConfig`,通常来自 `agent` 表的一行:

```text
AgentConfig
  - user_id            会话归属用户(per-request 注入,不存表)
  - llm_id             模型 ID,空则用默认 conversation_model
  - mcp_ids            绑定的 MCP Server ID 列表(安糖心语启动时自动绑系统 MCP)
  - tool_ids           绑定的内置工具或 OpenAPI 工具 ID
  - agent_skill_ids    绑定的 Skill ID 列表(安糖心语启动时自动绑系统 Skill)
  - knowledge_ids      历史兼容字段,普通 Agent 不再使用,安糖走专门的 retrieve_diabetes_knowledge 工具
  - system_prompt      该 Agent 的系统提示词
  - enable_memory      历史字段,实际记忆开关由 antang 子模块自己掌握
  - name
```

### 初始化:三类能力 → 一个 tool list

```python
GeneralAgent.init_agent()
  → mcp_agent_as_tools  = setup_mcp_agent_as_tools()    # MCP → tool
  → tools               = setup_tools()                  # 本地内置 + OpenAPI
  → skill_agent_as_tools= setup_agent_skill_as_tools()   # Skill → tool
  → setup_language_model()                               # ModelManager 装出 chat model
  → setup_agent_middleware()                             # EmitEventAgentMiddleware
  → setup_react_agent()
      → create_agent(model, tools + mcp + skill, middleware, StreamAgentState)
```

最后这三类工具拼到一起塞给 LangChain 的 `create_agent`,**主 Agent 的 LLM 根本不知道哪个工具背后是 MCP 协议、哪个背后是另一个 LLM**,它只看到一个工具列表 + 每个工具的 description。

### Skill 包装的具体方式([general_agent.py:281-307](../../src/backend/AnTang/core/agents/general_agent.py#L281-L307))

```python
@tool(agent_skill.as_tool_name, description=agent_skill.description)
async def call_skill_agent(query: str):
    skill_agent = SkillAgent(agent_skill, self.agent_config.user_id)
    await skill_agent.init_skill_agent()
    messages = await skill_agent.ainvoke([HumanMessage(content=query)])
    return "\n".join([message.content for message in messages])
```

每次主 Agent 调用这个工具,**后端 new 一个 `SkillAgent` 实例,把整个 folder JSON 加载进去,跑一个完整的 ReAct 循环**(子 Agent 内部还有自己的 LLM 调用、自己的 system prompt = SKILL.md、自己的两个工具 `get_file_content` / `list_skill_files`),最后输出一段字符串作为工具结果返回。

这就像主 Agent 雇了一个一次性的"专业临时工"。两个 LLM 之间只通过字符串通信,SkillAgent 看不到主对话历史、看不到 DB,只能读自己 folder 里的 reference / scripts —— **故意的隔离**,既能给 SkillAgent 设强输出约束(SKILL.md),又不会污染主 Agent prompt。

### MCP 同款机制

[general_agent.py:309-349](../../src/backend/AnTang/core/agents/general_agent.py#L309-L349) 包装 MCP 的逻辑跟 Skill 几乎一模一样:每个 MCP Server 包成一个 `MCPAgent`,`MCPAgent` 再包成一个 langchain tool。运行时 MCPAgent 内部通过 MCP 协议跟远程服务通信,把结果包成字符串回来。

凭证从哪儿来:[mcp_agent.py:75-78](../../src/backend/AnTang/core/agents/mcp_agent.py#L75-L78) 在每次工具调用前从 `app_settings.mcp_credentials.<server_name>` 里取(替代了旧的 `MCPUserConfigTable` 用户填密钥流程)。

### 流式输出

```text
LangGraph stream_mode=["messages", "custom"]

messages 通道:
  AIMessageChunk → 主 Agent 的回复 token 流 → completion.py 包成 response_chunk → SSE

custom 通道:
  工具 START / END / ERROR → event → 前端事件卡片
```

工具事件由 `EmitEventAgentMiddleware` 负责包装。它通过 `tool_metadata_map` 把内部函数名翻译成前端展示名,例如:

```text
retrieve_diabetes_knowledge → 糖尿病知识库
lookup_weather              → 天气查询
import_cgm_report           → 导入 CGM 报告
get_latest_cgm_report       → 查看最近 CGM 报告
cgm_interpretation_skill    → Skill: CGM 报告解读
高德地图 MCP                 → MCP: 高德地图
```

> 流式细节:LangGraph 默认会把节点内**任何 LLM 调用的 token** 都吐到 messages 通道(包括 SkillAgent 内部调 LLM 时也会冒出来)。`general_agent.py:374-383` 显式过滤了 `langgraph_node == "model"`,只放行主 Agent 模型节点,避免子 Agent 内部 token 污染主对话流。

## 9. AnTangAgent

`src/backend/AnTang/core/agents/antang_agent.py` 继承 `GeneralAgent`,在通用 ReAct Agent 之前/之后注入安糖业务逻辑,工具列表里追加安糖专属工具:

```text
AnTangAgent
  复用 GeneralAgent:
    - 模型选择 / 内置工具 / MCP as tool / Skill as tool
    - LangGraph streaming / 工具事件中间件

  在 GeneralAgent 之上扩展:
    - 长期记忆并行召回(画像 + 向量)
    - 轻量分析器(小快模型生成 4 段备忘录)
    - 血糖分层(确定性规则,落进 prompt)
    - 跨轮 DialogState 滚动
    - 安糖系统提示词组装
    - finalize_turn 双任务 fire-and-forget(写向量库 + 更画像)

  追加的安糖专属工具:
    - get_current_beijing_time / analyze_uploaded_image
    - retrieve_diabetes_knowledge / web_search / lookup_weather
    - get_diet_support / get_exercise_support / text_to_image
    - import_cgm_report / get_latest_cgm_report (CGM 报告链路)
```

### 9.1 实例化模型:per-request

`AnTangAgent` 是 **per-request 实例化**的(`api/v1/completion.py:96` 每次请求 new 一个)。这让 Agent 可以安全地用 `self.current_*` 字段缓存"本轮临时状态":

```python
self.current_glucose_context: GlucoseContext | None
self.current_file_url: str | None
self.current_file_name: str | None
self.current_vision_analysis: AnTangVisionAnalysis | None
self.last_analyzer_understanding: str          # light_analyzer 输出,留给 finalize_turn 用
```

工具闭包(`_build_antang_tools`)直接读 `self.current_*`,**模型调工具时不需要把图片 URL、血糖数值这些 in-band 参数再暴露到工具签名里**。这是项目里把"会话级上下文"跟"工具签名"解耦的关键技巧。

### 9.2 一轮对话的完整流程

```text
api/v1/completion.py 传入:
  user_input, short_history, history_summary,
  glucose_context, file_url / file_name,
  previous_dialog_state  (从最近 history events 反向扫加载)

AnTangAgent.astream():
  ① 缓存本轮上下文到 self.current_*
  ② classify_glucose_zone(glucose_context)            # 确定性规则
  ③ 并行启动 recall_task:                              # AnTangProfileService.recall_context()
       - 读 antang_profile 表(结构化画像 9 字段)
       - 向 Chroma 做语义检索(mem0 client)
       - 合并成 AnTangMemoryContext
  ④ await light_analyzer.analyze(...)                  # ~1.6s,通常比 recall 慢
       → LightAnalyzerResult { caution_level, memo: {understanding, core_worry, reply_rhythm, avoid} }
       → self.last_analyzer_understanding = memo.understanding   # 留给 finalize_turn
  ⑤ memory_ctx = await recall_task                     # 通常零等待
  ⑥ build_turn_system_prompt_v2 拼出 system prompt:
       base + 分析器备忘录 + 血糖分层 + 长期画像段 + 短期历史摘要
  ⑦ 调用 super().astream(messages) → 跑 LangChain ReAct
       → 边流 token,边累积 response_chunks
  ⑧ 跑完后用 _update_dialog_state 提炼新的 DialogState
  ⑨ yield 一个 hidden event "antang_dialog_state"     # 不推前端,只入库

api/v1/completion.py finally 块:
  ⑩ AnTangAgent.finalize_turn(user_input, assistant_response)
       → asyncio.create_task(_store_turn_memory_safely)   # fire-and-forget 写向量库
       → asyncio.create_task(_update_profile_safely)      # fire-and-forget 更画像
  ⑪ HistoryService.save_chat_history(...)              # 落 history 表
  ⑫ DialogService.update_dialog_summary(...)           # 累计 token 超阈值时触发短期历史压缩
```

### 9.3 血糖分层(`policies.classify_glucose_zone`)

`services/antang/policies.py` 的 `classify_glucose_zone()` 是确定性规则:

```text
value < 3.0                          → severe_low
3.0 <= value < 3.9                   → low
3.9 <= value <= 4.5 且 trend falling → low_warning
其他                                  → normal / unknown
```

`severe_low` 不向前端推安全卡片,而是通过 system prompt 让主回复**在自然语气中表达紧急提醒**(不弹卡片是产品方向决定的:卡片会触发恐惧型用户的二次焦虑)。

### 9.4 轻量分析器

`services/antang/light_analyzer.py` 是在主 ReAct 链路**之前**插入的小模型分析步骤:

```text
输入: 用户原话 + 最近 N 条历史 + 历史摘要 + 上一轮 DialogState + 血糖/附件/记忆
输出: LightAnalyzerResult {
        caution_level:  normal | careful | high_attention,
        memo: {
          understanding,    # 这轮在发生什么(≤80 字)
          core_worry,       # 具体怕什么(≤50 字)
          reply_rhythm,     # 回复节奏建议(≤100 字)
          avoid             # 本轮要避开的坑(≤60 字)
        }
      }
```

设计要点:

- **不是用户可见的回复**,只是注入主 Agent system prompt 增强语境理解
- **超时 / JSON 非法时返回保守 fallback**,不抛异常,不中断主链路
- **模型独立配置**:`multi_models.light_analyzer` 与 `conversation_model` 等并列,推荐填 flash/turbo 类亚秒级返回的小模型
- **行为开关单独成节**:`antang_light_analyzer.{enabled, timeout_ms, max_output_tokens, max_short_history_messages, show_internal_trace}`
- **memo.understanding 被持久化用途**:`AnTangAgent.astream` 把它存到 `self.last_analyzer_understanding`,`finalize_turn` 把它作为 `assessment_reason` 传给画像更新 LLM,让画像更新知道"本轮上下文是什么"。这是分析器跟画像系统的隐式串联点。

### 9.5 DialogState 跨轮滚动

`DialogState` 是跨轮的轻量状态(`current_stage / dominant_emotions / last_followup_question`),通过 hidden history event 落库,**不新增表**。下一轮由 `completion.py:_load_dialog_state_from_history()` 从最近 events 反向扫一次取出,传给 `astream()`。

旧版基于关键词的硬匹配(`assess_safety` / `build_safety_notice` / `DANGER_HELP_KEYWORDS`)已删除。语境是否谨慎、要不要先接住情绪,全部交给 light_analyzer 判断。

## 10. 安糖工具集

AnTangAgent 在 `_build_antang_tools()` 中注册 10 个工具(`@tool(parse_docstring=True)` 闭包,工具描述从 docstring 抽取):

```text
get_current_beijing_time()
  获取当前北京时间。

analyze_uploaded_image()
  分析本轮上传图片,复用 AnTangVisionService,结果在本轮缓存。

retrieve_diabetes_knowledge(query)
  检索安糖内置糖尿病知识库(Chroma 向量检索 + Rerank)。

web_search(query)
  Tavily 联网搜索,不限健康话题。

lookup_weather(city)
  高德天气查询,主要辅助运动 / 外出建议。

get_diet_support(query)
  调用模型生成一段饮食建议依据,供主助手整合。

get_exercise_support(query)
  调用模型生成一段运动建议依据,供主助手整合。

text_to_image(prompt)
  调用文生图工具(dashscope qwen-image-2.0)。

import_cgm_report()                                            ← CGM 链路:导入
  检测本轮上传的 PDF 是否 CGM 评估报告,触发 CGMReportService.parse_and_store()。
  入参为空,内部从 self.current_file_url / self.current_file_name 取。
  返回简短中文摘要(监测时段、TIR/TAR/TBR、低血糖风险)给主 Agent。

get_latest_cgm_report()                                         ← CGM 链路:查询
  从 cgm_report 表取当前用户最新一份 success 状态的报告。
  返回 JSON 字符串(slim 版,去掉 raw_text),主 Agent 拿去塞给 cgm_interpretation_skill。
```

这些工具大多不是直接面向用户输出最终答案,而是给主 Agent 提供可整合的中间依据。**最终语气和回复节奏由安糖系统提示词 + Skill 控制**,工具只负责拿到事实。

> **CGM 三件套的串联**:用户问"我最近报告怎么样" → 主 Agent 调 `get_latest_cgm_report` 拿 JSON → 把 JSON 塞进 query 调 `cgm_interpretation_skill`(子 Agent)→ Skill 按 SKILL.md 模板生成 4 段固定结构的解读 → 主 Agent 再自然转述给用户。详见第 14-bis 节(CGM 报告链路)。

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

8. 收尾(在 finally 块里跑)
   AnTangAgent.finalize_turn(user_input=raw_input, assistant_response=response_content)
     → asyncio.create_task(_store_turn_memory_safely)   # fire-and-forget 写向量库, 3-10s
     → asyncio.create_task(_update_profile_safely)      # fire-and-forget 用 LLM 更画像, 2-5s
   保存 assistant history(hidden event 一起进 history.events)
   DialogService.update_dialog_summary()                # 累计 token 超阈值时压缩短期历史
```

> finalize_turn **不再是"空钩子"**(旧版文档的说法已过时)。两条后台任务都是 fire-and-forget,失败只打 warning 日志,不阻塞 SSE 关闭,也不影响用户看到回复。

前端接收逻辑在 `src/frontend/src/pages/conversation/chatPage/chatPage.vue`：

- `response_chunk`：累加到最后一条 AI 消息。
- `event`：更新工具/安全事件卡片。
- `error`：显示错误消息。
- `heartbeat`：忽略。

## 12. 会话、短期历史与上下文压缩

> 长期记忆系统单独成节,见第 12-bis 节。本节只讲会话级 / 短期级。

会话主表:

- `dialog`:会话 ID、绑定 Agent、Agent 类型、用户、会话摘要、summary_last_time 游标。
- `history`:用户 / 助手消息、events JSON 列表、token_usage、role(user/assistant)。

短期历史上下文分两部分:

```text
短期上下文:
  HistoryService.get_short_term_messages()
  从 dialog.summary_last_time 之后读取所有消息,转成 LangChain Message。
  这部分直接进主 Agent 的 messages 上下文。

长期压缩摘要:
  DialogService.update_dialog_summary()
  每轮对话结束时检查累计 token 数,超阈值时:
    - 取 summary_last_time 之前的所有 user/assistant pair
    - 用 LLM 生成一段摘要,写回 dialog.summary
    - 推进 summary_last_time 到当前
  下一轮对话 AnTangAgent.astream 把 dialog.summary 作为 history_summary 注入 prompt。
```

这样的设计避免了"长对话爆 context": 早期内容被滚动压缩成摘要,只保留细节在最近 N 轮。

DialogState(`current_stage / dominant_emotions / last_followup_question`)是另一条平行轨道,通过 hidden history event 落库,服务 light_analyzer 跨轮上下文(见第 9.5 节)。

## 12-bis. 安糖长期记忆系统(画像 + 向量记忆)

这是这个项目里需要单独理解的子系统。**双层架构**,代码长期处于"写好但没接通"状态,最近才完整打开:

| 层 | 存哪 | 存什么 | 用在哪 |
|---|---|---|---|
| 结构化画像 | MySQL `antang_profile` 表(1 用户 1 行) | 9 字段:`common_low_glucose_times / common_triggers / night_low_tendency / dietary_preferences / exercise_patterns / emotional_patterns / soothing_preferences / recent_risk_notes / summary` | 每轮对话开始前注入主 prompt 的"用户长期特征"段 |
| 向量记忆 | Chroma(走 mem0 client) | 每轮 user/assistant 对话的语义化片段 | 每轮按用户当前问题做语义检索,捞出相关历史片段拼到 prompt |

入口都在 `services/antang/profile.py` 的 `AnTangProfileService`,4 个方法:

```text
get_profile(user_id)                              # 读画像
recall_context(user_id, query)                    # 同时拉画像 + 向量召回,合并成 AnTangMemoryContext
store_turn_memory(user_id, user_input, response)  # 把对话写进向量库(mem0)
update_profile(user_id, user_input, response,     # 用 LLM 把"旧画像 + 本轮对话" → 新画像
              assessment_reason, ...)
```

### 读取链路(每轮对话开始时)

`AnTangAgent.astream` 第 ③ 步:启动 `recall_task = asyncio.create_task(recall_context(...))`,跟 light_analyzer **并行**跑。`recall_context` 内部:

```text
- AnTangProfileDao.get_by_user_id() 拿画像
- memory_client.search(query, user_id, agent_id, limit=5) 做向量检索
- 合并成 AnTangMemoryContext { profile, profile_summary, recalled_memories, last_memory_excerpt }
```

然后在 `_format_long_term_memory_block(ctx)` 里拼成两段文字 → 塞进 system prompt。**主 LLM 能区分"用户的稳定特征" vs "上次提过的具体事件"**。

### 写入链路(每轮对话结束时)

`AnTangAgent.finalize_turn` fire-and-forget 启动两个任务:

**任务 1: `_store_turn_memory_safely`**(3-10 秒)

```text
→ memory_client.add(messages=[user, assistant], user_id, agent_id)
  mem0 内部跑 LLM 抽事实、合并去重,写入 Chroma
```

**任务 2: `_update_profile_safely`**(2-5 秒)

```text
→ AnTangProfileService.update_profile(
     user_id, user_input, assistant_response,
     assessment_reason=self.last_analyzer_understanding,    # 来自 light_analyzer
  )
  → 拼 PROFILE_UPDATE_PROMPT(旧画像 + 本轮对话 + 上下文摘要)
  → StructuredResponseAgent(AnTangUserProfile).get_structured_response(prompt)
     [强 schema 约束,失败时返回旧画像不报错]
  → AnTangProfileDao.update_profile() 写回 antang_profile 表
```

> CGM 报告处理也会**额外触发画像更新**(`CGMReportService._sync_profile_from_report`),把"最新报告关键指标 + 当前画像"重新合成,**覆盖 `recent_risk_notes` 字段**。同步覆盖逻辑跟主对话的 update 走同一个 DAO 路径。

### 接通历史

`profile.py` 头部曾有这样的注释:"当前主对话流程里暂时关闭了 finalize_turn 的画像写入。" 这段已经过时,实际上:

| 接通状态 | 接通点 |
|---|---|
| `recall_context` 读 | 已通,`AnTangAgent.astream` 第 296 行 |
| `store_turn_memory` 写 | 已通,`finalize_turn` fire-and-forget |
| `update_profile` 写 | **新通**,改了签名(`assessment: AnTangStateAssessment` → `assessment_reason: str`),通过 `self.last_analyzer_understanding` 串到 finalize_turn |

接通过程中踩过的坑:

1. `update_profile` 原签名要 `AnTangStateAssessment`,但 light_analyzer 实际输出是 `LightAnalyzerResult`。改成接 `assessment_reason: str` 字符串就行,内部只用了那个 reason 字段。
2. MySQL CLI 读出画像内容是乱码 `?` —— 实际是 cli 默认字符集问题,数据本身是 utf8mb4。查询时加 `--default-character-set=utf8mb4` 即可。
3. 第一次接通时 update_time 不变 —— 镜像 reload 没生效。`docker compose restart backend` 或 `up -d --build backend` 后才正常。

### 还没做的优化

- **节流**:目前每轮对话都跑一次 LLM 更新画像,聊得密时一小时几十次。后续可加 Redis cooldown(如 5 分钟一次)。
- **CGM 触发的画像覆盖,只覆盖 `recent_risk_notes`**:其他字段保留 LLM 从对话里学到的内容。这条策略写在 `cgm_report.py:_PROFILE_SYNC_PROMPT_TEMPLATE` 里。

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

## 14-bis. CGM 评估报告链路

合作方提供硅基仿生 CGM 设备的患者评估报告(动态葡萄糖监测,持续 5-14 天监测期 + 多种统计指标,PDF 形式)。AI 需要解析这些医学数据并把"长期支持"沉淀到画像里。

### 总体设计

```
路径 A: 专门上传板块             路径 B: 对话附件
前端 /cgm/upload 板块            主对话里上传 PDF 附件
    │                               │
    ▼                               ▼
POST /api/v1/cgm/import         主 Agent 看到 file_url
    │                               │
    │           调内置 Tool `import_cgm_report`
    │                               │
    └─────────┬─────────────────────┘
              ▼
     CGMReportService.parse_and_store
     ① 先插一行 status=pending 占位(失败也能查到)
     ② 从对象存储下载 PDF 到临时文件
     ③ pymupdf 抽纯文本(每页 page.get_text() 拼起来)
     ④ StructuredResponseAgent + CGMReportExtraction schema 抽 24 个字段
     ⑤ 类型转换(str→date / float→Decimal / 时长字符串→分钟数)
     ⑥ 更新记录 status=success + 所有字段 + raw_text
     ⑦ 同步触发 _sync_profile_from_report → 覆盖 antang_profile 的近期风险
```

**关键设计**:两条上传路径**最后汇到同一个 service**(`services/antang/cgm_report.py`)。失败兜底统一,数据格式一致,以后改 schema 改一处就行。

### 表设计哲学

`cgm_report` 表([database/models/cgm_report.py](../../src/backend/AnTang/database/models/cgm_report.py))核心字段:

```text
id / user_id / file_url / file_name / report_source / device_model / device_serial
monitoring_start_date / monitoring_end_date / monitoring_days
patient_history / patient_age / patient_gender          # 不存姓名 / 手机
target_range_low / target_range_high / tir_threshold_pct  # 因病史而异
ehba1c / mg / sd / cv / hypo_risk_level
tir_pct / tir_duration_min / tar_pct / tar_duration_min / tbr_pct / tbr_duration_min
daily_metrics  JSON                                      # 每天 LAGE/MAGE/MODD/MG/TIR/TAR/TBR/SD/CV
hourly_metrics JSON                                      # 分时段(只长报告才有)
raw_text       MEDIUMTEXT                                # PDF 纯文本备份,失败时也能在 DB 看到原文
parse_status / parse_error
INDEX (user_id, monitoring_end_date DESC)               # 取"最新一份"
INDEX (user_id, create_time DESC)                       # 历史列表
```

设计原则:

- **append-only,不去重**:每份报告独立一行,同一用户多份按 `monitoring_end_date` 取最新。**绝对不能覆盖** —— 报告是时间窗口快照,覆盖会失去趋势对比能力。
- **隐私字段不抽**:姓名 / 手机号属于隐私,LLM prompt 里明确要求不要抽。
- **目标范围以报告自己声明的为准**:妊娠糖尿病 3.5-7.8 + TIR ≥90%,常规 3.9-7.8 + TIR ≥70%。厂家会按用户病史自动选,我们不在代码里重新推。
- **raw_text 保留**:虽然占空间,但抽错时能复查;同时也是后续"重抽"或"改 prompt 重新解析"的基础。

### 工具分工

| 文件 | 类型 | 干什么 |
|---|---|---|
| `services/antang/cgm_report.py: CGMReportService` | service | ETL,纯过程式代码,**不是独立 Agent** |
| `core/agents/antang_agent.py: import_cgm_report` | Tool | 对话过程中触发解析入库,返回简短摘要给主 Agent |
| `core/agents/antang_agent.py: get_latest_cgm_report` | Tool | 主 Agent 想看报告时从 DB 取 JSON |
| `skills/cgm_interpretation/` | Skill | 拿到数据后按 SKILL.md 模板生成 4 段对话式解读 |
| `api/v1/cgm_report.py` | 路由 | 路径 A(import / list / detail / delete) |

### 为什么解析是 service 而不是独立 Agent

讨论过这个选型。ReAct Agent 的优势是**多步骤动态决策**,但 CGM 解析是确定性 ETL 流程(PDF → 文字 → 字段 → 写表),没有需要 LLM 决策的分支点。如果做成独立 Agent:

- 延迟:3-15 秒(ReAct 多步规划)vs 2-5 秒(单次结构化抽字段)
- token:5-10k(多轮工具调用)vs 2-3k(单次抽字段)
- 可预测性:LLM 可能跳步、漏字段 vs 代码流程固定

所以解析走 service,**只有"解读"这一步需要 LLM 灵活性,所以解读做成 Skill**。

### 解读 Skill 的硬约束

`skills/cgm_interpretation/SKILL.md` 严格规定解读的 4 段结构:

```
## 这段时间(YYYY/MM/DD ~ YYYY/MM/DD,N 天)总体怎么样
## 关键数值看点
## 值得关注的细节(强制扫描 daily_metrics,不允许跳过)
## 我的建议
```

`reference/` 目录放 TIR 标准 / 术语表 / 安全话术,`scripts/` 目录放三种档位(良好 / 一般 / 高风险)的输出模板。SkillAgent 只能读自己 folder 里的文件,**摸不到 DB**,所以数据必须由主 Agent 通过 query 参数塞进去。

### 折线图怎么办

CGM 报告里有大量折线图(每日血糖曲线、AGP 图谱、多日对比曲线),全是**像素图**,pymupdf 抽出来只有文字层,折线点位丢失。但**报告右侧 / 下方的明细表格是文字层**,LLM 能完整抽进 daily_metrics 和 hourly_metrics。

短期决定不做视觉模型读折线图:
- 文字明细表已覆盖主要解读需求
- 视觉模型读数值会引入幻觉(精度差 ±1-2 mmol/L,医疗场景敏感)
- 每页加 3-5 秒延迟

未来如果做,优先**AGP 图谱视觉理解**(只一张图、概括性强、长报告才有),而不是逐日折线图。

### 与画像系统的联动

`CGMReportService._sync_profile_from_report` 在写完 cgm_report 表后,**额外跑一次 LLM**:输入"当前画像 + 这份最新报告的关键数值",输出新的 `AnTangUserProfile`,**覆盖 `recent_risk_notes` 字段**(其他字段保留 LLM 从对话里学到的内容,这条策略在 `_PROFILE_SYNC_PROMPT_TEMPLATE` 里)。

这意味着每份新报告会**触发画像更新**,主对话下一轮就能基于最新报告做个性化回应。

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

路由核心(产品收敛后):

```text
/login
/register
/
  → /conversation                  # 用户主要入口
  → /conversation/chatPage
  → /configuration                 # 隐藏入口,直接 URL 访问
  → /agent                         # 隐藏入口,直接 URL 访问
  → /agent/editor                  # 隐藏入口,直接 URL 访问
  → /tool                          # 隐藏入口,直接 URL 访问
  → /model                         # 隐藏入口,直接 URL 访问
  → /model/editor                  # 隐藏入口,直接 URL 访问
  → /profile
/:catchAll(.*)
```

根路径 `/` 默认重定向到 `/conversation`。

**已经删除的路由**(后端化改造一并清掉):

```text
/mcp-server                       # MCP Server 管理页
/mcp-server/chat                  # OpenAPI → MCP 对话式生成页
/agent-skill                      # Skill 管理页 + Monaco 编辑器
```

对应删除的文件/目录:
- `pages/mcp-server/` 整个目录(含 `mcp-chat.vue` Monaco 集成页)
- `pages/agent-skill/` 整个目录
- `apis/mcp-server.ts` / `apis/agent-skill.ts` / `apis/mcp-chat.ts`
- `assets/mcp.svg` / `assets/skill.svg`
- `components/dialog/create_agent/` 整个目录(孤儿,改造时顺手清掉)

`agent-editor.vue` **保留**,但内部删除了 MCP / Skill 折叠段;`agent.vue` 列表卡片上的 MCP / Skill 角标删除。`Agent` 类型仍保留 `mcp_ids` / `agent_skill_ids` 字段(后端透传,UI 不再编辑)。

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

核心数据按职责分组(模型定义在 `database/models/`,DAO 在 `database/dao/`):

**用户**:

```text
user                  用户主表
```

**对话与 Agent**:

```text
agent                 Agent 配置(含 mcp_ids / tool_ids / agent_skill_ids JSON 字段)
dialog                会话(绑定 Agent,记录 summary 和 summary_last_time 游标)
history               每条消息 + events JSON 列表 + token_usage
message               点赞 / 点踩
```

**长期记忆**:

```text
antang_profile        结构化用户画像,1 用户 1 行,9 个语义字段
memory_history        mem0 变更记录(向量库具体内容在 Chroma)
```

**能力底层**:

```text
tool                  内置工具配置 + OpenAPI 工具 schema(目前只读)
llm                   模型连接配置(目前只读)
agent_skill           Skill 内容(folder JSON),由 skills/ 目录 seed
mcp_server            MCP 配置,由 config/mcp_server.json seed,user_id=SystemUser
mcp_agent             历史字段,运行时不再使用
mcp_user_config       历史字段(用户 MCP 密钥),已不再读写
```

**CGM 报告**(本轮新增):

```text
cgm_report            患者评估报告,多份 append-only,JSON 字段含 daily_metrics + hourly_metrics
```

**知识库底层**:

```text
knowledge             知识库元信息(实际只有 SystemUser 的"安糖默认知识库"一行)
knowledge_file        每份 PDF 的导入状态(向量本体在 Chroma)
```

**已废弃但表仍在 MySQL 里**(没做 migration,代码不再读写):

```text
register_mcp          原 OpenAPI → MCP 注册主表
register_mcp_tool     原 OpenAPI → MCP 工具元信息
register_task         原 HITL 审批任务
mcp_user_config       用户填 MCP 密钥(改 docker_config.yaml 后弃用)
```

要彻底清理 4 张空表可以执行:

```sql
DROP TABLE register_mcp, register_task, register_mcp_tool, mcp_user_config;
```

不影响运行。

## 19. MCP 与工具体系

项目里的工具来源(产品收敛后)只剩两类:

```text
1. 平台内置工具
   tools/*
   例如天气、联网搜索、文生图、Arxiv、文档转换、邮件等;
   AnTangAgent 在 _build_antang_tools() 里追加专属工具(图片理解、糖尿病知识库、
   饮食/运动建议、CGM 导入/查询、文生图等)。

2. 系统 MCP Server (后端开发者维护)
   config/mcp_server.json   ← 列出所有系统 MCP(高德地图 / 必应搜索 / 飞书)
   services/mcp/manager.py  ← runtime MCPManager (跟远端通信)
   core/agents/mcp_agent.py ← 每个 MCP Server 包成一个 MCPAgent 子 Agent
   docker_config.yaml: mcp_credentials.<server_name>   ← 密钥统一在这里填
```

两类工具最终都通过 `general_agent.py` 的 `setup_tools()` + `setup_mcp_agent_as_tools()` **统一抽象为 LangChain `BaseTool`** 塞给主 Agent(详见第 8 节 Sub-Agent as Tool)。

### MCP 凭证流转

旧版每个用户在前端给 MCP 填密钥(`MCPUserConfigTable`),`MCPAgent` 在 tool 调用前从 DB 取。**这条路径已经废弃**:

- 改成 `app_settings.mcp_credentials.<server_name>` 从配置文件读
- 每个 MCP Server **整个平台共用一套凭证**(比如全平台用一个飞书账号),不再 per-user
- `MCPUserConfigTable` 表保留但代码不读不写

`MCPAgent` 在调工具时 `request.tool_call["args"].update(app_settings.mcp_credentials.get(self.mcp_config.server_name, {}))` 把密钥合并到工具参数里。

### 已经删除的部分

```text
mcp_proxy/                       OpenAPI 转 MCP 协议代理(包括 json_rpc / agent / session)
api/mcp_proxy/                   MCP Proxy 的 SSE + Streamable HTTP 路由
api/v1/register_mcp*.py          OpenAPI 上传 → 对话生成 MCP → HITL 审批的整条链路
prompts/register_mcp.py
database/models/register_*.py    register_mcp / register_task / register_mcp_tool 三张表的 model
database/dao/register_*.py
schemas/register_mcp.py
前端 mcp-server / mcp-chat 整套
api/v1/agent_skill.py + mcp_server.py + mcp_user_config.py (路由文件保留但不挂载,
                                                            便于未来回滚或恢复用户管理形态)
```

这次清理减少了 ~3500 行代码 + 一类合规风险(用户上传任意 OpenAPI 可能被用来打外网)。

### 旁置:`mcp_servers/` 目录

`src/backend/AnTang/mcp_servers/` 里有 lark_mcp / weather / arxiv 三个**独立可运行**的 MCP Server 示例,**跟运行时无关**,只是参考代码,以后想自建 MCP Server 时可以照着改。

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

产品侧(安糖心语对外的能力):

```text
安糖心语 Agent(主对话 + ReAct 工具调度)
安糖会话 UI (前端唯一保留的产品入口)
血糖上下文输入(用户填血糖数值 / 趋势 / 时间)
图片上传与理解(qwen-vl 视觉分析)
CGM 评估报告上传与解读(专门板块 + 对话附件两条路径)
内置糖尿病知识库检索(用户不可见,主 Agent 调用)
长期画像 + 向量记忆(自动维护,用户不可见)
饮食 / 运动 / 天气 / 搜索 / 时间 等辅助能力
```

平台底座(后端开发者维护,前端不暴露):

```text
GeneralAgent / AnTangAgent / SkillAgent / MCPAgent 类
"Sub-Agent as Tool" 统一抽象
LangChain ReAct + LangGraph 流式编排
轻量分析器 + DialogState 跨轮滚动
长期画像 LLM 维护链路
mem0 + Chroma 向量记忆
系统 MCP / Skill 文件系统 seed 机制
CGM 报告 ETL + 解读 Skill 模板
RAG 底座(可替换)
Storage / SSE / JWT 鉴权
```

被改为"后端开发者维护"的能力(原本暴露给用户):

```text
MCP Server: config/mcp_server.json + docker_config.yaml: mcp_credentials
Agent Skill: skills/<name>/ 目录 + SKILL.md
Tool / LLM: 仍可通过隐藏 URL 编辑,但前端导航不暴露
Knowledge: data/antang_knowledge_pdfs/ + 默认知识库唯一可见
```

## 23. 当前架构的几个重要判断

1. **安糖不是一个完全独立的后端服务**。它复用了底座的 Agent、工具、模型、历史、存储、SSE 等基础设施,只在 `AnTangAgent` 和 `services/antang/*` 中放置产品差异化逻辑。

2. **知识库是系统资料源,不是用户能力**。`knowledge` / `knowledge_file` 表仍存在,但只服务于安糖默认知识库。

3. **GeneralAgent 和 AnTangAgent 的边界清晰**。GeneralAgent 负责通用能力编排(模型 / 工具 / Skill / MCP),AnTangAgent 继承后追加情境分析、长期记忆、CGM、专属工具等安糖侧逻辑。

4. **"Sub-Agent as Tool"是核心抽象**。MCP / Skill / 本地工具被**统一收成 `BaseTool`**,主 Agent 的 ReAct 循环不需要分支处理。这条抽象大大降低了"加一个新能力"的成本:加 Tool 就写一个 `@tool` 闭包;加 Skill 就建一个 markdown 目录 + 重启;加 MCP 就改 JSON 配置。

5. **语境理解走轻量分析器,不再依赖关键词硬匹配**。`policies.py` 现在只剩 `classify_glucose_zone()` 这一条确定性规则处理客观生理边界。情绪 / 语境 / 引用判断全部交给 `light_analyzer`,失败有 fallback。

6. **长期记忆双层(画像 + 向量)**。`update_profile` 和 `store_turn_memory` 已经接通,在 `finalize_turn` 里 fire-and-forget;`recall_context` 在每轮对话开始时并行召回。CGM 报告会额外触发画像同步,覆盖 `recent_risk_notes`。

7. **MCP / Skill 完全后端化**是产品方向收敛后的关键决策。前端只剩聊天,所有"创建 / 编辑"路径下线;系统能力通过文件 + 启动 seed 维护,降低代码维护面 + 消除一类合规风险。

8. **RAG 工程基础可替换**。安糖侧只依赖 `retrieve_diabetes_knowledge(query)` 工具契约和 `AnTangCapabilityService.retrieve_knowledge()`。后续替换 RAG 时,理想情况下只需替换 `services/rag/*` 内部,不必改前端和 Agent 工具契约。

## 24. 后续最值得维护的扩展点

加新能力的"标准路径":

| 想加什么 | 改哪里 | 重启 | 用户感知 |
|---|---|---|---|
| 新内置工具(查股价、查日程……) | `antang_agent.py: _build_antang_tools` 追加 `@tool` 闭包 + 更新 `tool_metadata_map` | 要 | 主 Agent 按需调 |
| 新 Skill(专项任务模板) | `skills/<new_skill>/` 目录 + SKILL.md frontmatter | 要,`refresh_system_skills_on_startup: true` 时覆盖 | 主 Agent 按 description 自动调 |
| 新 MCP Server | `config/mcp_server.json` 加一条 + `docker_config.yaml: mcp_credentials` 加密钥 | 要 | 主 Agent 按需调 |
| 新体检报告类型(尿酸 / 血脂……) | 复用 CGM 那套五件套(model + DAO + schema + service + 解读 Skill) | 要 | 上传 / 对话两条路径 |
| 画像新字段 | `AnTangUserProfile` 加字段 + `PROFILE_UPDATE_PROMPT` 告诉 LLM 什么时候写 | 要 | 自动维护 |
| 修主对话 prompt | `services/antang/prompts.py: DEFAULT_ANTANG_SYSTEM_PROMPT` | 要(`_ensure_antang_agent` 启动时同步到 DB) | 立即生效 |

具体扩展点文件:

```text
安糖 Agent 体验:
  src/backend/AnTang/core/agents/antang_agent.py
  src/backend/AnTang/services/antang/prompts.py
  src/backend/AnTang/services/antang/policies.py
  src/backend/AnTang/services/antang/capabilities.py
  src/backend/AnTang/services/antang/light_analyzer.py

长期记忆 / CGM:
  src/backend/AnTang/services/antang/profile.py
  src/backend/AnTang/services/antang/cgm_report.py
  src/backend/AnTang/database/models/antang_profile.py
  src/backend/AnTang/database/models/cgm_report.py
  src/backend/AnTang/skills/cgm_interpretation/

知识库 / RAG:
  src/backend/AnTang/services/antang/knowledge.py
  src/backend/AnTang/services/rag/*
  data/antang_knowledge_pdfs/
  data/vector_db/

对话 UI:
  src/frontend/src/pages/conversation/chatPage/chatPage.vue
  src/frontend/src/apis/chat.ts
  src/frontend/src/store/history_chat_msg/index.ts

平台工具与 MCP:
  src/backend/AnTang/tools/*
  src/backend/AnTang/services/mcp/manager.py
  src/backend/AnTang/core/agents/mcp_agent.py
  src/backend/AnTang/config/mcp_server.json
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

当前项目是一个"安糖心语产品层 + 通用智能体底座"的混合架构,**核心抽象是 Sub-Agent as Tool**:前端只剩 Vue 聊天界面承载产品体验;后端 FastAPI 走 SSE 流式对话,LangChain + LangGraph 编排 ReAct + 工具/MCP/Skill 三类能力统一抽象;MySQL 持久化对话历史、长期画像、CGM 报告等结构化数据,Chroma 通过 mem0 承担对话向量记忆,辅以 Redis、MinIO 等基础设施。安糖的差异化集中在 `AnTangAgent`、`services/antang/*`、`skills/cgm_interpretation/` 和内置系统知识库上;系统 MCP / Skill 由文件系统 → 启动时 seed 进数据库,前端管理界面已经下线,产品形态收敛为"聊天为主,管理后端化"。

---

> **阅读建议**:这份文档面向想从零接手项目的工程师。对应的更"讲故事式"版本见 `项目理解指南.md`(同目录),那里把"为什么这么设计"展开讲;本文档则以**模块边界 + 数据流 + 表结构 + 文件路径**为主,适合查阅。
