"""add message attachments

Revision ID: i26c7d9e4f30
Revises: h15a8c4d2e71
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i26c7d9e4f30"
down_revision: str | None = "h15a8c4d2e71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    attachment_kind = sa.Enum("photo", "report", name="message_attachment_kind")
    op.create_table(
        "message_attachments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("kind", attachment_kind, nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name="ck_message_attachments_positive_size",
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(
        "ix_message_attachments_user_id_id",
        "message_attachments",
        ["user_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_attachments_user_id_id",
        table_name="message_attachments",
    )
    op.drop_table("message_attachments")
    sa.Enum(name="message_attachment_kind").drop(op.get_bind())
