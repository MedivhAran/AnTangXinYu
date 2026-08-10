import os
import time
from pathlib import Path


def test_runner_forwards_termination_to_pytest() -> None:
    ready_file = Path(os.environ["ANTANG_RUNNER_READY_FILE"])
    ready_file.write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(300)
