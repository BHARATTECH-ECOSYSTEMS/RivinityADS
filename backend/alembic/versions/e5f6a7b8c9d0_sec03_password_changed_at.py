"""SEC-03: Add password_changed_at to users

Revision ID: e5f6a7b8c9d0
Revises: c4d5e6f7a8b9
Create Date: 2026-10-21 00:30:00.000000

SEC-03:
- Add nullable password_changed_at timestamp column to users table
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add password_changed_at column to users table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = [col['name'] for col in inspector.get_columns('users')]
    if 'password_changed_at' not in columns:
        op.add_column('users', sa.Column('password_changed_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema: drop password_changed_at column from users table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = [col['name'] for col in inspector.get_columns('users')]
    if 'password_changed_at' in columns:
        op.drop_column('users', 'password_changed_at')

