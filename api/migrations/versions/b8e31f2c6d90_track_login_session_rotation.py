"""track login session rotation

Revision ID: b8e31f2c6d90
Revises: 9d4c7b8e1a2f
Create Date: 2026-07-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e31f2c6d90"
down_revision: Union[str, Sequence[str], None] = "9d4c7b8e1a2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "login_sessions",
        sa.Column("replaced_by_session_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_login_sessions_replaced_by_session_id_login_sessions",
        "login_sessions",
        "login_sessions",
        ["replaced_by_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_login_sessions_replaced_by_session_id",
        "login_sessions",
        ["replaced_by_session_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_login_sessions_replaced_by_session_id",
        "login_sessions",
        type_="unique",
    )
    op.drop_constraint(
        "fk_login_sessions_replaced_by_session_id_login_sessions",
        "login_sessions",
        type_="foreignkey",
    )
    op.drop_column("login_sessions", "replaced_by_session_id")
