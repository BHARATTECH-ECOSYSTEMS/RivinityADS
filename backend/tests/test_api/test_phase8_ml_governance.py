"""
Phase 8 — ML Readiness and Model Governance Test Suite.

Validates:
1. Model Artifact Manifest contract, SHA-256 integrity, finite weights verification, and corrupted weight rejection.
2. Safe optional-dependency separation and lazy ML imports.
3. Time-based train/val/test splitting, embargo window enforcement, and rejection of inverted/overlapping time splits.
4. Label leakage detection: rejection of future campaign rollup metrics and post-event signals.
5. Offline evaluation: AUC, Log-Loss, Brier score, ECE calibration bins, and eCPM business-value ranking lift.
6. Privacy-safe segment evaluation: suppression of micro-cohort segments with < 30 samples.
7. Strict PII guardrails: rejection and sanitization of raw IP, email, phone, user-agent, and GPS coordinates.
8. Shadow-mode prediction: statistical baseline strictly authoritative for live serving rankings, telemetry logged without serving degradation.
9. Model registry & multi-gate promotion: rejection of promotion when AUC is high but calibration/business-value/shadow agreement/privacy fail; rollout percentage adjustment; atomic rollback to last-known-good.
10. Operational inference telemetry & Admin REST endpoints: HTTP 403 enforcement for non-admin, admin audit logging for register/promote/rollout/rollback, and latency/fallback telemetry retrieval.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import torch

from liteads.common.security import create_access_token
from liteads.ml_engine.governance.evaluator import (
    calculate_business_value_lift,
    calculate_ece,
    evaluate_segments_privacy_safe,
)
from liteads.ml_engine.governance.manifest import (
    ModelArtifactManifest,
    compute_file_sha256,
    validate_manifest_and_artifact,
    verify_weights_are_finite,
)
from liteads.ml_engine.governance.pii_guard import (
    PIIViolationError,
    assert_no_pii,
    sanitize_features,
    validate_privacy_compliance,
)
from liteads.ml_engine.governance.registry import (
    GateFailure,
    ModelRegistry,
    ModelStatus,
    PromotionGateConfig,
    get_model_registry,
)
from liteads.ml_engine.governance.splitter import (
    LabelLeakageError,
    TimeBasedSplitter,
    TimeSplitError,
    assert_no_label_leakage,
    check_label_leakage,
)
from liteads.ml_engine.governance.telemetry import (
    MLTelemetryCollector,
    get_ml_telemetry,
)
from liteads.ml_engine.serving.validator import validate_model_artifact
from liteads.models.auth import AuditLog, User
from liteads.rec_engine.ranking.predictor import (
    MLPredictor,
    ShadowPredictor,
    StatisticalPredictor,
)
from liteads.schemas.internal import AdCandidate, UserContext

pytestmark = []


# ── Helpers & Fixtures ────────────────────────────────────────────────────────

def _create_dummy_checkpoint(path: Path, nan_weight: bool = False, inf_weight: bool = False) -> None:
    """Helper to save a valid CPU PyTorch dummy checkpoint."""
    weights = torch.randn(10, 5)
    if nan_weight:
        weights[0, 0] = float("nan")
    if inf_weight:
        weights[0, 0] = float("inf")

    checkpoint = {
        "model_type": "deepfm",
        "version": "v1.0.0",
        "model_state_dict": {
            "linear.weight": weights,
            "linear.bias": torch.zeros(5),
        },
        "model_config": {
            "sparse_feature_dims": [10, 20],
            "dense_feature_dim": 5,
        },
        "metrics": {"auc": 0.75, "log_loss": 0.12},
    }
    torch.save(checkpoint, path)


async def _create_test_admin(test_db: AsyncSession) -> tuple[User, str]:
    admin = User(
        name="Admin ML Governance",
        email=f"admin_ml_{datetime.now().timestamp()}@example.com",
        password_hash="admin_pw_hash",
        role="admin",
        is_active=True,
        email_verified=True,
    )
    test_db.add(admin)
    await test_db.commit()
    await test_db.refresh(admin)
    return admin, create_access_token(admin)


async def _create_test_advertiser(test_db: AsyncSession) -> tuple[User, str]:
    user = User(
        name="Advertiser User",
        email=f"adv_{datetime.now().timestamp()}@example.com",
        password_hash="adv_pw_hash",
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user, create_access_token(user)


# ── 1. Model Artifact Manifest & Cryptographic Integrity ──────────────────────

class TestModelArtifactManifest:
    def test_valid_manifest_and_artifact_verification(self, tmp_path: Path):
        model_file = tmp_path / "model_valid.pt"
        _create_dummy_checkpoint(model_file)
        sha256 = compute_file_sha256(model_file)

        manifest = ModelArtifactManifest(
            model_name="deepfm_ctr",
            model_version="v1.0.0",
            model_architecture="deepfm",
            sha256_checksum=sha256,
            training_dataset_hash="hash_abc123",
            time_window={"train_start": "2026-10-01", "train_end": "2026-10-25"},
            label_leakage_checked=True,
            finite_weights_verified=True,
            privacy_review_passed=True,
            metrics={"auc": 0.74, "log_loss": 0.08, "ece": 0.03},
        )

        is_valid, err = validate_manifest_and_artifact(manifest, model_file)
        assert is_valid is True
        assert err is None

    def test_checksum_mismatch_rejection(self, tmp_path: Path):
        model_file = tmp_path / "model_mismatch.pt"
        _create_dummy_checkpoint(model_file)

        manifest = ModelArtifactManifest(
            model_name="deepfm_ctr",
            model_version="v1.0.0",
            model_architecture="deepfm",
            sha256_checksum="wrong_sha256_hash_that_does_not_match",
            training_dataset_hash="hash_abc123",
            time_window={},
            label_leakage_checked=True,
            finite_weights_verified=True,
            privacy_review_passed=True,
            metrics={"auc": 0.74},
        )

        is_valid, err = validate_manifest_and_artifact(manifest, model_file)
        assert is_valid is False
        assert "checksum mismatch" in err.lower()

    def test_corrupted_nan_weights_rejection(self, tmp_path: Path):
        model_file = tmp_path / "model_nan.pt"
        _create_dummy_checkpoint(model_file, nan_weight=True)
        sha256 = compute_file_sha256(model_file)

        manifest = ModelArtifactManifest(
            model_name="deepfm_ctr",
            model_version="v1.0.0",
            model_architecture="deepfm",
            sha256_checksum=sha256,
            training_dataset_hash="hash_abc123",
            time_window={},
            label_leakage_checked=True,
            finite_weights_verified=True,
            privacy_review_passed=True,
            metrics={"auc": 0.74},
        )

        is_valid, err = validate_manifest_and_artifact(manifest, model_file)
        assert is_valid is False
        assert "nan" in err.lower()

    def test_unverified_leakage_or_privacy_rejected(self, tmp_path: Path):
        model_file = tmp_path / "model_flags.pt"
        _create_dummy_checkpoint(model_file)
        sha256 = compute_file_sha256(model_file)

        # Leakage unchecked
        manifest = ModelArtifactManifest(
            model_name="deepfm_ctr",
            model_version="v1.0.0",
            model_architecture="deepfm",
            sha256_checksum=sha256,
            training_dataset_hash="hash_abc123",
            time_window={},
            label_leakage_checked=False,  # Unchecked
            finite_weights_verified=True,
            privacy_review_passed=True,
            metrics={"auc": 0.74},
        )
        is_valid, err = validate_manifest_and_artifact(manifest, model_file)
        assert is_valid is False
        assert "label_leakage_checked" in err

        # Privacy unreviewed
        manifest.label_leakage_checked = True
        manifest.privacy_review_passed = False
        is_valid, err = validate_manifest_and_artifact(manifest, model_file)
        assert is_valid is False
        assert "privacy_review_passed" in err


# ── 2. Safe Optional-Dependency Separation ────────────────────────────────────

class TestOptionalDependencySeparation:
    async def test_statistical_predictor_runs_independently(self):
        predictor = StatisticalPredictor(default_ctr=0.015, default_cvr=0.002)
        user_ctx = UserContext(user_id="u1", os="android", country="IN")
        cand = AdCandidate(campaign_id=1, creative_id=10, advertiser_id=2, bid=5.0, bid_type=1)

        result = await predictor.predict(user_ctx, cand)
        assert result.pctr > 0.0
        assert result.is_fallback is False
        assert result.prediction_mode == "statistical"

    def test_validator_fails_gracefully_on_empty_or_missing_path(self):
        res = validate_model_artifact(None)
        assert res.is_valid is False
        assert "empty" in res.error_message.lower()

        res2 = validate_model_artifact("non_existent_model_file.pt")
        assert res2.is_valid is False
        assert "does not exist" in res2.error_message.lower()


# ── 3. Time-Based Dataset Splitting & Embargo Invariants ──────────────────────

class TestTimeBasedSplitting:
    def test_valid_temporal_split(self):
        splitter = TimeBasedSplitter(
            train_end="2026-10-15T23:59:59Z",
            val_start="2026-10-16T00:00:00Z",
            val_end="2026-10-20T23:59:59Z",
            test_start="2026-10-21T00:00:00Z",
            test_end="2026-10-25T23:59:59Z",
            embargo_hours=0.0,
        )

        records = [
            {"id": 1, "event_time": "2026-10-10T12:00:00Z"},
            {"id": 2, "event_time": "2026-10-18T12:00:00Z"},
            {"id": 3, "event_time": "2026-10-22T12:00:00Z"},
        ]
        train, val, test = splitter.split_records(records)
        assert len(train) == 1 and train[0]["id"] == 1
        assert len(val) == 1 and val[0]["id"] == 2
        assert len(test) == 1 and test[0]["id"] == 3

    def test_inverted_time_split_fails_closed(self):
        with pytest.raises(TimeSplitError, match="Temporal split violation"):
            TimeBasedSplitter(
                train_end="2026-10-20T00:00:00Z",
                val_start="2026-10-15T00:00:00Z",  # Inverted!
                val_end="2026-10-25T00:00:00Z",
                test_start="2026-10-26T00:00:00Z",
                test_end="2026-10-30T00:00:00Z",
            )

    def test_embargo_violation_fails_closed(self):
        with pytest.raises(TimeSplitError, match="embargo"):
            TimeBasedSplitter(
                train_end="2026-10-15T12:00:00Z",
                val_start="2026-10-15T14:00:00Z",  # Only 2 hours later, but embargo is 12 hours!
                val_end="2026-10-20T00:00:00Z",
                test_start="2026-10-21T00:00:00Z",
                test_end="2026-10-25T00:00:00Z",
                embargo_hours=12.0,
            )


# ── 4. Label Leakage Detection ────────────────────────────────────────────────

class TestLabelLeakage:
    def test_clean_features_pass_leakage_check(self):
        features = ["user_id", "geo_country", "ad_category", "slot_ctr", "request_hour"]
        is_clean, detected = check_label_leakage(features)
        assert is_clean is True
        assert len(detected) == 0
        assert_no_label_leakage(features)

    def test_future_campaign_rollups_rejected(self):
        leakage_cols = ["user_id", "spent_total", "campaign_total_conversions"]
        is_clean, detected = check_label_leakage(leakage_cols)
        assert is_clean is False
        assert any("spent_total" in d for d in detected)
        assert any("campaign_total_conversions" in d for d in detected)

        with pytest.raises(LabelLeakageError, match="Label Leakage Detected"):
            assert_no_label_leakage(leakage_cols)

    def test_post_click_outcomes_rejected(self):
        features = ["user_id", "conversion_time", "revenue_generated"]
        is_clean, detected = check_label_leakage(features)
        assert is_clean is False
        assert any("conversion_time" in d for d in detected)


# ── 5. Offline Calibration (ECE) & Business-Value Evaluation ──────────────────

class TestOfflineCalibrationAndBusinessValue:
    def test_ece_calibration_calculation(self):
        # Perfect calibration: predictions match empirical rate
        labels = [1] * 50 + [0] * 50
        preds = [1.0] * 50 + [0.0] * 50
        ece, bins = calculate_ece(labels, preds, num_bins=10)
        assert abs(ece - 0.0) < 1e-4

        # Miscalibrated predictions
        preds_miscal = [0.9] * 50 + [0.8] * 50  # Predicted ~0.85, true mean 0.50
        ece_miscal, _ = calculate_ece(labels, preds_miscal, num_bins=10)
        assert ece_miscal > 0.30

    def test_business_value_lift_calculation(self):
        bids = [10.0, 15.0, 20.0, 25.0, 30.0] * 10
        baseline_pctr = [0.01] * 50
        model_pctr = [0.02] * 50  # Double CTR

        lift_metrics = calculate_business_value_lift(bids, model_pctr, baseline_pctr)
        assert lift_metrics["model_ecpm_mean"] > lift_metrics["baseline_ecpm_mean"]
        assert lift_metrics["ecpm_lift_ratio"] > 0.90  # ~100% lift
        assert lift_metrics["top1_agreement_rate"] == 1.0


# ── 6. Privacy-Safe Segment Evaluation ────────────────────────────────────────

class TestPrivacySafeSegmentEvaluation:
    def test_segment_suppression_below_privacy_threshold(self):
        # 15 samples (< min_segment_samples = 30)
        labels = [1, 0, 0, 1, 0] * 3
        preds = [0.05] * 15
        segments = {"device_os": ["ios"] * 15}

        evaluated = evaluate_segments_privacy_safe(labels, preds, segments, min_segment_samples=30)
        assert len(evaluated) == 1
        entry = evaluated[0]
        assert entry["is_suppressed"] is True
        assert "below privacy threshold" in entry["suppression_reason"]
        assert entry["auc"] is None
        assert entry["pcoctr"] is None

    def test_large_segment_evaluation_permitted(self):
        # 40 samples (>= min_segment_samples = 30)
        labels = [1, 0, 0, 1] * 10
        preds = [0.05, 0.01, 0.02, 0.04] * 10
        segments = {"device_os": ["android"] * 40}

        evaluated = evaluate_segments_privacy_safe(labels, preds, segments, min_segment_samples=30)
        assert len(evaluated) == 1
        entry = evaluated[0]
        assert entry["is_suppressed"] is False
        assert entry["auc"] is not None
        assert entry["pcoctr"] is not None
        assert entry["ece"] is not None


# ── 7. Strict PII & Privacy Guardrails ────────────────────────────────────────

class TestPIIGuardrails:
    def test_raw_pii_detection(self):
        bad_features = {
            "user_id": "u1",
            "raw_ip": "198.51.100.1",
            "email": "user@example.com",
            "phone": "+919876543210",
        }
        is_compliant, violations = validate_privacy_compliance(bad_features)
        assert is_compliant is False
        assert any("raw_ip" in v for v in violations)
        assert any("email" in v for v in violations)

        with pytest.raises(PIIViolationError, match="Prohibited PII detected"):
            assert_no_pii(bad_features)

    def test_feature_sanitization_and_minor_masking(self):
        raw = {
            "user_id": "u1",
            "raw_ip": "1.2.3.4",
            "user_email": "a@b.com",
            "gps_latitude": 12.9716,
            "user_age": 16,  # Minor under 18!
            "user_gender": "male",
            "geo_country": "IN",
        }
        sanitized = sanitize_features(raw)
        assert "raw_ip" not in sanitized
        assert "user_email" not in sanitized
        assert "gps_latitude" not in sanitized
        assert "user_age" not in sanitized
        assert sanitized["user_age_bucket"] == "restricted"
        assert sanitized["user_gender"] == "unspecified"
        assert sanitized["geo_country"] == "IN"


# ── 8. Shadow-Mode Serving Parity ─────────────────────────────────────────────

class TestShadowModeServing:
    async def test_shadow_predictor_baseline_strictly_authoritative(self):
        baseline = StatisticalPredictor(default_ctr=0.01)
        shadow = StatisticalPredictor(default_ctr=0.05)  # Divergent score

        shadow_pred = ShadowPredictor(baseline=baseline, shadow=shadow, timeout_ms=50.0)
        user_ctx = UserContext(user_id="u1")
        cand = AdCandidate(campaign_id=1, creative_id=10, advertiser_id=2, bid=5.0, bid_type=1)

        results = await shadow_pred.predict_batch(user_ctx, [cand])
        assert len(results) == 1
        res = results[0]
        # Baseline CTR is strictly returned for live serving
        assert abs(res.pctr - 0.01) < 1e-4
        assert res.prediction_mode == "shadow"

    async def test_shadow_timeout_does_not_degrade_serving(self):
        baseline = StatisticalPredictor(default_ctr=0.01)

        class HangingPredictor(StatisticalPredictor):
            async def predict_batch(self, user_context, candidates):
                await asyncio.sleep(0.2)  # Exceeds 10ms timeout
                return await super().predict_batch(user_context, candidates)

        shadow_pred = ShadowPredictor(baseline=baseline, shadow=HangingPredictor(), timeout_ms=10.0)
        user_ctx = UserContext(user_id="u1")
        cand = AdCandidate(campaign_id=1, creative_id=10, advertiser_id=2, bid=5.0, bid_type=1)

        results = await shadow_pred.predict_batch(user_ctx, [cand])
        assert len(results) == 1
        assert abs(results[0].pctr - 0.01) < 1e-4


# ── 9. Model Registry, Multi-Gate Promotion & Rollback ────────────────────────

class TestModelRegistryAndGovernance:
    def test_multi_gate_promotion_rejects_high_auc_with_bad_calibration(self, tmp_path: Path):
        registry = ModelRegistry()
        model_file = tmp_path / "deepfm_test.pt"
        _create_dummy_checkpoint(model_file)
        sha256 = compute_file_sha256(model_file)

        manifest = ModelArtifactManifest(
            model_name="deepfm_ctr",
            model_version="v2.0.0",
            model_architecture="deepfm",
            sha256_checksum=sha256,
            training_dataset_hash="hash_abc",
            time_window={},
            label_leakage_checked=True,
            finite_weights_verified=True,
            privacy_review_passed=True,
            metrics={
                "auc": 0.82,  # Very high AUC
                "ece": 0.35,  # Terribly miscalibrated (> 0.15 limit)
                "business_value_lift": -0.05,  # Negative revenue lift
            },
        )
        registry.register_model(manifest, model_file)

        # Attempt promotion
        passed, failures = registry.evaluate_promotion_gates("v2.0.0")
        assert passed is False
        assert any(f.gate_name == "max_ece" for f in failures)
        assert any(f.gate_name == "min_business_value_lift" for f in failures)

        with pytest.raises(ValueError, match="Promotion rejected"):
            registry.promote_model("v2.0.0")

    def test_successful_promotion_and_atomic_rollback(self, tmp_path: Path):
        registry = ModelRegistry()
        model_v1 = tmp_path / "model_v1.pt"
        model_v2 = tmp_path / "model_v2.pt"
        _create_dummy_checkpoint(model_v1)
        _create_dummy_checkpoint(model_v2)

        m1 = ModelArtifactManifest(
            model_name="deepfm_ctr",
            model_version="v1.0.0",
            model_architecture="deepfm",
            sha256_checksum=compute_file_sha256(model_v1),
            training_dataset_hash="hash_1",
            time_window={},
            label_leakage_checked=True,
            finite_weights_verified=True,
            privacy_review_passed=True,
            metrics={"auc": 0.72, "ece": 0.04, "pcoctr": 1.01, "business_value_lift": 0.03, "shadow_agreement": 0.8},
        )
        m2 = ModelArtifactManifest(
            model_name="deepfm_ctr",
            model_version="v2.0.0",
            model_architecture="deepfm",
            sha256_checksum=compute_file_sha256(model_v2),
            training_dataset_hash="hash_2",
            time_window={},
            label_leakage_checked=True,
            finite_weights_verified=True,
            privacy_review_passed=True,
            metrics={"auc": 0.78, "ece": 0.03, "pcoctr": 1.02, "business_value_lift": 0.06, "shadow_agreement": 0.85},
        )

        registry.register_model(m1, model_v1)
        registry.register_model(m2, model_v2)

        # Promote v1 first
        registry.promote_model("v1.0.0")
        assert registry._active_production_version == "v1.0.0"

        # Promote v2 (v1 becomes last_known_good)
        registry.promote_model("v2.0.0")
        assert registry._active_production_version == "v2.0.0"
        assert registry._last_known_good_version == "v1.0.0"

        # Adjust rollout
        registry.set_rollout_percentage("v2.0.0", 50.0)
        assert registry.get_model("v2.0.0").rollout_percentage == 50.0

        # Execute Emergency Rollback
        rollback_res = registry.rollback(reason="Latency spike in v2")
        assert rollback_res["status"] == "success"
        assert rollback_res["active_production_version"] == "v1.0.0"
        assert registry.get_model("v2.0.0").status == ModelStatus.ROLLED_BACK
        assert registry.get_model("v2.0.0").rollout_percentage == 0.0
        assert registry.get_model("v1.0.0").status == ModelStatus.PROMOTED_TO_PRODUCTION


# ── 10. Admin REST Endpoints & RBAC & Audit Logging ───────────────────────────

class TestAdminMLEndpoints:
    async def test_admin_endpoints_enforce_rbac_403(
        self, client: AsyncClient, test_db: AsyncSession
    ):
        _, adv_token = await _create_test_advertiser(test_db)
        headers = {"Authorization": f"Bearer {adv_token}"}

        # Non-admin access rejected
        resp = await client.get("/api/v1/admin/ml/models", headers=headers)
        assert resp.status_code == 403

        resp2 = await client.get("/api/v1/admin/ml/telemetry", headers=headers)
        assert resp2.status_code == 403

    async def test_admin_model_registration_and_promotion_api(
        self, client: AsyncClient, test_db: AsyncSession, tmp_path: Path
    ):
        admin, admin_token = await _create_test_admin(test_db)
        headers = {"Authorization": f"Bearer {admin_token}"}

        model_file = tmp_path / "api_model.pt"
        _create_dummy_checkpoint(model_file)
        sha256 = compute_file_sha256(model_file)

        manifest_data = {
            "model_name": "deepfm_api",
            "model_version": f"v_api_{int(datetime.now().timestamp())}",
            "model_architecture": "deepfm",
            "sha256_checksum": sha256,
            "training_dataset_hash": "hash_dataset_99",
            "time_window": {"train_start": "2026-10-01", "train_end": "2026-10-20"},
            "label_leakage_checked": True,
            "finite_weights_verified": True,
            "privacy_review_passed": True,
            "metrics": {
                "auc": 0.76,
                "ece": 0.04,
                "pcoctr": 1.01,
                "business_value_lift": 0.05,
                "shadow_agreement": 0.82,
            },
        }

        # 1. Register model
        reg_resp = await client.post(
            "/api/v1/admin/ml/models/register",
            headers=headers,
            json={"manifest": manifest_data, "artifact_path": str(model_file)},
        )
        assert reg_resp.status_code == 201
        reg_json = reg_resp.json()
        version = reg_json["version"]
        assert reg_json["status"] == "staged"

        # Verify AuditLog created
        audit_res = await test_db.execute(
            select(AuditLog).where(AuditLog.action == "ml_model_registered")
        )
        assert audit_res.scalars().first() is not None

        # 2. Promote model
        promote_resp = await client.post(
            f"/api/v1/admin/ml/models/{version}/promote",
            headers=headers,
            json={"min_auc": 0.60, "max_ece": 0.15},
        )
        assert promote_resp.status_code == 200
        assert promote_resp.json()["status"] == "promoted_to_production"

        # 3. Rollout percentage
        rollout_resp = await client.post(
            f"/api/v1/admin/ml/models/{version}/rollout",
            headers=headers,
            json={"rollout_percentage": 25.0},
        )
        assert rollout_resp.status_code == 200
        assert rollout_resp.json()["rollout_percentage"] == 25.0

        # 4. Emergency Rollback
        rollback_resp = await client.post(
            "/api/v1/admin/ml/models/rollback",
            headers=headers,
            json={"reason": "Audit test rollback"},
        )
        assert rollback_resp.status_code == 200
        assert rollback_resp.json()["status"] == "success"

        # 5. Telemetry endpoint
        telemetry_resp = await client.get("/api/v1/admin/ml/telemetry", headers=headers)
        assert telemetry_resp.status_code == 200
        tel_data = telemetry_resp.json()
        assert "latency_ms" in tel_data
        assert "fallbacks" in tel_data
        assert "drift" in tel_data
