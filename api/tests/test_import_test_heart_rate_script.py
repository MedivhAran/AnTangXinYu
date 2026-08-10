import argparse
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.health_profile.wearable_service import process_wearable_import
from antang_api.models import (
    PersonalProfile,
    ProactiveCareSettings,
    ProactiveCareTask,
    User,
)
from antang_api.proactive_care.heart_rate_shadow import evaluate_heart_rate_shadow
from antang_api.proactive_care.service import record_health_import
from antang_api.schemas.health_profile import (
    WearableImportRequest,
    WearableImportResponse,
)
from scripts.import_test_heart_rate import (
    build_import_payload,
    import_test_heart_rate,
    local_api_url,
    validate_test_username,
)


def test_payload_is_fresh_complete_automatic_and_idempotent_within_slot() -> None:
    first_now = datetime(2026, 8, 10, 10, 7, 5, tzinfo=timezone.utc)
    retried_now = first_now + timedelta(minutes=7)

    first = build_import_payload(
        username="local_hr_high_acceptance",
        direction="high",
        now=first_now,
    )
    retried = build_import_payload(
        username="local_hr_high_acceptance",
        direction="high",
        now=retried_now,
    )

    assert retried == first
    assert first["record_type"] == "heart_rate"
    assert first["health_context_complete"] is True
    record = first["records"][0]
    assert record["external_record_id"].startswith("antang-local-test-heart-rate-")
    assert record["source_package"] == "com.huami.watch.hmwatchmanager"
    assert record["recording_method"] == 2
    assert len(record["data"]["samples"]) == 30
    assert {sample["beats_per_minute"] for sample in record["data"]["samples"]} == {110}
    WearableImportRequest.model_validate(first)


def test_next_slot_and_low_direction_produce_a_distinct_import() -> None:
    now = datetime(2026, 8, 10, 10, 7, tzinfo=timezone.utc)
    high = build_import_payload(
        username="local_hr_high_acceptance",
        direction="high",
        now=now,
    )
    low = build_import_payload(
        username="local_hr_low_acceptance",
        direction="low",
        now=now,
    )
    next_slot = build_import_payload(
        username="local_hr_high_acceptance",
        direction="high",
        now=now + timedelta(minutes=15),
    )

    assert low["client_sync_id"] != high["client_sync_id"]
    assert next_slot["client_sync_id"] != high["client_sync_id"]
    assert {
        sample["beats_per_minute"] for sample in low["records"][0]["data"]["samples"]
    } == {45}


def test_direction_specific_test_account_prefix_is_required() -> None:
    validate_test_username("local_hr_high_acceptance", "high")
    validate_test_username("local_hr_low_acceptance", "low")

    with pytest.raises(ValueError, match="local_hr_high_"):
        validate_test_username("normal-user", "high")
    with pytest.raises(ValueError, match="local_hr_low_"):
        validate_test_username("local_hr_high_acceptance", "low")


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.2:8000",
        "https://example.com",
        "http://user:password@localhost:8000",
        "http://localhost:8000/api",
    ],
)
def test_only_plain_local_api_origins_are_accepted(url: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        local_api_url(url)

    assert local_api_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"


def test_script_uses_login_and_normal_wearable_import_without_exposing_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(
                200,
                json={
                    "access_token": "secret-access-token",
                    "refresh_token": "secret-refresh-token",
                },
            )
        if request.url.path == "/api/v1/auth/logout":
            assert request.content == b'{"refresh_token":"secret-refresh-token"}'
            return httpx.Response(204)
        assert request.headers["authorization"] == "Bearer secret-access-token"
        if request.url.path == "/api/v1/health-profile":
            return httpx.Response(200, json={"personal_profile": {"age_years": 30}})
        if request.url.path == "/api/v1/proactive-care/settings":
            return httpx.Response(200, json={"health_events_enabled": True})
        if request.url.path == "/api/v1/health-profile/wearable-imports":
            return httpx.Response(
                201,
                json={
                    "import_id": "0198-acceptance",
                    "records_created": 1,
                    "records_unchanged": 0,
                },
            )
        raise AssertionError(f"unexpected request: {request.url.path}")

    with httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = import_test_heart_rate(
            client=client,
            username="local_hr_high_acceptance",
            password="test-password",
            direction="high",
            now=datetime(2026, 8, 10, 10, 7, tzinfo=timezone.utc),
        )

    assert result["records_created"] == 1
    assert [request.url.path for request in requests] == [
        "/api/v1/auth/login",
        "/api/v1/health-profile",
        "/api/v1/proactive-care/settings",
        "/api/v1/health-profile/wearable-imports",
        "/api/v1/auth/logout",
    ]
    assert "secret-access-token" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("age", "health_events_enabled", "message"),
    [
        (None, True, "成年年龄"),
        (17, True, "成年年龄"),
        (30, False, "开启健康关怀"),
    ],
)
def test_prerequisites_are_checked_before_import(
    age: int | None,
    health_events_enabled: bool,
    message: str,
) -> None:
    imported = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal imported
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(
                200,
                json={
                    "access_token": "secret",
                    "refresh_token": "refresh-secret",
                },
            )
        if request.url.path == "/api/v1/auth/logout":
            return httpx.Response(204)
        if request.url.path == "/api/v1/health-profile":
            return httpx.Response(200, json={"personal_profile": {"age_years": age}})
        if request.url.path == "/api/v1/proactive-care/settings":
            return httpx.Response(
                200,
                json={"health_events_enabled": health_events_enabled},
            )
        if request.url.path == "/api/v1/health-profile/wearable-imports":
            imported = True
            return httpx.Response(201, json={})
        raise AssertionError(f"unexpected request: {request.url.path}")

    with httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(RuntimeError, match=message):
            import_test_heart_rate(
                client=client,
                username="local_hr_high_acceptance",
                password="test-password",
                direction="high",
                now=datetime(2026, 8, 10, 10, 7, tzinfo=timezone.utc),
            )

    assert imported is False


async def test_generated_payload_reaches_a_candidate_through_the_normal_services(
    db_session: AsyncSession,
) -> None:
    suffix = uuid4().hex[:10]
    username = f"local_hr_high_{suffix}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            PersonalProfile(user_id=user.id, age_years=30),
            ProactiveCareSettings(user_id=user.id, health_events_enabled=True),
        ]
    )
    await db_session.flush()

    request = WearableImportRequest.model_validate(
        build_import_payload(
            username=username,
            direction="high",
            # The fixture transaction starts before this test. Keeping the last
            # sample one slot behind avoids a boundary race with PostgreSQL now().
            now=datetime.now(timezone.utc) - timedelta(minutes=15),
        )
    )
    imported: WearableImportResponse = await process_wearable_import(
        db_session,
        user_id=user.id,
        request=request,
    )
    task_id = await record_health_import(
        db_session,
        user_id=user.id,
        import_id=imported.import_id,
    )

    assert task_id is not None
    task = await db_session.get(ProactiveCareTask, task_id)
    assert task is not None
    result = await evaluate_heart_rate_shadow(
        db_session,
        task=task,
        timezone_name="Asia/Shanghai",
    )

    assert result.decision == "candidate"
    assert result.direction == "high"
    assert result.covered_minutes == 30
