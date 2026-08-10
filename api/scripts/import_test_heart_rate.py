"""Import synthetic heart-rate samples through the normal local API.

This script is deliberately outside the mobile product. It is only for a
dedicated local test account and never creates care tasks, messages, or pushes
directly; the wearable import endpoint owns the rest of that pipeline.
"""

from __future__ import annotations

import argparse
import getpass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

import httpx

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_SLOT_MINUTES = 15
_SAMPLE_COUNT = 30
_BPM = {"high": 110, "low": 45}
_USERNAME_PREFIX = {"high": "local_hr_high_", "low": "local_hr_low_"}


def local_api_url(value: str) -> str:
    """Accept only an explicit loopback HTTP(S) API URL."""

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOCAL_HOSTS:
        raise argparse.ArgumentTypeError(
            "测试心率只能写入本机 API（localhost、127.0.0.1 或 ::1）"
        )
    if parsed.username is not None or parsed.password is not None:
        raise argparse.ArgumentTypeError("API 地址不能包含用户名或密码")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("API 地址只能包含协议、主机和端口")
    return value.rstrip("/")


def slot_end(now: datetime) -> datetime:
    """Return a stable recent boundary so immediate retries reuse one payload."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    utc_now = now.astimezone(timezone.utc)
    return utc_now.replace(
        minute=utc_now.minute - utc_now.minute % _SLOT_MINUTES,
        second=0,
        microsecond=0,
    )


def validate_test_username(username: str, direction: str) -> None:
    """Keep high and low fixtures in visibly separate local test accounts."""

    prefix = _USERNAME_PREFIX.get(direction)
    if prefix is None:
        raise ValueError("direction must be high or low")
    if not username.startswith(prefix):
        raise ValueError(f"{direction} 场景账号必须以 {prefix} 开头")


def build_import_payload(
    *,
    username: str,
    direction: str,
    now: datetime,
) -> dict[str, Any]:
    """Build one deterministic, fresh Health Connect-shaped import page."""

    if direction not in _BPM:
        raise ValueError("direction must be high or low")

    end = slot_end(now)
    start = end - timedelta(minutes=_SAMPLE_COUNT - 1)
    identity = (
        f"antang-local-heart-rate:{username.casefold()}:{direction}:{end.isoformat()}"
    )
    external_suffix = uuid5(NAMESPACE_URL, identity).hex
    samples = [
        {
            "time": (start + timedelta(minutes=index)).isoformat(),
            "beats_per_minute": _BPM[direction],
        }
        for index in range(_SAMPLE_COUNT)
    ]
    return {
        "client_sync_id": str(uuid5(NAMESPACE_URL, f"{identity}:import")),
        "record_type": "heart_rate",
        "health_context_complete": True,
        "records": [
            {
                "external_record_id": f"antang-local-test-heart-rate-{external_suffix}",
                "record_type": "heart_rate",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "start_zone_offset_seconds": 0,
                "end_zone_offset_seconds": 0,
                # The production validator currently accepts only the Zepp package.
                # The external ID and device label keep this local synthetic record
                # distinguishable in the dedicated test account.
                "source_package": "com.huami.watch.hmwatchmanager",
                "recording_method": 2,
                "device": {
                    "manufacturer": "AnTang local test",
                    "model": "synthetic heart-rate input",
                },
                "source_last_modified_at": end.isoformat(),
                "data": {"samples": samples},
            }
        ],
        "deleted_record_ids": [],
    }


def _response_json(response: httpx.Response, operation: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise RuntimeError(f"{operation}失败，HTTP {response.status_code}") from error
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{operation}返回了无效响应")
    return payload


def import_test_heart_rate(
    *,
    client: httpx.Client,
    username: str,
    password: str,
    direction: str,
    now: datetime,
) -> dict[str, Any]:
    """Authenticate, check prerequisites, then call the normal import route."""

    validate_test_username(username, direction)
    auth = _response_json(
        client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        ),
        "登录",
    )
    access_token = auth.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("登录响应缺少访问凭证")
    refresh_token = auth.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise RuntimeError("登录响应缺少刷新凭证")
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        profile = _response_json(
            client.get("/api/v1/health-profile", headers=headers),
            "读取健康档案",
        )
        personal_profile = profile.get("personal_profile")
        age = (
            personal_profile.get("age_years")
            if isinstance(personal_profile, dict)
            else None
        )
        if not isinstance(age, int) or isinstance(age, bool) or age < 18:
            raise RuntimeError("专用测试账号必须先在健康档案中填写成年年龄")

        care_settings = _response_json(
            client.get("/api/v1/proactive-care/settings", headers=headers),
            "读取主动关怀设置",
        )
        if care_settings.get("health_events_enabled") is not True:
            raise RuntimeError("专用测试账号必须先在 App 中开启健康关怀")

        return _response_json(
            client.post(
                "/api/v1/health-profile/wearable-imports",
                headers=headers,
                json=build_import_payload(
                    username=username,
                    direction=direction,
                    now=now,
                ),
            ),
            "导入测试心率",
        )
    finally:
        logout = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
        )
        try:
            logout.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"退出测试登录失败，HTTP {logout.status_code}"
            ) from error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "通过本机正常手环导入链写入一段合成心率。仅限专用测试账号；"
            "它不会直接创建关怀任务、聊天消息或推送。"
        )
    )
    result.add_argument(
        "--api-url",
        type=local_api_url,
        default="http://127.0.0.1:8000",
        help="本机 API 地址（默认：http://127.0.0.1:8000）",
    )
    result.add_argument("--username", required=True, help="专用测试账号用户名")
    result.add_argument(
        "--direction",
        choices=sorted(_BPM),
        default="high",
        help="要触发的持续方向（默认：high）",
    )
    result.add_argument(
        "--confirm-test-account",
        action="store_true",
        help="确认该账号只用于本地测试，允许永久写入合成手环记录",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if not args.confirm_test_account:
        raise SystemExit(
            "请使用 --confirm-test-account 明确确认这是专用测试账号；"
            "不要向日常使用的账号写入合成健康数据。"
        )

    try:
        validate_test_username(args.username, args.direction)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    repeated_username = input(
        f"即将向本机专用测试账号 {args.username} 永久写入合成数据。"
        "请再次输入完整用户名确认："
    ).strip()
    if repeated_username != args.username:
        raise SystemExit("两次用户名不一致，已取消")

    password = getpass.getpass("专用测试账号密码：")
    try:
        with httpx.Client(
            base_url=args.api_url,
            timeout=15,
            trust_env=False,
        ) as client:
            result = import_test_heart_rate(
                client=client,
                username=args.username,
                password=password,
                direction=args.direction,
                now=datetime.now(timezone.utc),
            )
    except (httpx.HTTPError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error

    created = result.get("records_created")
    unchanged = result.get("records_unchanged")
    print(
        "本时间片请求已幂等接受。API 返回的是该请求首次处理时保存的统计："
        f"新增 {created} 条，未变化 {unchanged} 条。"
    )
    print("后续规则判断、Agent、审核、聊天消息和推送均由后台真实链路处理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
