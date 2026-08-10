"""add companion memory cursor

Revision ID: c43b27a54e61
Revises: a7d2f6c91b34
Create Date: 2026-07-16 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c43b27a54e61"
down_revision: Union[str, Sequence[str], None] = "a7d2f6c91b34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "companion_memory_cursors",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("through_message_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["through_message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("companion_memory_cursors")
