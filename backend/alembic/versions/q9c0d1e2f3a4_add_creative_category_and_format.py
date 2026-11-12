"""Add category and format columns to creatives.

Revision ID: q9c0d1e2f3a4
Revises: p8b9c0d1e2f3
Create Date: 2026-10-30 15:30:00.000000

Schema changes:
- Adds `category` (String(64), nullable=True) to `creatives`
- Adds `format` (String(64), nullable=True) to `creatives`
- Backfills existing creatives based on creative_type
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "q9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "p8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "creatives" in tables:
        cols = [c["name"] for c in inspector.get_columns("creatives")]
        if "category" not in cols:
            op.add_column(
                "creatives",
                sa.Column("category", sa.String(length=64), nullable=True),
            )
        if "format" not in cols:
            op.add_column(
                "creatives",
                sa.Column("format", sa.String(length=64), nullable=True),
            )

        # Backfill existing creatives using a single statement
        conn.execute(
            sa.text(
                """
                UPDATE creatives
                SET
                    category = CASE
                        WHEN creative_type = 1 THEN 'banner'
                        WHEN creative_type IN (5, 6, 7) THEN 'ai_chatbot'
                        ELSE 'legacy'
                    END,
                    format = CASE
                        WHEN creative_type = 1 THEN 'banner'
                        WHEN creative_type = 5 THEN 'display_card'
                        WHEN creative_type = 6 THEN 'label_ad'
                        WHEN creative_type = 7 THEN 'element_ad'
                        ELSE 'legacy'
                    END
                WHERE category IS NULL OR format IS NULL
                """
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "creatives" in tables:
        cols = [c["name"] for c in inspector.get_columns("creatives")]
        if "format" in cols:
            op.drop_column("creatives", "format")
        if "category" in cols:
            op.drop_column("creatives", "category")
