# Runbook 03: Encrypted Database Restoration & Disaster Recovery

## Objective
Step-by-step restoration of PostgreSQL from an encrypted, authenticated backup artifact produced by `backend/scripts/backup_db.py`.

---

## 1. Prerequisites
1. **Backup Artifact:** Encrypted backup archive (`*.sql.gz.enc`) and companion SHA-256 checksum manifest (`*.sha256`).
2. **Decryption Key:** The backup passphrase (`BACKUP_PASSPHRASE` or `BACKUP_ENCRYPTION_KEY` environment variable or `--passphrase` parameter).
3. **Target Database Instance:** A provisioned PostgreSQL server accessible via connection string.

---

## 2. Integrity & Authentication Verification
Before restoring into any database, verify the cryptographic integrity of the backup file:
```bash
# 1. Verify SHA-256 checksum
sha256sum -c backup_20261112_180000.sql.gz.enc.sha256
```
*Note:* The restoration script (`restore_db.py`) uses AES-256-GCM authenticated encryption. Any tampered ciphertext byte or altered GCM auth tag will cause immediate decryption failure before any database connection is opened. You can also run an offline verification drill using `--verify-only`:
```bash
python backend/scripts/restore_db.py \
  --input /var/backups/liteads/backup_20261112_180000.sql.gz.enc \
  --passphrase "$BACKUP_PASSPHRASE" \
  --verify-only
```

---

## 3. Restoration Procedure
Execute the automated restore script using `--verify` to inspect table counts and transaction records:
```bash
# Export the backup passphrase securely
export BACKUP_PASSPHRASE="<production-backup-passphrase>"

# Execute restoration to target database (add --allow-production-target when targeting production)
python backend/scripts/restore_db.py \
  --input /var/backups/liteads/backup_20261112_180000.sql.gz.enc \
  --passphrase "$BACKUP_PASSPHRASE" \
  --db-url "postgresql://${LITEADS_DATABASE__USER}:${LITEADS_DATABASE__PASSWORD}@postgres:5432/liteads" \
  --verify
```

---

## 4. WAL Archive Replay (Point-in-Time Recovery - PITR)
If PostgreSQL is configured with continuous archiving (`archive_mode = on`):
1. Restore the base backup as described in Step 3.
2. Create `/var/lib/postgresql/data/recovery.signal`.
3. In `postgresql.conf`, specify the restore command:
   ```conf
   restore_command = 'cp /var/backups/wal_archive/%f %p'
   recovery_target_time = '2026-11-12 17:45:00 UTC'
   ```
4. Start PostgreSQL and monitor log output until recovery completes and accepts write connections.

---

## 5. Post-Restoration Verification Queries
Run sanity queries to ensure ledger consistency and record integrity:
```sql
-- 1. Check total campaigns and advertisers
SELECT count(*) FROM advertisers;
SELECT count(*) FROM campaigns;

-- 2. Verify financial ledger balance consistency
SELECT 
    a.id AS advertiser_id,
    a.balance AS wallet_balance,
    COALESCE(SUM(t.amount), 0) AS calculated_ledger_balance,
    (a.balance - COALESCE(SUM(t.amount), 0)) AS drift
FROM advertisers a
LEFT JOIN transactions t ON a.id = t.advertiser_id
GROUP BY a.id, a.balance
HAVING (a.balance - COALESCE(SUM(t.amount), 0)) != 0;

-- 3. Verify Alembic schema migration version (must match authoritative head: x3y4z5a6b7c8)
SELECT version_num FROM alembic_version;
```
If the drift query returns 0 rows, the financial state is completely consistent and ad serving may safely be resumed.

