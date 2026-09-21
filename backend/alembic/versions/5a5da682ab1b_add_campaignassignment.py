"""Add CampaignAssignment

Revision ID: 5a5da682ab1b
Revises: 01f780f77602
Create Date: 2026-09-18 16:42:56.034517

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a5da682ab1b'
down_revision: Union[str, Sequence[str], None] = '01f780f77602'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('campaign_assignments',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('campaign_id', sa.Integer(), nullable=False),
    sa.Column('publisher_id', sa.Integer(), nullable=False),
    sa.Column('slot_id', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['publisher_id'], ['publishers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['slot_id'], ['ad_slots.slot_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('campaign_assignments')
