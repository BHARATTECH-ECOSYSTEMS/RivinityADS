"""Backfill advertiser balances into the financial ledger.

Revision ID: y4z5a6b7c8d9
Revises: x3y4z5a6b7c8
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "y4z5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "x3y4z5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO transactions (
                type, amount, currency, advertiser_id, reference_id, description
            )
            SELECT
                CASE WHEN a.balance < 0 THEN 6 ELSE 1 END,
                SIGN(a.balance) * LEAST(
                    999999.999999::numeric,
                    ABS(a.balance::numeric)
                        - opening.entry_no * 999999.999999::numeric
                ),
                'INR',
                a.id,
                'opening_balance:advertiser:' || a.id::text || ':' || opening.entry_no::text,
                'Opening balance brought forward during ledger migration'
            FROM advertisers AS a
            CROSS JOIN LATERAL generate_series(
                0,
                CEIL(ABS(a.balance::numeric) / 999999.999999)::integer - 1
            ) AS opening(entry_no)
            WHERE a.balance <> 0
              AND NOT EXISTS (
                  SELECT 1
                  FROM transactions AS t
                  WHERE t.advertiser_id = a.id
              )
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM transactions
            WHERE reference_id LIKE 'opening_balance:advertiser:%'
              AND description = 'Opening balance brought forward during ledger migration'
            """
        )
    )
