# RivinityADS — Phase 11: Conversion Measurement & Advertiser Outcome Reporting Implementation Report

**Date:** 2026-10-28  
**Phase:** Phase 11 — Conversion Measurement & Advertiser Outcome Reporting  
**Status:** PASS  
**Migration Head:** `n6f7a8b9c0d1` (Revises `m5e6f7a8b9c0`)  
**Workspace:** `c:\Users\Asus\Documents\bharattech\open adserver`

---

## Executive Summary

Phase 11 implements an end-to-end conversion measurement pipeline and trustworthy advertiser outcome reporting system for RivinityADS. It connects user ad interactions (impressions and clicks) to advertiser downstream conversion events via signed click attribution tokens and authenticated S2S postbacks. The system enforces a conservative, explainable 30-day last-touch (last-click) attribution model in UTC, strict deduplication via partial unique indexing, atomic CPA wallet debiting with canonical row-locking, and campaign-level outcome metrics (CVR, CPA, ROAS, and conversion value) with strict currency compatibility and zero-division protection.

All 15 required automated test scenarios passed (15/15). The Alembic migration `n6f7a8b9c0d1` was independently verified against an isolated PostgreSQL database through upgrade, downgrade, and re-upgrade cycles without touching baseline development data. All 26 backend regression tests across Phases 9.3, 10F, 10G, and Finance passed. All 161 frontend tests and the production Vite bundle build completed with 0 errors.

---

## 1. Existing Functionality Audited

Prior to implementation, the repository was audited across the following areas:
- **`AdEvent` Model (`backend/liteads/models/ad.py`):** Previously contained `request_id`, `campaign_id`, `creative_id`, `event_type`, `event_time`, `user_id`, `cost`, and `idempotency_key`. It lacked attribution tracking (`is_attributed`, `attribution_reason`, `attributed_event_id`), monetary conversion values, currency scoping, and test flags.
- **Tracking Signatures (`backend/liteads/common/tracking_sig.py`):** Provided `sign_tracking` and `verify_tracking` with a 10-minute signature TTL (`settings.tracking.signature_ttl_seconds = 600`), suitable for immediate impressions/clicks, but lacked durable click attribution tokens for conversion lifecycles up to 30 days.
- **Event Router & Ingestion (`backend/liteads/ad_server/routers/event.py`):** Ingested impressions and clicks with replay guard reservations, but lacked dedicated client and S2S conversion postback endpoints and 1x1 conversion pixel tracking.
- **Advertiser & Ad Set Reporting (`backend/liteads/ad_server/routers/management.py`):** Computed basic impressions, clicks, conversions, and spend from raw counts, but lacked outcome reporting (CVR, CPA, ROAS, conversion value), did not exclude test traffic (`is_test`), and lacked multi-currency conflict detection.
- **Financial Architecture (`backend/liteads/ad_server/services/billing.py`):** Atomic debiting mechanism with row-locking (`SELECT FOR UPDATE`) on `Advertiser`, `Campaign`, and `Publisher` in canonical order. CPA conversion charges needed integration with this service without introducing secondary sources of truth.
- **Alembic Migration Chain:** Verified current head was `m5e6f7a8b9c0` (Phase 10E/10F).

---

## 2. Requirements Implemented and Files Changed

### Files Modified & Created

| File | Change Type | Description |
|---|---|---|
| `backend/docs/PHASE_11_CONVERSION_MEASUREMENT_CONTRACT.md` | Created | Authoritative Phase 11 event contract, mathematical formulas, and semantics. |
| `backend/alembic/versions/n6f7a8b9c0d1_phase11_conversion_measurement.py` | Created | Alembic migration adding Phase 11 schema, foreign keys, and indexes. |
| `backend/liteads/models/ad.py` | Modified | Extended `AdEvent` ORM model with 8 new fields, self-referential relationship, and composite indexes. |
| `backend/liteads/common/tracking_sig.py` | Modified | Added `sign_click_token`, `verify_click_token`, `sign_conversion_sig`, and `verify_conversion_sig`. |
| `backend/liteads/schemas/request.py` | Modified | Added `ConversionEventRequest` schema with validation for values, currencies, and test flags. |
| `backend/liteads/schemas/response.py` | Modified | Added `ConversionEventResponse` and added `click_token` to `TrackingUrls`. |
| `backend/liteads/schemas/management.py` | Modified | Extended `AdvertiserReportingResponse`, `AdSetReportingItem`, `AdSetReportingResponse`, and `DailyStatResponse` with outcome metrics. |
| `backend/liteads/ad_server/services/conversion_service.py` | Created | Core service for attribution, deduplication, CPA billing, and diagnostics. |
| `backend/liteads/ad_server/routers/conversion.py` | Created | Ingestion endpoints (`/event/conversion`, `/conversion/postback`, `/conversion/pixel`, `/conversion/diagnostics/*`). |
| `backend/liteads/ad_server/routers/ad.py` | Modified | Generates durable `click_token` upon ad candidate selection and embeds into `TrackingUrls`. |
| `backend/liteads/ad_server/routers/management.py` | Modified | Outcome metric calculation, CVR/CPA/ROAS logic, test-traffic exclusion, and currency validation. |
| `backend/liteads/ad_server/main.py` | Modified | Registered `conversion.router` under `/api/v1`. |
| `backend/liteads/static/rivinity.js` | Modified | Added `window.Rivinity.trackConversion` and automatic `rv_click_token` URL/sessionStorage handling. |
| `backend/tests/test_api/test_phase11_conversion_measurement.py` | Created | Comprehensive 15-scenario automated test suite. |
| `backend/docs/PHASE_11_IMPLEMENTATION_REPORT.md` | Created | This authoritative Phase 11 completion and acceptance report. |

---

## 3. Event Contract and Attribution Rules

### Semantic Event Types
- **Impression (`event_type == 1`):** Ad creative rendered on a verified publisher surface.
- **Click (`event_type == 2`):** User click on an ad placement navigating to advertiser destination.
- **Conversion (`event_type == 3`):** Action completed by the user (purchase, signup, lead) on the advertiser site.

### Click-to-Conversion Association
1. **Attribution Model:** Conservative **Last-Touch (Last-Click)** attribution. The most recent valid click interaction within the active attribution window receives attribution credit.
2. **Attribution Window:** Fixed duration of **30 days** (`2,592,000 seconds`).
3. **UTC Handling:** All event timestamps are parsed, converted, and compared in UTC.
4. **Boundary Evaluation:**
   - Interaction elapsed time: $\Delta t = t_{\text{conversion}} - t_{\text{click}}$
   - If $0 \le \Delta t \le 2,592,000\text{ seconds}$: `is_attributed = True`, `attribution_reason = "last_click"`.
   - If $\Delta t > 2,592,000\text{ seconds}$: `is_attributed = False`, `attribution_reason = "outside_window"`.
   - If click is missing or cannot be matched: `is_attributed = False`, `attribution_reason = "click_not_found"` or `"missing_click_identifier"`.

### Ingestion vs. Attribution
An ingested conversion event is never automatically counted as an attributed conversion. The event is first recorded with status flags and only attributed conversions increment campaign outcome metrics or trigger CPA charges.

---

## 4. API & SDK Integration

### Endpoints Implemented

1. **`POST /api/v1/event/conversion`**
   - Ingests client conversions via `click_token` or `request_id`.
   - Deduplicates identical orders via `(campaign_id, external_order_id)`.
   - Returns typed `ConversionEventResponse`.

2. **`POST /api/v1/conversion/postback`**
   - Server-to-server authenticated postback.
   - Validates HMAC-SHA256 signature signed over `{campaign_id}|{order_id}|{value}|{currency}|{timestamp}` within a 24-hour timestamp drift window.

3. **`GET /api/v1/conversion/pixel`**
   - Lightweight image pixel tracking returning 43-byte transparent `1x1` GIF with `Cache-Control: no-cache, no-store, must-revalidate`.

4. **`POST /api/v1/conversion/diagnostics/test-event`**
   - Authenticated simulation endpoint enforcing `is_test = True`. Allows advertisers to test attribution and webhook pipelines without billing charges or contaminating production reporting.

5. **`GET /api/v1/conversion/diagnostics/timeline`**
   - Authenticated chronological timeline of impressions, clicks, and conversions for a campaign. Protected by tenant authorization guards.

6. **`GET /api/v1/conversion/diagnostics/summary`**
   - Operational diagnostic summary reporting breakdown of attributed conversions, unattributed conversions, test events, and reasons.

### JavaScript SDK Integration (`rivinity.js`)
- `window.Rivinity.trackConversion({ orderId, value, currency, isTest })`: Automatically reads stored `rv_click_token` from URL parameter or `sessionStorage` and dispatches to `/api/v1/event/conversion`.

---

## 5. Metric Formulas and Currency Rules

### Reporting Formulas

| Metric | Formula | Denominator Condition | Fallback / Unavailable |
|---|---|---|---|
| **CTR** | $\left(\frac{\text{clicks}}{\text{impressions}}\right) \times 100$ | $\text{impressions} > 0$ | `Decimal("0")` |
| **CVR** | $\left(\frac{\text{attributed\_conversions}}{\text{clicks}}\right) \times 100$ | $\text{clicks} > 0$ | `Decimal("0")` |
| **CPA** | $\frac{\text{spend}}{\text{attributed\_conversions}}$ | $\text{attributed\_conversions} > 0$ | `None` (Null) |
| **ROAS** | $\frac{\text{conversion\_value}}{\text{spend}}$ | $\text{spend} > 0$ | `None` (Null) |

### Currency Invariants
- Default currency is `"INR"`. Supported values must match ISO-4217 three-letter codes (`^[A-Z]{3}$`).
- When querying advertiser or campaign reports, the system checks for distinct currencies across the queried range. If incompatible currencies are detected, the endpoint raises HTTP 422: `"Incompatible currencies detected in reporting period"`.

---

## 6. Migration Details and PostgreSQL Verification

### Alembic Revision: `n6f7a8b9c0d1`
- **Revises:** `m5e6f7a8b9c0` (Phase 10E/10F head).
- **Columns Added to `ad_events`:**
  - `ad_set_id`: Integer, ForeignKey(`ad_sets.id`, ondelete="SET NULL"), nullable.
  - `conversion_value`: Numeric(12, 4), nullable.
  - `currency`: String(3), server_default="INR", nullable=True.
  - `is_attributed`: Boolean, server_default=false, nullable=False.
  - `attribution_reason`: String(64), nullable.
  - `attributed_event_id`: Integer, ForeignKey(`ad_events.id`, ondelete="SET NULL"), nullable.
  - `external_order_id`: String(128), nullable.
  - `is_test`: Boolean, server_default=false, nullable=False.
- **Indexes Created:**
  - `ix_ad_events_attribution` on `(campaign_id, is_attributed, is_test)`.
  - `ix_ad_events_click_lookup` on `(request_id, event_type)`.
  - `uq_ad_events_order_dedup` unique partial index on `(campaign_id, external_order_id)` WHERE `external_order_id IS NOT NULL AND is_test = false`.

### PostgreSQL Test Execution
The migration was verified against an isolated PostgreSQL test database (`liteads_migration_test_db`) cloned from `liteads`:
- **Upgrade to head:** Succeeded (Exit code 0).
- **Schema inspection:** All 8 columns and 3 indexes verified in PostgreSQL system catalogs (`information_schema.columns` and `pg_indexes`).
- **Downgrade to `m5e6f7a8b9c0`:** Succeeded (Exit code 0). All Phase 11 columns, foreign keys, and indexes dropped cleanly.
- **Re-upgrade to head:** Succeeded (Exit code 0). Current revision returned `n6f7a8b9c0d1`.
- **Teardown:** Test database dropped cleanly; primary development database `liteads` remained untouched.

---

## 7. Security, Privacy, and Financial-Invariant Results

1. **No Duplicate Billing:** Retrying a conversion postback with the same `(campaign_id, external_order_id)` returns an idempotent response (`is_duplicate = True`) and makes zero ledger deductions.
2. **CPA Billing Scoping:** For CPA campaigns (`bid_type == 3`), charges occur **only** if `is_attributed == True` and `is_test == False`.
3. **Test Traffic Isolation:** Conversions marked `is_test = True` produce strictly `cost = Decimal("0")` and are filtered out of all production performance metrics.
4. **Tenant Isolation:** Cross-tenant access attempts to campaign diagnostics or reporting return HTTP 403 Forbidden.
5. **No PII Leakage:** User IDs are validated privacy-safe SHA-256 hashes. Diagnostics timelines do not expose signing secrets or raw tokens.

---

## 8. Test Execution Evidence

### Phase 11 Test Suite
**Command:** `backend\.venv\Scripts\pytest.exe backend/tests/test_api/test_phase11_conversion_measurement.py -v`  
**Exit Code:** 0  
**Results:** **15 passed, 0 failed** in 7.19s

```
backend\tests\test_api\test_phase11_conversion_measurement.py::test_valid_click_to_conversion_attribution PASSED [  6%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_conversion_outside_attribution_window PASSED [ 13%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_duplicate_postback_and_retry_idempotency PASSED [ 20%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_invalid_signature_postback PASSED [ 26%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_expired_or_replayed_request PASSED [ 33%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_multi_campaign_creative_mapping PASSED [ 40%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_cross_tenant_authorization_isolation PASSED [ 46%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_utc_attribution_window_boundary PASSED [ 53%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_missing_conversion_value_and_currency_validation PASSED [ 60%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_incompatible_currencies_in_reporting PASSED [ 66%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_zero_spend_and_division_by_zero PASSED [ 73%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_test_event_exclusion_from_production_metrics PASSED [ 80%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_invalid_payloads_and_tampered_click_tokens PASSED [ 86%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_existing_impression_click_tracking_regressions PASSED [ 93%]
backend\tests\test_api\test_phase11_conversion_measurement.py::test_no_duplicate_spend_or_wallet_deduction PASSED [100%]
```

### Backend Regression Suites
**Command:** `backend\.venv\Scripts\pytest.exe backend/tests/test_api/test_phase10f_monetization_consent.py backend/tests/test_api/test_phase10g_security_e2e.py backend/tests/test_api/test_phase9_3_persistence_lifecycle.py backend/tests/test_api/test_finance_api.py -v`  
**Exit Code:** 0  
**Results:** **26 passed, 0 failed** in 13.73s

### Frontend Build & Test Verification
1. **Build Command:** `npm run build` in `frontend` (`tsc --noEmit && vite build`)  
   **Exit Code:** 0  
   **Result:** 694 modules transformed, bundle emitted in 3.87s without TypeScript errors.
2. **Test Command:** `npm run test` in `frontend`  
   **Exit Code:** 0  
   **Results:** **161 passed, 0 failed** across 18 test suites in 15.03s.

---

## 9. Remaining Limitations and Deployment Prerequisites

1. **Multi-Touch Attribution:** Phase 11 implements conservative Last-Touch (Last-Click) attribution. Multi-touch attribution (first-touch, linear, time-decay) is slated for future analytical roadmap phases.
2. **Cross-Device Graph:** Attribution is currently single-token / single-browser session. Cross-device graph matching is intentionally excluded in accordance with privacy rules.
3. **Production Database Upgrade:** Before deploying to live production, run `alembic upgrade head` to apply revision `n6f7a8b9c0d1` to the production PostgreSQL instance.

---

## 10. Final Classification

### **Final Classification: PASS**

The Phase 11 conversion measurement pipeline, durable click-token attribution system, S2S HMAC postbacks, outcome reporting (CVR, CPA, ROAS), operational diagnostics, and database migrations are fully implemented, verified, and passing all automated test suites.
