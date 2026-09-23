"""add_phase6_publisher_platform

Revision ID: 726f7b71e811
Revises: 4364f18e3808
Create Date: 2026-09-23 13:15:06.571070

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '726f7b71e811'
down_revision: Union[str, Sequence[str], None] = '4364f18e3808'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create publisher_properties table
    op.create_table('publisher_properties',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('publisher_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('domain', sa.String(length=255), nullable=False),
    sa.Column('status', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    sa.ForeignKeyConstraint(['publisher_id'], ['publishers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )

    # 2. Add property_id and floor_price to ad_slots
    op.add_column('ad_slots', sa.Column('floor_price', sa.Numeric(precision=12, scale=6), nullable=False, server_default='0.0'))
    op.add_column('ad_slots', sa.Column('property_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_ad_slots_property_id', 'ad_slots', 'publisher_properties', ['property_id'], ['id'], ondelete='SET NULL')

    # 3. Add slot_id to publisher_daily_stats
    op.add_column('publisher_daily_stats', sa.Column('slot_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_pub_daily_stats_slot_id', 'publisher_daily_stats', 'ad_slots', ['slot_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    # 1. Drop constraints and columns from publisher_daily_stats
    op.drop_constraint('fk_pub_daily_stats_slot_id', 'publisher_daily_stats', type_='foreignkey')
    op.drop_column('publisher_daily_stats', 'slot_id')

    # 2. Drop constraints and columns from ad_slots
    op.drop_constraint('fk_ad_slots_property_id', 'ad_slots', type_='foreignkey')
    op.drop_column('ad_slots', 'property_id')
    op.drop_column('ad_slots', 'floor_price')

    # 3. Drop publisher_properties table
    op.drop_table('publisher_properties')
