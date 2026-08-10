import subprocess
import sys


def test_health_profile_schema_imports_in_a_clean_process() -> None:
    """防止包级重导出再次形成 schema 与 service 的循环导入。"""

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from antang_api.schemas.health_profile import WeightData",
        ],
        check=True,
    )
