"""phase5_work_unit_3_usage

Revision ID: b3c4d5e6f7a8
Revises: a2b372f5d6a1
Create Date: 2026-10-13 17:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = 'a2b372f5d6a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create api_key_daily_stats table for Phase 5 Work Unit 3."""
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    tables = inspector.get_table_names()

    if 'api_key_daily_stats' not in tables:
        op.create_table(
            'api_key_daily_stats',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('api_key_id', sa.Integer(), nullable=False),
            sa.Column('publisher_id', sa.Integer(), nullable=False),
            sa.Column('stat_date', sa.Date(), nullable=False),
            sa.Column('total_requests', sa.Integer(), server_default=sa.text('0'), nullable=False),
            sa.Column('admitted_requests', sa.Integer(), server_default=sa.text('0'), nullable=False),
            sa.Column('rate_limited_requests', sa.Integer(), server_default=sa.text('0'), nullable=False),
            sa.Column('quota_exceeded_requests', sa.Integer(), server_default=sa.text('0'), nullable=False),
            sa.Column('served_ads', sa.Integer(), server_default=sa.text('0'), nullable=False),
            sa.Column('is_sandbox', sa.Boolean(), server_default=sa.text('false'), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(['api_key_id'], ['publisher_api_keys.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['publisher_id'], ['publishers.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('api_key_id', 'stat_date', name='uq_api_key_daily_stats_key_date'),
        )
        op.create_index('ix_api_key_daily_stats_pub_date', 'api_key_daily_stats', ['publisher_id', 'stat_date'])


def downgrade() -> None:
    """Drop api_key_daily_stats table."""
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    tables = inspector.get_table_names()

    if 'api_key_daily_stats' in tables:
        op.drop_index('ix_api_key_daily_stats_pub_date', table_name='api_key_daily_stats')
        op.drop_table('api_key_daily_stats')
