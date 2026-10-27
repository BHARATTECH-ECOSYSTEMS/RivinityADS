"""Phase 7A: AdSet Data Model and Backward-Compatible Migration

Revision ID: k3c4d5e6f7a8
Revises: j2b3c4d5e6f7
Create Date: 2026-10-26 18:00:00.000000

Phase 7A Schema Changes:
- Create ad_sets table for first-class AdSet domain entity
- Add ad_set_id foreign key column and index to creatives table
- Add ad_set_id foreign key column and index to targeting_rules table
- Backfill exactly one default ad set per existing campaign
- Backfill existing creatives and targeting rules to their campaign's default ad set
- Preserve all existing campaign budget ceilings, statistics, and lifecycle statuses
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "k3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "j2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply Phase 7A schema changes and backfill legacy entities."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Create ad_sets table
    if "ad_sets" not in tables:
        op.create_table(
            "ad_sets",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("campaign_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("daily_budget", sa.Numeric(12, 4), nullable=True),
            sa.Column("bid_type", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("bid_amount", sa.Numeric(12, 4), nullable=False, server_default="0"),
            sa.Column("start_time", sa.DateTime(), nullable=True),
            sa.Column("end_time", sa.DateTime(), nullable=True),
            sa.Column("freq_cap_daily", sa.Integer(), nullable=True, server_default="10"),
            sa.Column("freq_cap_hourly", sa.Integer(), nullable=True, server_default="3"),
            sa.Column("delivery_mode", sa.String(length=32), nullable=False, server_default="standard"),
            sa.Column("targeting_summary", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ad_sets_campaign_id", "ad_sets", ["campaign_id"])
        op.create_index("ix_ad_sets_status", "ad_sets", ["status"])

    # 2. Add ad_set_id column to creatives table
    if "creatives" in tables:
        creative_cols = [c["name"] for c in inspector.get_columns("creatives")]
        if "ad_set_id" not in creative_cols:
            with op.batch_alter_table("creatives") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "ad_set_id",
                        sa.Integer(),
                        sa.ForeignKey("ad_sets.id", ondelete="SET NULL", name="fk_creatives_ad_set_id"),
                        nullable=True,
                    )
                )
                batch_op.create_index("ix_creatives_ad_set_id", ["ad_set_id"])

    # 3. Add ad_set_id column to targeting_rules table
    if "targeting_rules" in tables:
        rule_cols = [c["name"] for c in inspector.get_columns("targeting_rules")]
        if "ad_set_id" not in rule_cols:
            with op.batch_alter_table("targeting_rules") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "ad_set_id",
                        sa.Integer(),
                        sa.ForeignKey("ad_sets.id", ondelete="CASCADE", name="fk_targeting_rules_ad_set_id"),
                        nullable=True,
                    )
                )
                batch_op.create_index("ix_targeting_rules_ad_set_id", ["ad_set_id"])

    # 4. Backfill default AdSet for each campaign and link creatives/targeting rules
    if "campaigns" in tables:
        campaign_rows = conn.execute(
            sa.text("""
                SELECT id, name, status, budget_daily, bid_type, bid_amount,
                       start_time, end_time, freq_cap_daily, freq_cap_hourly, created_at, updated_at
                FROM campaigns
                ORDER BY id ASC
            """)
        ).fetchall()

        for camp in campaign_rows:
            c_id = camp[0]
            c_name = camp[1]
            c_status = camp[2]
            c_daily = camp[3]
            c_bid_type = camp[4]
            c_bid_amount = camp[5]
            c_start = camp[6]
            c_end = camp[7]
            c_fc_daily = camp[8]
            c_fc_hourly = camp[9]
            c_created = camp[10]
            c_updated = camp[11]

            # Check if an ad set already exists for this campaign
            existing_ad_set_id = conn.execute(
                sa.text("SELECT id FROM ad_sets WHERE campaign_id = :cid ORDER BY id ASC LIMIT 1"),
                {"cid": c_id},
            ).scalar()

            if existing_ad_set_id is None:
                ad_set_name = f"Default Ad Set - {c_name}"
                conn.execute(
                    sa.text("""
                        INSERT INTO ad_sets (
                            campaign_id, name, status, daily_budget, bid_type, bid_amount,
                            start_time, end_time, freq_cap_daily, freq_cap_hourly, delivery_mode, created_at, updated_at
                        ) VALUES (
                            :campaign_id, :name, :status, :daily_budget, :bid_type, :bid_amount,
                            :start_time, :end_time, :freq_cap_daily, :freq_cap_hourly, 'standard',
                            COALESCE(:created_at, CURRENT_TIMESTAMP), COALESCE(:updated_at, CURRENT_TIMESTAMP)
                        )
                    """),
                    {
                        "campaign_id": c_id,
                        "name": ad_set_name,
                        "status": c_status,
                        "daily_budget": c_daily,
                        "bid_type": c_bid_type,
                        "bid_amount": c_bid_amount,
                        "start_time": c_start,
                        "end_time": c_end,
                        "freq_cap_daily": c_fc_daily,
                        "freq_cap_hourly": c_fc_hourly,
                        "created_at": c_created,
                        "updated_at": c_updated,
                    },
                )
                target_ad_set_id = conn.execute(
                    sa.text("SELECT id FROM ad_sets WHERE campaign_id = :cid ORDER BY id DESC LIMIT 1"),
                    {"cid": c_id},
                ).scalar()
            else:
                target_ad_set_id = existing_ad_set_id

            # Backfill creatives where ad_set_id IS NULL
            if "creatives" in tables:
                conn.execute(
                    sa.text("UPDATE creatives SET ad_set_id = :asid WHERE campaign_id = :cid AND ad_set_id IS NULL"),
                    {"asid": target_ad_set_id, "cid": c_id},
                )

            # Backfill targeting rules where ad_set_id IS NULL
            if "targeting_rules" in tables:
                conn.execute(
                    sa.text("UPDATE targeting_rules SET ad_set_id = :asid WHERE campaign_id = :cid AND ad_set_id IS NULL"),
                    {"asid": target_ad_set_id, "cid": c_id},
                )


def downgrade() -> None:
    """Revert Phase 7A schema changes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Drop ad_set_id from targeting_rules
    if "targeting_rules" in tables:
        rule_cols = [c["name"] for c in inspector.get_columns("targeting_rules")]
        if "ad_set_id" in rule_cols:
            with op.batch_alter_table("targeting_rules") as batch_op:
                batch_op.drop_index("ix_targeting_rules_ad_set_id")
                batch_op.drop_column("ad_set_id")

    # 2. Drop ad_set_id from creatives
    if "creatives" in tables:
        creative_cols = [c["name"] for c in inspector.get_columns("creatives")]
        if "ad_set_id" in creative_cols:
            with op.batch_alter_table("creatives") as batch_op:
                batch_op.drop_index("ix_creatives_ad_set_id")
                batch_op.drop_column("ad_set_id")

    # 3. Drop ad_sets table
    if "ad_sets" in tables:
        op.drop_table("ad_sets")
