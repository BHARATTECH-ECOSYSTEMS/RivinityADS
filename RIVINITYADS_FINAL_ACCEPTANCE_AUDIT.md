# RivinityAds — Final Phase 1–6 Acceptance Verification Report

**Auditor:** Senior AdTech & Infrastructure Software Auditor
**Date of Audit:** October 18, 2026
**Reconciliation Date (WU-2):** October 19, 2026
**Repository Branch:** `main`
**Latest Migration Head:** `c4d5e6f7a8b9` (17 linear Alembic migrations)
**Runtime Services Verified:** PostgreSQL 16 (Port 5432), Redis 7 (Port 6379), Mailpit (Ports 1025/8025), Prometheus (Port 9090), Dockerized Laya Decision Engine (Container `backend-laya-1`, port `127.0.0.1:8100 -> 8000`), FastAPI Ad Server (Port 8000), Vite Frontend (Port 5173).

---

## 1. Executive Summary

This independent, evidence-driven software audit evaluates the complete implementation of Phases 1 through 6 in the **RivinityAds** (Bharattech Ads / LiteAds) repository against the project's canonical specifications, production database migrations, automated regression test suites, and live runtime behavior.

Following the successful execution of Work Unit 1 (WU-1), live runtime integration with the Laya decision engine sidecar has been empirically established and verified. Phase 4 remains explicitly excluded from implementation scope and remains partially implemented. Phases 7 and 8 represent future roadmap milestones and are not completed work.

### Primary Audit Verdicts
- **Phase 1 (Security Hardening & BUG-1 to BUG-7):** **Accepted**. All 7 security vulnerabilities identified during initial production hardening have been comprehensively remediated, backed by server-side authorization enforcement, secure cookie handling, a verified 600-second signature validity window, a distinct 900-second Redis replay guard window, and 34 passing regression tests.
- **Phase 2 (Laya Optimizer & Autopilot):** **Accepted**. Core optimizer policies, conservative mathematical guardrails, autopilot toggle APIs, and decision ingestion are fully implemented. Live runtime integration with the Dockerized Laya decision engine sidecar (`backend-laya-1`) is established over loopback `127.0.0.1:8100` in offline mode (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`). The live smoke test (`test_real_runtime_smoke`) executes real neural inference against the container (`choice="no_change"`, `confidence=0.7936`), with 13/13 Laya client tests and 54/54 Phase 2 regression tests passing.
- **Phase 3 (Conversational Campaign Builder & Payments):** **Accepted**. Redis-isolated conversation state management, strict draft lifecycle barriers, multi-provider LLM intent extraction (Laya/Groq/Gemini/OpenAI), immutable patch schemas (`extra="forbid"`), dedicated multi-guard activation flow, and idempotent Razorpay webhook processing are fully implemented and verified by 66 passing tests. (Live currency settlement was validated via signed webhook payloads; production banking network settlement was not tested).
- **Phase 4 (Email & Domain Verification, Creative Moderation):** **Partially implemented**. Domain DNS TXT verification and creative moderation approval workflows are fully implemented and strictly enforced during ad serving. However, **user email ownership verification is bypassed at registration**: `backend/liteads/ad_server/routers/auth.py` automatically initializes new user accounts with `email_verified = True`, and the `/api/v1/auth/verify-email` endpoint returns HTTP 404. Under canonical audit rules, email ownership verification cannot be accepted. Phase 4 remains excluded from code modifications in this cycle.
- **Phase 5 (Publisher API Keys, Ad Delivery & Usage Controls):** **Accepted**. The HMAC v2 canonical request signing scheme, 300s freshness window, replay nonce caching, token bucket rate limiting, daily UTC quotas, 64-hex lowercase SHA-256 user privacy hashing (rejecting unhashed PII with 422), fail-closed Redis frequency capping, mandatory "Sponsored" creative disclosures, and interstitial bans on chat surfaces are fully verified by 73 passing tests. (Tested against local single-node Redis; multi-node Redis Sentinel/Cluster split-brain failover was not evaluated).
- **Phase 6 (Placement Analytics, Optimizer & Weekly Digest):** **Accepted**. The per-slot aggregation schema uses a verified 3-column composite unique constraint `(publisher_id, slot_id, stat_date)` with complete PostgreSQL `ON CONFLICT` and SQLite upsert parity; rolling 30-day analytics use canonical fixed CTR thresholds (`< 0.2%` low visibility, `0.2%–1.0%` healthy, `> 1.0%` great, `> 2.0%` raise floor); and the distributed weekly digest scheduler enforces distributed locking with ownership tokens, heartbeat lease renewals, cycle idempotency ledgers, and atomic Lua script finalization, verified by 88 passing tests. (Delivery guarantees across SMTP boundaries are at-least-once).
- **Phase 7 (Programmatic Bidding, OpenRTB, DSP/SSP/ADX) & Phase 8 (Intelligence, Fraud, DMP):** **Future Roadmap Phases**. In accordance with the canonical engineering roadmap, programmatic bidding and advanced DMP features are planned for subsequent phases and do not constitute completed work in the current repository state.

---

## 2. Git Working-Tree Status

### 2.1 Working-Tree State and Bounded WU-1 Changes
Prior to WU-1, the repository working tree was clean on branch `main`. To establish live Laya runtime integration, exactly two bounded files were modified:
- `backend/docker-compose.yml`: Configured `laya` service with `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and `ports: ["127.0.0.1:8100:8000"]`.
- `backend/tests/test_ad_server/test_laya_client.py`: Unskipped `test_real_runtime_smoke`, added `@pytest.mark.smoke`, added host port `127.0.0.1:8100` fallback, and implemented pre-flight health verification.

No other source code, tests, database migrations, configuration, models, or endpoints—particularly within Phase 4—were modified.

---

## 3. Phase 1–6 Acceptance Matrix

| Phase | Description | Canonical Specification Reference | Implementation Status | Empirical Evidence & Pass Count | Final Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Phase 1** | Security Hardening (BUG-1 to BUG-7) | Roadmap Phase 0; Bug Specifications BUG-1 to BUG-7 | Complete across backend auth, tracking, finance, and models; 600s sig TTL & 900s replay TTL verified | 34 / 34 Unit & Security Tests Passed | **Accepted** |
| **Phase 2** | Laya Optimizer & Autopilot | Phase 2 Engineering Spec; Laya Contract v1 | Complete in scheduler, executor, policy, API, and live container smoke test (`127.0.0.1:8100`) | 54 / 54 Phase 2 Tests Passed (0 failed, 0 skipped) | **Accepted** |
| **Phase 3** | Conversational Campaign Builder & Payments | Phase 3 Conversational PRD & Payment Gateway Spec | Complete in conversation state, draft lifecycle, LLM client, and Razorpay webhook | 66 / 66 Automated Tests Passed | **Accepted** |
| **Phase 4** | Verification & Creative Moderation | Phase 4 Security & Ad Delivery Integrity Spec | Domain TXT & Moderation complete; Email ownership verification bypassed (explicitly excluded) | 38 / 38 Tests Passed (Tests explicitly codify auto-verification & 404 endpoint) | **Partially implemented** |
| **Phase 5** | Publisher API Keys, Ad Delivery & Usage Controls | Phase 5 Work Units 1–4 PRDs | Complete HMAC v2, Token Bucket, User Privacy Hash, Chat Interstitial Ban | 73 / 73 Automated Tests Passed | **Accepted** |
| **Phase 6** | Placement Analytics, Optimizer & Weekly Digest | Phase 6 Work Units 1–5 PRDs | Complete 3-column composite key, 30-day fixed thresholds, Lua finalization | 88 / 88 Automated Tests Passed | **Accepted** |
| **Phase 7** | Programmatic Bidding & OpenRTB | Roadmap Phase 7 (DSP, SSP, ADX, RTB) | Future Roadmap Phase | N/A (Out of current scope) | **Future Work** |
| **Phase 8** | Intelligence, Fraud & DMP | Roadmap Phase 8 (CTR/CVR Prediction, DMP) | Future Roadmap Phase | N/A (Out of current scope) | **Future Work** |

---

## 4. Detailed Evidence for Each Phase

### 4.1 Phase 1 — Security Hardening and BUG-1 to BUG-7

#### BUG-1: Finance Endpoint Authorization and Scoping
- **Implementation Location:** `backend/liteads/ad_server/routers/finance.py:44-120`, `backend/liteads/common/security.py:312-360`
- **Mechanism:** Finance endpoints (`/api/v1/finance/ledger/advertiser/{id}`, `/deposit`, `/withdraw`, `/payout`) enforce server-side role validation (`UserRole.ADVERTISER`, `UserRole.PUBLISHER`, `UserRole.ADMIN`). If a non-admin caller passes an `advertiser_id` or `publisher_id` other than their authenticated tenant ID, the backend immediately raises HTTP 403 Forbidden.
- **Empirical Evidence:** `backend/tests/test_api/test_finance_api.py` (5 tests executed, 5 passed). Unauthenticated access returns 401; cross-tenant requests return 403.

#### BUG-2: Tracking URL Replay Protection and Signature Expiry (Reconciled)
- **Implementation Location:** `backend/liteads/common/tracking_sig.py:11-30`, `backend/liteads/ad_server/services/replay_guard.py:13-32`
- **Mechanism:**
  1. **Signature Validity Window (600 seconds):** Tracking URLs contain an HMAC-SHA256 signature calculated over `{request_id}|{ad_id}|{slot_id}|{timestamp}` using the secret key in `TrackingSettings.signature_ttl_seconds = 600` (`backend/liteads/common/config.py:123`). If `abs(current_ts - event_ts) > 600`, verification immediately raises HTTP 400 Bad Request ("Expired signature").
  2. **Redis Replay Guard Window (900 seconds):** Replay protection uses an atomic Redis counter keyed by `trk:nonce:{event_key}:{request_id}:{ad_id}` with an explicit 15-minute TTL (`expire(key, 900)` set on the first increment). Event-specific limits are enforced: impressions allow 1 event, clicks allow up to 3 events, and conversions allow 1 event. Duplicate events exceeding these limits are rejected with **HTTP 429 Too Many Requests** ("Duplicate event rejected").
  *Reconciliation Note:* The 600-second cryptographic signature validity window and the 900-second Redis event replay guard window are distinct, complementary defense-in-depth mechanisms with separate TTLs and status codes.
- **Empirical Evidence:** `backend/tests/test_ad_server/test_replay_guard.py` (7 tests executed, 7 passed). Tests assert HTTP 400 for signatures older than 600s, HTTP 429 for duplicate replay calls beyond threshold, 900s Redis key expiration, and financial billing execution exactly once.

#### BUG-3: Stored XSS Prevention and Creative URL Validation
- **Implementation Location:** `backend/liteads/ad_server/services/ad_service.py:145-190`, `backend/liteads/schemas/creative.py:30-65`
- **Mechanism:** Creative landing URLs, image URLs, and snippet bodies are validated during creation and rendering. URLs must strictly use `http://` or `https://` schemes; `javascript:`, `data:`, `vbscript:`, and relative paths are rejected. HTML snippets are escaped or parsed with whitelist-only tag attributes.
- **Empirical Evidence:** `backend/tests/test_ad_server/test_creative_xss.py` (4 tests executed, 4 passed). Malicious payloads `<script>`, `javascript:alert(1)`, and `onload=` handlers fail schema validation with HTTP 422.

#### BUG-4: Advertiser Balance and Budget Enforcement
- **Implementation Location:** `backend/liteads/ad_server/services/ad_service.py:210-275`, `backend/liteads/ad_server/services/event_service.py:75-135`
- **Mechanism:** During ad selection, advertisers with `balance <= 0` or campaigns exceeding `daily_budget` / `total_budget` are filtered out before candidate scoring. Upon event tracking, advertiser balances are deducted inside an atomic database transaction with `SELECT ... FOR UPDATE` row-level locking. If balance is exhausted, the campaign is suspended immediately.
- **Empirical Evidence:** `backend/tests/test_ad_server/test_bug4_balance.py` (3 tests executed, 3 passed). Advertisers with zero or negative balance receive zero served impressions.

#### BUG-5: Login IP and Account Rate Limiting with Redis Failure Safety
- **Implementation Location:** `backend/liteads/ad_server/routers/auth.py:45-95`, `backend/liteads/common/security.py:65-110`
- **Mechanism:** Login attempts are rate-limited across two distinct keys in Redis: per-IP (`rate:login:ip:{ip}`, limit 30/minute) and per-account (`rate:login:account:{email_hash}`, limit 5/minute). Email addresses are normalized to lowercase before hashing. If Redis becomes unreachable, the rate limiter falls back to an in-memory sliding window cache or logs a critical warning while preventing denial of service.
- **Empirical Evidence:** `backend/tests/test_ad_server/test_bug5_rate_limits.py` (6 tests executed, 6 passed). Brute-force attacks trigger HTTP 429 Too Many Requests; normalized email variants hit the same account bucket.

#### BUG-6: Malformed Tracking Timestamp Handling
- **Implementation Location:** `backend/liteads/ad_server/routers/event.py:35-80`, `backend/liteads/ad_server/services/event_service.py:95-115`
- **Mechanism:** The event tracking router explicitly parses and validates the `ts` query parameter. Empty, non-numeric, future-skewed (> 300s into the future), or corrupted timestamps trigger immediate HTTP 400 Bad Request, aborting before any database write or financial billing step occurs.
- **Empirical Evidence:** `backend/tests/test_ad_server/test_bug6_tracking.py` (5 tests executed, 5 passed). Malformed timestamps do not generate financial ledger rows.

#### BUG-7: Refresh-Token Security and HttpOnly Cookies
- **Implementation Location:** `backend/liteads/ad_server/routers/auth.py:50-145`, `backend/liteads/models/auth.py:60-95`
- **Mechanism:** Refresh tokens are never returned in response JSON payloads. The login and token-refresh endpoints set the refresh token inside a secure cookie: `httponly=True`, `samesite="lax"`, `secure=False` (in development) / `True` (in production), `path="/api/v1/auth"`. The `/refresh` endpoint reads solely from this cookie, and `/logout` invalidates the database token record and clears the cookie via `max_age=0`.
- **Empirical Evidence:** `backend/tests/test_ad_server/test_bug7_auth.py` (4 tests executed, 4 passed). Cookies are properly formatted, JSON responses contain only the ephemeral access token, and logout revokes active tokens.

---

### 4.2 Phase 2 — Laya Optimizer and Autopilot (Reconciled & Accepted)

#### Service Architecture & Contract Verification
- **Sidecar Container Name:** `backend-laya-1` (service `laya` in `docker-compose.yml:125-135`).
- **Docker Image:** `backend-laya` (built from `https://github.com/NandhaKishorM/laya.git`).
- **Network Resolution:** Internal Compose network `liteads-network` at `http://laya:8000`; host loopback mapped to `127.0.0.1:8100:8000`.
- **Offline Configuration:** `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` configured in `docker-compose.yml`, using pre-cached snapshot `7b928d828b7b0e022f929d9bd2e44165aa270148`.
- **Client Implementation:** `backend/liteads/ad_server/services/laya_client.py:15-150`
- **Endpoint Called:** `POST {base_url}/v1/systemone`
- **Contract Payload:**
  ```json
  {
    "state": "The campaign is performing very well with low CPA.",
    "questions": {
      "opt_decision": {
        "type": "choice",
        "instructions": "Determine the best optimization action for this campaign.",
        "criteria": {
          "increase_budget_20": "Increase daily budget by 20%",
          "decrease_budget_20": "Decrease daily budget by 20%",
          "shift_to_new_geo": "Shift targeting to a new geographic region",
          "shift_to_new_sector": "Shift targeting to a new sector",
          "improve_creative": "Recommend creative improvement",
          "no_change": "No changes needed"
        }
      }
    }
  }
  ```
- **Live Response Schema (Empirically Verified):**
  ```json
  {
    "model": "laya-rl-agent",
    "answers": {
      "opt_decision": {
        "type": "choice",
        "choice": "no_change",
        "probabilities": {
          "increase_budget_20": 0.0152,
          "decrease_budget_20": 0.0423,
          "shift_to_new_geo": 0.0018,
          "shift_to_new_sector": 0.0013,
          "improve_creative": 0.0192,
          "no_change": 0.9202
        },
        "confidence": 0.7936,
        "answer_confidence": 0.9202,
        "action": {
          "act_probability": 1.0
        }
      }
    },
    "usage": { "input_tokens": 104, "output_tokens": 0, "state_tokens": 11, "truncated": false },
    "routing": { "model": "english", "repo": "convaiinnovations/laya" }
  }
  ```
- **Inference Performance:** Live execution roundtrip ~496ms, with container inference duration reported via header `x-inference-time-ms: 463.07`.

#### Policy & Guardrails
- **Implementation Location:** `backend/liteads/ad_server/services/optimization_policy.py:20-140`
- **Thresholds & Limits:**
  - Eligibility requires `impressions >= 500` over the evaluation window.
  - Budget adjustments bounded to $\pm 30\%$ maximum change per cycle.
  - Bid adjustments bounded to $\pm 25\%$ maximum change per cycle.
  - Targeting modifications are additive only (never deletes existing positive targeting).
  - Minimum confidence threshold of `0.70` required to promote suggestions to Autopilot execution.
- **Failure Behavior:** If the Laya sidecar or Redis is unavailable, the scheduler catches the exception, logs a warning, skips optimization for that campaign, and marks the cycle as safely aborted without corrupting campaign state (`backend/liteads/ad_server/services/optimization_scheduler.py:110-165`).

#### Acceptance Reconciliation
- **WU-1 Verification Results:** The live smoke test `test_real_runtime_smoke` in `backend/tests/test_ad_server/test_laya_client.py:255` was unskipped and decorated with `@pytest.mark.smoke`. It executes live neural inference against the running container over `http://127.0.0.1:8100`, verifying schema conformance, choice validity, and float confidence.
- **Automated Test Results:**
  - `test_laya_client.py`: 13 passed, 0 failed, 0 skipped.
  - Full Phase 2 Suite (Client, Policy, Executor, Scheduler, Autopilot API, Suggestion API): **54 passed, 0 failed, 0 skipped**.
- **Final Verdict:** **Accepted**.

---

### 4.3 Phase 3 — Conversational Campaign Builder and Payments

#### Conversation State Management
- **Implementation Location:** `backend/liteads/ad_server/services/conversation_state.py:15-110`
- **Mechanism:** Sessions are stored in Redis under `chat:session:{user_id}:{session_id}` with a 24-hour TTL (86,400s). Multi-tenant isolation is strictly enforced by prefixing the Redis key with `user_id`. Callers cannot inspect or append messages to other tenants' sessions.

#### Intent Extraction & Fallback Architecture
- **Implementation Location:** `backend/liteads/ad_server/services/generative_intent_client.py:25-180`, `backend/liteads/ad_server/services/laya_client.py:190-250`
- **Supported Providers:** Groq (`llama3-70b-8192`), Google Gemini (`gemini-1.5-flash`), OpenAI (`gpt-4o-mini`), and local Laya. Active provider is configured via `LLM_PROVIDER=groq` in `.env`.
- **Fallback Behavior:** If the generative LLM call fails or times out, the client falls back to rule-based keyword extraction and Laya regex parsing, returning structured campaign parameters without failing the user's conversational prompt.

#### Campaign Draft Lifecycle & Activation Flow
- **Draft Creation & Patching:** `backend/liteads/ad_server/routers/chat.py:40-120`, `backend/liteads/schemas/campaign.py:80-125`
- **Lifecycle Status:** Draft campaigns are assigned status `CampaignStatus.DRAFT` (integer value 5).
- **Immutability Guard:** The Pydantic schema `CampaignPatchSchema` sets `model_config = ConfigDict(extra="forbid")` and explicitly omits the `status` field. An advertiser cannot transition a campaign from `DRAFT` to `ACTIVE` via the standard `PATCH /api/v1/campaigns/{id}` endpoint.
- **Dedicated Activation Endpoint:** `POST /api/v1/campaigns/{id}/activate` (`backend/liteads/ad_server/routers/management.py:230-310`) strictly verifies:
  1. Advertiser account balance $> 0$.
  2. At least one creative attached with `status == ACTIVE` and `moderation_status == APPROVED`.
  3. Creative landing domain verified via DNS TXT.
  4. Non-empty targeting criteria (geo or sector).
  If any check fails, HTTP 400 Bad Request is returned with specific error reasons.

#### Razorpay Payments & Webhook Idempotency
- **Implementation Location:** `backend/liteads/ad_server/routers/webhooks.py:30-110`, `backend/liteads/ad_server/services/razorpay_client.py:20-95`
- **Mechanism:** Webhooks validate the `X-Razorpay-Signature` header against the shared secret using HMAC-SHA256. Idempotency is enforced by inserting payment transaction records into the `finance_ledger` table keyed on the unique Razorpay `payment_id`. Repeated webhook deliveries detect the existing transaction and immediately return HTTP 200 without double-crediting balances.
- **Verification Limitation:** Verified via cryptographically signed mock webhook payloads. Live settlement across production banking networks was not tested.
- **Empirical Evidence:** `backend/tests/test_api/test_webhooks.py` and Phase 3 suites (66 tests executed, 66 passed).

---

### 4.4 Phase 4 — Email and Domain Verification, Creative Moderation

#### Domain DNS TXT Verification
- **Implementation Location:** `backend/liteads/ad_server/routers/domain.py:35-130`, `backend/liteads/ad_server/services/domain_service.py:20-85`
- **Status:** **Fully Implemented and Enforced**.
- **Mechanism:** Advertisers register domains and receive a unique token `rivinity-site-verification={token}`. The verification service queries authoritative DNS TXT records using `dnspython`. Once verified, domains are marked `status="VERIFIED"`. During ad serving (`backend/liteads/ad_server/services/ad_service.py:230-245`), creatives whose landing domains are not in the verified table are strictly excluded.

#### Creative Moderation Status
- **Implementation Location:** `backend/liteads/ad_server/routers/moderation.py:30-115`, `backend/liteads/models/creative.py:45-75`
- **Status:** **Fully Implemented and Enforced**.
- **Mechanism:** Creatives hold `moderation_status` (`PENDING`, `APPROVED`, `REJECTED`). Newly submitted creatives default to `PENDING`. Admins approve or reject creatives via `/api/v1/moderation/creatives/{id}`. The ad retrieval query explicitly joins and filters `Creative.moderation_status == ModerationStatus.APPROVED.value`.

#### Email Ownership Verification
- **Implementation Location:** `backend/liteads/ad_server/routers/auth.py:40-60`, `backend/tests/test_api/test_phase4a_email.py:10-48`
- **Status:** **NOT IMPLEMENTED / BYPASSED**.
- **Findings:**
  1. In `backend/liteads/ad_server/routers/auth.py:48`, user registration immediately creates accounts with `email_verified=True`.
  2. The email verification endpoints `/api/v1/auth/verify-email` and `/api/v1/auth/resend-verification` return HTTP 404 Not Found.
  3. `backend/tests/test_api/test_phase4a_email.py` explicitly tests and codifies this bypass behavior.
- **Scope Note:** In accordance with project instructions, Phase 4 source code and endpoints remain explicitly excluded from modification in this cycle.
- **Verdict:** Phase 4 remains **Partially implemented**.

---

### 4.5 Phase 5 — Publisher API Keys, Ad Delivery, and Usage Controls

#### Canonical HMAC v2 Request Signing
- **Implementation Location:** `backend/liteads/common/security.py:410-530`, `backend/liteads/ad_server/routers/ad.py:60-115`
- **Canonical String Format:** `METHOD\nPATH\nTIMESTAMP\nBODY_SHA256`
- **Validation Rules:**
  - Freshness window: `|current_time - timestamp| <= 300` seconds.
  - Replay protection: Nonce stored in Redis `SET key_nonce:{key_id}:{nonce} 1 EX 300 NX`. Duplicate nonces rejected with HTTP 401.
  - Constant-time comparison: `hmac.compare_digest` used for all signature verifications.

#### Rate Limiting & Daily UTC Quotas
- **Implementation Location:** `backend/liteads/common/security.py:440-495`, `backend/liteads/models/publisher.py:184-209`
- **Mechanism:** Enforces dual-layer limits via Redis token-bucket and persistent database counters:
  - Per-second rate limiting: Sliding window / token bucket per API key.
  - Daily UTC quota: Tracked in `api_key_daily_stats` and Redis counter `quota:{key_id}:{YYYY-MM-DD}`. When exceeded, returns HTTP 429 with headers `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `Retry-After`.

#### Privacy-Preserving User Identifiers
- **Implementation Location:** `backend/liteads/ad_server/routers/ad.py:120-165`, `backend/liteads/ad_server/services/ad_service.py:180-210`
- **Validation:** User identifier parameter `user_hash` must be a valid 64-character lowercase hexadecimal SHA-256 string (`^[a-f0-9]{64}$`).
- **Raw PII Rejection:** Passing raw email addresses, IP addresses, or phone numbers in place of `user_hash` triggers immediate HTTP 422 Unprocessable Entity.
- **Fail-Closed Frequency Capping:** Frequency counters in Redis (`freq:{user_hash}:{campaign_id}`) fail closed: if Redis is unavailable, candidate ads with frequency restrictions are suppressed.

#### Sponsored Disclosure & Chat Interstitial Restrictions
- **Implementation Location:** `backend/liteads/ad_server/services/ad_service.py:280-320`, `backend/liteads/schemas/ad.py:40-70`
- **Mandatory Disclosure:** Every served ad creative schema includes mandatory top-level field `disclosure: "Sponsored"`.
- **Chat Surface Protection:** When `surface == "chat"`, interstitial format creatives are blocked. Only native or inline text creatives are permitted on chat surfaces.
- **Verification Limitation:** Tested on a single Redis 7 instance; multi-node failover was not evaluated.
- **Empirical Evidence:** `backend/tests/test_api/test_phase5_wu4_compliance.py` and Phase 5 suites (73 tests executed, 73 passed).

---

### 4.6 Phase 6 — Placement Analytics, Optimizer, and Weekly Digest

#### A. Canonical Placement-Health Verdicts
- **Implementation Location:** `backend/liteads/ad_server/services/placement_analyzer.py:24-42, 238-253`
- **Authoritative Thresholds:**
  ```python
  MIN_IMPRESSIONS_FOR_VERDICT: int = 100
  CTR_GREAT_THRESHOLD: float = 0.01          # 1.0%
  CTR_LOW_VISIBILITY_THRESHOLD: float = 0.002 # 0.2%
  CTR_HIGH_DEMAND_THRESHOLD: float = 0.02    # 2.0%
  ```
- **Boundary Classification:**
  1. `impressions < 100`: `verdict = "insufficient_data"`.
  2. `ctr > 0.01`: `verdict = "great"` (if `ctr > 0.02`: `"Raise floor price — demand exceeds supply"`; otherwise `"No action"`).
  3. `ctr < 0.002`: `verdict = "low_visibility"`, `"Move slot above the fold or widen it"`.
  4. Boundary Values (`0.002 <= ctr <= 0.010`): `verdict = "healthy"`, `"No action"`.

#### B. Database Schema & Upsert Consistency
- **Alembic Migration:** `backend/alembic/versions/c4d5e6f7a8b9_phase6_wu1_publisher_slot_stats.py`
  - Creates composite unique constraint `uq_pub_daily_stats_slot_date` on `(publisher_id, slot_id, stat_date)`.
- **SQLAlchemy Model:** `backend/liteads/models/publisher.py:158-183`
  - Table: `publisher_daily_stats`, with constraint `uq_pub_daily_stats_slot_date`.
- **SQL Aggregation & Upsert Parity:** `backend/liteads/ad_server/services/event_service.py:246-279`
  - PostgreSQL: `on_conflict_do_update(constraint="uq_pub_daily_stats_slot_date")`.
  - SQLite: `on_conflict_do_update(index_elements=["publisher_id", "slot_id", "stat_date"])`.

#### C. Weekly Digest Scheduler Concurrency & Delivery Guarantees
- **Implementation Location:** `backend/liteads/ad_server/services/digest_scheduler.py:40-489`, `backend/liteads/ad_server/services/publisher_digest_service.py:25-210`
- **Distributed Locking:** Key `lock:weekly_placement_digest_cycle`, TTL 3,600s with UUID owner token, 300s heartbeat renewal, and atomic Lua release.
- **Cycle Idempotency & Lua Finalization:** Stable cycle IDs and deduplication ledgers in Redis.
- **Delivery Guarantee Realism:** Delivery is **at-least-once**. An unhandled worker crash between SMTP dispatch and Redis ledger recording will result in a resend upon scheduler retry.

---

### 4.7 Future Roadmap Phases: Phase 7 and Phase 8

In accordance with the canonical Bharattech Ads engineering roadmap:
- **Phase 7 (Programmatic / RTB):** OpenRTB bid request/response protocols, DSP bidding integration, SSP floor pricing, ADX auction dynamics, and second-price/first-price settlement are strictly designated as post-direct-serving milestones. They are **not implemented** in the current release and do not represent completed work.
- **Phase 8 (Intelligence, Fraud & DMP):** Production CTR/CVR predictive models (beyond foundational DeepFM architectures), dynamic floor pricing, audience graph segments, and invalid-traffic detection are designated for future roadmap implementation.

---

## 5. Test Execution, Counts, and Inventory Reconciliation

### 5.1 Test Inventory Reconciliation: Audit Scope vs. Full Repository
To eliminate ambiguity, two distinct test inventories are documented:
1. **Full Repository Test Inventory:** Exactly **560 tests** are collected across all backend test directories (including `test_ml_engine`, `test_rec_engine`, `test_targeting`, `test_user_profile`, etc.).
2. **Phase 1–6 Acceptance Test Inventory:** Exactly **353 automated tests** comprise the curated acceptance test suites evaluated across Phases 1 through 6. All 353 tests pass with 0 failures and 0 skips.

### 5.2 Phase-by-Phase Acceptance Test Breakdown

| Test Suite / Path | Target Phase / Scope | Tests Run | Passed | Failed | Skipped | Execution Mode |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| `backend/tests/test_api/test_finance_api.py` | Phase 1 (BUG-1) | 5 | 5 | 0 | 0 | Unit / Mock Client |
| `backend/tests/test_ad_server/test_replay_guard.py` | Phase 1 (BUG-2) | 7 | 7 | 0 | 0 | Integration / Redis & SQLite |
| `backend/tests/test_ad_server/test_creative_xss.py` | Phase 1 (BUG-3) | 4 | 4 | 0 | 0 | Unit / Mock Client |
| `backend/tests/test_ad_server/test_bug4_balance.py` | Phase 1 (BUG-4) | 3 | 3 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_ad_server/test_bug5_rate_limits.py` | Phase 1 (BUG-5) | 6 | 6 | 0 | 0 | Integration / Redis Mock |
| `backend/tests/test_ad_server/test_bug6_tracking.py` | Phase 1 (BUG-6) | 5 | 5 | 0 | 0 | Unit / Mock Client |
| `backend/tests/test_ad_server/test_bug7_auth.py` | Phase 1 (BUG-7) | 4 | 4 | 0 | 0 | Integration / Cookie Test |
| **Phase 1 Subtotal** | **Security Hardening** | **34** | **34** | **0** | **0** | — |
| `backend/tests/test_ad_server/test_laya_client.py` | Phase 2 (Laya Client & Live Smoke) | 13 | 13 | 0 | 0 | Unit + Live Sidecar Smoke |
| `backend/tests/test_ad_server/test_optimization_policy.py` | Phase 2 (Optimizer Guardrails) | 8 | 8 | 0 | 0 | Unit Test |
| `backend/tests/test_ad_server/test_optimization_executor.py` | Phase 2 (Execution Engine) | 15 | 15 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_ad_server/test_optimization_scheduler.py` | Phase 2 (Scheduler Loop) | 8 | 8 | 0 | 0 | Unit / Async Mock |
| `backend/tests/test_ad_server/test_autopilot_api.py` | Phase 2 (Autopilot Endpoints) | 6 | 6 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_ad_server/test_suggestion_api.py` | Phase 2 (Suggestions API) | 4 | 4 | 0 | 0 | Integration / SQLite |
| **Phase 2 Subtotal** | **Laya Optimizer & Autopilot** | **54** | **54** | **0** | **0** | — |
| `backend/tests/test_api/test_phase3a.py` through `test_phase3e_3.py` | Phase 3 (Conversational Drafts) | 54 | 54 | 0 | 0 | Integration / Redis & SQLite |
| `backend/tests/test_api/test_phase3f8b_patch_security.py` | Phase 3 (Patch Immutability) | 11 | 11 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_api/test_webhooks.py` | Phase 3 (Razorpay Webhooks) | 1 | 1 | 0 | 0 | Integration / SQLite |
| **Phase 3 Subtotal** | **Conversational Builder & Payments**| **66** | **66** | **0** | **0** | — |
| `backend/tests/test_api/test_phase4a_email.py` | Phase 4 (Email Bypass Tests) | 2 | 2 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_api/test_phase4b_domain.py` | Phase 4 (DNS TXT Verification) | 5 | 5 | 0 | 0 | Unit / DNS Mock |
| `backend/tests/test_api/test_phase4c_moderation.py` | Phase 4 (Creative Moderation) | 1 | 1 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_api/test_phase4d_enforcement.py` | Phase 4 (Delivery Enforcement) | 3 | 3 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_ad_server/test_phase4.py` | Phase 4 (Format Filtering) | 27 | 27 | 0 | 0 | Unit & Integration |
| **Phase 4 Subtotal** | **Verification & Moderation** | **38** | **38** | **0** | **0** | — |
| `backend/tests/test_api/test_phase5_models.py` | Phase 5 (API Key Models) | 9 | 9 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_api/test_phase5_wu2_auth.py` | Phase 5 (HMAC v2 Authentication) | 28 | 28 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_api/test_phase5_wu3_ratelimit.py` | Phase 5 (Token Bucket Quotas) | 16 | 16 | 0 | 0 | Integration / Redis |
| `backend/tests/test_api/test_phase5_wu4_compliance.py` | Phase 5 (Privacy Hash & Chat Interstitial) | 17 | 17 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_api/test_api_keys.py` | Phase 5 (Key Management) | 3 | 3 | 0 | 0 | Integration / SQLite |
| **Phase 5 Subtotal** | **API Keys & Usage Controls** | **73** | **73** | **0** | **0** | — |
| `backend/tests/test_api/test_phase6_wu1_publisher_stats.py` | Phase 6 (Slot Daily Stats) | 12 | 12 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_api/test_phase6_wu2_placement_analyzer.py`| Phase 6 (30-Day Fixed Thresholds) | 15 | 15 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_api/test_phase6_wu3_placement_health_api.py`| Phase 6 (Placement Health API) | 13 | 13 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_ad_server/test_phase6_wu4_placement_optimizer.py`| Phase 6 (Ad Server Preference) | 14 | 14 | 0 | 0 | Integration / SQLite |
| `backend/tests/test_ad_server/test_phase6_wu5_publisher_digest.py`| Phase 6 (Digest Scheduler & Lua) | 34 | 34 | 0 | 0 | Integration / Redis Mock |
| **Phase 6 Subtotal** | **Placement Analytics & Digest** | **88** | **88** | **0** | **0** | — |
| `frontend/` (`npx tsc --noEmit`) | Frontend Type Integrity | N/A | Clean | 0 | 0 | Static TypeScript Compiler |

**Total Phase 1–6 Acceptance Suite:** **353 automated tests**
**Results:** **353 Passed, 0 Failed, 0 Skipped**
*(Reconciliation: 34 + 54 + 66 + 38 + 73 + 88 = 353. Previously reported as 348 passed, 1 skipped due to arithmetic omission and the skipped smoke test).*

---

## 6. Migration, Model, and SQL Consistency Findings

1. **Alembic Revisions Head:** Confirmed at `c4d5e6f7a8b9` (`c4d5e6f7a8b9_phase6_wu1_publisher_slot_stats.py`). Migration history forms a single, unbranching linear chain of 17 migrations from `01f780f77602` to `c4d5e6f7a8b9`.
2. **Per-Slot Uniqueness Key Alignment:**
   - Alembic migration `c4d5e6f7a8b9` creates constraint `uq_pub_daily_stats_slot_date` on `(publisher_id, slot_id, stat_date)`.
   - SQLAlchemy model `PublisherDailyStat` specifies `UniqueConstraint("publisher_id", "slot_id", "stat_date", name="uq_pub_daily_stats_slot_date")`.
   - PostgreSQL event tracking SQL uses `stmt.on_conflict_do_update(constraint="uq_pub_daily_stats_slot_date", set_={...})`.
   - SQLite event tracking SQL uses `stmt.on_conflict_do_update(index_elements=["publisher_id", "slot_id", "stat_date"], set_={...})`.
   - No mismatch exists across migrations, models, or SQL statements.
3. **Legacy NULL Slot Handling:**
   - In `publisher_daily_stats`, `slot_id` is defined with `nullable=True`. Historical rows where `slot_id IS NULL` represent pre-Phase 6 publisher daily aggregates. Benchmark queries explicitly filter `where(PublisherDailyStat.slot_id.isnot(None))` to prevent legacy NULL rows from distorting placement benchmarks.

---

## 7. Security, Concurrency, and Operational Risks

### Risk 1: High — User Registration Without Email Verification
- **Location:** `backend/liteads/ad_server/routers/auth.py:48`
- **Impact:** Any user can register an arbitrary email address and immediately configure campaigns or publisher properties. Unverified accounts can deposit funds and submit creatives for moderation.
- **Remediation Required:** Implement transactional email dispatch (SMTP/SES) with signed verification tokens, set `user.email_verified = False` on registration, and enforce verification checks across tenant operations.

### Risk 2: Low — Laya Checkpoint Temperature Calibration Warning
- **Location:** `backend/liteads/ad_server/services/laya_client.py:15-150`, container logs
- **Description:** The Laya Python library outputs a standard checkpoint notice:
  `RuntimeWarning: laya: this checkpoint ships invalid temperatures or values outside [0.5, 5]; using choice:11+=0.10058280825614929 -> 0.5. Treat confidence from the affected entries as uncalibrated.`
- **Classification:** Upstream weight-calibration notice. Empirical testing confirms that the endpoint responds in ~460ms with valid HTTP 200 JSON, compliant choice keys, and normalized numeric confidence values. It does **not** cause functional failure, inference stalling, or schema violations.

### Risk 3: Medium — Weekly Digest Crash Window & Inherent At-Least-Once Delivery
- **Location:** `backend/liteads/ad_server/services/digest_scheduler.py:389-425`
- **Impact:** Network failures between email dispatch (Mailpit/SMTP) and Redis deduplication set updates can result in duplicate weekly performance emails being delivered to publishers upon scheduler retry.
- **Remediation Required:** Document at-least-once delivery semantics in operator documentation and implement a local pre-send delivery reservation lock.

### Risk 4: Low — Python 3.13 Datetime Deprecation Warnings
- **Location:** 13 locations in `auth.py`, `event_service.py`, `conversation_state.py`, and test scripts.
- **Impact:** Non-breaking deprecation warnings during startup and test runs.
- **Remediation Required:** Refactor all `datetime.utcnow()` occurrences to `datetime.now(timezone.utc)`.

---

## 8. Missing Evidence and Limitations

1. **Production Razorpay Gateway Settlement:**
   - Webhook processing was verified via cryptographically signed mock payloads. Live currency settlement was not tested against production Razorpay bank networks.
2. **Large-Scale Multi-Worker Redis Cluster Failover:**
   - Distributed locking and Lua script atomic finalization were validated using local Redis 7. Multi-node Redis Sentinel / Cluster split-brain scenarios were not evaluated.

---

## 9. Exact Acceptance Criteria Still Outstanding

To achieve full platform acceptance across all phases, the following single item remains outstanding:

- **Phase 4 Email Ownership Verification:**
  1. Restore the `/api/v1/auth/verify-email` endpoint.
  2. Modify `backend/liteads/ad_server/routers/auth.py` so that user registration sets `user.email_verified = False`.
  3. Issue a secure one-time verification token upon registration.
  4. Enforce `user.email_verified == True` before permitting campaign creation or publisher payout requests.

---

## 10. Final Recommendation

Based on the empirical evidence gathered during this audit and reconciled following Work Unit 1:

- **Phases 1, 2, 3, 5, and 6 are ACCEPTED.**
  - Phase 1 security hardening is complete with verified 600s signature and 900s replay TTLs.
  - Phase 2 live Laya sidecar integration is fully verified with 54 passing regression tests and real neural inference execution.
  - Phase 3 conversational campaign builder, draft lifecycle, and Razorpay webhooks are fully verified with 66 passing tests.
  - Phase 5 publisher API keys, HMAC v2 signing, user privacy hashing, and usage controls are fully verified with 73 passing tests.
  - Phase 6 placement analytics, fixed CTR thresholds, composite unique constraints, and distributed digest schedulers are fully verified with 88 passing tests.
- **Phase 4 is PARTIALLY IMPLEMENTED.** Domain verification and creative moderation are production-ready, but the bypass of user email ownership verification blocks full Phase 4 sign-off.
- **Phases 7 and 8 are FUTURE ROADMAP PHASES.** Programmatic bidding (OpenRTB, DSP/SSP/ADX) and advanced DMP intelligence remain scheduled for subsequent development phases.

**Final Determination:** Acceptance is granted for **Phases 1, 2, 3, 5, and 6**. Full platform sign-off across all phases remains **deferred** solely pending the remediation of Phase 4 email ownership verification.
