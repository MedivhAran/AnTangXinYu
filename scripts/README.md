# scripts

如果你是docker部署的就不需要看这个文件了。


仓库辅助脚本。日常开发推荐用 Docker 一键启动（见根目录 [README.md](../README.md)）；本目录的脚本主要服务于宿主机开发场景。

## start.py

宿主机一键拉起前后端：

- 后端：`uvicorn AnTang.main:app --port 7860`，工作目录 `src/backend/`
- 前端：`npm run dev`，工作目录 `src/frontend/`

依赖需自行安装好（推荐 `uv sync` 在 `src/backend/` 下完成）。脚本会查找根目录下的 `request*.txt` 作为 pip fallback，但当前项目以 `uv` + `pyproject.toml` 管理依赖，请优先用 `uv`。

```bash
# 推荐：先用 uv 装好后端依赖
cd src/backend && uv sync

# 然后从仓库根目录启动
python scripts/start.py
```

`Ctrl + C` 终止时脚本会回收前后端进程。

## fix_fastapi_jwt_auth.py

修补 `fastapi-jwt-auth` 与新版 Pydantic 之间的兼容问题。如果首次启动时遇到 JWT 相关 import 错误再跑：

```bash
python scripts/fix_fastapi_jwt_auth.py
```

Docker 部署不需要手动跑这个脚本；镜像构建时 `uv sync --frozen` 已锁定可用版本。
