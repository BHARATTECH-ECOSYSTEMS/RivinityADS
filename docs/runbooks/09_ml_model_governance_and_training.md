# Runbook 09: ML Model Governance, Reproducible Training & Serving Safety

## Objective
Authoritative operating runbook for Machine Learning model governance, artifact manifest contracts, reproducible training pipelines, offline calibration, shadow-mode evaluation, multi-gate promotion, and emergency rollback in Bharattech Ads / RivinityADS.

---

## 1. Architectural Foundations: Statistical Authority & CPU Safety

RivinityADS strictly separates business-critical ad serving from experimental ML inference:
1. **Statistical Serving Default:** By default, live serving uses the deterministic `StatisticalPredictor` (computing smoothed historical CTR/CVR within 0.1 ms). This ensures 100% uptime, zero external model dependencies, and zero reliance on heavy neural network runtimes during core auction delivery.
2. **CPU-First Runtime Safety:** All model artifacts and PyTorch weights are loaded with `map_location=torch.device("cpu")`. Online serving nodes never mandate GPU hardware or CUDA drivers.
3. **Graceful Fallback Invariant:** If an ML model times out (> 20 ms), raises an exception, encounters unreadable weights, or is disabled, the system instantly and safely falls back to the statistical baseline. Serving is never blocked or aborted.
4. **Lazy / Optional Dependency Separation:** Baseline statistical serving starts and functions completely even in environments where PyTorch is not installed or when ML prediction is turned off.

---

## 2. Model Artifact Manifest Contract

Every trained model artifact (`.pt` checkpoint) must be accompanied by an immutable `ModelArtifactManifest`:

```json
{
  "schema_version": "1.0.0",
  "model_name": "deepfm_ctr",
  "model_version": "v1.0.0",
  "model_architecture": "deepfm",
  "sha256_checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "training_dataset_hash": "a1b2c3d4e5f6...",
  "time_window": {
    "train_start": "2026-10-01T00:00:00Z",
    "train_end": "2026-10-25T23:59:59Z",
    "validation_start": "2026-10-26T00:00:00Z",
    "validation_end": "2026-10-31T23:59:59Z"
  },
  "label_leakage_checked": true,
  "finite_weights_verified": true,
  "privacy_review_passed": true,
  "metrics": {
    "auc": 0.742,
    "log_loss": 0.082,
    "brier_score": 0.015,
    "pcoctr": 1.02,
    "ece": 0.038,
    "business_value_lift": 0.045,
    "shadow_agreement": 0.78
  },
  "created_at": "2026-11-01T12:00:00Z"
}
```

### Manifest Verification Checklist:
- `sha256_checksum` matches the exact hash of the checkpoint file.
- All model weights are finite numbers (strictly no `NaN` or `Inf` tensors).
- `label_leakage_checked` is `true`.
- `privacy_review_passed` is `true`.
- `metrics` contain valid AUC, Log-Loss, ECE, PCOCTR, and non-negative business-value lift.

---

## 3. Time-Based Dataset Splits & Label Leakage Prevention

Random k-fold and uniform shuffling across ad event datasets are strictly prohibited because they leak future information into past training instances.

### Temporal Invariants:
1. **Ordering Rule:** `train_end <= val_start <= test_start`.
2. **Embargo Gap:** Training and validation windows can incorporate an embargo gap (e.g. 6 to 24 hours) to isolate conversion attribution delays.
3. **Lookahead / Inversion Prevention:** If `val_start < train_end` or `val_end < val_start`, the `TimeBasedSplitter` fails closed immediately with `TimeSplitError`.

### Label Leakage Audit:
Feature pipelines are checked against `PROHIBITED_LEAKAGE_COLUMNS`:
- **Future Campaign Rollups:** `spent_total`, `campaign_total_conversions`, `campaign_total_clicks`, `total_spend`.
- **Post-Event Outcomes:** `conversion_time`, `click_timestamp`, `attribution_window`, `post_click_event`, `is_converted`, `conversion_value`, `revenue_generated`.
Any presence of these columns raises `LabelLeakageError` and fails the training run.

---

## 4. Privacy, PII Guardrails & Segment Fairness

### Strict PII Denylist:
Machine learning tensors and feature dictionaries must never ingest raw personal identifiable information. The following fields are prohibited:
`raw_ip`, `ip_address`, `client_ip`, `email`, `phone`, `user_agent`, `cookie`, `raw_device_id`, `imei`, `advertising_id`, `idfa`, `gps_latitude`, `gps_longitude`.

### Minor Protection & Coarse Geo:
- Minor protection: Users under age 18 have age bucketing set to `"restricted"` and gender set to `"unspecified"`.
- Coarse geographic data only: country, state/region, and city strings are permitted. Raw GPS coordinates are stripped.

### Privacy-Preserving Segment Evaluation:
When evaluating offline slices (e.g. by device OS, ad format, publisher category):
- **Suppression Threshold:** Any segment slice with fewer than `min_segment_samples = 30` events is marked `is_suppressed = true`.
- Sensitive metrics (AUC, PCOCTR, Brier score, ECE) are redacted (`null`) for small slices to prevent micro-cohort identification or targeting re-identification.

---

## 5. Offline Calibration & Business-Value Evaluation

Evaluation cannot rely solely on ROC-AUC, as AUC only measures ranking order and is insensitive to probability calibration or eCPM revenue lift.

### Evaluation Gates:
1. **Expected Calibration Error (ECE):**
   $$ECE = \sum_{b=1}^B \frac{|B_b|}{N} | \text{acc}(B_b) - \text{conf}(B_b) |$$
   Models must maintain $ECE \le 0.15$ to prevent bid over-inflation.
2. **PCOCTR (Predicted CTR / Observed CTR):**
   Must fall within $[0.70, 1.30]$ to guarantee realistic pacing and budget spending.
3. **Business-Value Lift:**
   eCPM-weighted ranking value compared to the statistical baseline:
   $$\text{Lift} = \frac{\text{Mean}(eCPM_{\text{model}}) - \text{Mean}(eCPM_{\text{baseline}})}{\text{Mean}(eCPM_{\text{baseline}})} \ge 0.0$$
   A candidate model that increases raw AUC but reduces auction eCPM revenue is rejected.

---

## 6. Shadow-Mode Evaluation

Before any model receives live traffic, it must run in **Shadow Mode** (`prediction_mode: "shadow"`):
1. **Authoritative Serving:** The live ad decision is strictly rendered by `StatisticalPredictor`. Live advertiser bids and publisher payouts are never touched by the shadow model.
2. **Bounded Asynchronous Inference:** The candidate ML model runs concurrently within a strict latency budget (`timeout_ms = 20.0`).
3. **Telemetry Logging:** Discrepancies between statistical and ML predictions are recorded (prediction divergence $\Delta \text{pCTR}$, top-1 slate agreement rate, timeout counts).
4. **Safety Isolation:** Any exception or timeout in the shadow model is safely caught and discarded without affecting live user traffic.

---

## 7. Multi-Gate Promotion Policy & Gradual Rollout

Promotion from staging to production requires satisfying **all** gates simultaneously. The system explicitly blocks promotion based on AUC alone:

| Gate | Requirement | Default Threshold |
| :--- | :--- | :--- |
| **Leakage Check** | `label_leakage_checked` | `true` |
| **Privacy Sign-off** | `privacy_review_passed` | `true` |
| **Discrimination** | Area Under ROC Curve (AUC) | $\ge 0.60$ |
| **Calibration** | Expected Calibration Error (ECE) | $\le 0.15$ |
| **Ratio Calibration**| PCOCTR Ratio | $0.70 \le \text{PCOCTR} \le 1.30$ |
| **Business Value** | eCPM-Weighted Value Lift | $\ge 0.0$ (Non-negative) |
| **Shadow Parity** | Slate Top-1 Agreement Rate | $\ge 0.50$ |

### Gradual Rollout:
- Upon passing all gates, the model is registered as production-ready.
- Operators can increment traffic percentage via `/api/v1/admin/ml/models/{version}/rollout` (e.g. 5% $\to$ 20% $\to$ 50% $\to$ 100%).
- The previous production model is automatically saved as `last_known_good_version`.

---

## 8. Emergency Rollback Procedures

If an active production model causes performance regressions, unexpected fallbacks, or revenue drops:

### Fast Atomic Rollback via REST:
```bash
POST /api/v1/admin/ml/models/rollback
Authorization: Bearer <ADMIN_JWT>
Content-Type: application/json

{
  "reason": "Production latency spike exceeding SLA (p99 > 35ms)",
  "target_version": null
}
```

### Rollback Guarantees:
1. The failing model's rollout percentage is immediately set to `0.0%`.
2. The failing model's status is updated to `ROLLED_BACK`.
3. Serving traffic is instantaneously routed to `last_known_good_version` (or `statistical_baseline` if no previous ML model exists).
4. An immutable `AuditLog` entry is written to PostgreSQL recording the operator email, reason, and timestamp.

---

## 9. Operational Telemetry & Drift Monitoring

The ML telemetry subsystem continuously tracks online inference health via `/api/v1/admin/ml/telemetry`:
- **Latency Percentiles:** $p50$, $p95$, $p99$, and mean latency in milliseconds.
- **Timeout Monitoring:** Total timeout counts and timeout rate.
- **Fallback Breakdown:** Categorized counts for `ml_disabled`, `timeout`, `model_unloaded`, `invalid_output`, `feature_missing`, and `exception`.
- **Feature Freshness:** Maximum staleness in seconds.
- **Population Stability Index (PSI):** Continuous drift tracking of prediction distributions against baseline:
  - $PSI < 0.10$: Stable / No significant shift
  - $0.10 \le PSI < 0.25$: Moderate drift (warning)
  - $PSI \ge 0.25$: Significant drift (triggers automated alert and review)
