# Runbook 01: Production Deployment

## Objective
Standardized, zero-downtime, fail-safe deployment procedure for Bharattech Ads (RivinityADS) production clusters.

---

## 1. Pre-Deployment Checklist
Before triggering any production release:
1. **CI Verification:** Ensure all backend multi-phase tests (Phases 1–7) pass and frontend TypeScript build succeeds cleanly (`npm run build`).
2. **Environment Provisioning & Validation:** Copy the canonical backend template to `.env.prod` at repository root (`.env.prod` is gitignored and must NEVER be committed). Verify no placeholder or default passwords (`changeme`, `postgres`, `liteads_password`, `your_...`) remain and all cryptographic secrets exceed 32 characters:
   ```bash
   cp backend/.env.example .env.prod
   ```
   Required variables in `.env.prod`:
   ```dotenv
   LITEADS_ENV=prod
   LITEADS_AUTH__JWT_SECRET=<32+ chars>
   LITEADS_TRACKING__SECRET=<32+ chars>
   LITEADS_DATABASE__USER=liteads
   LITEADS_DATABASE__PASSWORD=<strong-secret>
   LITEADS_REDIS__PASSWORD=<strong-secret>
   LITEADS_SERVER__FRONTEND_URL=https://ads.bharattech.com
   GRAFANA_ADMIN_PASSWORD=<strong-secret>
   TLS_DOMAIN=ads.bharattech.com
   CERTBOT_EMAIL=ops@bharattech.com
   ```
   Validate the rendered Compose specification before any container action:
   ```bash
   docker compose --env-file .env.prod -f backend/docker-compose.prod.yml config --quiet
   ```
3. **Database Backup:** Create a pre-deployment snapshot before running migrations:
   ```bash
   python backend/scripts/backup_db.py \
     --db-url "postgresql://${LITEADS_DATABASE__USER}:${LITEADS_DATABASE__PASSWORD}@postgres:5432/liteads" \
     --backup-dir /var/backups/liteads \
     --encrypt
   ```
   *(Ensure `BACKUP_PASSPHRASE` is exported in the environment or passed via `--passphrase`)*.
4. **Image Tagging:** Verify that Docker images are built and pinned with immutable Git commit SHAs or version tags (no `:latest` tags in production).
5. **Optimization Worker & External Dependencies:**
   - Verify `LITEADS_OPTIMIZATION__MODE` is explicitly configured or left to default to `suggestions_only` (zero automated campaign mutations). Supported modes: `suggestions_only`, `shadow`, `limited_pilot`, `autopilot`. Any invalid string fails closed at container startup.
   - Verify Laya external dependency settings: Laya is an external decision service (not run or bundled in `docker-compose.prod.yml`). Set `LAYA_ENABLED=false` (default) when not deployed, or set `LAYA_ENABLED=true` and provide `LAYA_BASE_URL` when connected to an external Laya cluster.
6. **TLS Certificate Bootstrap (`certs_data` Volume — Mandatory Before Starting `nginx`):**
   Nginx (`backend/deployment/nginx/nginx.prod.conf`) mounts `certs_data:/etc/nginx/certs:ro` and requires both `/etc/nginx/certs/fullchain.pem` and `/etc/nginx/certs/privkey.pem` to exist and be non-empty. Starting `nginx` before populating `certs_data` will fail immediately. Choose one provisioning path:
   - **Option A — Production ACME / Let's Encrypt Initial Issuance (Standalone while port 80 is free):**
     ```bash
     docker compose --env-file .env.prod -f backend/docker-compose.prod.yml --profile tls run --rm -p 80:80 --entrypoint sh certbot -c '
       certbot certonly --standalone --non-interactive --agree-tos \
         -m "${CERTBOT_EMAIL}" -d "${TLS_DOMAIN}" && \
       cp -L "/etc/letsencrypt/live/${TLS_DOMAIN}/fullchain.pem" /etc/nginx/certs/fullchain.pem && \
       cp -L "/etc/letsencrypt/live/${TLS_DOMAIN}/privkey.pem" /etc/nginx/certs/privkey.pem && \
       chmod 600 /etc/nginx/certs/privkey.pem
     '
     ```
   - **Option B — Externally Managed Certificates (Copy from secure host path into `certs_data`):**
     ```bash
     docker compose --env-file .env.prod -f backend/docker-compose.prod.yml --profile tls run --rm \
       --entrypoint sh -v /etc/ssl/rivinityads:/host-certs:ro certbot -c '
         cp -L /host-certs/fullchain.pem /etc/nginx/certs/fullchain.pem && \
         cp -L /host-certs/privkey.pem /etc/nginx/certs/privkey.pem && \
         chmod 600 /etc/nginx/certs/privkey.pem
       '
     ```
   - **Option C — Staging / Local Smoke-Test Ephemeral Self-Signed Bootstrap (Never commit keys):**
     ```bash
     mkdir -p /tmp/rivinity-selfsigned && \
     openssl req -x509 -nodes -newkey rsa:2048 -days 30 \
       -keyout /tmp/rivinity-selfsigned/privkey.pem \
       -out /tmp/rivinity-selfsigned/fullchain.pem \
       -subj "/CN=localhost" && \
     docker compose --env-file .env.prod -f backend/docker-compose.prod.yml --profile tls run --rm \
       --entrypoint sh -v /tmp/rivinity-selfsigned:/tmp/src:ro certbot -c '
         cp /tmp/src/fullchain.pem /etc/nginx/certs/fullchain.pem && \
         cp /tmp/src/privkey.pem /etc/nginx/certs/privkey.pem && \
         chmod 600 /etc/nginx/certs/privkey.pem
       ' && \
     rm -rf /tmp/rivinity-selfsigned
     ```
   - **Pre-Flight Verification (Required before `up -d nginx`):**
     ```bash
     docker compose --env-file .env.prod -f backend/docker-compose.prod.yml run --rm --no-deps --entrypoint sh nginx -c \
       'test -s /etc/nginx/certs/fullchain.pem && test -s /etc/nginx/certs/privkey.pem && echo "TLS certificates verified"'
     ```

---

## 2. Release Migration Step (Fail-Closed)
Migrations execute in an isolated release container before new API traffic is routed:
```bash
docker compose --env-file .env.prod -f backend/docker-compose.prod.yml run --rm migration
```
*Note:* The `migration` service executes `alembic upgrade head` against PostgreSQL before API workers boot. If migration fails, the deployment halts immediately; existing API replicas remain running without traffic disruption.

---

## 3. Container Rolling Upgrade Order
Execute container upgrades in strict dependency order:
1. **Core Data Stores:**
   - PostgreSQL 15.8 and Redis 7.2.6 (verify health checks report `healthy`).
   ```bash
   docker compose --env-file .env.prod -f backend/docker-compose.prod.yml up -d postgres redis
   ```
2. **API Instances (Rolling Upgrade):**
   ```bash
   docker compose --env-file .env.prod -f backend/docker-compose.prod.yml up -d --no-deps --build ad-server
   ```
3. **Dedicated Asynchronous Workers:**
   ```bash
   docker compose --env-file .env.prod -f backend/docker-compose.prod.yml up -d --no-deps webhook-worker optimization-worker notification-worker digest-worker
   ```
   *Note on `optimization-worker`:* Operates in `suggestions_only` mode by default. Recommends optimizations without mutating campaign budgets, bids, or statuses. Laya decision calls degrade fail-closed if `LAYA_ENABLED=false` or unreachable.
4. **Edge Ingress / TLS Reverse Proxy (`nginx`):**
   ```bash
   docker compose --env-file .env.prod -f backend/docker-compose.prod.yml up -d --no-deps nginx
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
   *Expected dependencies output when Laya is disabled (`LAYA_ENABLED=false`):*
   ```json
   {
     "status": "healthy",
     "dependencies": {
       "database": {"status": "ok"},
       "redis": {"status": "ok"},
       "laya_sidecar": {"status": "disabled"}
     }
   }
   ```
   *Note:* Core system readiness (`/health/ready`) is decoupled from Laya; it returns HTTP 200 as long as PostgreSQL and Redis are healthy.
2. **Ad Serving Smoke Request:**
   ```bash
   curl -X POST https://ads.bharattech.com/api/v1/ad/request \
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

---

## 6. TLS Certificate Renewal & Zero-Downtime Reload
While `nginx` is serving port 80 (`location /.well-known/acme-challenge/` mapped to `certbot_webroot:/var/www/certbot:ro`), renew certificates via webroot challenge and reload Nginx without dropping active connections:
```bash
docker compose --env-file .env.prod -f backend/docker-compose.prod.yml --profile tls run --rm \
  --entrypoint sh certbot -c '
    certbot renew --webroot -w /var/www/certbot --non-interactive \
      --deploy-hook "cp -L /etc/letsencrypt/live/${TLS_DOMAIN}/fullchain.pem /etc/nginx/certs/fullchain.pem && cp -L /etc/letsencrypt/live/${TLS_DOMAIN}/privkey.pem /etc/nginx/certs/privkey.pem && chmod 600 /etc/nginx/certs/privkey.pem"
  ' && \
docker compose --env-file .env.prod -f backend/docker-compose.prod.yml exec nginx nginx -s reload
```
Verify the active served certificate expiration date afterwards:
```bash
echo | openssl s_client -servername "${TLS_DOMAIN}" -connect "${TLS_DOMAIN}:443" 2>/dev/null | openssl x509 -noout -dates
```
