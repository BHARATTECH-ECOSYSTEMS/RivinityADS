# Runbook 01: Production Deployment

## Objective
Standardized, zero-downtime, fail-safe deployment procedure for Bharattech Ads (RivinityADS) production clusters.

---

## 1. Pre-Deployment Checklist
Before triggering any production release:
1. **CI Verification:** Ensure all backend multi-phase tests (Phases 1–7) pass and frontend TypeScript build succeeds cleanly (`npm run build`).
2. **Environment Validation:** Verify `.env.prod` does not contain default passwords (`changeme`, `postgres`, etc.) and all secrets exceed 32 characters:
   ```bash
   LITEADS_ENV=prod
   LITEADS_AUTH__JWT_SECRET=<32+ chars>
   LITEADS_TRACKING__SECRET=<32+ chars>
   POSTGRES_PASSWORD=<strong-secret>
   REDIS_PASSWORD=<strong-secret>
   ```
3. **Database Backup:** Create a pre-deployment snapshot before running migrations:
   ```bash
   python backend/scripts/backup_db.py --backup-dir /var/backups/liteads --encrypt
   ```
4. **Image Tagging:** Verify that Docker images are built and pinned with immutable Git commit SHAs (no `:latest` tags in production).

---

## 2. Release Migration Step (Fail-Closed)
Migrations execute in an isolated release container before new API traffic is routed:
```bash
docker compose -f backend/docker-compose.prod.yml run --rm migration
```
*Note:* The container runs `python backend/scripts/release_migration.py --upgrade head` with a strict lock timeout. If migration fails, the deployment halts immediately; existing API replicas remain running without traffic disruption.

---

## 3. Container Rolling Upgrade Order
Execute container upgrades in strict dependency order:
1. **Core Data Stores:**
   - PostgreSQL 15.8 and Redis 7.2.6 (verify health checks report `healthy`).
2. **API Instances (Rolling Upgrade):**
   ```bash
   docker compose -f backend/docker-compose.prod.yml up -d --no-deps --build ad-server
   ```
3. **Dedicated Asynchronous Workers:**
   ```bash
   docker compose -f backend/docker-compose.prod.yml up -d --no-deps webhook-worker optimization-worker notification-worker digest-worker
   ```
4. **Edge Ingress / Reverse Proxy:**
   ```bash
   docker compose -f backend/docker-compose.prod.yml up -d --no-deps proxy
   ```

---

## 4. Post-Deployment Smoke Test
Verify system operational readiness immediately following container startup:
1. **Liveness & Readiness:**
   ```bash
   curl -f -s https://ads.bharattech.com/health | jq .
   curl -f -s https://ads.bharattech.com/health/ready | jq .
   curl -f -s https://ads.bharattech.com/health/dependencies | jq .
   ```
2. **Ad Serving Smoke Request:**
   ```bash
   curl -X POST https://ads.bharattech.com/api/v1/ads/request \
     -H "Content-Type: application/json" \
     -H "X-API-Key: <test-publisher-key>" \
     -d '{"unit_id": "test-unit-01", "ip": "1.1.1.1", "user_agent": "Mozilla/5.0 SmokeTest"}'
   ```
3. **Prometheus Metrics Scrape:**
   Verify Prometheus is scraping endpoints and error rates (`liteads_http_requests_total{status=~"5.."}`) remain 0.

---

## 5. Traffic Cutover & Monitoring
1. Monitor latency p95 and error rates in Grafana for 15 minutes post-deployment.
2. Confirm outbox diagnostic queues report zero backlog:
   ```bash
   curl -s https://ads.bharattech.com/health/outbox | jq .
   ```
3. Signal deployment completion in the engineering channel with the release git SHA and release timestamp.

