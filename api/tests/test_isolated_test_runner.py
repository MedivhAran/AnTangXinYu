import os
import re
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from antang_api.settings import settings

API_DIR = Path(__file__).resolve().parents[1]
RUNNER = API_DIR / "scripts" / "test"
FAILURE_CASE = Path("tests/runner_cases/isolated_failure.py")
LONG_RUNNING_CASE = Path("tests/runner_cases/isolated_long_running.py")


def test_runner_removes_migrated_temporary_database_after_failure() -> None:
    result = subprocess.run(
        [str(RUNNER), str(FAILURE_CASE)],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 1
    assert "ISOLATED_RUNNER_INTENTIONAL_FAILURE" in output

    match = re.search(
        r"Temporary test database: (?P<name>antang_test_[0-9a-f]+)",
        output,
    )
    assert match is not None

    database_name = match.group("name")
    source_url = urlsplit(settings.database_url)
    admin_url = urlunsplit(
        ("postgresql", source_url.netloc, "/postgres", source_url.query, "")
    )
    with psycopg.connect(admin_url) as connection:
        row = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)",
            (database_name,),
        ).fetchone()

    assert row is not None
    assert row[0] is False


def test_runner_forwards_termination_and_removes_database(
    tmp_path: Path,
) -> None:
    ready_file = tmp_path / "runner-ready"
    environment = {
        **dict(os.environ),
        "ANTANG_RUNNER_READY_FILE": str(ready_file),
    }
    process = subprocess.Popen(
        [str(RUNNER), str(LONG_RUNNING_CASE)],
        cwd=API_DIR,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.monotonic() + 30
    while (
        not ready_file.exists()
        and process.poll() is None
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)

    assert ready_file.exists()
    child_pid = int(ready_file.read_text(encoding="utf-8"))
    process.terminate()

    forwarded = True
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        forwarded = False
        os.kill(child_pid, signal.SIGKILL)
        output, _ = process.communicate(timeout=10)

    match = re.search(
        r"Temporary test database: (?P<name>antang_test_[0-9a-f]+)",
        output,
    )
    assert match is not None

    source_url = urlsplit(settings.database_url)
    admin_url = urlunsplit(
        ("postgresql", source_url.netloc, "/postgres", source_url.query, "")
    )
    with psycopg.connect(admin_url) as connection:
        row = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)",
            (match.group("name"),),
        ).fetchone()

    assert row is not None
    assert row[0] is False
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    assert forwarded, output
    assert process.returncode == 143
