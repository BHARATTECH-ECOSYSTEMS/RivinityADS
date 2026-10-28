"""Phase 10B: Website Builder Data Model Migration

Revision ID: l4d5e6f7a8b9
Revises: k3c4d5e6f7a8
Create Date: 2026-10-28 14:00:00.000000

Phase 10B Schema Changes:
- Create website_projects table with tenant ownership (owner_user_id), unique slug,
  theme_config JSON, and lifecycle/monetization status fields.
- Create website_pages table with project foreign key, unique page slug per project,
  ordering index, and schema-versioned blocks JSON.
- Create website_versions table with project foreign key, unique version number per project,
  immutable snapshot JSON, and publication audit trail.
- Create website_contact_submissions table for capturing hosted form submissions.

Architectural Note on current_published_version_id:
To prevent circular foreign key dependencies between website_projects and website_versions,
current_published_version_id is created as an indexed Integer column without an explicit
foreign key constraint.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "l4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "k3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Create website_projects table
    if "website_projects" not in tables:
        op.create_table(
            "website_projects",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("owner_user_id", sa.Integer(), nullable=False),
            sa.Column("publisher_id", sa.Integer(), nullable=True),
            sa.Column("property_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=63), nullable=False),
            sa.Column("custom_domain", sa.String(length=255), nullable=True),
            sa.Column("template_id", sa.String(length=64), server_default="business_services", nullable=False),
            sa.Column("theme_config", sa.JSON(), nullable=False),
            sa.Column("status", sa.Integer(), server_default="0", nullable=False),
            sa.Column("monetization_status", sa.Integer(), server_default="0", nullable=False),
            sa.Column("monetization_consent", sa.JSON(), nullable=True),
            sa.Column("current_published_version_id", sa.Integer(), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["publisher_id"], ["publishers.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["property_id"], ["publisher_properties.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("slug", name="uq_website_projects_slug"),
            sa.UniqueConstraint("custom_domain", name="uq_website_projects_custom_domain"),
        )
        op.create_index("ix_website_projects_owner_user_id", "website_projects", ["owner_user_id"])
        op.create_index("ix_website_projects_publisher_id", "website_projects", ["publisher_id"])
        op.create_index("ix_website_projects_property_id", "website_projects", ["property_id"])
        op.create_index("ix_website_projects_slug", "website_projects", ["slug"], unique=True)
        op.create_index("ix_website_projects_status", "website_projects", ["status"])
        op.create_index("ix_website_projects_monetization_status", "website_projects", ["monetization_status"])
        op.create_index("ix_website_projects_is_deleted", "website_projects", ["is_deleted"])
        op.create_index("ix_website_projects_current_published_version_id", "website_projects", ["current_published_version_id"])

    # 2. Create website_pages table
    if "website_pages" not in tables:
        op.create_table(
            "website_pages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=100), nullable=False),
            sa.Column("is_home", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("order_index", sa.Integer(), server_default="0", nullable=False),
            sa.Column("seo_meta", sa.JSON(), nullable=False),
            sa.Column("blocks", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["website_projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "slug", name="uq_website_pages_project_slug"),
        )
        op.create_index("ix_website_pages_project_id", "website_pages", ["project_id"])
        op.create_index("ix_website_pages_project_order", "website_pages", ["project_id", "order_index"])

    # 3. Create website_versions table
    if "website_versions" not in tables:
        op.create_table(
            "website_versions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("published_by_user_id", sa.Integer(), nullable=False),
            sa.Column("snapshot_data", sa.JSON(), nullable=False),
            sa.Column("notes", sa.String(length=255), nullable=True),
            sa.Column("published_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("rollback_from_version_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["website_projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "version_number", name="uq_website_versions_project_version"),
        )
        op.create_index("ix_website_versions_project_id", "website_versions", ["project_id"])
        op.create_index("ix_website_versions_project_version", "website_versions", ["project_id", "version_number"])

    # 4. Create website_contact_submissions table
    if "website_contact_submissions" not in tables:
        op.create_table(
            "website_contact_submissions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("sender_name", sa.String(length=255), nullable=False),
            sa.Column("sender_email", sa.String(length=255), nullable=False),
            sa.Column("sender_phone", sa.String(length=64), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("submitted_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("client_ip", sa.String(length=45), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["website_projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_website_contact_submissions_project_id", "website_contact_submissions", ["project_id"])
    else:
        sub_cols = [c["name"] for c in inspector.get_columns("website_contact_submissions")]
        if "client_ip" not in sub_cols:
            op.add_column("website_contact_submissions", sa.Column("client_ip", sa.String(length=45), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "website_contact_submissions" in tables:
        op.drop_table("website_contact_submissions")
    if "website_versions" in tables:
        op.drop_table("website_versions")
    if "website_pages" in tables:
        op.drop_table("website_pages")
    if "website_projects" in tables:
        op.drop_table("website_projects")
