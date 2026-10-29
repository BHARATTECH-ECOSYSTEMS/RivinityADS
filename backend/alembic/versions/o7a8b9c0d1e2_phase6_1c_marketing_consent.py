"""Phase 6.1C: Marketing Consent, Subscriber Models and List Synchronization Migration.

Revision ID: o7a8b9c0d1e2
Revises: n6f7a8b9c0d1
Create Date: 2026-10-29 11:30:00.000000

Phase 6.1C Schema Changes:
- Creates `marketing_consents` table to store authoritative marketing consent records,
  decoupling marketing consent from transactional emails, account creation, or billing.
- Supports both registered users and external contacts.
- Enforces unique constraint on `(email, category)`.
- Adds explicit revocation timestamp to ensure retries never re-subscribe opted-out addresses.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "o7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "n6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "marketing_consents" not in tables:
        op.create_table(
            "marketing_consents",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("category", sa.String(length=64), server_default="product_newsletter", nullable=False),
            sa.Column("status", sa.String(length=32), server_default="pending_confirmation", nullable=False),
            sa.Column("source", sa.String(length=64), server_default="preferences", nullable=False),
            sa.Column("policy_version", sa.String(length=32), server_default="2026.1", nullable=False),
            sa.Column("confirmation_token", sa.String(length=128), nullable=True),
            sa.Column("confirmation_token_expires_at", sa.DateTime(), nullable=True),
            sa.Column("consented_at", sa.DateTime(), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.String(length=255), nullable=True),
            sa.Column("listmonk_subscriber_id", sa.Integer(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(), nullable=True),
            sa.Column("sync_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email", "category", name="uq_marketing_consents_email_category"),
        )
        op.create_index("ix_marketing_consents_email", "marketing_consents", ["email"])
        op.create_index("ix_marketing_consents_user_id", "marketing_consents", ["user_id"])
        op.create_index("ix_marketing_consents_token", "marketing_consents", ["confirmation_token"])
        op.create_index(
            "ix_marketing_consents_status_category",
            "marketing_consents",
            ["status", "category"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "marketing_consents" in tables:
        op.drop_table("marketing_consents")

