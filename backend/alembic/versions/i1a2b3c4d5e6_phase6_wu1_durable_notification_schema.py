"""Phase 6 Work Unit 6.1: Durable notification schema and preferences

Revision ID: i1a2b3c4d5e6
Revises: h9c0d1e2f3a4
Create Date: 2026-10-26 15:45:00.000000

Phase 6 WU 6.1 Schema Changes:
- Create notifications table with user scoping, lifecycle timestamps, dedupe key, and query indexes
- Create notification_preferences table with per-user, per-channel opt-in/opt-out unique constraint
- Add foreign keys cascading on user deletion
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "i1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "h9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create notifications and notification_preferences tables and indexes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Create notifications table
    if "notifications" not in tables:
        op.create_table(
            "notifications",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("type", sa.String(length=64), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False, server_default="in_app"),
            sa.Column("severity", sa.String(length=32), nullable=False, server_default="info"),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("link", sa.String(length=512), nullable=True),
            sa.Column("resource_type", sa.String(length=64), nullable=True),
            sa.Column("resource_id", sa.String(length=128), nullable=True),
            sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("read_at", sa.DateTime(), nullable=True),
            sa.Column("dedupe_key", sa.String(length=128), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
        op.create_index("ix_notifications_category", "notifications", ["category"])
        op.create_index("ix_notifications_type", "notifications", ["type"])
        op.create_index("ix_notifications_channel", "notifications", ["channel"])
        op.create_index("ix_notifications_is_read", "notifications", ["is_read"])
        op.create_index("ix_notifications_dedupe_key", "notifications", ["dedupe_key"])
        op.create_index("ix_notifications_expires_at", "notifications", ["expires_at"])
        op.create_index("ix_notifications_user_created_at", "notifications", ["user_id", "created_at"])
        op.create_index("ix_notifications_user_is_read", "notifications", ["user_id", "is_read"])
        op.create_index("ix_notifications_user_category", "notifications", ["user_id", "category"])
        op.create_index(
            "uq_notifications_user_dedupe",
            "notifications",
            ["user_id", "dedupe_key"],
            unique=True,
            postgresql_where=sa.text("dedupe_key IS NOT NULL"),
            sqlite_where=sa.text("dedupe_key IS NOT NULL"),
        )

    # 2. Create notification_preferences table
    if "notification_preferences" not in tables:
        op.create_table(
            "notification_preferences",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False, server_default="in_app"),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "channel", "category", name="uq_notification_preference_user_channel_category"),
        )
        op.create_index("ix_notification_preferences_user_id", "notification_preferences", ["user_id"])


def downgrade() -> None:
    """Drop notifications and notification_preferences tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "notification_preferences" in tables:
        op.drop_table("notification_preferences")
    if "notifications" in tables:
        op.drop_table("notifications")
