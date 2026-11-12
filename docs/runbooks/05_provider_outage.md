# Runbook 05: Third-Party Provider Outage & Degraded Modes

## Objective
Procedures for managing upstream provider degradation (Razorpay payment gateway, Listmonk/SMTP email delivery, Laya sidecar) while maintaining 100% uptime for core ad serving.

---

## 1. Third-Party Isolation Philosophy
Core ad serving (`/api/v1/ads/request`) and tracking (`/api/v1/track/*`) must **never depend synchronously** on third-party APIs. All third-party interactions (billing, emails, LLM/embeddings) are managed via circuit breakers, local database outbox queues, or asynchronous background workers.

---

## 2. Payment Gateway Outage (Razorpay Down)
### Symptoms
- `LiteAdsPaymentWebhookBacklog` alert fires.
- Deposit creation API returns 502/504 errors when attempting to contact Razorpay.
- Users report failures on the wallet recharge screen.

### Triage & Fallback
1. **Outage Confirmation:** Check Razorpay status page (`status.razorpay.com`) and API error logs.
2. **Circuit Breaker Behavior:**
   - Ad serving is completely unaffected; advertisers with active balances continue serving ads.
   - For deposit creation: The UI displays "Payment gateway temporarily experiencing delays. Please retry in a few moments."
   - Webhook retries: Razorpay automatically queues and retries webhook delivery with exponential backoff for up to 24 hours.
3. **Recovery Action:**
   - Once Razorpay recovers, verify that delayed webhooks process idempotently without duplicate balance credits.
   - Run daily reconciliation query:
     ```bash
     python backend/scripts/reconcile_wallets.py --audit-only
     ```

---

## 3. Email Provider Outage (Listmonk / SMTP Down)
### Symptoms
- `LiteAdsNotificationDeliveryLag` alert fires.
- Email jobs accumulate in `notification_queue` with status `pending` or `failed`.

### Triage & Fallback
1. **Non-Blocking Operation:** Email notifications are queued asynchronously in PostgreSQL outbox tables. The API never blocks on SMTP calls.
2. **Backlog Buffer:**
   - Background worker pauses SMTP calls when circuit breaker trips (5 consecutive timeouts).
   - Failed notifications stay in `failed` status or retry with exponential backoff up to 5 attempts.
3. **Recovery Action:**
   - Restore SMTP service or point to backup provider (e.g., fallback AWS SES or SendGrid).
   - Re-queue failed notifications via admin endpoint or management script:
     ```sql
     UPDATE notification_outbox 
     SET status = 'pending', retry_count = 0 
     WHERE status = 'failed' AND created_at > NOW() - INTERVAL '24 hours';
     ```

---

## 4. Laya Sidecar Outage (Embedding / Creative Scoring Down)
### Symptoms
- `LiteAdsOptimizationCycleFailed` alert fires.
- Latency warning in `/health/dependencies` for `laya_sidecar`.

### Triage & Fallback
1. **Fallback Heuristic:**
   - When Laya sidecar is unreachable, the ranking engine automatically falls back to statistical CTR/CVR scoring without failing ad requests.
   - Optimization worker catches sidecar connection errors and logs warnings, preserving active campaign bids.
2. **Recovery Action:**
   - Check Laya container logs:
     ```bash
     docker compose -f backend/docker-compose.prod.yml logs --tail 100 laya
     ```
   - Restart Laya service if stalled:
     ```bash
     docker compose -f backend/docker-compose.prod.yml restart laya
     ```

