"""Phase 11: Conversion Measurement and Advertiser Outcome Reporting Migration

Revision ID: n6f7a8b9c0d1
Revises: m5e6f7a8b9c0
Create Date: 2026-10-28 23:30:00.000000

Phase 11 Schema Changes:
- Add conversion and attribution columns to ad_events:
    - ad_set_id (Integer, nullable, FK to ad_sets.id)
    - conversion_value (Numeric(12, 4), nullable)
    - currency (String(3), nullable, default 'INR')
    - is_attributed (Boolean, server_default false, nullable false)
    - attribution_reason (String(64), nullable)
    - attributed_event_id (Integer, nullable, FK to ad_events.id)
    - external_order_id (String(128), nullable)
    - is_test (Boolean, server_default false, nullable false)
- Create indexes for fast click-to-conversion attribution and outcome reporting:
    - ix_ad_events_attribution on (campaign_id, is_attributed, is_test)
    - ix_ad_events_click_lookup on (request_id, event_type)
    - ix_ad_events_order_dedup unique partial index on (campaign_id, external_order_id)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "n6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "m5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "ad_events" in tables:
        columns = [c["name"] for c in inspector.get_columns("ad_events")]

        # 1. Add ad_set_id column
        if "ad_set_id" not in columns:
            op.add_column(
                "ad_events",
                sa.Column("ad_set_id", sa.Integer(), nullable=True),
            )
            op.create_foreign_key(
                "fk_ad_events_ad_set_id",
                "ad_events",
                "ad_sets",
                ["ad_set_id"],
                ["id"],
                ondelete="SET NULL",
            )
            op.create_index("ix_ad_events_ad_set_id", "ad_events", ["ad_set_id"])

        # 2. Add conversion_value column
        if "conversion_value" not in columns:
            op.add_column(
                "ad_events",
                sa.Column("conversion_value", sa.Numeric(precision=12, scale=4), nullable=True),
            )

        # 3. Add currency column
        if "currency" not in columns:
            op.add_column(
                "ad_events",
                sa.Column("currency", sa.String(length=3), server_default="INR", nullable=True),
            )

        # 4. Add is_attributed column
        if "is_attributed" not in columns:
            op.add_column(
                "ad_events",
                sa.Column("is_attributed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            )

        # 5. Add attribution_reason column
        if "attribution_reason" not in columns:
            op.add_column(
                "ad_events",
                sa.Column("attribution_reason", sa.String(length=64), nullable=True),
            )

        # 6. Add attributed_event_id column
        if "attributed_event_id" not in columns:
            op.add_column(
                "ad_events",
                sa.Column("attributed_event_id", sa.Integer(), nullable=True),
            )
            op.create_foreign_key(
                "fk_ad_events_attributed_event_id",
                "ad_events",
                "ad_events",
                ["attributed_event_id"],
                ["id"],
                ondelete="SET NULL",
            )
            op.create_index("ix_ad_events_attributed_event_id", "ad_events", ["attributed_event_id"])

        # 7. Add external_order_id column
        if "external_order_id" not in columns:
            op.add_column(
                "ad_events",
                sa.Column("external_order_id", sa.String(length=128), nullable=True),
            )
            op.create_index("ix_ad_events_external_order_id", "ad_events", ["external_order_id"])

        # 8. Add is_test column
        if "is_test" not in columns:
            op.add_column(
                "ad_events",
                sa.Column("is_test", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            )
            op.create_index("ix_ad_events_is_test", "ad_events", ["is_test"])

        # Composite Indexes
        indexes = [idx["name"] for idx in inspector.get_indexes("ad_events")]
        if "ix_ad_events_attribution" not in indexes:
            op.create_index(
                "ix_ad_events_attribution",
                "ad_events",
                ["campaign_id", "is_attributed", "is_test"],
            )

        if "ix_ad_events_click_lookup" not in indexes:
            op.create_index(
                "ix_ad_events_click_lookup",
                "ad_events",
                ["request_id", "event_type"],
            )

        if "uq_ad_events_order_dedup" not in indexes:
            op.create_index(
                "uq_ad_events_order_dedup",
                "ad_events",
                ["campaign_id", "external_order_id"],
                unique=True,
                postgresql_where=sa.text("external_order_id IS NOT NULL AND is_test = false"),
                sqlite_where=sa.text("external_order_id IS NOT NULL AND is_test = 0"),
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "ad_events" in tables:
        indexes = [idx["name"] for idx in inspector.get_indexes("ad_events")]
        for idx in [
            "uq_ad_events_order_dedup",
            "ix_ad_events_click_lookup",
            "ix_ad_events_attribution",
            "ix_ad_events_is_test",
            "ix_ad_events_external_order_id",
            "ix_ad_events_attributed_event_id",
            "ix_ad_events_ad_set_id",
        ]:
            if idx in indexes:
                op.drop_index(idx, table_name="ad_events")

        fks = [fk["name"] for fk in inspector.get_foreign_keys("ad_events")]
        if "fk_ad_events_attributed_event_id" in fks:
            op.drop_constraint("fk_ad_events_attributed_event_id", "ad_events", type_="foreignkey")
        if "fk_ad_events_ad_set_id" in fks:
            op.drop_constraint("fk_ad_events_ad_set_id", "ad_events", type_="foreignkey")

        columns = [c["name"] for c in inspector.get_columns("ad_events")]
        for col in [
            "is_test",
            "external_order_id",
            "attributed_event_id",
            "attribution_reason",
            "is_attributed",
            "currency",
            "conversion_value",
            "ad_set_id",
        ]:
            if col in columns:
                op.drop_column("ad_events", col)
