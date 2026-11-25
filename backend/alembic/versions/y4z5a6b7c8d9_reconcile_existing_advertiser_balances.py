"""reconcile existing advertiser balances with the financial ledger

Revision ID: y4z5a6b7c8d9
Revises: x3y4z5a6b7c8
"""

from collections.abc import Sequence

from alembic import op

revision: str = "y4z5a6b7c8d9"
down_revision: str | Sequence[str] | None = "x3y4z5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO transactions (
            type, amount, currency, advertiser_id, reference_id, description
        )
        SELECT
            6,
            a.balance - COALESCE(SUM(t.amount), 0),
            'INR',
            a.id,
            'opening_balance:' || a.id::text,
            'Opening balance reconciliation during ledger migration'
        FROM advertisers AS a
        LEFT JOIN transactions AS t ON t.advertiser_id = a.id
        GROUP BY a.id, a.balance
        HAVING a.balance - COALESCE(SUM(t.amount), 0) <> 0
        """
    )


def downgrade() -> None:
    # Keep the adjustment rows: deleting financial history would break ledger immutability.
    pass
