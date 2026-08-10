"""track push installation revision

Revision ID: e72b4f9a13c6
Revises: d61f9a2c4e70
Create Date: 2026-07-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e72b4f9a13c6"
down_revision: Union[str, Sequence[str], None] = "d61f9a2c4e70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "push_installations",
        sa.Column(
            "registration_revision",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_push_installations_registration_revision",
        "push_installations",
        "registration_revision >= 1",
    )
    op.add_column(
        "push_deliveries",
        sa.Column(
            "installation_revision",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_push_deliveries_installation_revision",
        "push_deliveries",
        "installation_revision >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_push_deliveries_installation_revision",
        "push_deliveries",
        type_="check",
    )
    op.drop_column("push_deliveries", "installation_revision")
    op.drop_constraint(
        "ck_push_installations_registration_revision",
        "push_installations",
        type_="check",
    )
    op.drop_column("push_installations", "registration_revision")
