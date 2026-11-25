# Runbook 04: Production Key & Secret Rotation

## Objective
Standard operating procedures for rotating JWT signing keys, ad tracking HMAC secrets, payment webhook secrets, and database credentials without disrupting active user sessions or ad-serving traffic.

---

## 1. JWT Signing Secret Rotation (Dual-Key Window)
To avoid immediately invalidating all active advertiser/admin sessions, rotate JWT secrets using a dual-key validation window:
1. **Prepare New Secret:** Generate a cryptographically secure 64-character secret:
   ```bash
   openssl rand -hex 32
   ```
2. **Phase 1 (Dual-Verification):**
   - Set `LITEADS_AUTH__JWT_SECRET_PREVIOUS` to the current active secret.
   - Set `LITEADS_AUTH__JWT_SECRET` to the newly generated secret.
   - New tokens are signed with the new secret; existing tokens signed with the previous secret remain valid until their expiration (default: 60 minutes).
3. **Deploy API Instances:**
   ```bash
   docker compose --env-file .env.prod -f backend/docker-compose.prod.yml up -d --no-deps ad-server
   ```
4. **Phase 2 (Deprecate Old Secret):**
   - After the token expiration window (e.g., 2 hours), remove `LITEADS_AUTH__JWT_SECRET_PREVIOUS`.
   - Redeploy API instances.

---

## 2. Tracking Secret Rotation (HMAC URLs)
Tracking tokens (impressions, clicks, conversions) are time-limited (TTL default: 600s / 10 minutes).
1. Generate the new tracking secret (&ge; 32 characters):
   ```bash
   openssl rand -hex 32
   ```
2. Configure during low-traffic maintenance window or dual-acceptance period:
   - Tokens generated previously will expire naturally within 10 minutes.
   - Update `LITEADS_TRACKING__SECRET` in `.env.prod`.
   - Restart `ad-server` containers.

---

## 3. Razorpay Webhook Secret Rotation
1. Log in to the Razorpay Dashboard -> Settings -> Webhooks.
2. Generate a new webhook secret.
3. Update `RAZORPAY_WEBHOOK_SECRET` in `.env.prod`.
4. Restart the API servers:
   ```bash
   docker compose --env-file .env.prod -f backend/docker-compose.prod.yml up -d --no-deps ad-server
   ```
5. Update and activate the new webhook secret in Razorpay Dashboard.
6. Trigger a test webhook event from Razorpay and verify `200 OK` in server logs.

---

## 4. Database Password Rotation
1. Connect to PostgreSQL as admin and alter the user password:
   ```sql
   ALTER USER liteads WITH PASSWORD '<new-secure-password>';
   ```
2. Update `LITEADS_DATABASE__PASSWORD` (and `POSTGRES_PASSWORD` if mirrored) in `.env.prod`.
3. Perform rolling restarts of all containers communicating with PostgreSQL:
   ```bash
   docker compose --env-file .env.prod -f backend/docker-compose.prod.yml restart ad-server webhook-worker optimization-worker notification-worker digest-worker
   ```
4. Verify `/health/dependencies` reports `database: ok`.

