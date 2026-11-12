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
    bind = op.get_bind()
    constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in sa.inspect(bind).get_unique_constraints("geo_locations")
    }
    target_columns = ("country", "state_code", "city", "pincode")
    legacy_columns = ("country", "pincode")

    target = constraints.get("uq_geo_location_full")
    if target is not None and target != target_columns:
        raise RuntimeError(
            "uq_geo_location_full exists with unexpected columns: "
            f"{target!r}; expected {target_columns!r}"
        )

    legacy = constraints.get("uq_country_pincode")
    if legacy is not None:
        if legacy != legacy_columns:
            raise RuntimeError(
                "uq_country_pincode exists with unexpected columns: "
                f"{legacy!r}; expected {legacy_columns!r}"
            )
        op.drop_constraint("uq_country_pincode", "geo_locations", type_="unique")

    if target is None:
        op.create_unique_constraint(
            "uq_geo_location_full",
            "geo_locations",
            list(target_columns),
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in sa.inspect(bind).get_unique_constraints("geo_locations")
    }
    target_columns = ("country", "state_code", "city", "pincode")
    legacy_columns = ("country", "pincode")

    target = constraints.get("uq_geo_location_full")
    if target is not None:
        if target != target_columns:
            raise RuntimeError(
                "uq_geo_location_full exists with unexpected columns: "
                f"{target!r}; expected {target_columns!r}"
            )
        op.drop_constraint("uq_geo_location_full", "geo_locations", type_="unique")

    legacy = constraints.get("uq_country_pincode")
    if legacy is None:
        op.create_unique_constraint(
            "uq_country_pincode",
            "geo_locations",
            list(legacy_columns),
        )
    elif legacy != legacy_columns:
        raise RuntimeError(
            "uq_country_pincode exists with unexpected columns: "
            f"{legacy!r}; expected {legacy_columns!r}"
        )
