"""phase6_wu1_publisher_slot_stats

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-10-17 15:45:00.000000

Phase 6 Work Unit 1:
- Drop legacy unique constraint on (publisher_id, stat_date)
- Add unique constraint uq_pub_daily_stats_slot_date on (publisher_id, slot_id, stat_date)
- Add index idx_pub_daily_stats_lookup on (publisher_id, stat_date) for reporting queries
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade publisher_daily_stats unique constraint to include slot_id."""
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    
    # 1. Inspect existing unique constraints on publisher_daily_stats
    existing_uqs = {uq['name'] for uq in inspector.get_unique_constraints('publisher_daily_stats')}
    
    # Drop legacy uniqueness on (publisher_id, stat_date) if present
    legacy_uq = 'publisher_daily_stats_publisher_id_stat_date_key'
    if legacy_uq in existing_uqs:
        op.drop_constraint(legacy_uq, 'publisher_daily_stats', type_='unique')
    
    # 2. Add composite unique constraint over (publisher_id, slot_id, stat_date)
    new_uq = 'uq_pub_daily_stats_slot_date'
    if new_uq not in existing_uqs:
        op.create_unique_constraint(
            new_uq,
            'publisher_daily_stats',
            ['publisher_id', 'slot_id', 'stat_date']
        )
    
    # 3. Add lookup index over (publisher_id, stat_date) for fast date-range reporting
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('publisher_daily_stats')}
    new_idx = 'idx_pub_daily_stats_lookup'
    if new_idx not in existing_indexes:
        op.create_index(
            new_idx,
            'publisher_daily_stats',
            ['publisher_id', 'stat_date'],
            unique=False
        )


def downgrade() -> None:
    """Restore original unique constraint on publisher_daily_stats.
    
    NOTE: Downgrade limitation: If multiple slot rows exist for the same
    publisher on the same date, restoring uniqueness on (publisher_id, stat_date)
    will fail in PostgreSQL until duplicate date records are manually resolved.
    """
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('publisher_daily_stats')}
    if 'idx_pub_daily_stats_lookup' in existing_indexes:
        op.drop_index('idx_pub_daily_stats_lookup', table_name='publisher_daily_stats')
        
    existing_uqs = {uq['name'] for uq in inspector.get_unique_constraints('publisher_daily_stats')}
    if 'uq_pub_daily_stats_slot_date' in existing_uqs:
        op.drop_constraint('uq_pub_daily_stats_slot_date', 'publisher_daily_stats', type_='unique')
        
    legacy_uq = 'publisher_daily_stats_publisher_id_stat_date_key'
    if legacy_uq not in existing_uqs:
        op.create_unique_constraint(
            legacy_uq,
            'publisher_daily_stats',
            ['publisher_id', 'stat_date']
        )
