from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import get_session
from antang_api.health_profile.errors import (
    HealthProfileChangedError,
    HealthProfileInvariantError,
    InvalidProfileCardDecisionError,
    InvalidProfileProposalError,
    ProfileClientActionConflictError,
    ProfileCardDecisionConflictError,
    ProfileCardNotFoundError,
    WearableImportConflictError,
)
from antang_api.health_profile.service import (
    answer_profile_card,
    apply_manual_health_profile_change,
    decide_profile_card,
    load_health_profile_snapshot,
    profile_card_options,
)
from antang_api.health_profile.wearable_service import (
    process_wearable_import,
    read_latest_wearable_observations,
)
from antang_api.proactive_care.service import record_health_import
from antang_api.models import (
    HealthProfileChange,
    ProfileChangeMode,
    ProfileChangeStatus,
    User,
)
from antang_api.routers.auth import get_current_user
from antang_api.schemas.health_profile import (
    HealthProfileChangeRequest,
    HealthProfileChangeResponse,
    HealthProfileResponse,
    ProfileCardAnswerRequest,
    ProfileCardDecisionRequest,
    ProfileCardDecisionResponse,
    ProfileCardResponse,
    ProfileCardsResponse,
    WearableImportRequest,
    WearableImportResponse,
    WearableLatestResponse,
)

router = APIRouter(prefix="/api/v1/health-profile", tags=["health-profile"])


@router.get("", response_model=HealthProfileResponse)
async def get_health_profile(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthProfileResponse:
    snapshot = await load_health_profile_snapshot(session, user.id)
    latest = await read_latest_wearable_observations(
        session,
        user_id=user.id,
    )
    return HealthProfileResponse(
        personal_profile=snapshot.personal_profile,
        health_facts=snapshot.health_facts,
        wearable_latest=[
            WearableLatestResponse(
                record_type=item.record_type,
                observed_at=item.end_time,
                data=item.data,
                source_package=item.source_package,
            )
            for item in latest
        ],
    )


@router.get("/cards", response_model=ProfileCardsResponse)
async def get_profile_cards(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> ProfileCardsResponse:
    changes = list(
        await session.scalars(
            select(HealthProfileChange)
            .where(
                HealthProfileChange.user_id == user.id,
                HealthProfileChange.status == ProfileChangeStatus.PENDING,
            )
            .order_by(HealthProfileChange.created_at, HealthProfileChange.id)
            .limit(limit)
        )
    )
    cards: list[ProfileCardResponse] = []
    for change in changes:
        if change.question is None:
            raise HealthProfileInvariantError("pending profile change has no question")
        kind = cast(
            Literal[
                ProfileChangeMode.CONFIRMATION,
                ProfileChangeMode.CLARIFICATION,
            ],
            change.mode,
        )
        options, allow_custom_input, placeholder = profile_card_options(change)
        cards.append(
            # 选项与输入方式由服务端固定规则生成。
            ProfileCardResponse(
                id=change.id,
                kind=kind,
                target_type=change.target_type,
                field_name=change.field_name,
                operation=change.operation,
                question=change.question,
                proposed_value=change.proposed_value,
                options=options,
                allow_custom_input=allow_custom_input,
                custom_input_placeholder=placeholder,
                created_at=change.created_at,
            )
        )
    return ProfileCardsResponse(cards=cards)


@router.post(
    "/changes",
    response_model=HealthProfileChangeResponse,
)
async def change_health_profile(
    request: HealthProfileChangeRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthProfileChangeResponse:
    try:
        change = await apply_manual_health_profile_change(
            session,
            user_id=user.id,
            request=request,
        )
        await session.commit()
    except HealthProfileChangedError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "health_profile_changed"},
        ) from error
    except ProfileClientActionConflictError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "client_action_id_reused"},
        ) from error
    except InvalidProfileProposalError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_health_profile_change"},
        ) from error

    if change.result_revision is None:
        raise HealthProfileInvariantError("applied profile change has no revision")
    return HealthProfileChangeResponse(
        change_id=change.id,
        status=ProfileChangeStatus.APPLIED,
        target_type=change.target_type,
        field_name=change.field_name,
        operation=change.operation,
        target_id=change.target_id,
        result_revision=change.result_revision,
    )


@router.post(
    "/cards/{card_id}/answer",
    response_model=ProfileCardDecisionResponse,
    responses={409: {"model": ProfileCardDecisionResponse}},
)
async def answer_card(
    card_id: UUID,
    request: ProfileCardAnswerRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileCardDecisionResponse | JSONResponse:
    try:
        card_status = await answer_profile_card(
            session,
            user_id=user.id,
            card_id=card_id,
            request=request,
        )
        await session.commit()
    except ProfileCardNotFoundError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="档案卡片不存在",
        ) from error
    except ProfileCardDecisionConflictError:
        await session.rollback()
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ProfileCardDecisionResponse(
                card_id=card_id,
                status=ProfileChangeStatus.CONFLICTED,
            ).model_dump(mode="json"),
        )
    except (InvalidProfileCardDecisionError, InvalidProfileProposalError) as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_profile_card_answer"},
        ) from error

    response_status = cast(
        Literal[
            ProfileChangeStatus.APPLIED,
            ProfileChangeStatus.REJECTED,
            ProfileChangeStatus.CONFLICTED,
        ],
        card_status,
    )
    response = ProfileCardDecisionResponse(card_id=card_id, status=response_status)
    if card_status is ProfileChangeStatus.CONFLICTED:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/cards/{card_id}/decision",
    response_model=ProfileCardDecisionResponse,
    responses={409: {"model": ProfileCardDecisionResponse}},
)
async def decide_card(
    card_id: UUID,
    request: ProfileCardDecisionRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileCardDecisionResponse | JSONResponse:
    try:
        card_status = await decide_profile_card(
            session,
            user_id=user.id,
            card_id=card_id,
            client_action_id=request.client_action_id,
            decision=request.decision,
        )
        await session.commit()
    except ProfileCardNotFoundError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="档案卡片不存在",
        ) from error
    except (
        ProfileCardDecisionConflictError,
        InvalidProfileCardDecisionError,
    ):
        await session.rollback()
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ProfileCardDecisionResponse(
                card_id=card_id,
                status=ProfileChangeStatus.CONFLICTED,
            ).model_dump(mode="json"),
        )

    response_status = cast(
        Literal[
            ProfileChangeStatus.APPLIED,
            ProfileChangeStatus.REJECTED,
            ProfileChangeStatus.CONFLICTED,
        ],
        card_status,
    )
    response = ProfileCardDecisionResponse(card_id=card_id, status=response_status)
    if card_status is ProfileChangeStatus.CONFLICTED:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/wearable-imports",
    response_model=WearableImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_wearable_records(
    request: WearableImportRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WearableImportResponse:
    try:
        result = await process_wearable_import(
            session,
            user_id=user.id,
            request=request,
        )
        await record_health_import(
            session,
            user_id=user.id,
            import_id=result.import_id,
        )
        await session.commit()
        return result
    except WearableImportConflictError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code},
        ) from error
