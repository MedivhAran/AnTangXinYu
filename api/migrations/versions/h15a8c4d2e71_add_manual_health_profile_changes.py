"""add manual health profile changes and interactive card answers

Revision ID: h15a8c4d2e71
Revises: g94d7b3e5f10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "h15a8c4d2e71"
down_revision: str | None = "g94d7b3e5f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("health_profile_changes", "trigger_message_id", nullable=True)
    op.alter_column("health_profile_changes", "agent_run_id", nullable=True)
    op.alter_column("health_profile_changes", "proposal_index", nullable=True)
    op.add_column(
        "health_profile_changes",
        sa.Column(
            "origin", sa.String(length=16), server_default="agent", nullable=False
        ),
    )
    op.add_column(
        "health_profile_changes",
        sa.Column("clarification_reason", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "health_profile_changes",
        sa.Column("client_action_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "health_profile_changes",
        sa.Column(
            "answer_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_health_profile_changes_origin",
        "health_profile_changes",
        "origin IN ('agent', 'user')",
    )
    op.create_check_constraint(
        "ck_health_profile_changes_provenance",
        "health_profile_changes",
        "(origin = 'agent' AND trigger_message_id IS NOT NULL "
        "AND agent_run_id IS NOT NULL AND proposal_index IS NOT NULL) OR "
        "(origin = 'user' AND trigger_message_id IS NULL "
        "AND agent_run_id IS NULL AND proposal_index IS NULL "
        "AND decision_client_action_id IS NOT NULL "
        "AND mode = 'direct' AND status = 'applied')",
    )


def downgrade() -> None:
    # 旧结构强制关联 Message 和 AgentRun，无法表达页面直接修改的审计记录。
    op.execute("DELETE FROM health_profile_changes WHERE origin = 'user'")
    op.drop_constraint(
        "ck_health_profile_changes_provenance",
        "health_profile_changes",
        type_="check",
    )
    op.drop_constraint(
        "ck_health_profile_changes_origin",
        "health_profile_changes",
        type_="check",
    )
    op.drop_column("health_profile_changes", "answer_value")
    op.drop_column("health_profile_changes", "client_action_hash")
    op.drop_column("health_profile_changes", "clarification_reason")
    op.drop_column("health_profile_changes", "origin")
    op.alter_column("health_profile_changes", "proposal_index", nullable=False)
    op.alter_column("health_profile_changes", "agent_run_id", nullable=False)
    op.alter_column("health_profile_changes", "trigger_message_id", nullable=False)
