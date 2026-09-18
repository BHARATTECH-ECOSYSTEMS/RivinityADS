"""Add PublisherApiKey table

Revision ID: 01f780f77602
Revises: 61c90e027f1a
Create Date: 2026-09-18 15:47:58.933700

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '01f780f77602'
down_revision: Union[str, Sequence[str], None] = '61c90e027f1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('DROP TABLE IF EXISTS publisher_api_keys CASCADE')
    op.create_table('publisher_api_keys',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('publisher_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('key_prefix', sa.String(length=32), nullable=False),
    sa.Column('key_hash', sa.String(length=255), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['publisher_id'], ['publishers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('publisher_api_keys')
