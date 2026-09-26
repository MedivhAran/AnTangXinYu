from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import get_session
from antang_api.health_profile.service import apply_health_profile_proposals
from antang_api.health_profile.types import (
    ClarificationReason,
    PersonalProfileProposal,
)
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    HealthProfileChange,
    Message,
    MessageRole,
    MessageStatus,
    PersonalProfile,
    PersonalProfileField,
    ProfileChangeMode,
    ProfileOperation,
    ProactiveCareTask,
    User,
)
from antang_api.routers.auth import router as auth_router
from antang_api.routers.health_profile import router as health_profile_router


async def _client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(health_profile_router)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def test_registration_creates_profile_and_authenticated_profile_api(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        username = f"route_{uuid4().hex[:16]}"
        registered = await client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "correct-password"},
        )
        assert registered.status_code == 201
        auth = registered.json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}

        profile_response = await client.get(
            "/api/v1/health-profile",
            headers=headers,
        )
        assert profile_response.status_code == 200
        body = profile_response.json()
        assert body["personal_profile"]["revision"] == 0
        assert body["health_facts"] == []
        assert body["wearable_latest"] == []
        assert body["heart_rate_trend"] == []

        ids_response = await client.get(
            "/api/v1/health-profile/wearable-health-connect-ids",
            params={"record_type": "exercise"},
            headers=headers,
        )
        assert ids_response.status_code == 200
        assert ids_response.json() == {"ids": [], "next_after": None}
        unauthorized = await client.get(
            "/api/v1/health-profile/wearable-health-connect-ids",
            params={"record_type": "exercise"},
        )
        assert unauthorized.status_code == 401

        user = await db_session.get(User, auth["user"]["id"])
        assert user is not None
        assert await db_session.get(PersonalProfile, user.id) is not None


async def test_weight_clarification_card_returns_fixed_options_and_applies_jin(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        registered = await client.post(
            "/api/v1/auth/register",
            json={
                "username": f"card_route_{uuid4().hex[:12]}",
                "password": "correct-password",
            },
        )
        auth = registered.json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        user = await db_session.get(User, UUID(auth["user"]["id"]))
        assert user is not None

        message = Message(
            client_message_id=uuid4(),
            user_id=user.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="我的体重是130",
            sources=[],
        )
        db_session.add(message)
        await db_session.flush()
        run = AgentRun(
            user_id=user.id,
            trigger_message_id=message.id,
            result_message_id=None,
            parent_run_id=None,
            parent_tool_call_id=None,
            agent_name="test_health_profile_card_route",
            model="test-model",
            status=AgentRunStatus.COMPLETED,
        )
        db_session.add(run)
        await db_session.flush()
        result = await apply_health_profile_proposals(
            db_session,
            user_id=user.id,
            trigger_message_id=message.id,
            agent_run_id=run.id,
            proposals=[
                PersonalProfileProposal(
                    field_name=PersonalProfileField.WEIGHT_KG,
                    operation=ProfileOperation.SET,
                    mode=ProfileChangeMode.CLARIFICATION,
                    value=130,
                    unit=None,
                    evidence_quote="体重是130",
                    clarification_reason=ClarificationReason.MISSING_UNIT,
                )
            ],
        )
        await db_session.commit()

        response = await client.get("/api/v1/health-profile/cards", headers=headers)
        assert response.status_code == 200
        card = response.json()["cards"][0]
        assert card["proposed_value"] == {
            "source_value": 130,
            "source_unit": None,
        }
        assert [(option["id"], option["label"]) for option in card["options"]] == [
            ("kg", "130 公斤"),
            ("jin", "130 斤"),
            ("reject", "暂不写入"),
        ]
        assert card["allow_custom_input"] is True

        legacy_conflict = await client.post(
            f"/api/v1/health-profile/cards/{card['id']}/decision",
            headers=headers,
            json={"client_action_id": str(uuid4()), "decision": "accept"},
        )
        assert legacy_conflict.status_code == 409
        assert legacy_conflict.json() == {
            "card_id": card["id"],
            "status": "conflicted",
        }

        action_id = str(uuid4())
        answer = await client.post(
            f"/api/v1/health-profile/cards/{card['id']}/answer",
            headers=headers,
            json={"client_action_id": action_id, "option_id": "jin"},
        )
        assert answer.status_code == 200
        assert answer.json()["status"] == "applied"
        repeated = await client.post(
            f"/api/v1/health-profile/cards/{card['id']}/answer",
            headers=headers,
            json={"client_action_id": action_id, "option_id": "jin"},
        )
        assert repeated.json() == answer.json()

        profile = await db_session.get(PersonalProfile, user.id)
        assert profile is not None
        assert str(profile.weight_kg) == "65.00"

        stored = await db_session.get(HealthProfileChange, result.changes[0].change_id)
        assert stored is not None
        assert stored.proposed_value == {"source_value": 130, "source_unit": None}
        assert stored.answer_value == {
            "value": "65.00",
            "canonical_unit": "kg",
            "source_value": "130",
            "source_unit": "jin",
        }


async def test_manual_personal_change_is_idempotent_and_revision_checked(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        registered = await client.post(
            "/api/v1/auth/register",
            json={
                "username": f"manual_route_{uuid4().hex[:12]}",
                "password": "correct-password",
            },
        )
        auth = registered.json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        action_id = str(uuid4())
        payload = {
            "client_action_id": action_id,
            "expected_revision": 0,
            "target_type": "personal_profile",
            "field_name": "height_cm",
            "operation": "set",
            "value": 169,
        }

        first = await client.post(
            "/api/v1/health-profile/changes", headers=headers, json=payload
        )
        assert first.status_code == 200
        assert first.json()["result_revision"] == 1
        repeated = await client.post(
            "/api/v1/health-profile/changes", headers=headers, json=payload
        )
        assert repeated.json() == first.json()

        stale = await client.post(
            "/api/v1/health-profile/changes",
            headers=headers,
            json={**payload, "client_action_id": str(uuid4()), "value": 170},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "health_profile_changed"

        reused = await client.post(
            "/api/v1/health-profile/changes",
            headers=headers,
            json={**payload, "value": 170},
        )
        assert reused.status_code == 409
        assert reused.json()["detail"]["code"] == "client_action_id_reused"

        user = await db_session.get(User, UUID(auth["user"]["id"]))
        assert user is not None
        profile = await db_session.get(PersonalProfile, user.id)
        assert profile is not None
        assert str(profile.height_cm) == "169.00"
        change = await db_session.get(HealthProfileChange, first.json()["change_id"])
        assert change is not None
        assert change.origin == "user"
        assert change.trigger_message_id is None
        assert change.agent_run_id is None


async def test_wearable_import_route_rejects_payload_change_for_same_sync_id(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        registered = await client.post(
            "/api/v1/auth/register",
            json={
                "username": f"wearable_route_{uuid4().hex[:12]}",
                "password": "correct-password",
            },
        )
        headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        sync_id = str(uuid4())
        payload = {
            "client_sync_id": sync_id,
            "record_type": "heart_rate",
            "health_context_complete": True,
            "records": [],
            "deleted_record_ids": ["deleted-record"],
        }
        first = await client.post(
            "/api/v1/health-profile/wearable-imports",
            headers=headers,
            json=payload,
        )
        assert first.status_code == 201

        repeated = await client.post(
            "/api/v1/health-profile/wearable-imports",
            headers=headers,
            json=payload,
        )
        assert repeated.status_code == 201
        assert repeated.json() == first.json()
        task_ids = list(
            await db_session.scalars(
                select(ProactiveCareTask.id).where(
                    ProactiveCareTask.wearable_import_id
                    == UUID(first.json()["import_id"])
                )
            )
        )
        assert len(task_ids) == 1

        payload["deleted_record_ids"] = ["another-record"]
        conflict = await client.post(
            "/api/v1/health-profile/wearable-imports",
            headers=headers,
            json=payload,
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "client_sync_id_payload_mismatch"


async def test_card_conflict_shape_and_cross_user_access_are_stable(
    db_session: AsyncSession,
) -> None:
    async for client in _client(db_session):
        owner_auth = (
            await client.post(
                "/api/v1/auth/register",
                json={
                    "username": f"card_owner_{uuid4().hex[:10]}",
                    "password": "correct-password",
                },
            )
        ).json()
        other_auth = (
            await client.post(
                "/api/v1/auth/register",
                json={
                    "username": f"card_other_{uuid4().hex[:10]}",
                    "password": "correct-password",
                },
            )
        ).json()
        owner_headers = {"Authorization": f"Bearer {owner_auth['access_token']}"}
        other_headers = {"Authorization": f"Bearer {other_auth['access_token']}"}
        owner = await db_session.get(User, UUID(owner_auth["user"]["id"]))
        assert owner is not None

        card_ids: list[UUID] = []
        for index in range(2):
            message = Message(
                client_message_id=uuid4(),
                user_id=owner.id,
                role=MessageRole.USER,
                status=MessageStatus.COMPLETED,
                content="我的体重是130",
                sources=[],
            )
            db_session.add(message)
            await db_session.flush()
            run = AgentRun(
                user_id=owner.id,
                trigger_message_id=message.id,
                result_message_id=None,
                parent_run_id=None,
                parent_tool_call_id=None,
                agent_name=f"cross_user_card_{index}",
                model="test-model",
                status=AgentRunStatus.COMPLETED,
            )
            db_session.add(run)
            await db_session.flush()
            result = await apply_health_profile_proposals(
                db_session,
                user_id=owner.id,
                trigger_message_id=message.id,
                agent_run_id=run.id,
                proposals=[
                    PersonalProfileProposal(
                        field_name=PersonalProfileField.WEIGHT_KG,
                        operation=ProfileOperation.SET,
                        mode=ProfileChangeMode.CLARIFICATION,
                        value=130,
                        unit=None,
                        evidence_quote="体重是130",
                        clarification_reason=ClarificationReason.MISSING_UNIT,
                    )
                ],
            )
            card_ids.append(result.changes[0].change_id)
        await db_session.commit()

        hidden = await client.get("/api/v1/health-profile/cards", headers=other_headers)
        assert hidden.json()["cards"] == []
        foreign = await client.post(
            f"/api/v1/health-profile/cards/{card_ids[0]}/answer",
            headers=other_headers,
            json={"client_action_id": str(uuid4()), "option_id": "reject"},
        )
        assert foreign.status_code == 404

        action_id = str(uuid4())
        first = await client.post(
            f"/api/v1/health-profile/cards/{card_ids[0]}/answer",
            headers=owner_headers,
            json={"client_action_id": action_id, "option_id": "reject"},
        )
        assert first.status_code == 200
        conflict = await client.post(
            f"/api/v1/health-profile/cards/{card_ids[1]}/answer",
            headers=owner_headers,
            json={"client_action_id": action_id, "option_id": "reject"},
        )
        assert conflict.status_code == 409
        assert conflict.json() == {
            "card_id": str(card_ids[1]),
            "status": "conflicted",
        }

        added = await client.post(
            "/api/v1/health-profile/changes",
            headers=owner_headers,
            json={
                "client_action_id": str(uuid4()),
                "expected_revision": None,
                "target_type": "health_fact",
                "operation": "add",
                "fact_type": "allergy",
                "statement": "对青霉素过敏",
                "assertion": "present",
                "temporal_status": "current",
            },
        )
        target_id = added.json()["target_id"]
        foreign_fact = await client.post(
            "/api/v1/health-profile/changes",
            headers=other_headers,
            json={
                "client_action_id": str(uuid4()),
                "expected_revision": 1,
                "target_type": "health_fact",
                "operation": "update",
                "fact_type": "allergy",
                "target_id": target_id,
                "statement": "对花生过敏",
                "assertion": "present",
                "temporal_status": "current",
            },
        )
        assert foreign_fact.status_code == 409
        assert foreign_fact.json()["detail"]["code"] == "health_profile_changed"
