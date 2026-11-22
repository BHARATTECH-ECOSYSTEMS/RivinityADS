"""
Model artifact validation service (Phase 4 Task B).

Validates:
1. File existence and non-empty file size.
2. PyTorch checkpoint integrity (unpicklable/corrupted weights).
3. CPU-only runtime safety (map_location='cpu', no GPU requirement).
4. Schema and version compatibility (model_type, model_config, model_state_dict).
5. Weight sanity checks (absence of NaN / Inf values).
6. Optional FeatureBuilder artifact compatibility.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import math

try:
    import torch
    HAS_TORCH = True
except ImportError:
    torch = None
    HAS_TORCH = False

from liteads.common.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_MODEL_TYPES = {"lr", "fm", "fm_lr", "deepfm"}


@dataclass
class ModelValidationResult:
    """Outcome of model artifact validation."""
    is_valid: bool
    error_message: str | None = None
    model_type: str | None = None
    version: str | None = None
    sparse_feature_dims: list[int] | None = None
    dense_feature_dim: int | None = None
    metrics: dict[str, Any] | None = None

    def __iter__(self):
        yield self.is_valid
        yield self.error_message

    def __bool__(self) -> bool:
        return self.is_valid


def validate_model_artifact(
    model_path: str | Path | None,
    feature_builder_path: str | Path | None = None,
) -> ModelValidationResult:
    """
    Validate a model artifact file for online serving safety.
    Never fails with unhandled exceptions.
    """
    if not model_path:
        return ModelValidationResult(
            is_valid=False,
            error_message="Model path is empty or not configured",
        )

    path = Path(model_path)
    if not path.exists():
        return ModelValidationResult(
            is_valid=False,
            error_message=f"Model artifact file does not exist: {path}",
        )

    try:
        size_bytes = path.stat().st_size
        if size_bytes == 0:
            return ModelValidationResult(
                is_valid=False,
                error_message=f"Model artifact file is empty (0 bytes): {path}",
            )
    except Exception as e:
        return ModelValidationResult(
            is_valid=False,
            error_message=f"Failed to access model file {path}: {e}",
        )

    # Check PyTorch availability
    if not HAS_TORCH:
        return ModelValidationResult(
            is_valid=False,
            error_message="PyTorch is not installed in the serving environment",
        )

    # Load PyTorch checkpoint safely mapped to CPU
    try:
        # Use weights_only=False because checkpoints contain dictionary metadata with configs
        checkpoint = torch.load(path, map_location=torch.device("cpu"), weights_only=False)
    except Exception as e:
        logger.warning("Corrupted or unreadable PyTorch checkpoint", path=str(path), error=str(e))
        return ModelValidationResult(
            is_valid=False,
            error_message=f"Corrupted or invalid PyTorch checkpoint: {e}",
        )

    if not isinstance(checkpoint, dict):
        return ModelValidationResult(
            is_valid=False,
            error_message=f"Checkpoint root must be a dict, got {type(checkpoint).__name__}",
        )

    # Check for model state dict
    state_dict = checkpoint.get("model_state_dict")
    if state_dict is None or not isinstance(state_dict, dict):
        return ModelValidationResult(
            is_valid=False,
            error_message="Checkpoint missing valid 'model_state_dict'",
        )

    # Check weights for NaN / Inf
    for param_name, tensor in state_dict.items():
        if isinstance(tensor, torch.Tensor):
            if torch.isnan(tensor).any():
                return ModelValidationResult(
                    is_valid=False,
                    error_message=f"Corrupted weights: parameter '{param_name}' contains NaN values",
                )
            if torch.isinf(tensor).any():
                return ModelValidationResult(
                    is_valid=False,
                    error_message=f"Corrupted weights: parameter '{param_name}' contains Inf values",
                )

    # Validate model type
    model_type = checkpoint.get("model_type", "deepfm").lower()
    if model_type not in SUPPORTED_MODEL_TYPES:
        return ModelValidationResult(
            is_valid=False,
            error_message=f"Unsupported model_type '{model_type}'. Expected one of {SUPPORTED_MODEL_TYPES}",
        )

    # Validate config
    model_config = checkpoint.get("model_config", {})
    sparse_dims = model_config.get("sparse_feature_dims", [])
    dense_dim = model_config.get("dense_feature_dim", 0)
    version = checkpoint.get("version", path.stem)
    metrics = checkpoint.get("metrics")

    # Optional FeatureBuilder check
    if feature_builder_path:
        fb_path = Path(feature_builder_path)
        if not fb_path.exists():
            return ModelValidationResult(
                is_valid=False,
                error_message=f"Configured feature_builder_path does not exist: {fb_path}",
            )
        try:
            fb_checkpoint = torch.load(fb_path, map_location=torch.device("cpu"), weights_only=False)
            if isinstance(fb_checkpoint, dict):
                fb_sparse_dims = fb_checkpoint.get("sparse_feature_dims", [])
                if sparse_dims and fb_sparse_dims and len(sparse_dims) != len(fb_sparse_dims):
                    return ModelValidationResult(
                        is_valid=False,
                        error_message=(
                            f"Dimension mismatch between model ({len(sparse_dims)} sparse features) "
                            f"and feature builder ({len(fb_sparse_dims)} sparse features)"
                        ),
                    )
        except Exception as e:
            logger.warning("Feature builder validation failed", error=str(e))
            return ModelValidationResult(
                is_valid=False,
                error_message=f"Feature builder artifact unreadable: {e}",
            )

    return ModelValidationResult(
        is_valid=True,
        model_type=model_type,
        version=version,
        sparse_feature_dims=sparse_dims,
        dense_feature_dim=dense_dim,
        metrics=metrics,
    )
