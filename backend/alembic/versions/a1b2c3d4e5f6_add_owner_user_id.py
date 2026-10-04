"""add_owner_user_id

Revision ID: a1b2c3d4e5f6
Revises: 726f7b71e811
Create Date: 2026-10-04 23:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '726f7b71e811'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Add owner_user_id to advertisers
    op.add_column('advertisers', sa.Column('owner_user_id', sa.Integer(), nullable=True))
    op.create_index('idx_advertisers_owner_user_id', 'advertisers', ['owner_user_id'])
    op.create_foreign_key('fk_advertisers_owner_user_id', 'advertisers', 'users', ['owner_user_id'], ['id'], ondelete='SET NULL')

    # Add owner_user_id to publishers
    op.add_column('publishers', sa.Column('owner_user_id', sa.Integer(), nullable=True))
    op.create_index('idx_publishers_owner_user_id', 'publishers', ['owner_user_id'])
    op.create_foreign_key('fk_publishers_owner_user_id', 'publishers', 'users', ['owner_user_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    # Drop from publishers
    op.drop_constraint('fk_publishers_owner_user_id', 'publishers', type_='foreignkey')
    op.drop_index('idx_publishers_owner_user_id', table_name='publishers')
    op.drop_column('publishers', 'owner_user_id')

    # Drop from advertisers
    op.drop_constraint('fk_advertisers_owner_user_id', 'advertisers', type_='foreignkey')
    op.drop_index('idx_advertisers_owner_user_id', table_name='advertisers')
    op.drop_column('advertisers', 'owner_user_id')
