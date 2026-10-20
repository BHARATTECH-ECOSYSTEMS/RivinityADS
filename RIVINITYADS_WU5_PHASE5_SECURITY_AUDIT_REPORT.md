# RivinityAds WU-5 — Phase 5 Security and Regression Audit Report

**Audit Date:** 2026-10-19
**Product:** Bharattech Ads (RivinityAds / LiteAds)
**Work Unit:** WU-5 — Phase 5 Security and Regression Audit
**Scope:** Phase 5 Publisher API Security, HMAC v2 Authentication, Rate Limiting, Daily Quotas, Privacy, and Regression Verification
**Phase Exclusions:** Phase 4 strictly untouched; Phase 6 not started
**Audit Verdict:** **ACCEPTED**

---

## 1. Executive Summary

A comprehensive, read-first, evidence-driven security and regression audit was conducted on Phase 5 of the RivinityAds platform. Phase 5 establishes the core enterprise publisher integration layer: cryptographic publisher API key management, method- and path-bound HMAC v2 request signatures, atomic Redis sliding-window rate limiting, UTC calendar daily quotas, privacy-safe 64-character SHA-256 user identification, server-authoritative sponsored ad disclosures, and chat-surface format restrictions.

### Key Audit Findings:
1. **API Key Generation & Storage:** Keys are generated via `secrets.token_urlsafe(32)` with distinct prefixes (`pk_live_` / `pk_test_`). Keys are salted and hashed using `scrypt` ($N=16384, r=8, p=1$). Raw keys are presented strictly once upon creation and never stored or returned in usage or management responses.
2. **HMAC v2 Request Authentication:** Implements canonical method- and path-bound signature verification: `HMAC-SHA256(raw_key, "{METHOD}|{PATH}|{timestamp}|{sha256(body)}")` with constant-time `hmac.compare_digest`. Timestamps enforce a strict 300-second window. Any authentication or authorization failure strictly fails closed (HTTP 401/403/503) and **never** falls back to anonymous ad serving.
3. **Concurrency-Safe Rate Limiting & Quotas:** Enforced atomically via Redis Lua scripts (`_SLIDING_WINDOW_LUA` with ZSET and `_DAILY_QUOTA_LUA` with INCR). Concurrency tests with simultaneous asynchronous requests confirm zero limit bypass. Both services enforce a strict fail-closed policy (HTTP 503) when Redis is unreachable.
4. **Privacy & Compliance:** User identifiers require 64-character hexadecimal SHA-256 hashes (`^[0-9a-f]{64}$`); raw PII (emails, short strings) is rejected with HTTP 422. User IDs in structured logs are masked. Ads unconditionally include `sponsored: true` and `disclosure_label: "Sponsored"`. Chat surfaces prohibit interstitial ads.
5. **Regression Verification:** All 70 Phase 5 backend tests passed (`70 passed, 0 failed`), all 34 Phase 1 security regression tests passed (`34 passed, 0 failed`), and live E2E container checks confirmed full integration.

---

## 2. Mandatory Preflight & Working Tree Status

- **Git Branch:** `main`
- **Initial Working Tree (`git status --short`):**
  ```text
   M backend/docker-compose.yml
   M backend/tests/test_ad_server/test_laya_client.py
   M frontend/src/pages/advertiser/CampaignBuilder.tsx
  ?? RIVINITYADS_FINAL_ACCEPTANCE_AUDIT.md
  ?? RIVINITYADS_PHASE_1_TO_6_IMPLEMENTATION_AUDIT.md
  ?? RIVINITYADS_PHASE_1_TO_6_IMPLEMENTATION_AUDIT_CORRECTED.md
  ?? RIVINITYADS_WU1_LAYA_INTEGRATION_REPORT.md
  ?? RIVINITYADS_WU3_CAMPAIGN_CHAT_E2E_REPORT.md
  ?? RIVINITYADS_WU4_PUBLISHER_DASHBOARD_REPORT.md
  ?? "RivinityAds_Phase2_Engineering (1).md"
  ```
- **Preservation Assurance:** All existing user changes, staged files, and untracked report artifacts from WU-1, WU-3, and WU-4 were preserved. No code refactoring or destructive Git operations were executed.
- **Sensitive Data Handling:** No credentials, raw keys, tokens, or signing secrets are printed in plain text.

---

## 3. Scope & Files Inspected

### 3.1 Backend Application Files
- [api_key_service.py](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/management/api_key_service.py): API key generation, prefixing, `scrypt` hashing, verification, listing, and revocation.
- [security.py](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/common/security.py): Password/key hashing, JWT verification, HMAC v2 method/path binding, timestamp drift window, and `authenticate_ad_request`.
- [publisher.py](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/models/publisher.py): SQLAlchemy models for `PublisherApiKey`, `AdSlot`, and `ApiKeyDailyStat`.
- [management.py (schemas)](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/schemas/management.py): Pydantic models for API key creation, responses, and usage breakdowns.
- [management.py (router)](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/ad_server/routers/management.py): Endpoints for publisher keys, usage statistics, and tenant isolation (`_assert_publisher_access`).
- [ad.py](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/ad_server/routers/ad.py): Ad serving endpoint, caller authentication gating, rate limiting, daily quota checks, and format compliance.
- [rate_limit_service.py](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/ad_server/services/rate_limit_service.py): Redis Lua atomic scripts, sliding window rate limits, daily quotas, usage tracking, and DB sync.
- [replay_guard.py](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/ad_server/services/replay_guard.py): Redis event deduplication guard for tracking URLs.

### 3.2 Automated Test Suites Inspected
- `tests/test_api/test_phase5_models.py` (9 tests)
- `tests/test_api/test_phase5_wu2_auth.py` (28 tests)
- `tests/test_api/test_phase5_wu3_ratelimit.py` (16 tests)
- `tests/test_api/test_phase5_wu4_compliance.py` (17 tests)
- `tests/test_ad_server/test_replay_guard.py` (7 tests)
- `tests/test_ad_server/test_creative_xss.py` (4 tests)
- `tests/test_ad_server/test_bug4_balance.py` (3 tests)
- `tests/test_ad_server/test_bug5_rate_limits.py` (6 tests)
- `tests/test_ad_server/test_bug6_tracking.py` (5 tests)
- `tests/test_ad_server/test_bug7_auth.py` (4 tests)
- `tests/test_ad_server/test_auth_regression.py` (3 tests)
- `tests/test_ad_server/test_budget_concurrency.py` (1 test)

---

## 4. Security Audit Matrix

| Category | Security Control / Invariant | Implementation Mechanism | Audit Findings & Evidence | Status |
|:---|:---|:---|:---|:---:|
| **API Key Security** | Cryptographic Randomness | `secrets.token_urlsafe(32)` in [api_key_service.py](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/management/api_key_service.py#L24) | Generated with 32 cryptographically secure random bytes; high entropy. | **VERIFIED** |
| **API Key Security** | Hash at Rest | `scrypt` ($N=16384, r=8, p=1$) with 16-byte random salt | Database table `publisher_api_keys` persists only `key_hash`. Raw key is never stored in DB. | **VERIFIED** |
| **API Key Security** | Zero Secret Exposure | Pydantic response models omit `key_hash` / `secret_hash` | `ApiKeyUsageResponse` and `ApiKeyResponse` exclude secret hashes. Verified via `test_schemas_do_not_expose_secret_hash`. | **VERIFIED** |
| **API Key Security** | One-Time Raw Key Presentation | `create_publisher_key` populates `raw_key` only on HTTP 201 | Subsequent `GET /keys` and `GET /keys/usage` omit `raw_key`. Table in UI displays only prefix. | **VERIFIED** |
| **API Key Security** | Tenant Ownership Scoping | `_assert_publisher_access(user, publisher_id)` in [management.py](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/ad_server/routers/management.py#L89-L92) | Enforced on every publisher endpoint. Verified via `test_sec_7_cross_publisher_slot_impersonation_prevented` (403). | **VERIFIED** |
| **API Key Security** | Revocation Enforcement | Status check in `resolve_publisher_api_key` & `status=0` | Revoked keys are rejected with HTTP 401; does not consume quota; does not fall back to anonymous. | **VERIFIED** |
| **HMAC v2 Auth** | Method & Path Cryptographic Binding | `msg = "{METHOD}\|{PATH}\|{ts}\|{sha256(body)}"` | Modifying method or request path invalidates signature. Verified via `test_method_or_path_tampering_invalidates_bound_signature`. | **VERIFIED** |
| **HMAC v2 Auth** | Constant-Time Comparison | `hmac.compare_digest(sig_v2, signature)` in [security.py](file:///c:/Users/Asus/Documents/bharattech/open%20adserver/backend/liteads/common/security.py#L397) | Prevents side-channel timing attacks on signature validation. | **VERIFIED** |
| **HMAC v2 Auth** | Freshness Drift Window | `abs(time.time() - ts) <= 300` | Timestamps at $\pm 295$s succeed; timestamps at $\pm 305$s fail with HTTP 401. Verified via `test_timestamp_boundary_*`. | **VERIFIED** |
| **HMAC v2 Auth** | Legacy Format Containment | v1 requires explicit `X-Signature-Version: 1` header | Legacy signatures strictly blocked on non-ad paths and rejected by default without version header. | **VERIFIED** |
| **Rate Limiting** | Sliding-Window Atomicity | Redis Lua script `_SLIDING_WINDOW_LUA` with ZSET | Atomic removal and addition. 8 concurrent requests against limit 3 yielded exactly 3 successes and 5 rejections (429). | **VERIFIED** |
| **Rate Limiting** | Daily Quota Atomicity | Redis Lua script `_DAILY_QUOTA_LUA` with atomic INCR | 10 concurrent requests against quota 4 yielded exactly 4 successes and 6 rejections (429). | **VERIFIED** |
| **Rate Limiting** | Fail-Closed Policy | Catches Redis connection failure, raises HTTP 503 | When Redis is unavailable, requests fail closed (HTTP 503) rather than allowing unmetered traffic. | **VERIFIED** |
| **Accounting** | Counter Accuracy | Redis hash `usage:apikey:{id}:{date}` with 30-day TTL | `admitted_requests`, `rate_limited_requests`, `quota_exceeded_requests` increment accurately. | **VERIFIED** |
| **Privacy** | 64-Hex SHA-256 User ID | Pydantic regex `^[0-9a-f]{64}$` in `AdRequest` | Raw emails, non-hex strings, and short IDs are rejected with HTTP 422. Uppercase hex is normalized. | **VERIFIED** |
| **Privacy** | Cache Key Defense-in-Depth | `CacheKeys._safe_user_key(user_id)` hashes all keys | Frequency capping Redis keys never store raw user identifiers or email addresses. | **VERIFIED** |
| **Privacy** | Log Sanitization | User ID masked in `routers/ad.py` (`id[:8]...id[-4:]`) | Raw identifiers are never leaked to application logs. | **VERIFIED** |
| **Compliance** | Sponsored Ad Labeling | Server sets `sponsored=True`, `disclosure_label="Sponsored"` | Client requests cannot suppress or override the sponsored disclosure. | **VERIFIED** |
| **Compliance** | Chat Surface Restrictions | `is_chat_surface=True` slots forbid format 4 (interstitial) | Interstitial ads are blocked on chat surfaces; non-chat slots allow them. Interstitial rejection is not billed. | **VERIFIED** |

---

## 5. Security Findings Classified by Severity

### 5.1 Critical Severity Findings
*None.* No authorization bypasses, unauthenticated admin actions, plaintext secret leaks, or financial manipulation vulnerabilities exist in Phase 5.

### 5.2 High Severity Findings
*None.*

### 5.3 Medium Severity Findings
*None.*

### 5.4 Low Severity & Hardening Observations

#### SEC-WU5-01: Ad-Request Signature Deduplication vs. Replay Drift Window
- **Component:** `backend/liteads/common/security.py:authenticate_ad_request`
- **Observation:** Financial tracking events (`/api/v1/event/track`) implement strict Redis nonce deduplication via `enforce_replay_guard` (`trk:nonce:{event}:{req}:{ad}` with 900s TTL, rejecting duplicates with HTTP 429). In contrast, S2S ad decision requests (`POST /api/v1/ad/request`) rely on the 300-second timestamp drift window, atomic per-minute rate limiting, and daily quotas. If an attacker intercepts a valid signed ad request in transit, they could replay that exact query within its 300-second validity window.
- **Risk Assessment:** Low. Replaying an ad request does **not** charge an advertiser or credit a publisher because financial billing is strictly bound to tracking events, which are guarded against replay. However, a replayed ad request could consume a publisher's per-minute rate limit (60 req/min) or daily quota.
- **Recommendation:** In Phase 7 (Programmatic / Public Gateway), consider adding an optional Redis signature cache (`SET trk:sig:{sha256(signature)} 1 EX 300 NX`) to enforce single-use signatures on public S2S ad requests.
- **Action Taken:** Documented as an architectural boundary. No immediate code change required as existing rate limits prevent flood attacks.

#### SEC-WU5-02: Deprecated Utility Calls in Supporting Modules
- **Component:** `backend/liteads/ad_server/routers/auth.py`, `backend/liteads/common/cache.py`
- **Observation:** Test runs surfaced non-blocking deprecation warnings:
  - `datetime.datetime.utcnow()` is deprecated in Python 3.12+ (scheduled for removal in future Python versions; replace with `datetime.now(datetime.UTC)`).
  - `redis_client.close()` is deprecated in `redis-py 5.0+` (replace with `await redis_client.aclose()`).
- **Risk Assessment:** Low / Technical Debt. No security or behavioral impact under Python 3.13.
- **Recommendation:** Modernize datetime and Redis async client calls during scheduled platform maintenance.

---

## 6. Regression Test Suite Execution

All test suites were executed independently using the virtual environment Python interpreter (`.venv\Scripts\pytest`):

### 6.1 Phase 5 Core Test Suites
```bash
.venv\Scripts\pytest tests/test_api/test_phase5_models.py
.venv\Scripts\pytest tests/test_api/test_phase5_wu2_auth.py
.venv\Scripts\pytest tests/test_api/test_phase5_wu3_ratelimit.py
.venv\Scripts\pytest tests/test_api/test_phase5_wu4_compliance.py
```
- `test_phase5_models.py`: **9 passed** (2.70s)
- `test_phase5_wu2_auth.py`: **28 passed** (14.80s)
- `test_phase5_wu3_ratelimit.py`: **16 passed** (16.76s)
- `test_phase5_wu4_compliance.py`: **17 passed** (8.74s)
- **Phase 5 Total: 70 passed, 0 failed, 0 skipped** (Exit code: `0`)

### 6.2 Phase 1 Security Hardening Regression Suite
```bash
.venv\Scripts\pytest tests/test_api/test_finance_api.py
.venv\Scripts\pytest tests/test_ad_server/test_replay_guard.py
.venv\Scripts\pytest tests/test_ad_server/test_creative_xss.py
.venv\Scripts\pytest tests/test_ad_server/test_bug4_balance.py
.venv\Scripts\pytest tests/test_ad_server/test_bug5_rate_limits.py
.venv\Scripts\pytest tests/test_ad_server/test_bug6_tracking.py
.venv\Scripts\pytest tests/test_ad_server/test_bug7_auth.py
```
- `test_finance_api.py`: **5 passed**
- `test_replay_guard.py`: **7 passed**
- `test_creative_xss.py`: **4 passed**
- `test_bug4_balance.py`: **3 passed**
- `test_bug5_rate_limits.py`: **6 passed**
- `test_bug6_tracking.py`: **5 passed**
- `test_bug7_auth.py`: **4 passed**
- **Phase 1 Security Total: 34 passed, 0 failed, 0 skipped** (Exit code: `0`)

### 6.3 Shared Auth & Concurrency Regression Suite
```bash
.venv\Scripts\pytest tests/test_ad_server/test_auth_regression.py tests/test_ad_server/test_budget_concurrency.py
```
- `test_auth_regression.py`: **3 passed** (advertiser tenant isolation, publisher tenant isolation, login rate limiting)
- `test_budget_concurrency.py`: **1 passed** (atomic row locking on advertiser balance deductions)
- **Total: 4 passed, 0 failed** (Exit code: `0`)

### 6.4 Phase 2 & Phase 3 Shared Regression Suite
```bash
.venv\Scripts\pytest tests/test_ad_server/test_sector_classifier.py tests/test_ad_server/test_phase2_models.py tests/test_api/test_phase3a.py
```
- `test_sector_classifier.py`: **5 passed**
- `test_phase2_models.py`: **4 passed**
- `test_phase3a.py`: **4 passed**
- **Total: 13 passed, 0 failed** (Exit code: `0`)

### 6.5 Live HTTP Integration Script
```bash
.venv\Scripts\python scratch/verify_publisher_dashboard_e2e.py
```
- **Result:** **16/16 live assertions passed** against the running container (Exit code: `0`).

---

## 7. Scope Boundaries & Code Changes

- **Code Changes:** Zero code modifications were made during WU-5. The read-first audit confirmed that Phase 5 meets all security, architectural, and compliance requirements without defects requiring immediate patching.
- **Phase 4 Excluded:** Phase 4 models, tests, endpoints, migrations, and schemas remain completely untouched.
- **Phase 6 Boundary:** Phase 6 (Yield optimization, RTB bidding, SSP floor pricing) was not started.
- **Final Git Status:** Clean preservation of prior work units:
  ```text
   M backend/docker-compose.yml
   M backend/tests/test_ad_server/test_laya_client.py
   M frontend/src/pages/advertiser/CampaignBuilder.tsx
  ?? RIVINITYADS_FINAL_ACCEPTANCE_AUDIT.md
  ?? RIVINITYADS_PHASE_1_TO_6_IMPLEMENTATION_AUDIT.md
  ?? RIVINITYADS_PHASE_1_TO_6_IMPLEMENTATION_AUDIT_CORRECTED.md
  ?? RIVINITYADS_WU1_LAYA_INTEGRATION_REPORT.md
  ?? RIVINITYADS_WU3_CAMPAIGN_CHAT_E2E_REPORT.md
  ?? RIVINITYADS_WU4_PUBLISHER_DASHBOARD_REPORT.md
  ?? RIVINITYADS_WU5_PHASE5_SECURITY_AUDIT_REPORT.md
  ?? "RivinityAds_Phase2_Engineering (1).md"
  ```

---

## 8. Residual Risks & Unverified Areas

1. **Multi-Node Redis Clustering:** Redis sliding-window and quota Lua scripts were audited and verified against single-node Redis (and local Docker Compose Redis). In a distributed Redis Cluster deployment where keys reside on different hash slots, multi-key pipelines must use hash tags (e.g., `{apikey:123}`) to ensure Lua execution colocation.
2. **Periodic Cron Worker for DB Sync:** `sync_daily_stat_to_db` accurately flushes Redis daily usage hashes to `ApiKeyDailyStat` in PostgreSQL. Production deployments must configure a persistent background scheduler (e.g. Celery beat or Cronjob) to execute this synchronization periodically.

---

## 9. Final Audit Verdict

**Audit Verdict: ACCEPTED**

All required Phase 5 security and regression audit areas were rigorously examined:
- Publisher API key generation, `scrypt` hashing, one-time raw secret presentation, and tenant isolation are completely verified.
- Method- and path-bound HMAC v2 signatures with SHA-256 body digests and 300s drift boundaries are proven correct.
- Concurrency-safe sliding window rate limiting and daily quotas function atomically with fail-closed error handling.
- User privacy safeguards (64-character hex SHA-256 requirement and log masking) are strictly enforced.
- All 70 Phase 5 backend tests, 34 Phase 1 security regression tests, and 17 shared regression tests passed with zero failures.
- No unresolved Critical or High security findings remain.
