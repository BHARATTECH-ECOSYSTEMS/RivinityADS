"""Phase 3: Add idempotency_key to ad_events

Revision ID: e6f7a8b9c0d1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-21 12:30:00.000000

Phase 3:
- Add nullable idempotency_key column to ad_events table
- Safely backfill existing rows with deterministic keys ({request_id}:{creative_id or 0}:{event_type}), appending :dup:{id} for historical duplicates so zero data is lost
- Add unique constraint uq_ad_events_idempotency_key
- Add index ix_ad_events_idempotency_key
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add idempotency_key column, backfill, and add unique constraint."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = [col['name'] for col in inspector.get_columns('ad_events')]
    if 'idempotency_key' not in columns:
        op.add_column('ad_events', sa.Column('idempotency_key', sa.String(128), nullable=True))

    # Safely backfill existing rows deterministically
    rows = conn.execute(
        sa.text("SELECT id, request_id, creative_id, event_type FROM ad_events ORDER BY id ASC")
    ).fetchall()

    seen_counts: dict[str, int] = {}
    for row in rows:
        row_id = row[0]
        req_id = row[1]
        creative_id = row[2] if row[2] is not None else 0
        event_type = row[3]
        base_key = f"{req_id}:{creative_id}:{event_type}"

        seen_counts[base_key] = seen_counts.get(base_key, 0) + 1
        seq = seen_counts[base_key]
        if seq == 1:
            final_key = f"{base_key}:1"
        else:
            final_key = f"{base_key}:{seq}:dup:{row_id}"

        conn.execute(
            sa.text("UPDATE ad_events SET idempotency_key = :k WHERE id = :id"),
            {"k": final_key, "id": row_id},
        )


    # Re-inspect before creating constraints
    inspector = sa.inspect(conn)
    existing_constraints = [c['name'] for c in inspector.get_unique_constraints('ad_events')]
    if 'uq_ad_events_idempotency_key' not in existing_constraints:
        op.create_unique_constraint('uq_ad_events_idempotency_key', 'ad_events', ['idempotency_key'])

    existing_indexes = [idx['name'] for idx in inspector.get_indexes('ad_events')]
    if 'ix_ad_events_idempotency_key' not in existing_indexes:
        op.create_index('ix_ad_events_idempotency_key', 'ad_events', ['idempotency_key'])


def downgrade() -> None:
    """Downgrade schema: drop unique constraint, index, and column."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_indexes = [idx['name'] for idx in inspector.get_indexes('ad_events')]
    if 'ix_ad_events_idempotency_key' in existing_indexes:
        op.drop_index('ix_ad_events_idempotency_key', table_name='ad_events')

    existing_constraints = [c['name'] for c in inspector.get_unique_constraints('ad_events')]
    if 'uq_ad_events_idempotency_key' in existing_constraints:
        op.drop_constraint('uq_ad_events_idempotency_key', 'ad_events', type_='unique')

    columns = [col['name'] for col in inspector.get_columns('ad_events')]
    if 'idempotency_key' in columns:
        op.drop_column('ad_events', 'idempotency_key')
