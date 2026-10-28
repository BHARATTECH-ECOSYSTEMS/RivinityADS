"""Phase 10E/10F: Publisher Property Verification and Website Consent Logs Migration

Revision ID: m5e6f7a8b9c0
Revises: l4d5e6f7a8b9
Create Date: 2026-10-28 17:30:00.000000

Phase 10E & 10F Schema Changes:
- Add verification columns to publisher_properties:
    - verification_status (String(50), default 'PENDING')
    - verification_method (String(50), default 'DNS_TXT')
    - verification_token (String(255), nullable)
    - challenge_expires_at (DateTime, nullable)
    - verified_at (DateTime, nullable)
    - last_checked_at (DateTime, nullable)
    - last_error (Text, nullable)
    - is_platform_subdomain (Boolean, default False)
- Create website_consent_logs table for auditable publisher monetization and visitor consent trails.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "m5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "l4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Add verification columns to publisher_properties if table exists
    if "publisher_properties" in tables:
        columns = [c["name"] for c in inspector.get_columns("publisher_properties")]

        if "verification_status" not in columns:
            op.add_column(
                "publisher_properties",
                sa.Column("verification_status", sa.String(length=50), server_default="PENDING", nullable=False),
            )
        if "verification_method" not in columns:
            op.add_column(
                "publisher_properties",
                sa.Column("verification_method", sa.String(length=50), server_default="DNS_TXT", nullable=False),
            )
        if "verification_token" not in columns:
            op.add_column(
                "publisher_properties",
                sa.Column("verification_token", sa.String(length=255), nullable=True),
            )
        if "challenge_expires_at" not in columns:
            op.add_column(
                "publisher_properties",
                sa.Column("challenge_expires_at", sa.DateTime(), nullable=True),
            )
        if "verified_at" not in columns:
            op.add_column(
                "publisher_properties",
                sa.Column("verified_at", sa.DateTime(), nullable=True),
            )
        if "last_checked_at" not in columns:
            op.add_column(
                "publisher_properties",
                sa.Column("last_checked_at", sa.DateTime(), nullable=True),
            )
        if "last_error" not in columns:
            op.add_column(
                "publisher_properties",
                sa.Column("last_error", sa.Text(), nullable=True),
            )
        if "is_platform_subdomain" not in columns:
            op.add_column(
                "publisher_properties",
                sa.Column("is_platform_subdomain", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            )

    # 2. Create website_consent_logs table
    if "website_consent_logs" not in tables:
        op.create_table(
            "website_consent_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("consent_type", sa.String(length=50), nullable=False),
            sa.Column("consent_scope", sa.JSON(), nullable=False),
            sa.Column("version", sa.String(length=32), server_default="v1.0", nullable=False),
            sa.Column("is_granted", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column("client_ip_hash", sa.String(length=64), nullable=True),
            sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
            sa.Column("recorded_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["website_projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_website_consent_logs_project_id", "website_consent_logs", ["project_id"])
        op.create_index("ix_website_consent_logs_consent_type", "website_consent_logs", ["consent_type"])
        op.create_index("ix_website_consent_logs_recorded_at", "website_consent_logs", ["recorded_at"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "website_consent_logs" in tables:
        op.drop_table("website_consent_logs")

    if "publisher_properties" in tables:
        columns = [c["name"] for c in inspector.get_columns("publisher_properties")]
        for col in [
            "is_platform_subdomain",
            "last_error",
            "last_checked_at",
            "verified_at",
            "challenge_expires_at",
            "verification_token",
            "verification_method",
            "verification_status",
        ]:
            if col in columns:
                op.drop_column("publisher_properties", col)
