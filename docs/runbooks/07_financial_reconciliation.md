# Runbook 07: Financial Reconciliation & Ledger Auditing

## Objective
Procedures for executing daily financial reconciliation, detecting wallet/ledger drift, recovering failed payment outbox events, and performing auditable balance adjustments.

---

## 1. Accounting Invariants (Mandatory)
In Bharattech Ads, the financial state is governed by three strict invariants:
1. **Double-Entry Ledger Authority:** `advertisers.balance` is a cached projection; `transactions` is the immutable, append-only source of truth.
2. **Zero Floating-Point Drift:** All amounts are stored and computed as integer cents or fixed-precision Decimals (`NUMERIC(12, 4)`).
3. **No Unaudited Mutations:** Direct `UPDATE advertisers SET balance = ...` without a corresponding `transactions` entry is strictly prohibited.

---

## 2. Daily Reconciliation Routine
The automated reconciliation worker runs nightly at 00:00 UTC. To run the reconciliation audit on demand:
```bash
docker compose --env-file .env.prod -f backend/docker-compose.prod.yml exec ad-server \
  python -c "
import asyncio
from liteads.common.database import get_session
from liteads.ad_server.services.billing import reconcile_all_wallets

async def main():
    async for s in get_session():
        anomalies = await reconcile_all_wallets(s)
        print(f'Reconciliation completed. Discrepancies found: {len(anomalies)}')
        for a in anomalies:
            print(f'Advertiser {a.advertiser_id}: Wallet={a.wallet_balance}, Ledger={a.ledger_balance}, Drift={a.drift}')
        break

asyncio.run(main())
"
```

---

## 3. Investigating & Resolving Wallet Drift
If `LiteAdsFinancialLedgerDrift` alert fires:
1. Identify the affected advertiser ID from alert labels or query:
   ```sql
   SELECT 
       a.id AS advertiser_id,
       a.balance AS wallet_balance,
       COALESCE(SUM(t.amount), 0) AS ledger_sum,
       (a.balance - COALESCE(SUM(t.amount), 0)) AS drift
   FROM advertisers a
   LEFT JOIN transactions t ON a.id = t.advertiser_id
   GROUP BY a.id, a.balance
   HAVING (a.balance - COALESCE(SUM(t.amount), 0)) != 0;
   ```
2. **Root Cause Analysis:**
   - Inspect transaction logs for interrupted database sessions during balance reservation.
   - Check if an uncommitted spend reservation was released improperly.
3. **Resolution:**
   - Always resolve discrepancies by posting a compensating ledger entry with type `ADJUSTMENT` (or `REFUND`) and an explicit description.
   - Never overwrite the balance directly without an audit record.

---

## 4. Manual Ledger Adjustment with Audit Log
When issuing an operational refund (e.g., fraud mitigation or dispute resolution):
```sql
BEGIN;

-- 1. Insert immutable transaction entry
INSERT INTO transactions (
    id,
    advertiser_id,
    type,
    amount,
    currency,
    reference_id,
    description,
    is_cleared,
    created_at
) VALUES (
    gen_random_uuid(),
    1,
    'REFUND',
    25.0000,
    'USD',
    'INCIDENT-2026-11-12-FRAUD',
    'Approved operational refund for fraudulent ad unit activity per Runbook 06',
    true,
    NOW()
);

-- 2. Update cached advertiser balance atomically
UPDATE advertisers
SET 
    balance = balance + 25.0000,
    updated_at = NOW()
WHERE id = 1;

COMMIT;
```

---

## 5. Webhook Dead-Letter Queue (DLQ) Remediation
If Razorpay deposit webhooks fail to process after 5 retries, they are archived in the DLQ:
```sql
SELECT id, event_type, payload, last_error, retry_count 
FROM webhook_outbox 
WHERE status = 'failed'
ORDER BY created_at DESC;
```
To re-process a resolved failed deposit webhook:
```sql
UPDATE webhook_outbox 
SET status = 'pending', retry_count = 0 
WHERE id = '<failed-event-uuid>';
```
The `webhook-worker` will re-attempt delivery and update the advertiser balance within 60 seconds.

