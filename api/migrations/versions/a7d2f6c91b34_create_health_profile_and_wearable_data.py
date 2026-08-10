"""create health profile and wearable data

Revision ID: a7d2f6c91b34
Revises: e0a13a3bc8f8
Create Date: 2026-07-14 15:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7d2f6c91b34"
down_revision: Union[str, Sequence[str], None] = "e0a13a3bc8f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    health_fact_type = sa.Enum(
        "medical_history",
        "allergy",
        "severe_hypoglycemia_history",
        "treatment",
        name="health_fact_type",
    )
    health_fact_assertion = sa.Enum(
        "present", "absent", name="health_fact_assertion"
    )
    health_fact_temporal_status = sa.Enum(
        "current", "past", "unknown", name="health_fact_temporal_status"
    )
    health_fact_status = sa.Enum(
        "active", "retracted", name="health_fact_status"
    )
    profile_change_mode = sa.Enum(
        "direct", "confirmation", "clarification", name="profile_change_mode"
    )
    profile_change_status = sa.Enum(
        "pending",
        "applied",
        "rejected",
        "conflicted",
        "superseded",
        name="profile_change_status",
    )
    profile_target_type = sa.Enum(
        "personal_profile", "health_fact", name="profile_target_type"
    )
    profile_operation = sa.Enum(
        "set", "clear", "add", "update", "retract", name="profile_operation"
    )
    wearable_record_type = sa.Enum(
        "steps",
        "exercise",
        "distance",
        "elevation_gained",
        "weight",
        "respiratory_rate",
        "resting_heart_rate",
        "heart_rate",
        "sleep",
        "oxygen_saturation",
        name="wearable_record_type",
    )

    op.create_table(
        "personal_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sex", sa.String(length=32), nullable=True),
        sa.Column("age_years", sa.Integer(), nullable=True),
        sa.Column("age_as_of_date", sa.Date(), nullable=True),
        sa.Column("height_cm", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("weight_kg", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("resident_area", sa.String(length=255), nullable=True),
        sa.Column("schedule_type", sa.String(length=128), nullable=True),
        sa.Column("occupation", sa.String(length=128), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "field_revisions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "age_years IS NULL OR (age_years >= 0 AND age_years <= 150)",
            name="ck_personal_profiles_age_years",
        ),
        sa.CheckConstraint(
            "height_cm IS NULL OR height_cm > 0",
            name="ck_personal_profiles_height_cm",
        ),
        sa.CheckConstraint(
            "weight_kg IS NULL OR weight_kg > 0",
            name="ck_personal_profiles_weight_kg",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    # 迁移执行前已有的用户也必须满足“一位用户一行基础档案”的不变量。
    op.execute(
        "INSERT INTO personal_profiles (user_id) SELECT id FROM users"
    )

    op.create_table(
        "health_facts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("fact_type", health_fact_type, nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("assertion", health_fact_assertion, nullable=False),
        sa.Column("temporal_status", health_fact_temporal_status, nullable=False),
        sa.Column("effective_start", sa.Date(), nullable=True),
        sa.Column("effective_end", sa.Date(), nullable=True),
        sa.Column(
            "status",
            health_fact_status,
            server_default="active",
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "effective_end IS NULL OR effective_start IS NULL "
            "OR effective_end >= effective_start",
            name="ck_health_facts_effective_dates",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_health_facts_user_id", "health_facts", ["user_id"])
    op.create_index(
        "ix_health_facts_user_status", "health_facts", ["user_id", "status"]
    )

    op.create_table(
        "health_profile_changes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_message_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_index", sa.Integer(), nullable=False),
        sa.Column("mode", profile_change_mode, nullable=False),
        sa.Column("status", profile_change_status, nullable=False),
        sa.Column("target_type", profile_target_type, nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("operation", profile_operation, nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column(
            "before_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "proposed_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("result_revision", sa.Integer(), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("decision_client_action_id", sa.Uuid(), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["trigger_message_id"], ["messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_run_id",
            "proposal_index",
            name="uq_health_profile_changes_run_proposal",
        ),
    )
    op.create_index(
        "ix_health_profile_changes_agent_run_id",
        "health_profile_changes",
        ["agent_run_id"],
    )
    op.create_index(
        "ix_health_profile_changes_trigger_message_id",
        "health_profile_changes",
        ["trigger_message_id"],
    )
    op.create_index(
        "ix_health_profile_changes_user_id",
        "health_profile_changes",
        ["user_id"],
    )
    op.create_index(
        "ix_health_profile_changes_user_status_created",
        "health_profile_changes",
        ["user_id", "status", "created_at"],
    )
    op.create_index(
        "uq_health_profile_changes_user_client_action",
        "health_profile_changes",
        ["user_id", "decision_client_action_id"],
        unique=True,
        postgresql_where=sa.text("decision_client_action_id IS NOT NULL"),
    )

    op.create_table(
        "wearable_imports",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("client_sync_id", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("records_created", sa.Integer(), nullable=False),
        sa.Column("records_updated", sa.Integer(), nullable=False),
        sa.Column("records_unchanged", sa.Integer(), nullable=False),
        sa.Column("records_deleted", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "records_created >= 0 AND records_updated >= 0 "
            "AND records_unchanged >= 0 AND records_deleted >= 0",
            name="ck_wearable_imports_counts",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "client_sync_id",
            name="uq_wearable_imports_user_client_sync",
        ),
    )
    op.create_index("ix_wearable_imports_user_id", "wearable_imports", ["user_id"])

    op.create_table(
        "wearable_observations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=32),
            server_default="health_connect",
            nullable=False,
        ),
        sa.Column("external_record_id", sa.String(length=255), nullable=False),
        sa.Column("record_type", wearable_record_type, nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_zone_offset_seconds", sa.Integer(), nullable=True),
        sa.Column("end_zone_offset_seconds", sa.Integer(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data_hash", sa.String(length=64), nullable=False),
        sa.Column("source_package", sa.String(length=255), nullable=False),
        sa.Column("recording_method", sa.Integer(), nullable=True),
        sa.Column("device", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_last_modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_import_id", sa.Uuid(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "end_time >= start_time", name="ck_wearable_observations_time_range"
        ),
        sa.CheckConstraint(
            "start_zone_offset_seconds IS NULL OR "
            "start_zone_offset_seconds BETWEEN -64800 AND 64800",
            name="ck_wearable_observations_start_offset",
        ),
        sa.CheckConstraint(
            "end_zone_offset_seconds IS NULL OR "
            "end_zone_offset_seconds BETWEEN -64800 AND 64800",
            name="ck_wearable_observations_end_offset",
        ),
        sa.ForeignKeyConstraint(
            ["last_import_id"], ["wearable_imports.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "external_record_id",
            name="uq_wearable_observations_user_provider_external",
        ),
    )
    op.create_index(
        "ix_wearable_observations_user_id", "wearable_observations", ["user_id"]
    )
    op.create_index(
        "ix_wearable_observations_user_type_start",
        "wearable_observations",
        ["user_id", "record_type", "start_time"],
    )

    op.create_table(
        "wearable_record_tombstones",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=32),
            server_default="health_connect",
            nullable=False,
        ),
        sa.Column("external_record_id", sa.String(length=255), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["import_id"], ["wearable_imports.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "external_record_id",
            name="uq_wearable_tombstones_user_provider_external",
        ),
    )
    op.create_index(
        "ix_wearable_record_tombstones_user_id",
        "wearable_record_tombstones",
        ["user_id"],
    )

    # agent_tool_calls 已在旧迁移创建；此时 ALTER 可以安全建立循环引用。
    op.add_column(
        "agent_runs", sa.Column("parent_tool_call_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_agent_runs_parent_tool_call_id_agent_tool_calls",
        "agent_runs",
        "agent_tool_calls",
        ["parent_tool_call_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_runs_parent_tool_call_id",
        "agent_runs",
        ["parent_tool_call_id"],
    )
    op.create_index(
        "uq_agent_runs_parent_agent_name",
        "agent_runs",
        ["parent_run_id", "agent_name"],
        unique=True,
        postgresql_where=sa.text("parent_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    # 先解除 AgentRun -> AgentToolCall，避免循环外键影响结构回退。
    op.drop_index("uq_agent_runs_parent_agent_name", table_name="agent_runs")
    op.drop_index("ix_agent_runs_parent_tool_call_id", table_name="agent_runs")
    op.drop_constraint(
        "fk_agent_runs_parent_tool_call_id_agent_tool_calls",
        "agent_runs",
        type_="foreignkey",
    )
    op.drop_column("agent_runs", "parent_tool_call_id")

    op.drop_index(
        "ix_wearable_record_tombstones_user_id",
        table_name="wearable_record_tombstones",
    )
    op.drop_table("wearable_record_tombstones")
    op.drop_index(
        "ix_wearable_observations_user_type_start",
        table_name="wearable_observations",
    )
    op.drop_index(
        "ix_wearable_observations_user_id", table_name="wearable_observations"
    )
    op.drop_table("wearable_observations")
    op.drop_index("ix_wearable_imports_user_id", table_name="wearable_imports")
    op.drop_table("wearable_imports")
    op.drop_index(
        "uq_health_profile_changes_user_client_action",
        table_name="health_profile_changes",
    )
    op.drop_index(
        "ix_health_profile_changes_user_status_created",
        table_name="health_profile_changes",
    )
    op.drop_index(
        "ix_health_profile_changes_user_id", table_name="health_profile_changes"
    )
    op.drop_index(
        "ix_health_profile_changes_trigger_message_id",
        table_name="health_profile_changes",
    )
    op.drop_index(
        "ix_health_profile_changes_agent_run_id",
        table_name="health_profile_changes",
    )
    op.drop_table("health_profile_changes")
    op.drop_index("ix_health_facts_user_status", table_name="health_facts")
    op.drop_index("ix_health_facts_user_id", table_name="health_facts")
    op.drop_table("health_facts")
    op.drop_table("personal_profiles")

    op.execute("DROP TYPE wearable_record_type")
    op.execute("DROP TYPE profile_operation")
    op.execute("DROP TYPE profile_target_type")
    op.execute("DROP TYPE profile_change_status")
    op.execute("DROP TYPE profile_change_mode")
    op.execute("DROP TYPE health_fact_status")
    op.execute("DROP TYPE health_fact_temporal_status")
    op.execute("DROP TYPE health_fact_assertion")
    op.execute("DROP TYPE health_fact_type")
