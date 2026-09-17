"""update geo_locations unique constraint

Revision ID: 61c90e027f1a
Revises: 09062f9ba9c2
Create Date: 2026-09-18 01:06:13.945537

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '61c90e027f1a'
down_revision: Union[str, Sequence[str], None] = '09062f9ba9c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('uq_country_pincode', 'geo_locations', type_='unique')
    op.create_unique_constraint('uq_geo_location_full', 'geo_locations', ['country', 'state_code', 'city', 'pincode'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_geo_location_full', 'geo_locations', type_='unique')
    op.create_unique_constraint('uq_country_pincode', 'geo_locations', ['country', 'pincode'])
