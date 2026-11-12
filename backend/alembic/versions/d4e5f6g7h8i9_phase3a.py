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
    bind = op.get_bind()
    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in sa.inspect(bind).get_indexes("transactions")
    }
    constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in sa.inspect(bind).get_unique_constraints("transactions")
    }
    target_columns = ("reference_id",)

    target = constraints.get("uq_transactions_reference_id")
    if target is not None and target != target_columns:
        raise RuntimeError(
            "uq_transactions_reference_id exists with unexpected columns: "
            f"{target!r}; expected {target_columns!r}"
        )

    if target is None:
        for index_name in ("ix_transactions_reference_id", "idx_transactions_reference_id"):
            if index_name in indexes:
                if indexes[index_name] != target_columns:
                    raise RuntimeError(
                        f"{index_name} exists with unexpected columns: "
                        f"{indexes[index_name]!r}; expected {target_columns!r}"
                    )
                op.drop_index(index_name, table_name="transactions")
        op.create_unique_constraint(
            "uq_transactions_reference_id",
            "transactions",
            list(target_columns),
        )


def downgrade() -> None:
    bind = op.get_bind()
    constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in sa.inspect(bind).get_unique_constraints("transactions")
    }
    if "uq_transactions_reference_id" in constraints:
        if constraints["uq_transactions_reference_id"] != ("reference_id",):
            raise RuntimeError(
                "uq_transactions_reference_id exists with unexpected columns: "
                f"{constraints['uq_transactions_reference_id']!r}"
            )
        op.drop_constraint(
            "uq_transactions_reference_id",
            "transactions",
            type_="unique",
        )

    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in sa.inspect(bind).get_indexes("transactions")
    }
    if "idx_transactions_reference_id" not in indexes:
        op.create_index(
            "idx_transactions_reference_id",
            "transactions",
            ["reference_id"],
            unique=False,
        )
