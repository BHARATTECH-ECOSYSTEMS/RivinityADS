"""add finance ledger and publisher balance

Revision ID: 4364f18e3808
Revises: 5a5da682ab1b
Create Date: 2026-09-21 16:10:50.570320

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4364f18e3808'
down_revision: Union[str, Sequence[str], None] = '5a5da682ab1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create transactions table
    op.create_table(
        'transactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('type', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'),
        sa.Column('advertiser_id', sa.Integer(), nullable=True),
        sa.Column('publisher_id', sa.Integer(), nullable=True),
        sa.Column('campaign_id', sa.Integer(), nullable=True),
        sa.Column('reference_id', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['advertiser_id'], ['advertisers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['publisher_id'], ['publishers.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('idx_transactions_reference_id'), 'transactions', ['reference_id'], unique=False)

    # 2. Create platform_settings table
    op.create_table(
        'platform_settings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('publisher_rev_share', sa.Numeric(precision=4, scale=3), nullable=False, server_default='0.700'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Insert default platform settings
    op.execute("INSERT INTO platform_settings (publisher_rev_share) VALUES (0.700)")

    # 3. Add balance and payout_details to publishers
    op.add_column('publishers', sa.Column('balance', sa.Numeric(precision=12, scale=6), nullable=False, server_default='0.000000'))
    op.add_column('publishers', sa.Column('payout_details', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('publishers', 'payout_details')
    op.drop_column('publishers', 'balance')
    op.drop_table('platform_settings')
    op.drop_index(op.f('idx_transactions_reference_id'), table_name='transactions')
    op.drop_table('transactions')
