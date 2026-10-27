"""Phase 5 Corrective: Database-enforced fencing token and audit fencing tracking

Revision ID: h9c0d1e2f3a4
Revises: g8b9c0d1e2f3
Create Date: 2026-10-23 15:00:00.000000

Phase 5 Corrective Changes:
- Add optimization_fencing_token (BigInteger, default=0, not null) to campaigns table
- Add fencing_token (BigInteger, nullable=True) to optimization_decisions table
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "h9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "g8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply Phase 5 corrective schema changes for database fencing."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Update campaigns table with optimization_fencing_token
    if "campaigns" in tables:
        cols = [c["name"] for c in inspector.get_columns("campaigns")]
        if "optimization_fencing_token" not in cols:
            op.add_column(
                "campaigns",
                sa.Column(
                    "optimization_fencing_token",
                    sa.BigInteger(),
                    nullable=False,
                    server_default="0",
                ),
            )

    # 2. Update optimization_decisions table with fencing_token
    if "optimization_decisions" in tables:
        cols = [c["name"] for c in inspector.get_columns("optimization_decisions")]
        if "fencing_token" not in cols:
            op.add_column(
                "optimization_decisions",
                sa.Column("fencing_token", sa.BigInteger(), nullable=True),
            )


def downgrade() -> None:
    """Revert Phase 5 corrective schema changes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "optimization_decisions" in tables:
        cols = [c["name"] for c in inspector.get_columns("optimization_decisions")]
        if "fencing_token" in cols:
            op.drop_column("optimization_decisions", "fencing_token")

    if "campaigns" in tables:
        cols = [c["name"] for c in inspector.get_columns("campaigns")]
        if "optimization_fencing_token" in cols:
            op.drop_column("campaigns", "optimization_fencing_token")
