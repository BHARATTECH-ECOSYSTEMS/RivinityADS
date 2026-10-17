"""phase5_work_unit_1

Revision ID: a2b372f5d6a1
Revises: 2615292b1cd8
Create Date: 2026-10-13 14:47:04.674315

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'a2b372f5d6a1'
down_revision: Union[str, Sequence[str], None] = '2615292b1cd8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema safely adding Phase 5 foundational fields."""
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)

    # 1. publisher_api_keys table
    key_cols = [c['name'] for c in inspector.get_columns('publisher_api_keys')]
    if 'is_sandbox' not in key_cols:
        op.add_column('publisher_api_keys', sa.Column('is_sandbox', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    if 'secret_hash' not in key_cols:
        op.add_column('publisher_api_keys', sa.Column('secret_hash', sa.String(length=255), nullable=True))
    if 'rate_limit_per_minute' not in key_cols:
        op.add_column('publisher_api_keys', sa.Column('rate_limit_per_minute', sa.Integer(), server_default=sa.text('60'), nullable=False))
    if 'daily_quota' not in key_cols:
        op.add_column('publisher_api_keys', sa.Column('daily_quota', sa.Integer(), server_default=sa.text('10000'), nullable=False))
    if 'last_used_at' not in key_cols:
        op.add_column('publisher_api_keys', sa.Column('last_used_at', sa.DateTime(), nullable=True))

    # Add check constraints on publisher_api_keys if running under PostgreSQL
    if conn.dialect.name == 'postgresql':
        op.create_check_constraint(
            'ck_publisher_api_keys_rate_limit_positive',
            'publisher_api_keys',
            'rate_limit_per_minute > 0',
        )
        op.create_check_constraint(
            'ck_publisher_api_keys_daily_quota_positive',
            'publisher_api_keys',
            'daily_quota > 0',
        )

    # 2. publishers table
    pub_cols = [c['name'] for c in inspector.get_columns('publishers')]
    if 'is_sandbox' not in pub_cols:
        op.add_column('publishers', sa.Column('is_sandbox', sa.Boolean(), server_default=sa.text('false'), nullable=False))

    # 3. ad_slots table
    slot_cols = [c['name'] for c in inspector.get_columns('ad_slots')]
    if 'is_chat_surface' not in slot_cols:
        op.add_column('ad_slots', sa.Column('is_chat_surface', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    """Downgrade schema removing Phase 5 foundational fields."""
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)

    # 1. Drop check constraints if postgresql
    if conn.dialect.name == 'postgresql':
        op.drop_constraint('ck_publisher_api_keys_daily_quota_positive', 'publisher_api_keys', type_='check')
        op.drop_constraint('ck_publisher_api_keys_rate_limit_positive', 'publisher_api_keys', type_='check')

    # 2. Drop columns from publisher_api_keys
    key_cols = [c['name'] for c in inspector.get_columns('publisher_api_keys')]
    for col in ['last_used_at', 'daily_quota', 'rate_limit_per_minute', 'secret_hash', 'is_sandbox']:
        if col in key_cols:
            op.drop_column('publisher_api_keys', col)

    # 3. Drop columns from publishers
    pub_cols = [c['name'] for c in inspector.get_columns('publishers')]
    if 'is_sandbox' in pub_cols:
        op.drop_column('publishers', 'is_sandbox')

    # 4. Drop columns from ad_slots
    slot_cols = [c['name'] for c in inspector.get_columns('ad_slots')]
    if 'is_chat_surface' in slot_cols:
        op.drop_column('ad_slots', 'is_chat_surface')
