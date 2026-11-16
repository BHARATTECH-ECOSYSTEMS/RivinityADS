"""Phase 2: Channel adapter truthfulness, explicit states, and remote verification.

Revision ID: w2x3y4z5a6b7
Revises: v1w2x3y4z5a6
Create Date: 2026-11-13 17:50:00.000000

Schema changes:
- Adds adapter_mode, last_verified_at, token_state, sync_cursor, and remote_metadata to channel_connections.
- Adds remote_object_status, moderation_status, provider_request_id, remote_resource_name, sync_status, and reporting_synced_at to external_campaign_mappings.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "w2x3y4z5a6b7"
down_revision: Union[str, Sequence[str], None] = "v1w2x3y4z5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add Phase 2 columns to channel_connections
    op.add_column(
        "channel_connections",
        sa.Column("adapter_mode", sa.String(length=32), server_default="LIVE", nullable=False),
    )
    op.add_column(
        "channel_connections",
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "channel_connections",
        sa.Column("token_state", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "channel_connections",
        sa.Column("sync_cursor", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "channel_connections",
        sa.Column("remote_metadata", sa.JSON(), nullable=True),
    )

    # 2. Add Phase 2 columns to external_campaign_mappings
    op.add_column(
        "external_campaign_mappings",
        sa.Column("remote_object_status", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "external_campaign_mappings",
        sa.Column("moderation_status", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "external_campaign_mappings",
        sa.Column("provider_request_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "external_campaign_mappings",
        sa.Column("remote_resource_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "external_campaign_mappings",
        sa.Column("sync_status", sa.String(length=32), server_default="SYNCED", nullable=False),
    )
    op.add_column(
        "external_campaign_mappings",
        sa.Column("reporting_synced_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    # Drop external_campaign_mappings columns
    op.drop_column("external_campaign_mappings", "reporting_synced_at")
    op.drop_column("external_campaign_mappings", "sync_status")
    op.drop_column("external_campaign_mappings", "remote_resource_name")
    op.drop_column("external_campaign_mappings", "provider_request_id")
    op.drop_column("external_campaign_mappings", "moderation_status")
    op.drop_column("external_campaign_mappings", "remote_object_status")

    # Drop channel_connections columns
    op.drop_column("channel_connections", "remote_metadata")
    op.drop_column("channel_connections", "sync_cursor")
    op.drop_column("channel_connections", "token_state")
    op.drop_column("channel_connections", "last_verified_at")
    op.drop_column("channel_connections", "adapter_mode")

