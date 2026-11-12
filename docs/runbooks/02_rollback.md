# Runbook 02: Emergency Rollback

## Objective
Procedures for rapidly rolling back code and database changes in the event of severe production regressions, elevated 5xx error rates, or data integrity anomalies.

---

## 1. When to Trigger a Rollback
Trigger an immediate rollback if any of the following occur within 30 minutes of deployment:
1. **HTTP 5xx Error Rate > 1%** sustained over 3 consecutive minutes (`LiteAdsHighHttpErrorRate`).
2. **Ad Serving Latency p95 > 500ms** sustained over 5 minutes (`LiteAdsAdServingLatencyDegraded`).
3. **Database Connection Saturation > 85%** or unrecoverable lock contention.
4. **Financial Invariant Failure:** Discrepancy between advertiser wallet balance and ledger entries detected.
5. **Critical Feature Regression:** Advertisers unable to fund wallets or tracking links returning signature failures.

---

## 2. DB Migration Downgrade Safety (Expand / Contract Model)
Database migrations must follow the **Expand / Contract pattern**:
- **Expand Phase (Current Release):** New nullable columns, new tables, or new views are added. Old code continues reading old columns.
- **Contract Phase (Subsequent Release):** Old columns are deprecated and removed only after all old code is replaced and verified.

### Safe Downgrade Guidelines
1. **Never drop a column** in a migration if a rollback may require the old code to read that column.
2. If the current release migration only *expanded* the schema (added nullable columns or new tables), **do NOT downgrade the database schema** during code rollback unless strictly necessary. Old code will simply ignore the new columns.
3. If an explicit downgrade is mandated:
   ```bash
   # Check current migration revision
   docker compose -f backend/docker-compose.prod.yml run --rm migration python backend/scripts/release_migration.py --current

   # Step back by 1 revision
   docker compose -f backend/docker-compose.prod.yml run --rm migration python backend/scripts/release_migration.py --rollback -1
   ```

---

## 3. Container & Image Rollback
1. **Identify Previous Stable Release SHA:**
   Check the deployment log or git tag for the previous stable commit (e.g., `git-sha-a1b2c3d`).
2. **Update Image Tags:**
   Update `docker-compose.prod.yml` or the deployment environment variable `IMAGE_TAG=<previous-sha>`.
3. **Re-deploy API & Workers to Previous Version:**
   ```bash
   docker compose -f backend/docker-compose.prod.yml up -d --no-deps ad-server webhook-worker optimization-worker notification-worker digest-worker
   ```
4. **Reload Reverse Proxy Cache:**
   ```bash
   docker compose -f backend/docker-compose.prod.yml exec proxy nginx -s reload
   ```

---

## 4. Post-Rollback Validation
1. Verify system health and error rates return to baseline:
   ```bash
   curl -f -s https://ads.bharattech.com/health/ready
   ```
2. Verify outbox queue processing resumes without errors:
   ```bash
   curl -s https://ads.bharattech.com/health/outbox | jq .
   ```
3. Convene an immediate incident debrief to analyze root cause before attempting redeployment.

