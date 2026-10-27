"""Phase 5: Optimization Loop Reliability - suggestions dedup_key and optimization_decisions audit table

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-10-22 16:30:00.000000

Phase 5:
- Add nullable dedup_key column to suggestions table
- Add unique partial index uq_suggestions_pending_dedup on (campaign_id, dedup_key) WHERE status = 'PENDING'
- Create optimization_decisions audit log table with foreign key to campaigns (CASCADE)
- Add indexes for decision_id, campaign_id, and created_at
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply Phase 5 optimization reliability schema changes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Update suggestions table
    if "suggestions" in tables:
        cols = [c["name"] for c in inspector.get_columns("suggestions")]
        if "dedup_key" not in cols:
            op.add_column("suggestions", sa.Column("dedup_key", sa.String(length=128), nullable=True))
            op.create_index("ix_suggestions_dedup_key", "suggestions", ["dedup_key"])

        indexes = [idx["name"] for idx in inspector.get_indexes("suggestions")]
        if "uq_suggestions_pending_dedup" not in indexes:
            op.create_index(
                "uq_suggestions_pending_dedup",
                "suggestions",
                ["campaign_id", "dedup_key"],
                unique=True,
                postgresql_where=sa.text("status = 'PENDING'"),
                sqlite_where=sa.text("status = 'PENDING'"),
            )

    # 2. Create optimization_decisions table
    if "optimization_decisions" not in tables:
        op.create_table(
            "optimization_decisions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("decision_id", sa.String(length=64), nullable=False),
            sa.Column("campaign_id", sa.Integer(), nullable=False),
            sa.Column("mode", sa.String(length=32), nullable=False),
            sa.Column("action", sa.String(length=64), nullable=True),
            sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
            sa.Column("policy_version", sa.String(length=32), nullable=False, server_default="v1.0"),
            sa.Column("outcome", sa.String(length=32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("target_payload", sa.JSON(), nullable=True),
            sa.Column("evidence", sa.JSON(), nullable=True),
            sa.Column("change_applied", sa.JSON(), nullable=True),
            sa.Column("actor", sa.String(length=64), nullable=False, server_default="system_optimizer"),
            sa.Column("execution_status", sa.String(length=32), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_optimization_decisions_decision_id", "optimization_decisions", ["decision_id"], unique=True)
        op.create_index("ix_optimization_decisions_campaign_id", "optimization_decisions", ["campaign_id"])
        op.create_index("ix_optimization_decisions_created_at", "optimization_decisions", ["created_at"])


def downgrade() -> None:
    """Revert Phase 5 schema changes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "optimization_decisions" in tables:
        op.drop_index("ix_optimization_decisions_created_at", table_name="optimization_decisions")
        op.drop_index("ix_optimization_decisions_campaign_id", table_name="optimization_decisions")
        op.drop_index("ix_optimization_decisions_decision_id", table_name="optimization_decisions")
        op.drop_table("optimization_decisions")

    if "suggestions" in tables:
        indexes = [idx["name"] for idx in inspector.get_indexes("suggestions")]
        if "uq_suggestions_pending_dedup" in indexes:
            op.drop_index("uq_suggestions_pending_dedup", table_name="suggestions")
        cols = [c["name"] for c in inspector.get_columns("suggestions")]
        if "dedup_key" in cols:
            if "ix_suggestions_dedup_key" in indexes:
                op.drop_index("ix_suggestions_dedup_key", table_name="suggestions")
            op.drop_column("suggestions", "dedup_key")
