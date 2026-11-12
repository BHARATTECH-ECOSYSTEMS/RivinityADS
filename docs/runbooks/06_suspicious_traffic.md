# Runbook 06: Suspicious Traffic & Fraud Mitigation

## Objective
Procedures for identifying, triaging, and mitigating click fraud, scraping attacks, credential stuffing, and volumetric DDoS incidents.

---

## 1. Initial Triage & Anomaly Detection
Trigger an investigation if any of the following alerts fire:
- `LiteAdsConversionAnomalySpike`: Conversions exceed 3x standard hourly deviation.
- `LiteAdsHighHttpErrorRate`: Elevated 429 (Rate Limited) or 401/403 errors.
- Disproportionate click-through rate (CTR > 25%) on specific publisher inventory.

---

## 2. IP & User-Agent Analysis
Inspect reverse proxy access logs to isolate abusive IPs, CIDR blocks, or suspicious user-agents:
```bash
# Top 20 requesting IP addresses in the past hour
docker compose -f backend/docker-compose.prod.yml exec proxy \
  awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -nr | head -n 20

# Top requesting User-Agents
docker compose -f backend/docker-compose.prod.yml exec proxy \
  awk -F\" '{print $6}' /var/log/nginx/access.log | sort | uniq -c | sort -nr | head -n 10
```

---

## 3. Dynamic IP Ban via Redis / Nginx
1. **Immediate Redis Blocklist:**
   Block an offending IP address directly in Redis for 24 hours:
   ```bash
   docker compose -f backend/docker-compose.prod.yml exec redis \
     redis-cli -a "$REDIS_PASSWORD" SET "blocklist:ip:198.51.100.42" 1 EX 86400
   ```
2. **Nginx Edge Block (Static CIDR):**
   Add the offending IP or subnet to `/etc/nginx/conf.d/blocklist.conf`:
   ```nginx
   deny 198.51.100.0/24;
   deny 203.0.113.50;
   ```
   Reload Nginx without downtime:
   ```bash
   docker compose -f backend/docker-compose.prod.yml exec proxy nginx -s reload
   ```

---

## 4. Rate Limit Adjustments
If scraping or distributed credential stuffing is detected across dispersed IPs:
1. Tighten the Redis rate limiter in `backend/liteads/common/config.py` or `.env.prod`:
   ```bash
   # Tighten auth rate limit from 5/min to 2/min
   LITEADS_AUTH_RATE_LIMIT=2/minute
   ```
2. Restart `ad-server` containers:
   ```bash
   docker compose -f backend/docker-compose.prod.yml restart ad-server
   ```

---

## 5. Click Fraud Settlement & Advertiser Protection
If fraudulent clicks were attributed to advertiser campaigns:
1. Identify the affected publisher ad unit ID and timestamp window.
2. Invalidate fraudulent impressions and clicks in the analytics table:
   ```sql
   UPDATE tracking_events 
   SET is_fraud = true, fraud_reason = 'high_frequency_bot_cluster'
   WHERE unit_id = '<abusive-unit-id>' AND created_at BETWEEN '<start>' AND '<end>';
   ```
3. Issue an automatic refund to the affected advertiser wallets via ledger credit (refer to Runbook 07).

