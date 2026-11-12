"""Phase 6: Business profile intake, custom domain verification, webmaster tools, and multilingual website builder support.

Revision ID: v1w2x3y4z5a6
Revises: u1v2w3x4y5z6
Create Date: 2026-11-12 17:40:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "v1w2x3y4z5a6"
down_revision = "u1v2w3x4y5z6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add Phase 6 columns to website_projects
    op.add_column(
        "website_projects",
        sa.Column("business_profile", sa.JSON(), nullable=True),
    )
    op.add_column(
        "website_projects",
        sa.Column("custom_domain_status", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "website_projects",
        sa.Column("custom_domain_token", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "website_projects",
        sa.Column("custom_domain_verified_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "website_projects",
        sa.Column("webmaster_tools", sa.JSON(), server_default="{}", nullable=False),
    )
    op.add_column(
        "website_projects",
        sa.Column("primary_language", sa.String(length=10), server_default="en", nullable=False),
    )

    # 2. Add Phase 6 columns to website_pages
    op.add_column(
        "website_pages",
        sa.Column("language", sa.String(length=10), server_default="en", nullable=False),
    )
    op.add_column(
        "website_pages",
        sa.Column("translation_group_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_website_pages_project_lang",
        "website_pages",
        ["project_id", "language"],
    )
    op.create_index(
        "ix_website_pages_project_trans_group",
        "website_pages",
        ["project_id", "translation_group_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_website_pages_project_trans_group", table_name="website_pages")
    op.drop_index("ix_website_pages_project_lang", table_name="website_pages")
    op.drop_column("website_pages", "translation_group_id")
    op.drop_column("website_pages", "language")

    op.drop_column("website_projects", "primary_language")
    op.drop_column("website_projects", "webmaster_tools")
    op.drop_column("website_projects", "custom_domain_verified_at")
    op.drop_column("website_projects", "custom_domain_token")
    op.drop_column("website_projects", "custom_domain_status")
    op.drop_column("website_projects", "business_profile")

