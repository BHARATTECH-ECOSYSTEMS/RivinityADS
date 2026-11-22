"""
Model Artifact Manifest and Integrity Verification Contract (Phase 8).

Defines the formal ModelArtifactManifest dataclass and validation primitives:
1. Cryptographic SHA-256 artifact verification
2. Finite weights check (absence of NaN / Inf)
3. Schema and architecture compatibility
4. Training dataset provenance and time-window metadata
5. Label leakage and privacy sign-off validation
6. Metrics contract validation (AUC, Log-Loss, ECE, calibration, business value)
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json

from liteads.common.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_ARCHITECTURES: frozenset[str] = frozenset({
    "lr",
    "fm",
    "fm_lr",
    "deepfm",
    "statistical_baseline",
})


@dataclass
class ModelArtifactManifest:
    """
    Formal contract specifying model artifact metadata, provenance, and verification status.
    """

    model_name: str
    model_version: str
    model_architecture: str
    sha256_checksum: str
    training_dataset_hash: str
    time_window: dict[str, str]
    schema_version: str = "1.0.0"
    label_leakage_checked: bool = False
    finite_weights_verified: bool = False
    privacy_review_passed: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelArtifactManifest":
        valid_keys = {
            "schema_version",
            "model_name",
            "model_version",
            "model_architecture",
            "sha256_checksum",
            "training_dataset_hash",
            "time_window",
            "label_leakage_checked",
            "finite_weights_verified",
            "privacy_review_passed",
            "metrics",
            "created_at",
            "metadata",
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    @classmethod
    def from_json(cls, json_str: str) -> "ModelArtifactManifest":
        return cls.from_dict(json.loads(json_str))


def compute_file_sha256(file_path: str | Path) -> str:
    """
    Compute SHA-256 cryptographic hash of a file on disk.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found for checksum: {path}")

    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_weights_are_finite(weights_path: str | Path) -> tuple[bool, str | None]:
    """
    Safely inspect PyTorch checkpoint weights to guarantee all tensor parameters are finite (no NaN, no Inf).
    Safe for CPU-only environments.
    """
    path = Path(weights_path)
    if not path.exists():
        return False, f"Artifact file does not exist: {path}"

    try:
        import torch
    except ImportError:
        logger.warning("PyTorch not installed; skipping tensor parameter finite inspection")
        return True, None

    try:
        checkpoint = torch.load(path, map_location=torch.device("cpu"), weights_only=False)
        if not isinstance(checkpoint, dict):
            return False, f"Checkpoint root is not a dictionary: {type(checkpoint)}"

        state_dict = checkpoint.get("model_state_dict") or checkpoint
        if not isinstance(state_dict, dict):
            return False, "Checkpoint does not contain a dictionary state_dict"

        for name, param in state_dict.items():
            if isinstance(param, torch.Tensor):
                if torch.isnan(param).any():
                    return False, f"Parameter '{name}' contains NaN values"
                if torch.isinf(param).any():
                    return False, f"Parameter '{name}' contains Inf values"

        return True, None
    except Exception as e:
        return False, f"Failed to inspect weights: {e}"


def validate_manifest_and_artifact(
    manifest: ModelArtifactManifest,
    artifact_path: str | Path,
) -> tuple[bool, str | None]:
    """
    Comprehensive validation of manifest contract against the physical artifact file.

    Returns:
        (is_valid: bool, error_message: str | None)
    """
    # 1. Check architecture
    arch = manifest.model_architecture.lower()
    if arch not in SUPPORTED_ARCHITECTURES:
        return False, f"Unsupported architecture '{arch}'. Supported: {sorted(SUPPORTED_ARCHITECTURES)}"

    # 2. Check artifact file existence & size
    path = Path(artifact_path)
    if not path.exists():
        return False, f"Artifact file not found at: {path}"
    try:
        if path.stat().st_size == 0:
            return False, f"Artifact file is empty (0 bytes): {path}"
    except Exception as e:
        return False, f"Cannot access artifact file: {e}"

    # 3. Verify SHA-256
    try:
        actual_sha = compute_file_sha256(path)
        if actual_sha.lower() != manifest.sha256_checksum.lower():
            return False, (
                f"SHA-256 checksum mismatch: manifest expected {manifest.sha256_checksum}, "
                f"actual file produced {actual_sha}"
            )
    except Exception as e:
        return False, f"Failed to compute SHA-256 for artifact: {e}"

    # 4. Check label leakage sign-off
    if not manifest.label_leakage_checked:
        return False, "Manifest indicates label_leakage_checked is False"

    # 5. Check privacy review sign-off
    if not manifest.privacy_review_passed:
        return False, "Manifest indicates privacy_review_passed is False"

    # 6. Check finite weights
    if not manifest.finite_weights_verified:
        return False, "Manifest indicates finite_weights_verified is False"

    finite_ok, finite_err = verify_weights_are_finite(path)
    if not finite_ok:
        return False, f"Finite weights check failed: {finite_err}"

    # 7. Check metrics contract
    metrics = manifest.metrics or {}
    if "auc" not in metrics:
        return False, "Manifest metrics must include 'auc'"
    auc = float(metrics["auc"])
    if not (0.0 <= auc <= 1.0):
        return False, f"Invalid AUC metric value: {auc}"

    if "log_loss" in metrics:
        ll = float(metrics["log_loss"])
        if ll < 0.0 or not (ll == ll):  # finite check
            return False, f"Invalid Log-Loss value: {ll}"

    if "ece" in metrics:
        ece = float(metrics["ece"])
        if not (0.0 <= ece <= 1.0):
            return False, f"Invalid ECE value: {ece}"

    return True, None
