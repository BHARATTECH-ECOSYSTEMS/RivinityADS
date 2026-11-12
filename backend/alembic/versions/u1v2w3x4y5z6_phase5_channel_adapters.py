"""Phase 5: Channel adapters, external ad platform mappings, and AI publisher contracts.

Revision ID: u1v2w3x4y5z6
Revises: t1u2v3w4x5y6
Create Date: 2026-11-12 17:15:00.000000

Schema changes:
- Creates `channel_connections` table for authorized external ad platform connections (OpenAI, Google Ads).
- Creates `external_campaign_mappings` table for deterministic campaign mapping and idempotency.
- Creates `publisher_inventory_contracts` table for verified AI publisher delivery agreements.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "u1v2w3x4y5z6"
down_revision: Union[str, Sequence[str], None] = "t1u2v3w4x5y6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create channel_connections table
    op.create_table(
        "channel_connections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_type", sa.String(length=32), server_default="advertiser", nullable=False),
        sa.Column("advertiser_id", sa.Integer(), nullable=True),
        sa.Column("publisher_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("external_account_id", sa.String(length=128), nullable=False),
        sa.Column("external_organization_id", sa.String(length=128), nullable=True),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("credentials_prefix", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="CONNECTED", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["advertiser_id"], ["advertisers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publisher_id"], ["publishers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("advertiser_id", "provider", "external_account_id", name="uq_advertiser_channel_account"),
    )
    op.create_index(
        "ix_channel_connections_advertiser_id",
        "channel_connections",
        ["advertiser_id"],
        unique=False,
    )
    op.create_index(
        "ix_channel_connections_publisher_id",
        "channel_connections",
        ["publisher_id"],
        unique=False,
    )
    op.create_index(
        "ix_channel_connections_provider",
        "channel_connections",
        ["provider"],
        unique=False,
    )
    op.create_index(
        "ix_channel_connections_external_account_id",
        "channel_connections",
        ["external_account_id"],
        unique=False,
    )

    # 2. Create external_campaign_mappings table
    op.create_table(
        "external_campaign_mappings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("advertiser_id", sa.Integer(), nullable=False),
        sa.Column("local_campaign_id", sa.Integer(), nullable=True),
        sa.Column("external_campaign_id", sa.String(length=128), nullable=False),
        sa.Column("external_campaign_name", sa.String(length=255), nullable=False),
        sa.Column("external_status", sa.String(length=64), nullable=False),
        sa.Column("objective", sa.String(length=64), nullable=True),
        sa.Column("daily_budget", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("total_spend", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["channel_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["advertiser_id"], ["advertisers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["local_campaign_id"], ["campaigns.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "external_campaign_id", name="uq_connection_external_campaign"),
    )
    op.create_index(
        "ix_external_campaign_mappings_connection_id",
        "external_campaign_mappings",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_external_campaign_mappings_advertiser_id",
        "external_campaign_mappings",
        ["advertiser_id"],
        unique=False,
    )
    op.create_index(
        "ix_external_campaign_mappings_local_campaign_id",
        "external_campaign_mappings",
        ["local_campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_external_campaign_mappings_external_campaign_id",
        "external_campaign_mappings",
        ["external_campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_external_campaign_mappings_idempotency_key",
        "external_campaign_mappings",
        ["idempotency_key"],
        unique=True,
    )

    # 3. Create publisher_inventory_contracts table
    op.create_table(
        "publisher_inventory_contracts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("publisher_id", sa.Integer(), nullable=False),
        sa.Column("platform_name", sa.String(length=255), nullable=False),
        sa.Column("signed_agreement_id", sa.String(length=128), nullable=False),
        sa.Column("agreement_status", sa.String(length=32), server_default="ACTIVE", nullable=False),
        sa.Column("approved_surfaces", sa.JSON(), nullable=False),
        sa.Column("policy_terms_hash", sa.String(length=64), nullable=False),
        sa.Column("contract_signed_at", sa.DateTime(), nullable=False),
        sa.Column("contract_valid_until", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["publisher_id"], ["publishers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_publisher_inventory_contracts_publisher_id",
        "publisher_inventory_contracts",
        ["publisher_id"],
        unique=False,
    )
    op.create_index(
        "ix_publisher_inventory_contracts_signed_agreement_id",
        "publisher_inventory_contracts",
        ["signed_agreement_id"],
        unique=True,
    )


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_index("ix_publisher_inventory_contracts_signed_agreement_id", table_name="publisher_inventory_contracts")
    op.drop_index("ix_publisher_inventory_contracts_publisher_id", table_name="publisher_inventory_contracts")
    op.drop_table("publisher_inventory_contracts")

    op.drop_index("ix_external_campaign_mappings_idempotency_key", table_name="external_campaign_mappings")
    op.drop_index("ix_external_campaign_mappings_external_campaign_id", table_name="external_campaign_mappings")
    op.drop_index("ix_external_campaign_mappings_local_campaign_id", table_name="external_campaign_mappings")
    op.drop_index("ix_external_campaign_mappings_advertiser_id", table_name="external_campaign_mappings")
    op.drop_index("ix_external_campaign_mappings_connection_id", table_name="external_campaign_mappings")
    op.drop_table("external_campaign_mappings")

    op.drop_index("ix_channel_connections_external_account_id", table_name="channel_connections")
    op.drop_index("ix_channel_connections_provider", table_name="channel_connections")
    op.drop_index("ix_channel_connections_publisher_id", table_name="channel_connections")
    op.drop_index("ix_channel_connections_advertiser_id", table_name="channel_connections")
    op.drop_table("channel_connections")

