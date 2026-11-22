"""
Model Registry, Multi-Gate Promotion, Rollout, and Rollback Governance (Phase 8).

Features:
1. Model registration and artifact manifest verification.
2. Multi-gate promotion policy:
   - Evaluates AUC, ECE calibration, PCOCTR, business-value lift, shadow agreement, label leakage check, and privacy review.
   - Blocks promotion when only AUC is checked or when any gate fails.
3. Gradual rollout percentage control (0-100%, default 0%).
4. Fast atomic rollback to last-known-good version or statistical baseline.
5. In-memory thread-safe registry with audit trails.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
import threading

from liteads.common.logger import get_logger
from liteads.ml_engine.governance.manifest import (
    ModelArtifactManifest,
    validate_manifest_and_artifact,
)

logger = get_logger(__name__)


class ModelStatus(str, Enum):
    REGISTERED = "registered"
    STAGED = "staged"
    APPROVED_FOR_SHADOW = "approved_for_shadow"
    SHADOW_EVALUATING = "shadow_evaluating"
    PROMOTED_TO_PRODUCTION = "promoted_to_production"
    DEPRECATED = "deprecated"
    ROLLED_BACK = "rolled_back"


@dataclass
class PromotionGateConfig:
    """Configurable thresholds for multi-gate model promotion."""
    min_auc: float = 0.60
    max_ece: float = 0.15
    min_pcoctr: float = 0.70
    max_pcoctr: float = 1.30
    min_business_value_lift: float = 0.0  # Must be non-negative
    min_shadow_agreement: float = 0.50
    require_leakage_check: bool = True
    require_privacy_review: bool = True


@dataclass
class GateFailure:
    """Record of a failed promotion gate check."""
    gate_name: str
    actual_value: Any
    threshold: Any
    reason: str


@dataclass
class ModelRecord:
    """Registry entry for a validated ML model."""
    version: str
    model_name: str
    manifest: ModelArtifactManifest
    status: ModelStatus
    artifact_path: str
    rollout_percentage: float = 0.0
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    promoted_at: str | None = None
    registered_by: str = "admin"
    audit_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "model_name": self.model_name,
            "manifest": self.manifest.to_dict(),
            "status": self.status.value,
            "artifact_path": self.artifact_path,
            "rollout_percentage": self.rollout_percentage,
            "registered_at": self.registered_at,
            "promoted_at": self.promoted_at,
            "registered_by": self.registered_by,
            "audit_history": self.audit_history,
        }


class ModelRegistry:
    """
    Central governance registry for model artifacts, promotion gates, rollout, and rollback.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._models: dict[str, ModelRecord] = {}
        self._active_production_version: str | None = None
        self._last_known_good_version: str = "statistical_baseline"
        self._shadow_version: str | None = None

    def register_model(
        self,
        manifest: ModelArtifactManifest,
        artifact_path: str | Path,
        registered_by: str = "admin",
    ) -> ModelRecord:
        """
        Validate artifact and register model in staged state.
        """
        with self._lock:
            # 1. Validate manifest and physical file
            is_valid, error_msg = validate_manifest_and_artifact(manifest, artifact_path)
            if not is_valid:
                raise ValueError(f"Model registration failed validation: {error_msg}")

            version = manifest.model_version
            if version in self._models:
                raise ValueError(f"Model version '{version}' is already registered")

            record = ModelRecord(
                version=version,
                model_name=manifest.model_name,
                manifest=manifest,
                status=ModelStatus.STAGED,
                artifact_path=str(artifact_path),
                rollout_percentage=0.0,
                registered_by=registered_by,
                audit_history=[{
                    "action": "registered",
                    "actor": registered_by,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "details": f"Registered version {version}",
                }],
            )
            self._models[version] = record
            logger.info("Registered model artifact in registry", version=version, registered_by=registered_by)
            return record

    def evaluate_promotion_gates(
        self,
        version: str,
        gates: PromotionGateConfig | None = None,
    ) -> tuple[bool, list[GateFailure]]:
        """
        Evaluate candidate model against multi-gate promotion criteria.
        Guarantees that high AUC alone is not sufficient if calibration, business value,
        shadow agreement, or privacy checks fail!
        """
        gates = gates or PromotionGateConfig()
        failures: list[GateFailure] = []

        if version not in self._models:
            raise KeyError(f"Model version '{version}' not found in registry")

        record = self._models[version]
        manifest = record.manifest
        metrics = manifest.metrics or {}

        # Gate 1: Label leakage sign-off
        if gates.require_leakage_check and not manifest.label_leakage_checked:
            failures.append(GateFailure(
                gate_name="label_leakage_checked",
                actual_value=False,
                threshold=True,
                reason="Model was not certified against label leakage",
            ))

        # Gate 2: Privacy review sign-off
        if gates.require_privacy_review and not manifest.privacy_review_passed:
            failures.append(GateFailure(
                gate_name="privacy_review_passed",
                actual_value=False,
                threshold=True,
                reason="Model did not pass mandatory privacy & PII review",
            ))

        # Gate 3: AUC Gate
        auc = float(metrics.get("auc", 0.0))
        if auc < gates.min_auc:
            failures.append(GateFailure(
                gate_name="min_auc",
                actual_value=auc,
                threshold=gates.min_auc,
                reason=f"Model AUC ({auc}) is below threshold ({gates.min_auc})",
            ))

        # Gate 4: Expected Calibration Error (ECE) Gate
        if "ece" in metrics:
            ece = float(metrics["ece"])
            if ece > gates.max_ece:
                failures.append(GateFailure(
                    gate_name="max_ece",
                    actual_value=ece,
                    threshold=gates.max_ece,
                    reason=f"Model ECE ({ece}) exceeds calibration threshold ({gates.max_ece})",
                ))
        else:
            failures.append(GateFailure(
                gate_name="max_ece",
                actual_value=None,
                threshold=gates.max_ece,
                reason="Model missing required ECE calibration metric",
            ))

        # Gate 5: PCOCTR Calibration Gate
        if "pcoctr" in metrics:
            pcoctr = float(metrics["pcoctr"])
            if pcoctr < gates.min_pcoctr or pcoctr > gates.max_pcoctr:
                failures.append(GateFailure(
                    gate_name="pcoctr_calibration",
                    actual_value=pcoctr,
                    threshold=f"[{gates.min_pcoctr}, {gates.max_pcoctr}]",
                    reason=f"PCOCTR calibration ratio ({pcoctr}) is outside acceptable bounds",
                ))

        # Gate 6: Business-Value Ranking Lift Gate (non-negative eCPM lift)
        if "business_value_lift" in metrics:
            lift = float(metrics["business_value_lift"])
            if lift < gates.min_business_value_lift:
                failures.append(GateFailure(
                    gate_name="min_business_value_lift",
                    actual_value=lift,
                    threshold=gates.min_business_value_lift,
                    reason=f"Business-value ranking lift ({lift}) is negative or below threshold",
                ))

        # Gate 7: Shadow Mode Agreement Gate
        if "shadow_agreement" in metrics:
            agreement = float(metrics["shadow_agreement"])
            if agreement < gates.min_shadow_agreement:
                failures.append(GateFailure(
                    gate_name="min_shadow_agreement",
                    actual_value=agreement,
                    threshold=gates.min_shadow_agreement,
                    reason=f"Shadow mode ranking agreement ({agreement}) is below threshold",
                ))

        passed = len(failures) == 0
        return passed, failures

    def promote_model(
        self,
        version: str,
        gates: PromotionGateConfig | None = None,
        promoted_by: str = "admin",
    ) -> ModelRecord:
        """
        Promote model to production after strictly verifying all promotion gates.
        Updates last_known_good version and production active state.
        """
        with self._lock:
            passed, failures = self.evaluate_promotion_gates(version, gates=gates)
            if not passed:
                failure_reasons = [f"{f.gate_name}: {f.reason}" for f in failures]
                raise ValueError(f"Promotion rejected. Gate failures: {failure_reasons}")

            record = self._models[version]

            # Save previous production model as last known good
            if self._active_production_version and self._active_production_version != version:
                self._last_known_good_version = self._active_production_version
                if self._last_known_good_version in self._models:
                    self._models[self._last_known_good_version].status = ModelStatus.DEPRECATED

            now_iso = datetime.now(timezone.utc).isoformat()
            record.status = ModelStatus.PROMOTED_TO_PRODUCTION
            record.promoted_at = now_iso
            record.rollout_percentage = 100.0  # Fully promoted
            record.audit_history.append({
                "action": "promoted_to_production",
                "actor": promoted_by,
                "timestamp": now_iso,
                "previous_production": self._last_known_good_version,
            })

            self._active_production_version = version
            logger.info(
                "Successfully promoted model to production",
                version=version,
                promoted_by=promoted_by,
                last_known_good=self._last_known_good_version,
            )
            return record

    def set_rollout_percentage(
        self,
        version: str,
        percentage: float,
        updated_by: str = "admin",
    ) -> ModelRecord:
        """
        Update model rollout traffic percentage (0.0 to 100.0).
        """
        with self._lock:
            if version not in self._models:
                raise KeyError(f"Model version '{version}' not found")

            if not (0.0 <= percentage <= 100.0):
                raise ValueError(f"Rollout percentage must be between 0.0 and 100.0; got {percentage}")

            record = self._models[version]
            old_pct = record.rollout_percentage
            record.rollout_percentage = round(float(percentage), 2)
            record.audit_history.append({
                "action": "rollout_percentage_updated",
                "actor": updated_by,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "old_percentage": old_pct,
                "new_percentage": record.rollout_percentage,
            })
            return record

    def set_shadow_model(self, version: str | None) -> None:
        """Set designated model version for shadow mode evaluation."""
        with self._lock:
            if version is not None and version not in self._models:
                raise KeyError(f"Model version '{version}' not found in registry")
            self._shadow_version = version
            if version and version in self._models:
                self._models[version].status = ModelStatus.SHADOW_EVALUATING

    def rollback(
        self,
        reason: str,
        rolled_back_by: str = "admin",
        target_version: str | None = None,
    ) -> dict[str, Any]:
        """
        Fast atomic rollback to last-known-good version or specified target.
        Immediately zeroes rollout percentage of current failing model.
        """
        with self._lock:
            current_active = self._active_production_version
            fallback_target = target_version or self._last_known_good_version or "statistical_baseline"

            now_iso = datetime.now(timezone.utc).isoformat()

            # Mark current failing version as rolled back
            if current_active and current_active in self._models:
                bad_record = self._models[current_active]
                bad_record.status = ModelStatus.ROLLED_BACK
                bad_record.rollout_percentage = 0.0
                bad_record.audit_history.append({
                    "action": "rolled_back",
                    "actor": rolled_back_by,
                    "reason": reason,
                    "timestamp": now_iso,
                    "fallback_target": fallback_target,
                })

            # Activate fallback target
            self._active_production_version = fallback_target if fallback_target != "statistical_baseline" else None
            if fallback_target in self._models:
                target_record = self._models[fallback_target]
                target_record.status = ModelStatus.PROMOTED_TO_PRODUCTION
                target_record.rollout_percentage = 100.0
                target_record.audit_history.append({
                    "action": "restored_via_rollback",
                    "actor": rolled_back_by,
                    "reason": reason,
                    "timestamp": now_iso,
                })

            result = {
                "status": "success",
                "rolled_back_version": current_active,
                "active_production_version": fallback_target,
                "reason": reason,
                "actor": rolled_back_by,
                "timestamp": now_iso,
            }
            logger.warning("Executed emergency model rollback", **result)
            return result

    def get_model(self, version: str) -> ModelRecord | None:
        with self._lock:
            return self._models.get(version)

    def list_models(self) -> list[dict[str, Any]]:
        with self._lock:
            return [m.to_dict() for m in self._models.values()]

    def get_registry_summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_production_version": self._active_production_version or "statistical_baseline",
                "last_known_good_version": self._last_known_good_version,
                "shadow_version": self._shadow_version,
                "total_models": len(self._models),
                "models": [m.to_dict() for m in self._models.values()],
            }

    def reset(self) -> None:
        """Reset registry state for tests."""
        with self._lock:
            self._models.clear()
            self._active_production_version = None
            self._last_known_good_version = "statistical_baseline"
            self._shadow_version = None


# Singleton
_model_registry_singleton: ModelRegistry | None = None
_registry_lock = threading.Lock()


def get_model_registry() -> ModelRegistry:
    """Get global ModelRegistry singleton."""
    global _model_registry_singleton
    if _model_registry_singleton is None:
        with _registry_lock:
            if _model_registry_singleton is None:
                _model_registry_singleton = ModelRegistry()
    return _model_registry_singleton
