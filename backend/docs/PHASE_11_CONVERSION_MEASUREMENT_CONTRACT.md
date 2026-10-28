# RivinityADS — Phase 11: Conversion Measurement & Event Contract Specification

## 1. Executive Summary & Purpose

Phase 11 introduces authoritative, privacy-respecting conversion measurement and campaign outcome reporting to RivinityADS. It links downstream advertiser conversion events (e.g., e-commerce purchases, lead submissions, account registrations) back to upstream ad interactions (clicks and impressions) within a deterministic, explainable attribution window.

This document establishes the canonical event contract, attribution rules, cryptographic signature standards, idempotency mechanisms, financial invariants, and diagnostic protocols.

---

## 2. Event Semantics & Lifecycle

### 2.1 The Three Event Tiers

RivinityADS distinguishes three distinct event types:

```text
Ad Request & Decisioning
  └── [Impression Event] (event_type = 1) — Ad served and displayed to user
        └── [Click Event] (event_type = 2) — User engages with ad; initiates attribution baseline
              └── [Conversion Event] (event_type = 3) — Desired outcome on advertiser site
```

1. **Impression Event (`EventType.IMPRESSION = 1`)**:
   - **Trigger**: Creative rendered in user viewport (pixel loaded or client SDK callback).
   - **Billing**: If campaign/ad set uses CPM (`bid_type == 1`), `cost = bid_amount / 1000`.
   - **TTL**: Signature valid for 600 seconds (10 minutes) from decision timestamp.
   - **Deduplication**: Enforced via `idempotency_key = "{request_id}:{creative_id}:1:{seq}"`.

2. **Click Event (`EventType.CLICK = 2`)**:
   - **Trigger**: User clicks creative link or interactive element.
   - **Billing**: If campaign/ad set uses CPC (`bid_type == 2`), `cost = bid_amount`.
   - **TTL**: Signature valid for 600 seconds (10 minutes) from decision timestamp.
   - **Deduplication**: Enforced via `idempotency_key = "{request_id}:{creative_id}:2:{seq}"`.
   - **Attribution Baseline**: Sets the anchor timestamp `click_time` for downstream conversion attribution.

3. **Conversion Event (`EventType.CONVERSION = 3`)**:
   - **Trigger**: User completes defined conversion action on advertiser site/app.
   - **Billing**: If campaign/ad set uses CPA (`bid_type == 3`), `cost = bid_amount` **ONLY IF** the conversion is successfully attributed (`is_attributed = True`) and is not a test event (`is_test = False`). Unattributed or test conversions generate `cost = Decimal("0")`.
   - **Lifecycle**: Validated upon receipt -> Attributed against historical click interaction -> Persisted with immutable idempotency key -> Aggregated into campaign outcome metrics.

---

## 3. Detailed Conversion Event Contract

### 3.1 Field Specification

| Field Name | Type | Presence | Description & Constraints | Derived vs Supplied |
| :--- | :--- | :--- | :--- | :--- |
| `click_id` | `String(128)` | Optional* | Identifier of the originating click (or original `request_id`). *Required for click attribution. | Supplied by advertiser / pixel |
| `request_id` | `String(64)` | Optional* | Originating ad request ID. Can serve as `click_id`. | Supplied by advertiser / pixel |
| `campaign_id` | `Integer` | Optional* | Target campaign ID. Derived from `click_id` if omitted, or supplied explicitly in S2S. | Supplied / Derived |
| `conversion_name` | `String(50)` | Optional | Conversion category: `purchase`, `lead`, `signup`, `add_to_cart`, `custom`. Default: `purchase`. | Supplied |
| `conversion_value`| `Numeric(12,4)`| Optional | Monetary value of the conversion event (e.g. order subtotal). Must be `>= 0`. Default: `0.00`. | Supplied |
| `currency` | `String(3)` | Optional | ISO 4217 3-letter currency code (e.g. `INR`, `USD`, `EUR`). Default: `INR`. | Supplied |
| `order_id` | `String(128)` | Optional | Advertiser-side transaction/order identifier (e.g. `ORD-98471`). Enables persistent deduplication. | Supplied |
| `sig` | `String(128)` | Optional* | Cryptographic tracking signature. Required for unauthenticated client pixels. | Supplied / Verified |
| `is_test` | `Boolean` | Optional | Test flag (`true`/`false`). When `true`, validates pipeline without charging or affecting metrics. | Supplied (Default: `false`) |
| `consent` | `Boolean` | Optional | User consent indicator. Default: `true`. | Supplied |
| `user_id` | `String(64)` | Optional | Privacy-safe SHA-256 pseudonymized user identifier. No raw email/phone. | Supplied / Derived |
| `is_attributed` | `Boolean` | Derived | Server-determined boolean indicating whether conversion met all attribution criteria. | Server Derived |
| `attribution_reason`| `String(64)` | Derived | Reason code: `last_click`, `outside_window`, `click_not_found`, `conversion_precedes_click`, `missing_click_id`. | Server Derived |
| `attributed_event_id`| `Integer` | Derived | Database ID of the matched `AdEvent` click record. | Server Derived |
| `event_time` | `DateTime` | Derived | UTC timestamp when conversion was validated and recorded. | Server Derived |

---

## 4. Attribution Model & Window Rules

### 4.1 Default Attribution Model: Last-Touch (Last-Click)
RivinityADS implements a conservative, deterministic, and explainable **Last-Touch Click Attribution** model:

1. **Eligible Interactions**: Only validated **Click Events** (`event_type == EventType.CLICK`) are eligible to receive attribution credit. Impression-only interactions do not receive conversion credit by default.
2. **Attribution Window**: Exactly **30 calendar days** (defined as `30 * 86,400 = 2,592,000 seconds`).
3. **Time-Zone Handling**: All timestamp calculations and comparisons are strictly evaluated in **UTC**:
   $$\Delta t = \text{conversion\_time}_{\text{UTC}} - \text{click\_time}_{\text{UTC}}$$
4. **Boundary Invariant**:
   - If $0 \le \Delta t \le 2,592,000 \text{ seconds}$: **Attributed** (`is_attributed = True`, `attribution_reason = "last_click"`).
   - If $\Delta t > 2,592,000 \text{ seconds}$: **Unattributed (Accepted, Ineligible)** (`is_attributed = False`, `attribution_reason = "outside_window"`).
   - If $\Delta t < 0$ (conversion precedes click): **Unattributed** (`is_attributed = False`, `attribution_reason = "conversion_precedes_click"`).
5. **Multiple Interactions**: If a user clicked multiple ads prior to converting, credit is assigned strictly to the **most recent eligible click** occurring before the conversion event.
6. **No Causality Claim**: Attributed conversion reporting reflects temporal correlation within the agreed attribution window; it is not presented to advertisers as scientific proof of incremental causality.

---

## 5. Outcome Reporting Metrics & Mathematical Formulas

Campaign outcome reporting incorporates conversion metrics into `AdvertiserReportingResponse`:

### 5.1 Metric Formulas & Denominators

1. **Total Validated Conversions**:
   $$\text{Total Conversions} = \sum \mathbf{1}_{(\text{event\_type}=3 \land \text{is\_test}=\text{false})}$$

2. **Attributed Conversions**:
   $$\text{Attributed Conversions} = \sum \mathbf{1}_{(\text{event\_type}=3 \land \text{is\_attributed}=\text{true} \land \text{is\_test}=\text{false})}$$

3. **Total Conversion Value**:
   $$\text{Conversion Value} = \sum \text{conversion\_value}_{(\text{event\_type}=3 \land \text{is\_attributed}=\text{true} \land \text{is\_test}=\text{false})}$$

4. **Conversion Rate (CVR)**:
   $$\text{CVR (\%)} = \begin{cases} \left(\frac{\text{Attributed Conversions}}{\text{Clicks}}\right) \times 100, & \text{if Clicks} > 0 \\ 0.00, & \text{if Clicks} = 0 \end{cases}$$
   *Explicit Denominator*: Total billable Clicks for the target campaign/ad set.

5. **Cost Per Acquisition (CPA)**:
   $$\text{CPA} = \begin{cases} \frac{\text{Total Spend}}{\text{Attributed Conversions}}, & \text{if Attributed Conversions} > 0 \\ \text{null / 0.00}, & \text{if Attributed Conversions} = 0 \end{cases}$$
   *Explicit Denominator*: Attributed Conversions. Prevents division by zero.

6. **Return On Ad Spend (ROAS)**:
   $$\text{ROAS} = \begin{cases} \frac{\text{Conversion Value}}{\text{Total Spend}}, & \text{if Total Spend} > 0 \\ \text{null / 0.00}, & \text{if Total Spend} = 0 \end{cases}$$
   *Explicit Denominator*: Total Spend in the campaign currency.

### 5.2 Currency Invariants
- All monetary metrics (`spend`, `conversion_value`, `cpa`) are denominated in the campaign/account currency (default: `INR`).
- When querying across campaigns, all events must share the same ISO currency. If multiple currencies are detected in a single aggregation scope, the reporting engine rejects the query with HTTP 422 rather than silently summing conflicting currencies.

---

## 6. Security, Authentication & Deduplication

1. **Authentication Channels**:
   - **Server-to-Server (S2S) Postback**: Authenticated via `X-API-Key` or `Authorization: Bearer <jwt>`. The server verifies that the caller owns the campaign.
   - **Client-Side Pixel**: Authenticated via signed conversion token `sig` generated with `settings.tracking.secret`.
2. **Durable Idempotency**:
   - When an advertiser supplies `order_id` (transaction ID), a unique partial index on `(campaign_id, external_order_id)` guarantees that retries or refreshed thank-you pages are handled as idempotent no-ops.
   - Deduplicated retries return HTTP 200 with `message: "Event already recorded (idempotent duplicate)"` and do not increment counters or generate duplicate ledger charges.
3. **Test Mode Isolation**:
   - Submissions with `is_test: true` are recorded with `is_test = True`.
   - Test events bypass billing (`cost = 0.00`) and are excluded from production reporting queries via `WHERE is_test = false`.
