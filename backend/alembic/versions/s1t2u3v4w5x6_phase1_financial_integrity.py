"""Phase 1: Financial integrity, conversion security, advertiser credentials, and publisher payouts.

Revision ID: s1t2u3v4w5x6
Revises: r0a1b2c3d4e5
Create Date: 2026-11-12 16:00:00.000000

Schema changes:
- Creates `advertiser_api_keys` table for advertiser-scoped S2S conversion postback credentials
- Creates `publisher_payouts` table for explicit payout reservation and lifecycle state machine
- Updates `transactions.currency` server default to 'INR'
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "s1t2u3v4w5x6"
down_revision: Union[str, Sequence[str], None] = "r0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Create advertiser_api_keys table
    if "advertiser_api_keys" not in tables:
        op.create_table(
            "advertiser_api_keys",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "advertiser_id",
                sa.Integer(),
                sa.ForeignKey("advertisers.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "campaign_id",
                sa.Integer(),
                sa.ForeignKey("campaigns.id", ondelete="SET NULL"),
                nullable=True,
                index=True,
            ),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("key_prefix", sa.String(32), nullable=False, index=True),
            sa.Column("key_hash", sa.String(255), nullable=False, index=True),
            sa.Column("secret_hash", sa.String(255), nullable=True),
            sa.Column("secret_encrypted", sa.Text(), nullable=True),
            sa.Column("is_sandbox", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("rate_limit_per_minute", sa.Integer(), server_default=sa.text("60"), nullable=False),
            sa.Column("daily_quota", sa.Integer(), server_default=sa.text("10000"), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
            sa.CheckConstraint("rate_limit_per_minute > 0", name="ck_advertiser_api_keys_rate_limit_positive"),
            sa.CheckConstraint("daily_quota > 0", name="ck_advertiser_api_keys_daily_quota_positive"),
        )

    # 2. Create publisher_payouts table
    if "publisher_payouts" not in tables:
        op.create_table(
            "publisher_payouts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "publisher_id",
                sa.Integer(),
                sa.ForeignKey("publishers.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("amount", sa.Numeric(precision=12, scale=6), nullable=False),
            sa.Column("currency", sa.String(3), server_default=sa.text("'INR'"), nullable=False),
            sa.Column("status", sa.String(32), server_default=sa.text("'requested'"), nullable=False, index=True),
            sa.Column("reference_id", sa.String(255), nullable=False, unique=True, index=True),
            sa.Column("provider_payout_id", sa.String(255), nullable=True, index=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("amount > 0", name="ck_publisher_payouts_amount_positive"),
        )

    # 3. Alter transactions.currency default to INR
    if "transactions" in tables:
        op.alter_column(
            "transactions",
            "currency",
            existing_type=sa.String(3),
            server_default=sa.text("'INR'"),
            nullable=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "transactions" in tables:
        op.alter_column(
            "transactions",
            "currency",
            existing_type=sa.String(3),
            server_default=sa.text("'USD'"),
            nullable=False,
        )

    if "publisher_payouts" in tables:
        op.drop_table("publisher_payouts")

    if "advertiser_api_keys" in tables:
        op.drop_table("advertiser_api_keys")

