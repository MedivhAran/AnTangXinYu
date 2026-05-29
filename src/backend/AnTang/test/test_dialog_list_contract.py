import unittest
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from AnTang.api.v1 import dialog as dialog_api
from AnTang.database.dao import dialog as dialog_dao_module
from AnTang.services.antang.policies import ANTANG_AGENT_TYPE


class _CaptureSession:
    def __init__(self):
        self.statement = None
        self.committed = False

    async def exec(self, statement):
        self.statement = statement

    async def commit(self):
        self.committed = True


class DialogListContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_dialog_list_preserves_dialog_fields_and_exposes_agent_metadata(self):
        dialog_a = {
            "dialog_id": "dialog-a",
            "agent_type": ANTANG_AGENT_TYPE,
            "name": "早餐记录",
            "create_time": "2026-04-20T10:00:00+08:00",
            "update_time": "2026-04-20T10:05:00+08:00",
        }
        dialog_b = {
            "dialog_id": "dialog-b",
            "agent_type": ANTANG_AGENT_TYPE,
            "name": "午餐复盘",
            "create_time": "2026-04-20T11:00:00+08:00",
            "update_time": "2026-04-20T11:06:00+08:00",
        }
        other_dialog = {
            "dialog_id": "dialog-c",
            "agent_type": "Agent",
            "name": "普通会话",
            "create_time": "2026-04-20T12:00:00+08:00",
            "update_time": "2026-04-20T12:01:00+08:00",
        }
        antang_agent = {
            "id": "agent-antang",
            "name": "安糖心语",
            "logo_url": "https://example.com/antang.png",
            "create_time": "2026-04-19T22:00:00+08:00",
            "update_time": "2026-04-19T22:30:00+08:00",
        }

        with patch.object(
            dialog_api.DialogService,
            "get_list_dialog",
            AsyncMock(return_value=[dialog_a, dialog_b, other_dialog]),
        ), patch.object(
            dialog_api.AgentService,
            "get_antang_agent",
            AsyncMock(return_value=antang_agent),
        ) as antang_agent_mock:
            response = await dialog_api.get_dialog(
                login_user=SimpleNamespace(user_id="user-1")
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(antang_agent_mock.await_count, 1)

        first, second = response.data
        self.assertEqual(first["name"], "早餐记录")
        self.assertEqual(first["create_time"], dialog_a["create_time"])
        self.assertEqual(first["update_time"], dialog_a["update_time"])
        self.assertEqual(first["last_active_time"], dialog_a["update_time"])
        self.assertEqual(first["agent_name"], "安糖心语")
        self.assertEqual(first["agent_logo_url"], "https://example.com/antang.png")

        self.assertEqual(second["name"], "午餐复盘")
        self.assertEqual(second["create_time"], dialog_b["create_time"])
        self.assertEqual(second["update_time"], dialog_b["update_time"])
        self.assertEqual(second["last_active_time"], dialog_b["update_time"])
        self.assertEqual(second["agent_name"], "安糖心语")
        self.assertEqual(second["agent_logo_url"], "https://example.com/antang.png")

    async def test_touch_dialog_last_active_only_updates_update_time(self):
        session = _CaptureSession()
        fixed_now = datetime(2026, 4, 20, 20, 19, 0)

        @asynccontextmanager
        async def fake_async_session_getter():
            yield session

        with patch.object(dialog_dao_module, "_get_dialog_now", return_value=fixed_now), patch.object(
            dialog_dao_module,
            "async_session_getter",
            fake_async_session_getter,
        ):
            await dialog_dao_module.DialogDao.touch_dialog_last_active("dialog-1")

        self.assertTrue(session.committed)
        self.assertIsNotNone(session.statement)

        value_columns = {column.key for column in session.statement._values.keys()}
        self.assertEqual(value_columns, {"update_time"})

        bind_parameter = next(iter(session.statement._values.values()))
        self.assertEqual(bind_parameter.value, fixed_now)


if __name__ == "__main__":
    unittest.main()
