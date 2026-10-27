"""Phase 4B: Add durable webhook_deliveries outbox table

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-10-21 16:45:00.000000

Phase 4B:
- Create webhook_deliveries table for durable webhook delivery outbox
- Add foreign key to webhooks with ondelete CASCADE
- Add unique constraint for (webhook_id, event_id, event_type) idempotency
- Add indexes for worker claim queries (status, next_attempt_at) and delivery history
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create webhook_deliveries outbox table and indexes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "webhook_deliveries" not in tables:
        op.create_table(
            "webhook_deliveries",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("webhook_id", sa.Integer(), nullable=False),
            sa.Column("owner_type", sa.String(length=32), nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("event_id", sa.String(length=128), nullable=True),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("lease_token", sa.String(length=64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_http_status", sa.Integer(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("webhook_id", "event_id", "event_type", name="uq_webhook_deliveries_idempotency"),
        )
        op.create_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"])
        op.create_index("ix_webhook_deliveries_owner", "webhook_deliveries", ["owner_type", "owner_id"])
        op.create_index("ix_webhook_deliveries_event_id", "webhook_deliveries", ["event_id"])
        op.create_index("ix_webhook_deliveries_event_type", "webhook_deliveries", ["event_type"])
        op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])
        op.create_index("ix_webhook_deliveries_lease_token", "webhook_deliveries", ["lease_token"])
        op.create_index("ix_webhook_deliveries_lease_expires_at", "webhook_deliveries", ["lease_expires_at"])
        op.create_index("ix_webhook_deliveries_next_attempt_at", "webhook_deliveries", ["next_attempt_at"])
        op.create_index("ix_webhook_deliveries_claim", "webhook_deliveries", ["status", "next_attempt_at"])
        op.create_index("ix_webhook_deliveries_history", "webhook_deliveries", ["webhook_id", "created_at"])


def downgrade() -> None:
    """Drop webhook_deliveries outbox table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "webhook_deliveries" in tables:
        op.drop_table("webhook_deliveries")
