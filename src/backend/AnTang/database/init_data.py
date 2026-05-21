import json
import re
import asyncio
from pathlib import Path

import httpx
import yaml
import aiofiles
from sqlalchemy import text
from loguru import logger
from sqlmodel import SQLModel

from AnTang.database import engine, SystemUser, ensure_mysql_database, AgentTable, ToolTable, AgentSkill
from AnTang.api.services.agent import AgentService
from AnTang.api.services.llm import LLMService
from AnTang.api.services.tool import ToolService
from AnTang.api.services.mcp_server import MCPService
from AnTang.database.dao.agent import AgentDao
from AnTang.database.dao.mcp_server import MCPServerDao
from AnTang.database.dao.agent_skill import AgentSkillDao
from AnTang.prompts.mcp import McpAsToolPrompt
from AnTang.schemas.agent_skill import AgentSkillFile, AgentSkillFolder
from AnTang.schemas.mcp import MCPResponseFormat
from AnTang.services.mcp.manager import MCPManager
from AnTang.services.antang.policies import ANTANG_AGENT_NAME
from AnTang.services.antang.prompts import DEFAULT_ANTANG_SYSTEM_PROMPT
from AnTang.services.storage import storage_client
from AnTang.settings import app_settings
from AnTang.utils.convert import convert_mcp_config
from AnTang.core.agents.structured_response_agent import StructuredResponseAgent
from AnTang.utils.helpers import get_provider_from_model

# 用 __file__ 推算路径，与运行时 cwd 解耦。
# init_data.py 位于 AnTang/database/，config / skills 都是 AnTang 包下的兄弟目录。
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _PACKAGE_ROOT / "config"
_SKILLS_DIR = _PACKAGE_ROOT / "skills"
_SKILL_FILE_SUFFIXES = (".md", ".txt")
_SKILL_FOLDER_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_SKILL_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


async def init_agentchat_system():
    """
    agentchat 启动入口（推荐用于每次服务启动）

    功能：
    - 初始化数据库（幂等）
    - 检查系统是否已初始化
    - 自动选择：
        - 未初始化 → 全量初始化
        - 已初始化 → 增量更新（LLM + MCP）
    """
    await init_database()

    try:
        agents = await AgentService.get_agent()
    except Exception as err:
        logger.error(f"agentchat init bootstrap failed: {err}")
        return

    if not agents:
        logger.info("First-time setup: initializing agentchat system...")

        first_time_steps = []
        if app_settings.bootstrap.init_default_tools:
            first_time_steps.append(("default tools", _init_default_tools))
        else:
            logger.info("Default tools bootstrap disabled by config")

        first_time_steps.extend(
            [
                ("default llms", _init_default_llms),
                ("default avatars", upload_user_avatars_storage),
            ]
        )

        if app_settings.bootstrap.init_system_mcp:
            first_time_steps.append(("system mcp", _init_system_mcp_server))
        else:
            logger.info("System MCP bootstrap disabled by config")

        if app_settings.bootstrap.init_system_skills:
            first_time_steps.append(("system skills", _init_system_skills))
        else:
            logger.info("System skills bootstrap disabled by config")

        # 安糖心语必须放最后：它会把当前 SystemUser 的 MCP / Skill ID 全部绑进 agent。
        first_time_steps.append(("安糖心语", _ensure_antang_agent))

        for step_name, step in first_time_steps:
            try:
                await step()
            except Exception as err:
                logger.error(f"Init {step_name} failed: {err}")

        logger.success("Initialized agentchat successfully")
        return

    logger.info(f"Existing system detected ({len(agents)} agents), updating config...")

    update_steps = [("existing llm", _update_exist_llm)]

    if app_settings.bootstrap.init_system_mcp:
        update_steps.append(
            (
                "mcp server",
                lambda: _update_mcp_server_into_mysql(
                    refresh_existing=app_settings.bootstrap.refresh_system_mcp_on_startup
                ),
            )
        )
    else:
        logger.info("System MCP bootstrap disabled by config")

    # 系统 Skill 增量同步：新增的 Skill 总是会被建出来，是否覆盖现有同名 Skill 由
    # refresh_system_skills_on_startup 控制（在 _init_system_skills 内部决定）。
    if app_settings.bootstrap.init_system_skills:
        update_steps.append(("system skills", _init_system_skills))

    # 同首次初始化：安糖心语放最后，吃到最新的 MCP / Skill 列表。
    update_steps.append(("安糖心语", _ensure_antang_agent))

    for step_name, step in update_steps:
        try:
            await step()
        except Exception as err:
            logger.error(f"Update {step_name} failed: {err}")

    logger.success("agentchat runtime ready")


async def init_database():
    """
    初始化数据库：
    - 创建数据库（如果不存在）
    - 创建所有表结构
    """
    try:
        ensure_mysql_database()
        SQLModel.metadata.create_all(engine)
        _ensure_agent_table_schema()
        logger.success("MySQL tables are ready")
    except Exception as err:
        logger.error(f"Create MySQL Table Error: {err}")


def _ensure_agent_table_schema():
    """
    对已有数据库做最小必要修复。
    当前主要保证 agent.system_prompt 至少为 TEXT，避免系统级长提示词插入失败。
    """
    database_name = engine.url.database
    if not database_name:
        return

    with engine.begin() as conn:
        result = conn.execute(
            text("""
                SELECT DATA_TYPE
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = :schema
                  AND TABLE_NAME = 'agent'
                  AND COLUMN_NAME = 'system_prompt'
                """),
            {"schema": database_name},
        ).fetchone()

        if not result:
            return

        data_type = (result[0] or "").lower()
        if data_type in {"text", "mediumtext", "longtext"}:
            return

        conn.execute(text("ALTER TABLE agent MODIFY COLUMN system_prompt TEXT NOT NULL"))
        logger.info("Adjusted agent.system_prompt column to TEXT")


async def load_json(path: str):
    """
    异步读取 JSON 文件（避免阻塞事件循环）

    Args:
        path: 文件路径

    Returns:
        dict/list: JSON 数据
    """
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        return json.loads(await f.read())


async def _init_default_tools():
    """初始化默认工具"""
    tools = await load_json(str(_CONFIG_DIR / "tool.json"))

    await asyncio.gather(
        *[
            ToolService.create_default_tool(ToolTable(**tool, user_id=SystemUser, is_user_defined=False))
            for tool in tools
        ]
    )

    logger.success("Default tools initialized")


async def _update_exist_llm():
    """
    更新已存在的 LLM 配置

    逻辑：
    - 获取当前系统已有 LLM
    - 对比配置（model / base_url / api_key）
    - 若无变化 → 跳过
    - 若 API Key 是掩码（包含 **）→ 跳过（防止覆盖真实 key）
    """
    settings = app_settings.multi_models.conversation_model

    api_key = settings.api_key
    base_url = settings.base_url
    model = settings.model_name
    provider = get_provider_from_model(model)

    llm = await LLMService.select_first_llm()

    if not llm:
        # 如果数据库还没有 LLM，直接初始化
        await _init_default_llms()
        return

    # 是否需要更新
    needs_update = not (llm.base_url == base_url and llm.model == model and llm.api_key == api_key)

    if not needs_update:
        logger.info("LLM config unchanged, skip update")
        return

    # 防止用掩码覆盖真实 key
    if api_key and "**" in api_key:
        logger.warning("Masked API key detected, skip update")
        return

    await LLMService.update_first_llm(
        llm_id=llm.llm_id,
        model=model,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
    )

    logger.success("LLM config updated")


async def _init_default_llms():
    """初始化默认 LLM"""
    settings = app_settings.multi_models.conversation_model

    await LLMService.create_llm(
        user_id=SystemUser,
        model=settings.model_name,
        llm_type="LLM",
        api_key=settings.api_key,
        base_url=settings.base_url,
        provider=get_provider_from_model(settings.model_name),
    )

    logger.success("Default LLM initialized")


async def _ensure_antang_agent():
    """确保系统级安糖心语 Agent 存在且配置符合 Phase 1 约束。

    MCP / Skill 列表从数据库实时拉取所有 SystemUser 的记录，这样：
    - 新增系统 MCP 或 Skill 后重启服务，安糖心语自动绑定；
    - 用户私有的 MCP/Skill 不会被误绑（前端入口本来就已经禁用）。
    """
    llm = await LLMService.get_one_llm()

    system_mcp_ids = [s.mcp_server_id for s in await MCPServerDao.get_mcp_servers_from_user(SystemUser)]
    system_skill_ids = [s.id for s in await AgentSkillDao.get_agent_skills(SystemUser)]

    desired_fields = {
        "name": ANTANG_AGENT_NAME,
        "description": "聚焦低血糖风险识别、情绪支持与安全提醒的专用陪伴智能体",
        "logo_url": app_settings.default_config.get("agent_logo_url"),
        "user_id": SystemUser,
        "is_custom": False,
        "system_prompt": DEFAULT_ANTANG_SYSTEM_PROMPT,
        "llm_id": llm.get("llm_id") if llm else "",
        "mcp_ids": system_mcp_ids,
        "tool_ids": [],
        "agent_skill_ids": system_skill_ids,
    }

    existing_agent = await AgentDao.select_agent_by_name(ANTANG_AGENT_NAME)
    if existing_agent:
        update_values = {}
        for field, expected in desired_fields.items():
            if getattr(existing_agent, field) != expected:
                update_values[field] = expected

        if update_values:
            await AgentDao.update_agent_by_id(existing_agent.id, update_values)
            logger.info("Updated system agent 安糖心语")
        return

    await AgentDao.create_agent(AgentTable(**desired_fields))
    logger.success("Created system agent 安糖心语")


async def _init_system_mcp_server():
    """
    初始化/同步系统 MCP Server。

    系统 MCP 的来源是 config/mcp_server.json。这里会创建配置里新增的 MCP；
    已存在的 MCP 只有在配置发生变化或开启 refresh_system_mcp_on_startup 时才刷新，
    避免每次启动都连接外部 MCP 和调用 LLM 生成元信息。
    """
    try:
        await _update_mcp_server_into_mysql(refresh_existing=app_settings.bootstrap.refresh_system_mcp_on_startup)
        logger.success("MCP servers initialized")

    except Exception as err:
        logger.error(f"MCP init failed: {err}")


async def _update_mcp_server_into_mysql(refresh_existing: bool):
    """
    将 config/mcp_server.json 中的系统 MCP 同步到数据库。

    Args:
        refresh_existing:
            True = 强制刷新已存在的系统 MCP 元信息。
            False = 只创建新增 MCP；如果配置字段变化，也会刷新对应 MCP。
    """
    servers = await load_json(str(_CONFIG_DIR / "mcp_server.json"))
    existing_servers = await MCPServerDao.get_mcp_servers_from_user(SystemUser)
    existing_by_name = {server.server_name: server for server in existing_servers}

    def config_changed(config_server: dict, db_server) -> bool:
        checks = {
            "url": config_server.get("url"),
            "type": config_server.get("type"),
            "config": config_server.get("config", {}),
            "config_enabled": config_server.get("config_enabled", False),
            "logo_url": config_server.get("logo_url"),
        }
        return any(getattr(db_server, field) != expected for field, expected in checks.items())

    servers_to_sync = []
    for server in servers:
        existing = existing_by_name.get(server["server_name"])
        if existing is None:
            servers_to_sync.append(server)
        elif refresh_existing or config_changed(server, existing):
            servers_to_sync.append(server)

    if not servers_to_sync:
        logger.info("System MCP config unchanged, skip sync")
        return

    logger.info(f"Syncing {len(servers_to_sync)} system MCP server(s)")

    servers_info = [{"type": s["type"], "url": s["url"], "server_name": s["server_name"]} for s in servers_to_sync]

    mcp_manager = MCPManager(convert_mcp_config(servers_info))
    servers_params = await mcp_manager.show_mcp_tools()

    semaphore = asyncio.Semaphore(5)

    async def build_meta(server_name, params):
        """
        构建 MCP Tool 元信息（调用 LLM）
        """
        async with semaphore:
            agent = StructuredResponseAgent(MCPResponseFormat)

            result = agent.get_structured_response(McpAsToolPrompt.format(tools_info=json.dumps(params, indent=2)))
            return server_name, params, result

    tasks = [build_meta(name, params) for name, params in servers_params.items()]

    results = await asyncio.gather(*tasks)

    for server_name, params, structured in results:
        server = next((s for s in servers_to_sync if s["server_name"] == server_name), None)
        existing = existing_by_name.get(server_name)

        tools_name = [t["name"] for t in params]
        update_data = {
            "url": server["url"],
            "type": server["type"],
            "config": server.get("config", {}),
            "config_enabled": server.get("config_enabled", False),
            "logo_url": server["logo_url"],
            "tools": tools_name,
            "params": params,
            "mcp_as_tool_name": structured.mcp_as_tool_name,
            "description": structured.description,
        }

        if existing:
            await MCPService.update_mcp_server(server_id=existing.mcp_server_id, update_data=update_data)
            logger.info(f"Updated system MCP server: {server_name}")
        else:
            await MCPService.create_mcp_server(
                server_name=server_name,
                user_id=SystemUser,
                user_name="Admin",
                url=server["url"],
                type=server["type"],
                config=server.get("config", {}),
                tools=tools_name,
                params=params,
                config_enabled=server.get("config_enabled", False),
                logo_url=server["logo_url"],
                mcp_as_tool_name=structured.mcp_as_tool_name,
                description=structured.description,
            )
            logger.info(f"Created system MCP server: {server_name}")


def _parse_skill_frontmatter(skill_md_text: str) -> tuple[str, str]:
    """从 SKILL.md 中抠出 name / description。frontmatter 缺失或字段缺失都视为错误。"""
    match = _SKILL_FRONTMATTER_RE.match(skill_md_text)
    if not match:
        raise ValueError("SKILL.md 缺少 frontmatter（--- ... ---）")

    fm = yaml.safe_load(match.group(1)) or {}
    if not isinstance(fm, dict):
        raise ValueError("SKILL.md frontmatter 必须是键值对")

    name = str(fm.get("name") or "").strip()
    description = str(fm.get("description") or "").strip()

    if not name:
        raise ValueError("SKILL.md frontmatter 缺少 name")
    if not description:
        raise ValueError("SKILL.md frontmatter 缺少 description")

    return name, description


def _build_skill_folder_json(skill_dir: Path, skill_name: str) -> dict:
    """把磁盘上一个 skill 目录展开成 AgentSkillFolder 形状的 JSON。

    只识别根目录下的 SKILL.md + reference/ + scripts/ 三件套。
    """

    def build_file(file_path: Path, mount_path: str) -> dict:
        return AgentSkillFile(
            name=file_path.name,
            path=mount_path,
            content=file_path.read_text(encoding="utf-8"),
        ).model_dump()

    def build_folder(local_dir: Path, mount_path: str) -> dict:
        items = []
        for entry in sorted(local_dir.iterdir()):
            if entry.is_file() and entry.suffix.lower() in _SKILL_FILE_SUFFIXES:
                items.append(build_file(entry, f"{mount_path}/{entry.name}"))
            elif entry.is_dir():
                items.append(build_folder(entry, f"{mount_path}/{entry.name}"))
        return AgentSkillFolder(
            name=local_dir.name,
            path=mount_path,
            folder=items,
        ).model_dump()

    root_mount = f"/{skill_name}"
    root_items: list = [build_file(skill_dir / "SKILL.md", f"{root_mount}/SKILL.md")]

    for subname in ("reference", "scripts"):
        subdir = skill_dir / subname
        if subdir.is_dir():
            root_items.append(build_folder(subdir, f"{root_mount}/{subname}"))
        else:
            # 即使为空也写空文件夹节点，跟前端创建时的初始结构对齐。
            root_items.append(AgentSkillFolder(name=subname, path=f"{root_mount}/{subname}", folder=[]).model_dump())

    return AgentSkillFolder(name=skill_name, path=root_mount, folder=root_items).model_dump()


async def _init_system_skills():
    """扫描 src/backend/AnTang/skills/ 下的子目录，幂等 seed 到 agent_skill 表。

    匹配规则：name 字段 + user_id=SystemUser。
    - 不存在 → 创建。
    - 存在 + refresh_system_skills_on_startup → 覆盖 folder/description/as_tool_name。
    - 存在 + 关 → 跳过，保留 DB 现状（开发者手工改过的不会被冲掉）。
    """
    if not _SKILLS_DIR.is_dir():
        logger.info(f"Skills dir not found at {_SKILLS_DIR}, skip system skill seeding")
        return

    refresh = app_settings.bootstrap.refresh_system_skills_on_startup
    created = refreshed = skipped = 0

    for skill_dir in sorted(_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue

        folder_name = skill_dir.name
        if not _SKILL_FOLDER_NAME_RE.match(folder_name):
            logger.warning(f"Skip skill '{folder_name}': folder name must match [a-z0-9_]+")
            continue

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            logger.warning(f"Skip skill '{folder_name}': SKILL.md not found")
            continue

        try:
            name, description = _parse_skill_frontmatter(skill_md.read_text(encoding="utf-8"))
            folder_json = _build_skill_folder_json(skill_dir, folder_name)
        except Exception as err:
            logger.warning(f"Skip skill '{folder_name}': {err}")
            continue

        as_tool_name = f"{folder_name}_skill"
        existing = await AgentSkillDao.get_agent_skill_by_name(name, SystemUser)

        if existing is None:
            await AgentSkillDao.create_agent_skill(
                AgentSkill(
                    name=name,
                    description=description,
                    user_id=SystemUser,
                    as_tool_name=as_tool_name,
                    folder=folder_json,
                )
            )
            created += 1
            logger.info(f"Created system skill: {name}")
        elif refresh:
            existing.description = description
            existing.as_tool_name = as_tool_name
            existing.folder = folder_json
            await AgentSkillDao.update_agent_skill(existing)
            refreshed += 1
            logger.info(f"Refreshed system skill: {name}")
        else:
            skipped += 1

    logger.success(f"System skills sync done (created={created}, refreshed={refreshed}, skipped={skipped})")


async def upload_user_avatars_storage():
    """上传默认用户头像到存储"""
    if storage_client.list_files_in_folder("icons/user"):
        return

    avatars = await load_json(str(_CONFIG_DIR / "avatars.json"))

    async with httpx.AsyncClient(timeout=10) as client:
        tasks = [_download_and_upload(client, url) for url in avatars["avatars"]]
        await asyncio.gather(*tasks)

    logger.success("User avatars uploaded")


async def _download_and_upload(client, url):
    """下载图片并上传到存储"""
    resp = await client.get(url)
    file_name = url.split("/")[-1]

    storage_client.upload_file(f"icons/user/{file_name}", resp.content)
