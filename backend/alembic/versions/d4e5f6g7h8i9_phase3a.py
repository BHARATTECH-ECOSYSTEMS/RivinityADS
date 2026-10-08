"""phase3a

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-10-07 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6g7h8i9'
down_revision = 'c3d4e5f6g7h8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Note: Status is an IntEnum stored as an Integer, so adding DRAFT=5 
    # does not require a database schema migration.
    
    # reference_id index dropping
    op.drop_index('ix_transactions_reference_id', table_name='transactions')
    op.create_unique_constraint('uq_transactions_reference_id', 'transactions', ['reference_id'])


def downgrade() -> None:
    op.drop_constraint('uq_transactions_reference_id', 'transactions', type_='unique')
    op.create_index('ix_transactions_reference_id', 'transactions', ['reference_id'], unique=False)
