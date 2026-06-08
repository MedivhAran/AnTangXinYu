"""RAG/LLM 隔离压测（SSE 对话端点）。

配合 LLM 打桩（ANTANG_LLM_STUB=sync|async）+ gunicorn worker 数（WEB_CONCURRENCY），
对比"同步阻塞 1 worker" vs "异步 N worker"的并发承载与 P99。

容器内运行（locust 已随镜像装好）：
    # Baseline: 同步阻塞 1 worker
    ANTANG_LLM_STUB=sync WEB_CONCURRENCY=1 docker compose up -d backend
    docker compose exec backend uv run locust -f AnTang/eval/locustfile.py \
        --host http://127.0.0.1:7860 -u 100 -r 10 -t 2m --headless --csv=/tmp/baseline

    # Optimized: 异步 4 worker
    ANTANG_LLM_STUB=async WEB_CONCURRENCY=4 docker compose up -d backend
    docker compose exec backend uv run locust -f AnTang/eval/locustfile.py \
        --host http://127.0.0.1:7860 -u 100 -r 10 -t 2m --headless --csv=/tmp/optimized
"""

from __future__ import annotations

from locust import HttpUser, task, between

TEST_USER = "locust_test"
TEST_EMAIL = "locust@antang.test"
TEST_PASSWORD = "antang_locust_2024"

LOGIN = "/api/v1/user/login"
REGISTER = "/api/v1/user/register"
DIALOG = "/api/v1/dialog"
COMPLETION = "/api/v1/completion"


class ChatUser(HttpUser):
    wait_time = between(1, 3)

    def _login(self) -> str:
        r = self.client.post(
            LOGIN,
            json={"user_name": TEST_USER, "user_password": TEST_PASSWORD},
            name=LOGIN,
        )
        if r.status_code == 200:
            try:
                return r.json()["data"]["access_token"]
            except Exception:
                return ""
        return ""

    def on_start(self) -> None:
        token = self._login()
        if not token:
            # 用户不存在 → 注册（并发下可能"用户名重复"，忽略）后重登
            self.client.post(
                REGISTER,
                json={"user_name": TEST_USER, "user_email": TEST_EMAIL, "user_password": TEST_PASSWORD},
                name=REGISTER,
            )
            token = self._login()
        if not token:
            raise RuntimeError("登录失败，无法继续压测")

        self.headers = {"Authorization": f"Bearer {token}"}

        # completion 需要已存在的 dialog → 建一个会话拿真实 dialog_id
        dlg = self.client.post(
            DIALOG,
            json={"name": "locust", "agent_type": "AnTangAgent"},
            headers=self.headers,
            name=DIALOG,
        )
        data = (dlg.json() or {}).get("data") or {}
        self.dialog_id = data.get("id") or data.get("dialog_id")
        if not self.dialog_id:
            raise RuntimeError(f"创建会话失败: {dlg.status_code} {dlg.text[:200]}")

    @task
    def chat(self) -> None:
        with self.client.post(
            COMPLETION,
            json={"user_input": "我最近总担心半夜低血糖怎么办", "dialog_id": self.dialog_id},
            headers=self.headers,
            catch_response=True,
            stream=True,
            name=COMPLETION,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
                return
            chunks = sum(1 for line in resp.iter_lines(decode_unicode=True) if line)
            if chunks == 0:
                resp.failure("SSE 流无输出")
