"""Phase 3: Measurement integrity, invalid traffic (IVT) controls, and billing disputes.

Revision ID: t1u2v3w4x5y6
Revises: s1t2u3v4w5x6
Create Date: 2026-11-12 16:30:00.000000

Schema changes:
- Adds IVT detection columns to `ad_events`: `is_invalid`, `invalid_reason`, `invalid_details`
- Adds index `ix_ad_events_ivt` on `ad_events(is_invalid, event_type)`
- Creates `billing_disputes` table for advertiser spend disputes and publisher IVT traffic appeals
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "t1u2v3w4x5y6"
down_revision: Union[str, Sequence[str], None] = "s1t2u3v4w5x6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Add IVT tracking columns to ad_events table
    if "ad_events" in tables:
        ad_events_columns = [col["name"] for col in inspector.get_columns("ad_events")]
        if "is_invalid" not in ad_events_columns:
            op.add_column(
                "ad_events",
                sa.Column("is_invalid", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            )
        if "invalid_reason" not in ad_events_columns:
            op.add_column(
                "ad_events",
                sa.Column("invalid_reason", sa.String(64), nullable=True),
            )
        if "invalid_details" not in ad_events_columns:
            op.add_column(
                "ad_events",
                sa.Column("invalid_details", sa.JSON(), nullable=True),
            )

        # Create index on (is_invalid, event_type)
        existing_indices = [idx["name"] for idx in inspector.get_indexes("ad_events")]
        if "ix_ad_events_ivt" not in existing_indices:
            op.create_index("ix_ad_events_ivt", "ad_events", ["is_invalid", "event_type"])

    # 2. Create billing_disputes table
    if "billing_disputes" not in tables:
        op.create_table(
            "billing_disputes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("dispute_type", sa.String(32), nullable=False, index=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
                index=True,
            ),
            sa.Column(
                "advertiser_id",
                sa.Integer(),
                sa.ForeignKey("advertisers.id", ondelete="SET NULL"),
                nullable=True,
                index=True,
            ),
            sa.Column(
                "publisher_id",
                sa.Integer(),
                sa.ForeignKey("publishers.id", ondelete="SET NULL"),
                nullable=True,
                index=True,
            ),
            sa.Column(
                "campaign_id",
                sa.Integer(),
                sa.ForeignKey("campaigns.id", ondelete="SET NULL"),
                nullable=True,
                index=True,
            ),
            sa.Column("slot_id", sa.String(255), nullable=True, index=True),
            sa.Column(
                "transaction_id",
                sa.Integer(),
                sa.ForeignKey("transactions.id", ondelete="SET NULL"),
                nullable=True,
                index=True,
            ),
            sa.Column("amount_disputed", sa.Numeric(12, 6), server_default=sa.text("0"), nullable=False),
            sa.Column("amount_credited", sa.Numeric(12, 6), server_default=sa.text("0"), nullable=False),
            sa.Column("currency", sa.String(3), server_default=sa.text("'INR'"), nullable=False),
            sa.Column("status", sa.String(32), server_default=sa.text("'OPEN'"), nullable=False, index=True),
            sa.Column("reason", sa.String(255), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=True),
            sa.Column("resolution_notes", sa.Text(), nullable=True),
            sa.Column(
                "resolved_by_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
            sa.CheckConstraint("amount_disputed >= 0", name="ck_disputes_amount_disputed_nonneg"),
            sa.CheckConstraint("amount_credited >= 0", name="ck_disputes_amount_credited_nonneg"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "billing_disputes" in tables:
        op.drop_table("billing_disputes")

    if "ad_events" in tables:
        existing_indices = [idx["name"] for idx in inspector.get_indexes("ad_events")]
        if "ix_ad_events_ivt" in existing_indices:
            op.drop_index("ix_ad_events_ivt", table_name="ad_events")

        ad_events_columns = [col["name"] for col in inspector.get_columns("ad_events")]
        if "invalid_details" in ad_events_columns:
            op.drop_column("ad_events", "invalid_details")
        if "invalid_reason" in ad_events_columns:
            op.drop_column("ad_events", "invalid_reason")
        if "is_invalid" in ad_events_columns:
            op.drop_column("ad_events", "is_invalid")

