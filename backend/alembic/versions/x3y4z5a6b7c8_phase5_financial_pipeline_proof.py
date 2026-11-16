"""Phase 5: Financial and event pipeline proof, earnings separation, rev share locking, and ledger immutability.

Revision ID: x3y4z5a6b7c8
Revises: w2x3y4z5a6b7
Create Date: 2026-11-13 21:00:00.000000

Schema changes:
- Adds pending_balance and rev_share_rate to publishers.
- Adds provider_event_id, rev_share_rate, hold_until, and is_cleared to transactions.
- Adds earnings_hold_hours to platform_settings.
- Adds cleared_earnings to publisher_daily_stats.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "x3y4z5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "w2x3y4z5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Update publishers table
    pub_cols = [c["name"] for c in inspector.get_columns("publishers")]
    if "pending_balance" not in pub_cols:
        op.add_column(
            "publishers",
            sa.Column("pending_balance", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False),
        )
    if "rev_share_rate" not in pub_cols:
        op.add_column(
            "publishers",
            sa.Column("rev_share_rate", sa.Numeric(precision=4, scale=3), nullable=True),
        )

    # 2. Update transactions table
    tx_cols = [c["name"] for c in inspector.get_columns("transactions")]
    if "provider_event_id" not in tx_cols:
        op.add_column(
            "transactions",
            sa.Column("provider_event_id", sa.String(length=255), nullable=True),
        )
        try:
            op.create_index("ix_transactions_provider_event_id", "transactions", ["provider_event_id"])
        except Exception:
            pass

    if "rev_share_rate" not in tx_cols:
        op.add_column(
            "transactions",
            sa.Column("rev_share_rate", sa.Numeric(precision=4, scale=3), nullable=True),
        )
    if "hold_until" not in tx_cols:
        op.add_column(
            "transactions",
            sa.Column("hold_until", sa.DateTime(), nullable=True),
        )
    if "is_cleared" not in tx_cols:
        op.add_column(
            "transactions",
            sa.Column("is_cleared", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        )

    # 3. Update platform_settings table
    ps_cols = [c["name"] for c in inspector.get_columns("platform_settings")]
    if "earnings_hold_hours" not in ps_cols:
        op.add_column(
            "platform_settings",
            sa.Column("earnings_hold_hours", sa.Integer(), server_default="0", nullable=False),
        )

    # 4. Update publisher_daily_stats table
    pds_cols = [c["name"] for c in inspector.get_columns("publisher_daily_stats")]
    if "cleared_earnings" not in pds_cols:
        op.add_column(
            "publisher_daily_stats",
            sa.Column("cleared_earnings", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Downgrade publisher_daily_stats
    pds_cols = [c["name"] for c in inspector.get_columns("publisher_daily_stats")]
    if "cleared_earnings" in pds_cols:
        op.drop_column("publisher_daily_stats", "cleared_earnings")

    # 2. Downgrade platform_settings
    ps_cols = [c["name"] for c in inspector.get_columns("platform_settings")]
    if "earnings_hold_hours" in ps_cols:
        op.drop_column("platform_settings", "earnings_hold_hours")

    # 3. Downgrade transactions
    tx_cols = [c["name"] for c in inspector.get_columns("transactions")]
    if "provider_event_id" in tx_cols:
        try:
            op.drop_index("ix_transactions_provider_event_id", table_name="transactions")
        except Exception:
            pass
        op.drop_column("transactions", "provider_event_id")
    if "rev_share_rate" in tx_cols:
        op.drop_column("transactions", "rev_share_rate")
    if "hold_until" in tx_cols:
        op.drop_column("transactions", "hold_until")
    if "is_cleared" in tx_cols:
        op.drop_column("transactions", "is_cleared")

    # 4. Downgrade publishers
    pub_cols = [c["name"] for c in inspector.get_columns("publishers")]
    if "rev_share_rate" in pub_cols:
        op.drop_column("publishers", "rev_share_rate")
    if "pending_balance" in pub_cols:
        op.drop_column("publishers", "pending_balance")
