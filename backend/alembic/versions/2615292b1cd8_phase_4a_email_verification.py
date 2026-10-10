"""Phase 4A email verification

Revision ID: 2615292b1cd8
Revises: bbfd4b1cbb30
Create Date: 2026-10-09 17:49:30.701085

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = '2615292b1cd8'
down_revision: Union[str, Sequence[str], None] = 'bbfd4b1cbb30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    
    # 1. Check and add email_verified to users
    columns = [col['name'] for col in inspector.get_columns('users')]
    if 'email_verified' not in columns:
        op.add_column('users', sa.Column('email_verified', sa.Boolean(), server_default='false', nullable=False))
        
    # 2. Check and add one_time_tokens table
    if not inspector.has_table('one_time_tokens'):
        op.create_table(
            'one_time_tokens',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('token_hash', sa.String(length=128), nullable=False),
            sa.Column('purpose', sa.String(length=32), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('used_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_one_time_tokens_token_hash'), 'one_time_tokens', ['token_hash'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    
    if inspector.has_table('one_time_tokens'):
        op.drop_index(op.f('ix_one_time_tokens_token_hash'), table_name='one_time_tokens')
        op.drop_table('one_time_tokens')
        
    columns = [col['name'] for col in inspector.get_columns('users')]
    if 'email_verified' in columns:
        op.drop_column('users', 'email_verified')
