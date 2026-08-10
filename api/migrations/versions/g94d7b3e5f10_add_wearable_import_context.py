"""add wearable import context

Revision ID: g94d7b3e5f10
Revises: f83c6a1d2e49
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "g94d7b3e5f10"
down_revision: str | None = "f83c6a1d2e49"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wearable_imports",
        sa.Column(
            "record_type",
            postgresql.ENUM(name="wearable_record_type", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "wearable_imports",
        sa.Column(
            "health_context_complete",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_wearable_imports_health_context",
        "wearable_imports",
        "NOT health_context_complete OR "
        "(record_type IS NOT NULL AND record_type = 'heart_rate')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_wearable_imports_health_context",
        "wearable_imports",
        type_="check",
    )
    op.drop_column("wearable_imports", "health_context_complete")
    op.drop_column("wearable_imports", "record_type")
