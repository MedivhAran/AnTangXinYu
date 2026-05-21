# 系统 Skill 目录

后端开发者把 Skill 文件放在这里，启动时由 `_init_system_skills()`（见 `database/init_data.py`）自动扫描并 seed 进 MySQL 的 `agent_skill` 表，`user_id = SystemUser ("0")`。运行时 `SkillAgent` 仍然从数据库的 `folder` JSON 字段加载，磁盘文件只在启动同步时被读取。

## 目录约定

每个子目录就是一个 Skill：

```
skills/
├── README.md                    ← 本文件，不会被同步
└── <skill_name>/                ← 必须是 snake_case 英文
    ├── SKILL.md                 ← 必需，含 frontmatter
    ├── reference/               ← 可选，存静态参考资料
    │   └── *.md / *.txt
    └── scripts/                 ← 可选，存流程化分步指令
        └── *.md / *.txt
```

约束：

- `skill_name` 必须是 snake_case 英文，自动派生 `as_tool_name = <skill_name>_skill`（满足 schema 约束：必须以 `skill` 结尾）。
- `SKILL.md` 是必需文件，缺失则该 Skill 会被跳过并打 warn 日志。
- 只识别 `reference/` 和 `scripts/` 两个子目录的内容，其它子目录会被忽略。
- 只识别 `.md` 和 `.txt` 后缀文件。

## SKILL.md 格式

必须含 YAML frontmatter：

```markdown
---
name: CBT 对话技能
description: 用 CBT 方法引导用户应对低血糖恐惧情绪
---

# 你的任务
（这部分会作为 SkillAgent 的 system prompt 主体）

# 工作流程
1. ...
2. 必要时读取 /<skill_name>/scripts/xxx.md
3. ...
```

frontmatter 字段：

- `name`：显示名，写入 `agent_skill.name`（主 Agent 在工具列表上看到的就是这个）。可以是中文。
- `description`：写入 `agent_skill.description`，作为这个 Skill 当 tool 时的工具描述。给主 Agent 看，要清晰说明"什么时候应该调用我"。

## 文件路径在 SkillAgent 里长什么样

启动 seed 时，磁盘路径 `skills/cbt_dialogue/reference/protocol.md` 会被转成 JSON 里的 `path = "/cbt_dialogue/reference/protocol.md"`。SkillAgent 内部 `get_file_content(file_path)` 工具就用这种路径。SKILL.md 引用其它文件时按这个格式写。

## 同步策略

- `bootstrap.init_system_skills = true`（默认）：首次启动扫描目录，缺哪个建哪个。
- `bootstrap.refresh_system_skills_on_startup = true`：每次启动强制覆盖已存在的同名 Skill（开发调试用，正式部署关掉避免无脑覆盖手工 DB 改动）。
- 同名匹配：按 frontmatter 里的 `name` 字段 + `user_id = SystemUser` 查询。改 `name` 等同于新建一个 Skill。

## 绑定到 AnTang Agent

`_ensure_antang_agent()` 启动时会把所有 SystemUser 的 Skill ID 自动写进安糖心语 Agent 的 `agent_skill_ids`，不需要手工绑。新增/删除 Skill 后重启即可生效。
