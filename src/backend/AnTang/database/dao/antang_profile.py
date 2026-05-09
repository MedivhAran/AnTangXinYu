from typing import Any, Dict, Optional

from sqlmodel import select

from AnTang.database.models.antang_profile import AnTangProfileTable
from AnTang.database.session import session_getter


class AnTangProfileDao:
    @classmethod
    async def get_by_user_id(cls, user_id: str) -> Optional[AnTangProfileTable]:
        with session_getter() as session:
            statement = select(AnTangProfileTable).where(AnTangProfileTable.user_id == user_id)
            return session.exec(statement).first()

    @classmethod
    async def create_profile(
        cls,
        *,
        user_id: str,
        profile_summary: str,
        profile_data: Dict[str, Any],
        last_memory_excerpt: str | None = None,
    ) -> AnTangProfileTable:
        with session_getter() as session:
            profile = AnTangProfileTable(
                user_id=user_id,
                profile_summary=profile_summary,
                profile_data=profile_data,
                last_memory_excerpt=last_memory_excerpt,
            )
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile

    @classmethod
    async def update_profile(
        cls,
        *,
        user_id: str,
        profile_summary: str,
        profile_data: Dict[str, Any],
        last_memory_excerpt: str | None = None,
    ) -> AnTangProfileTable:
        with session_getter() as session:
            statement = select(AnTangProfileTable).where(AnTangProfileTable.user_id == user_id)
            profile = session.exec(statement).first()
            if not profile:
                profile = AnTangProfileTable(
                    user_id=user_id,
                    profile_summary=profile_summary,
                    profile_data=profile_data,
                    last_memory_excerpt=last_memory_excerpt,
                )
                session.add(profile)
            else:
                profile.profile_summary = profile_summary
                profile.profile_data = profile_data
                profile.last_memory_excerpt = last_memory_excerpt

            session.commit()
            session.refresh(profile)
            return profile
