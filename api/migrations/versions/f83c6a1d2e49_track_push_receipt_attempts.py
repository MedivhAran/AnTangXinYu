"""track push receipt attempts

Revision ID: f83c6a1d2e49
Revises: e72b4f9a13c6
Create Date: 2026-07-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f83c6a1d2e49"
down_revision: Union[str, Sequence[str], None] = "e72b4f9a13c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "push_deliveries",
        sa.Column(
            "receipt_attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_push_deliveries_receipt_attempt_count",
        "push_deliveries",
        "receipt_attempt_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_push_deliveries_receipt_attempt_count",
        "push_deliveries",
        type_="check",
    )
    op.drop_column("push_deliveries", "receipt_attempt_count")
