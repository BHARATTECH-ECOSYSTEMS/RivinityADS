"""Phase 6.1D: Tenant-Safe Email Marketing Lists, Contacts and Campaigns Migration.

Revision ID: p8b9c0d1e2f3
Revises: o7a8b9c0d1e2
Create Date: 2026-10-29 11:45:00.000000

Phase 6.1D Schema Changes:
- Creates `email_marketing_lists` table for tenant-scoped mailing lists.
- Creates `email_marketing_contacts` table for permission-based subscriber contacts.
- Creates `email_marketing_campaigns` table for tenant-scoped email marketing campaigns.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "p8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "o7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. email_marketing_lists
    if "email_marketing_lists" not in tables:
        op.create_table(
            "email_marketing_lists",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_type", sa.String(length=32), server_default="advertiser", nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("type", sa.String(length=32), server_default="private", nullable=False),
            sa.Column("optin", sa.String(length=32), server_default="single", nullable=False),
            sa.Column("listmonk_list_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_em_lists_tenant", "email_marketing_lists", ["tenant_type", "tenant_id"])

    # 2. email_marketing_contacts
    if "email_marketing_contacts" not in tables:
        op.create_table(
            "email_marketing_contacts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_type", sa.String(length=32), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("list_id", sa.Integer(), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=32), server_default="subscribed", nullable=False),
            sa.Column("consent_source", sa.String(length=64), server_default="import", nullable=False),
            sa.Column("consent_at", sa.DateTime(), nullable=True),
            sa.Column("listmonk_subscriber_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["list_id"], ["email_marketing_lists.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_type", "tenant_id", "list_id", "email", name="uq_em_contacts_tenant_list_email"),
        )
        op.create_index("ix_em_contacts_email", "email_marketing_contacts", ["email"])
        op.create_index("ix_em_contacts_tenant_list", "email_marketing_contacts", ["tenant_type", "tenant_id", "list_id"])

    # 3. email_marketing_campaigns
    if "email_marketing_campaigns" not in tables:
        op.create_table(
            "email_marketing_campaigns",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_type", sa.String(length=32), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("list_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("content_type", sa.String(length=32), server_default="html", nullable=False),
            sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
            sa.Column("listmonk_campaign_id", sa.Integer(), nullable=True),
            sa.Column("send_at", sa.DateTime(), nullable=True),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("total_recipients", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["list_id"], ["email_marketing_lists.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_em_campaigns_tenant", "email_marketing_campaigns", ["tenant_type", "tenant_id"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "email_marketing_campaigns" in tables:
        op.drop_table("email_marketing_campaigns")
    if "email_marketing_contacts" in tables:
        op.drop_table("email_marketing_contacts")
    if "email_marketing_lists" in tables:
        op.drop_table("email_marketing_lists")

