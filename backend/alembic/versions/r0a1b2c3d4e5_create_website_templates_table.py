"""Create website_templates table and seed initial Open Design templates.

Revision ID: r0a1b2c3d4e5
Revises: q9c0d1e2f3a4
Create Date: 2026-11-02 16:30:00.000000

Schema changes:
- Creates `website_templates` table
- Seeds the initial 15 Open Design templates plus legacy compatibility templates
"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "r0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "q9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "website_templates" not in tables:
        op.create_table(
            "website_templates",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("category", sa.String(64), nullable=False, index=True),
            sa.Column("thumbnail_url", sa.Text(), nullable=True),
            sa.Column("badge", sa.String(32), nullable=True),
            sa.Column("default_theme_config", sa.JSON(), nullable=False),
            sa.Column("default_pages", sa.JSON(), nullable=False),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
                index=True,
            ),
            sa.Column("order_index", sa.Integer(), nullable=False, default=0, index=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    else:
        op.alter_column("website_templates", "thumbnail_url", type_=sa.Text())
        op.alter_column("website_templates", "created_at", server_default=sa.func.now())
        op.alter_column("website_templates", "updated_at", server_default=sa.func.now())

    # Seed initial templates
    from liteads.ad_server.services.website_templates_catalog import BUILTIN_TEMPLATES

    templates_table = sa.table(
        "website_templates",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("category", sa.String),
        sa.column("thumbnail_url", sa.Text),
        sa.column("badge", sa.String),
        sa.column("default_theme_config", sa.JSON),
        sa.column("default_pages", sa.JSON),
        sa.column("is_active", sa.Boolean),
        sa.column("order_index", sa.Integer),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    for tmpl in BUILTIN_TEMPLATES:
        # Check if already exists
        check_query = sa.select(templates_table.c.id).where(templates_table.c.id == tmpl["id"])
        existing = conn.execute(check_query).scalar_one_or_none()
        if existing is None:
            conn.execute(
                templates_table.insert().values(
                    id=tmpl["id"],
                    name=tmpl["name"],
                    description=tmpl["description"],
                    category=tmpl["category"],
                    thumbnail_url=tmpl.get("thumbnail_url"),
                    badge=tmpl.get("badge"),
                    default_theme_config=tmpl["default_theme_config"],
                    default_pages=tmpl["default_pages"],
                    is_active=tmpl.get("is_active", True),
                    order_index=tmpl.get("order_index", 0),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "website_templates" in tables:
        op.drop_table("website_templates")

