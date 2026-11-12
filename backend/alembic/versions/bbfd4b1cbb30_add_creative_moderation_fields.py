"""add creative moderation fields

Revision ID: bbfd4b1cbb30
Revises: 497d2807f36c
Create Date: 2026-10-09 15:51:07.631709

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'bbfd4b1cbb30'
down_revision: Union[str, Sequence[str], None] = '497d2807f36c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns for moderation
    op.add_column('creatives', sa.Column('moderation_status', sa.Integer(), server_default='2', nullable=False))
    op.add_column('creatives', sa.Column('moderation_reason', sa.Text(), nullable=True))
    op.add_column('creatives', sa.Column('moderated_at', sa.DateTime(), nullable=True))
    op.add_column('creatives', sa.Column('moderated_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'creatives', 'users', ['moderated_by_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    # Remove columns
    inspector = sa.inspect(op.get_bind())
    for constraint in inspector.get_foreign_keys('creatives'):
        if constraint.get('constrained_columns') == ['moderated_by_id']:
            if constraint.get('name'):
                op.drop_constraint(constraint['name'], 'creatives', type_='foreignkey')
            break
    op.drop_column('creatives', 'moderated_by_id')
    op.drop_column('creatives', 'moderated_at')
    op.drop_column('creatives', 'moderation_reason')
    op.drop_column('creatives', 'moderation_status')
