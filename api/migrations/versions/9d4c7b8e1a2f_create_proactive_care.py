"""create proactive care

Revision ID: 9d4c7b8e1a2f
Revises: c43b27a54e61
Create Date: 2026-07-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "9d4c7b8e1a2f"
down_revision: Union[str, Sequence[str], None] = "c43b27a54e61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    routine_cadence = sa.Enum(
        "disabled",
        "daily",
        "every_3_days",
        "weekly",
        name="routine_care_cadence",
    )
    care_plan_status = sa.Enum(
        "active",
        "completed",
        "cancelled",
        name="care_plan_status",
    )
    task_kind = sa.Enum(
        "health_event",
        "routine_check_in",
        "plan_follow_up",
        name="proactive_care_task_kind",
    )
    task_status = sa.Enum(
        "scheduled",
        "running",
        "completed",
        "skipped",
        "failed",
        "expired",
        "cancelled",
        name="proactive_care_task_status",
    )
    push_platform = sa.Enum("android", name="push_platform")
    push_permission = sa.Enum(
        "undetermined",
        "denied",
        "granted",
        name="push_permission_state",
    )
    push_delivery_status = sa.Enum(
        "pending",
        "sending",
        "ticket_accepted",
        "receipt_accepted",
        "retry_wait",
        "failed",
        "expired",
        name="push_delivery_status",
    )
    op.create_table(
        "proactive_care_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "routine_cadence",
            routine_cadence,
            server_default="disabled",
            nullable=False,
        ),
        sa.Column(
            "plan_follow_up_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "health_events_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default="Asia/Shanghai",
            nullable=False,
        ),
        sa.Column(
            "quiet_hours_start",
            sa.Time(),
            server_default="22:00:00",
            nullable=False,
        ),
        sa.Column(
            "quiet_hours_end",
            sa.Time(),
            server_default="08:00:00",
            nullable=False,
        ),
        sa.Column(
            "health_notification_preview_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.execute(
        "INSERT INTO proactive_care_settings (user_id) SELECT id FROM users"
    )
    op.create_table(
        "care_plans",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", care_plan_status, nullable=False),
        sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by_message_id", sa.Uuid(), nullable=False),
        sa.Column("last_changed_by_message_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision >= 1", name="ck_care_plans_revision"),
        sa.ForeignKeyConstraint(
            ["created_by_message_id"],
            ["messages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_changed_by_message_id"],
            ["messages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "last_changed_by_message_id",
            name="uq_care_plans_user_last_changed_message",
        ),
    )
    op.create_index("ix_care_plans_user_id", "care_plans", ["user_id"])
    op.create_index(
        "ix_care_plans_user_status_follow_up",
        "care_plans",
        ["user_id", "status", "follow_up_at"],
    )
    op.create_table(
        "proactive_care_tasks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", task_kind, nullable=False),
        sa.Column("status", task_status, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wearable_import_id", sa.Uuid(), nullable=True),
        sa.Column("care_plan_id", sa.Uuid(), nullable=True),
        sa.Column("care_plan_revision", sa.Integer(), nullable=True),
        sa.Column("health_event_key", sa.String(length=255), nullable=True),
        sa.Column("health_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("rule_id", sa.String(length=128), nullable=True),
        sa.Column("rule_version", sa.String(length=64), nullable=True),
        sa.Column("source_version_hash", sa.String(length=64), nullable=True),
        sa.Column("duplicate_of_task_id", sa.Uuid(), nullable=True),
        sa.Column("response_message_id", sa.Uuid(), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_reason", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_proactive_care_tasks_attempt_count",
        ),
        sa.CheckConstraint(
            "expires_at > due_at",
            name="ck_proactive_care_tasks_expiry",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_proactive_care_tasks_lease",
        ),
        sa.CheckConstraint(
            "(kind = 'health_event' AND wearable_import_id IS NOT NULL "
            "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
            "(kind = 'routine_check_in' AND wearable_import_id IS NULL "
            "AND care_plan_id IS NULL AND care_plan_revision IS NULL) OR "
            "(kind = 'plan_follow_up' AND wearable_import_id IS NULL "
            "AND care_plan_id IS NOT NULL AND care_plan_revision IS NOT NULL)",
            name="ck_proactive_care_tasks_source",
        ),
        sa.ForeignKeyConstraint(
            ["care_plan_id"],
            ["care_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_task_id"],
            ["proactive_care_tasks.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["response_message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["wearable_import_id"],
            ["wearable_imports.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "care_plan_id",
            "care_plan_revision",
            name="uq_proactive_care_tasks_plan_revision",
        ),
        sa.UniqueConstraint(
            "response_message_id",
            name="uq_proactive_care_tasks_response_message",
        ),
        sa.UniqueConstraint(
            "wearable_import_id",
            name="uq_proactive_care_tasks_wearable_import",
        ),
    )
    op.create_index(
        "ix_proactive_care_tasks_user_id",
        "proactive_care_tasks",
        ["user_id"],
    )
    op.create_index(
        "ix_proactive_care_tasks_status_due_at",
        "proactive_care_tasks",
        ["status", "due_at"],
    )
    op.create_index(
        "ix_proactive_care_tasks_running_lease",
        "proactive_care_tasks",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_proactive_care_tasks_running_user",
        "proactive_care_tasks",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_proactive_care_tasks_scheduled_routine_user",
        "proactive_care_tasks",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'routine_check_in' AND status = 'scheduled'"
        ),
    )
    op.create_table(
        "push_installations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("login_session_id", sa.Uuid(), nullable=False),
        sa.Column("expo_push_token", sa.String(length=255), nullable=True),
        sa.Column("permission", push_permission, nullable=False),
        sa.Column("platform", push_platform, nullable=False),
        sa.Column("app_version", sa.String(length=64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["login_session_id"],
            ["login_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("expo_push_token"),
    )
    op.create_index(
        "ix_push_installations_login_session_id",
        "push_installations",
        ["login_session_id"],
    )
    op.create_table(
        "push_deliveries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("show_message_preview", sa.Boolean(), nullable=False),
        sa.Column("status", push_delivery_status, nullable=False),
        sa.Column("provider_ticket_id", sa.String(length=255), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_error_code", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_push_deliveries_attempt_count",
        ),
        sa.CheckConstraint(
            "(status = 'sending' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'sending' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_push_deliveries_lease",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["push_installations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "installation_id",
            name="uq_push_deliveries_message_installation",
        ),
    )
    op.create_index(
        "ix_push_deliveries_installation_id",
        "push_deliveries",
        ["installation_id"],
    )
    op.create_index(
        "ix_push_deliveries_message_id",
        "push_deliveries",
        ["message_id"],
    )
    op.create_index(
        "ix_push_deliveries_status_next_attempt",
        "push_deliveries",
        ["status", "next_attempt_at"],
    )

    op.add_column(
        "agent_runs",
        sa.Column("trigger_care_task_id", sa.Uuid(), nullable=True),
    )
    op.alter_column(
        "agent_runs",
        "trigger_message_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_agent_runs_trigger_care_task_id_proactive_care_tasks",
        "agent_runs",
        "proactive_care_tasks",
        ["trigger_care_task_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_agent_runs_trigger_care_task_id",
        "agent_runs",
        ["trigger_care_task_id"],
    )
    op.create_index(
        "uq_agent_runs_active_care_task_agent",
        "agent_runs",
        ["trigger_care_task_id", "agent_name"],
        unique=True,
        postgresql_where=sa.text(
            "trigger_care_task_id IS NOT NULL "
            "AND status IN ('running', 'waiting_for_user')"
        ),
    )
    op.create_check_constraint(
        "ck_agent_runs_exactly_one_trigger",
        "agent_runs",
        "num_nonnulls(trigger_message_id, trigger_care_task_id) = 1",
    )
    op.drop_index("uq_agent_runs_active_root_user", table_name="agent_runs")
    op.create_index(
        "uq_agent_runs_active_root_user",
        "agent_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "parent_run_id IS NULL "
            "AND agent_name = 'core_agent' "
            "AND status IN ('running', 'waiting_for_user')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_runs_active_root_user", table_name="agent_runs")
    # 旧版 AgentRun 无法表示服务器任务触发。先移除这类运行，才能恢复
    # trigger_message_id 的非空约束和原来的根运行唯一索引。
    op.execute("DELETE FROM agent_runs WHERE trigger_care_task_id IS NOT NULL")
    op.create_index(
        "uq_agent_runs_active_root_user",
        "agent_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "parent_run_id IS NULL AND status IN ('running', 'waiting_for_user')"
        ),
    )
    op.drop_constraint(
        "ck_agent_runs_exactly_one_trigger",
        "agent_runs",
        type_="check",
    )
    op.drop_index(
        "uq_agent_runs_active_care_task_agent",
        table_name="agent_runs",
    )
    op.drop_index("ix_agent_runs_trigger_care_task_id", table_name="agent_runs")
    op.drop_constraint(
        "fk_agent_runs_trigger_care_task_id_proactive_care_tasks",
        "agent_runs",
        type_="foreignkey",
    )
    op.alter_column(
        "agent_runs",
        "trigger_message_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_column("agent_runs", "trigger_care_task_id")

    op.drop_index(
        "ix_push_deliveries_status_next_attempt",
        table_name="push_deliveries",
    )
    op.drop_index("ix_push_deliveries_message_id", table_name="push_deliveries")
    op.drop_index(
        "ix_push_deliveries_installation_id",
        table_name="push_deliveries",
    )
    op.drop_table("push_deliveries")
    op.drop_index(
        "ix_push_installations_login_session_id",
        table_name="push_installations",
    )
    op.drop_table("push_installations")
    op.execute("DROP TYPE push_delivery_status")
    op.execute("DROP TYPE push_permission_state")
    op.execute("DROP TYPE push_platform")

    op.drop_index(
        "uq_proactive_care_tasks_scheduled_routine_user",
        table_name="proactive_care_tasks",
    )
    op.drop_index(
        "uq_proactive_care_tasks_running_user",
        table_name="proactive_care_tasks",
    )
    op.drop_index(
        "ix_proactive_care_tasks_status_due_at",
        table_name="proactive_care_tasks",
    )
    op.drop_index(
        "ix_proactive_care_tasks_running_lease",
        table_name="proactive_care_tasks",
    )
    op.drop_index("ix_proactive_care_tasks_user_id", table_name="proactive_care_tasks")
    op.drop_table("proactive_care_tasks")
    op.execute("DROP TYPE proactive_care_task_status")
    op.execute("DROP TYPE proactive_care_task_kind")
    op.drop_index("ix_care_plans_user_status_follow_up", table_name="care_plans")
    op.drop_index("ix_care_plans_user_id", table_name="care_plans")
    op.drop_table("care_plans")
    op.execute("DROP TYPE care_plan_status")
    op.drop_table("proactive_care_settings")
    op.execute("DROP TYPE routine_care_cadence")
