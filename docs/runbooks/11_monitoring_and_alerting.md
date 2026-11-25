# Runbook 11: Monitoring, Exporters & Alert Delivery

## Objective
Standard operating procedure for RivinityADS observability infrastructure: Prometheus scraping, dependency exporters, worker heartbeat telemetry, Alertmanager routing, alert silences, and end-to-end synthetic alert verification.

---

## 1. Monitoring Architecture & Compose Services
The observability stack runs under the `monitoring` Compose profile in `backend/docker-compose.prod.yml`:
- **`prometheus` (`prom/prometheus:v2.54.1`)**: Scrapes targets on `liteads-internal` network every 10–15s and evaluates `backend/deployment/prometheus/alert_rules.yml`.
- **`alertmanager` (`prom/alertmanager:v0.27.0`)**: Routes firing alerts by `severity` using `backend/deployment/alertmanager/alertmanager.yml` with webhook URLs injected at startup from `.env.prod`.
- **`redis-exporter` (`oliver006/redis_exporter:v1.63.0`)**: Authenticated Redis health & memory telemetry (`redis_up`).
- **`postgres-exporter` (`prometheuscommunity/postgres-exporter:v0.15.0`)**: PostgreSQL connectivity & connection pool telemetry (`pg_up`).
- **`blackbox-exporter` (`prom/blackbox-exporter:v0.25.0`)**: HTTP 200 readiness probing against `http://ad-server:8000/health/ready` (`probe_success`).
- **`grafana` (`grafana/grafana:11.2.0`)**: Provisioned dashboards (anonymous access disabled).

To start the monitoring stack alongside production services:
```bash
docker compose --env-file .env.prod -f backend/docker-compose.prod.yml --profile monitoring up -d
```

---

## 2. Alert Ownership & Severity Tiers

| Severity | Receiver | Response SLA | Escalation Path |
| :--- | :--- | :--- | :--- |
| **`critical`** | `critical-pager` (`ALERTMANAGER_CRITICAL_WEBHOOK_URL`) | **< 15 minutes** (24/7 page) | On-call SRE -> Platform Lead -> Engineering Director |
| **`warning`** | `warning-webhook` (`ALERTMANAGER_WARNING_WEBHOOK_URL`) | **Next business hour / < 4 hours** | Ops Slack triage channel -> Service Owner |

### Alert Reference Anchors

<a id="runbook-alerts-5xx"></a>
#### `AdServerElevatedErrorRate` (Critical)
- Verify API logs:
  ```bash
  docker compose --env-file .env.prod -f backend/docker-compose.prod.yml logs --tail 200 ad-server
  ```
- Inspect `/health/dependencies` and initiate `Runbook 02` rollback if triggered by a recent deployment.

<a id="runbook-alerts-readiness"></a>
#### `AdServerReadinessProbeFailed` (Critical)
- Check `ad-server` container status and readiness endpoint:
  ```bash
  docker compose --env-file .env.prod -f backend/docker-compose.prod.yml exec ad-server curl -i -s http://localhost:8000/health/ready
  ```
- Check `DatabaseDependencyUnreachable` / `RedisDependencyUnreachable` (`inhibit_rules` suppress downstream alerts when PostgreSQL is unreachable).

<a id="runbook-alerts-workers"></a>
#### `DedicatedWorkerExecutionStalled` (Critical)
- `liteads_worker_last_heartbeat_timestamp{worker_type}` is published by `optimization`, `webhook`, `notification`, and `digest` workers into Redis and synced on `ad-server:8000/metrics`.
- Inspect container health and restart the stalled worker:
  ```bash
  docker compose --env-file .env.prod -f backend/docker-compose.prod.yml ps webhook-worker optimization-worker notification-worker digest-worker
  ```

---

## 3. Silencing Alerts During Planned Maintenance
Create a time-bounded silence in Alertmanager before planned database upgrades or migrations (never delete alert rules):
```bash
docker compose --env-file .env.prod -f backend/docker-compose.prod.yml exec alertmanager \
  amtool --alertmanager.url=http://localhost:9093 silence add \
    alertname="DatabaseDependencyUnreachable" \
    --duration="30m" \
    --author="ops-oncall" \
    --comment="Planned PostgreSQL maintenance window"
```
List or expire active silences:
```bash
docker compose --env-file .env.prod -f backend/docker-compose.prod.yml exec alertmanager \
  amtool --alertmanager.url=http://localhost:9093 silence query
```

---

## 4. Receiver Outage & Troubleshooting
1. Confirm `ALERTMANAGER_CRITICAL_WEBHOOK_URL` and `ALERTMANAGER_WARNING_WEBHOOK_URL` in `.env.prod` are valid HTTPS endpoints (not `.invalid` placeholders).
2. Inspect Alertmanager notification errors:
   ```bash
   docker compose --env-file .env.prod -f backend/docker-compose.prod.yml logs --tail 100 alertmanager
   ```
3. Verify Prometheus sees Alertmanager and all scrape targets are `UP`:
   ```bash
   docker compose --env-file .env.prod -f backend/docker-compose.prod.yml exec prometheus \
     wget -qO- http://localhost:9090/api/v1/targets | grep -E '"job"|"health"'
   ```

---

## 5. Synthetic End-to-End Alert Delivery Drill (Staging / Production)
Execute this drill after configuring real runtime webhook URLs in `.env.prod` to verify end-to-end delivery from Alertmanager to your external receiver:
```bash
docker compose --env-file .env.prod -f backend/docker-compose.prod.yml exec alertmanager \
  amtool --alertmanager.url=http://localhost:9093 alert add \
    RivinityADSSyntheticTestAlert \
    severity=critical \
    service=rivinity-ads \
    --annotation=summary="Synthetic Alert Delivery Verification" \
    --annotation=description="End-to-end Alertmanager to webhook receiver test"
```
**Acceptance Criteria:**
1. Prometheus `/api/v1/targets` shows `prometheus`, `liteads`, `redis`, `postgres`, and `blackbox-http` in `health: "up"`.
2. Prometheus `/api/v1/alertmanagers` shows `http://alertmanager:9093/api/v2/alerts` active.
3. The external receiver (`critical-pager` Slack/PagerDuty/Webhook) receives `RivinityADSSyntheticTestAlert` within 30 seconds and receives the resolved notification once expired.
