"""Phase 6 Work Unit 6.2: Durable notification deliveries outbox table

Revision ID: j2b3c4d5e6f7
Revises: i1a2b3c4d5e6
Create Date: 2026-10-26 16:05:00.000000

Phase 6 WU 6.2 Schema Changes:
- Create notification_deliveries table for durable email and multi-channel outbox jobs
- Add foreign keys cascading on notification and user deletion
- Add unique constraint for (notification_id, channel) idempotency
- Add indexes for worker claim queries (status, next_attempt_at), lease expiration, and delivery history
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "j2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "i1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create notification_deliveries outbox table and indexes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "notification_deliveries" not in tables:
        op.create_table(
            "notification_deliveries",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("notification_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("recipient_email", sa.String(length=255), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False, server_default="email"),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("body_text", sa.Text(), nullable=False),
            sa.Column("body_html", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("lease_token", sa.String(length=64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("notification_id", "channel", name="uq_notification_delivery_notif_channel"),
        )
        op.create_index("ix_notification_deliveries_notification_id", "notification_deliveries", ["notification_id"])
        op.create_index("ix_notification_deliveries_user_id", "notification_deliveries", ["user_id"])
        op.create_index("ix_notification_deliveries_channel", "notification_deliveries", ["channel"])
        op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])
        op.create_index("ix_notification_deliveries_lease_token", "notification_deliveries", ["lease_token"])
        op.create_index("ix_notification_deliveries_lease_expires_at", "notification_deliveries", ["lease_expires_at"])
        op.create_index("ix_notification_deliveries_next_attempt_at", "notification_deliveries", ["next_attempt_at"])
        op.create_index("ix_notification_deliveries_idempotency", "notification_deliveries", ["idempotency_key"])
        op.create_index("ix_notification_deliveries_claim", "notification_deliveries", ["status", "next_attempt_at"])


def downgrade() -> None:
    """Drop notification_deliveries table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "notification_deliveries" in tables:
        op.drop_table("notification_deliveries")
