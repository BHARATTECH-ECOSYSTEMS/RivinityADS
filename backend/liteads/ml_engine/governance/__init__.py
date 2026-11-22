"""
ML Model Governance and Safety Package (Phase 8).

Provides:
- Privacy & PII guardrails (PII denylist, feature sanitization)
- Model artifact manifest & SHA-256 integrity verification
- Time-based dataset splitting & label leakage detection
- Offline calibration (ECE), business-value ranking lift & privacy-safe segment evaluation
- Model registry, multi-gate promotion & rollback to last-known-good
- Operational inference telemetry & drift monitoring
"""

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
    ModelRecord,
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

__all__ = [
    "PIIViolationError",
    "assert_no_pii",
    "sanitize_features",
    "validate_privacy_compliance",
    "ModelArtifactManifest",
    "compute_file_sha256",
    "validate_manifest_and_artifact",
    "verify_weights_are_finite",
    "TimeSplitError",
    "LabelLeakageError",
    "TimeBasedSplitter",
    "check_label_leakage",
    "assert_no_label_leakage",
    "calculate_ece",
    "calculate_business_value_lift",
    "evaluate_segments_privacy_safe",
    "ModelStatus",
    "PromotionGateConfig",
    "GateFailure",
    "ModelRecord",
    "ModelRegistry",
    "get_model_registry",
    "MLTelemetryCollector",
    "get_ml_telemetry",
]
