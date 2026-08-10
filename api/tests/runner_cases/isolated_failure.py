import pytest
import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory

from antang_api.settings import settings


def test_runner_uses_a_migrated_temporary_database() -> None:
    expected_head = ScriptDirectory.from_config(
        Config("alembic.ini")
    ).get_current_head()

    with psycopg.connect(settings.langgraph_database_url) as connection:
        row = connection.execute(
            """
            SELECT current_database(), version_num
            FROM alembic_version
            """
        ).fetchone()

    assert row is not None
    database_name, migration_version = row
    assert database_name.startswith("antang_test_")
    assert migration_version == expected_head
    pytest.fail("ISOLATED_RUNNER_INTENTIONAL_FAILURE")
