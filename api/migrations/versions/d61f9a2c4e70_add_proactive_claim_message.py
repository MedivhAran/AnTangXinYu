"""add proactive task claim message

Revision ID: d61f9a2c4e70
Revises: b8e31f2c6d90
Create Date: 2026-07-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d61f9a2c4e70"
down_revision: Union[str, Sequence[str], None] = "b8e31f2c6d90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proactive_care_tasks",
        sa.Column("claimed_through_message_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_proactive_care_tasks_claimed_message",
        "proactive_care_tasks",
        "messages",
        ["claimed_through_message_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_proactive_care_tasks_claimed_message",
        "proactive_care_tasks",
        type_="foreignkey",
    )
    op.drop_column("proactive_care_tasks", "claimed_through_message_id")
