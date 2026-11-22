"""
Schemas for ML Governance and Model Lifecycle Endpoints (Phase 8).
"""

from typing import Any
from pydantic import BaseModel, Field


class ModelRegisterRequest(BaseModel):
    manifest: dict[str, Any] = Field(..., description="Complete ModelArtifactManifest dictionary")
    artifact_path: str = Field(..., description="File path to the model artifact weights on disk")


class ModelPromoteRequest(BaseModel):
    min_auc: float = Field(0.60, ge=0.0, le=1.0)
    max_ece: float = Field(0.15, ge=0.0, le=1.0)
    min_pcoctr: float = Field(0.70, ge=0.0)
    max_pcoctr: float = Field(1.30, ge=0.0)
    min_business_value_lift: float = Field(0.0)
    min_shadow_agreement: float = Field(0.50, ge=0.0, le=1.0)


class ModelRolloutRequest(BaseModel):
    rollout_percentage: float = Field(..., ge=0.0, le=100.0, description="Traffic percentage (0-100)")


class ModelRollbackRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="Justification for emergency rollback")
    target_version: str | None = Field(None, description="Optional target version; defaults to last-known-good")


class SetShadowModelRequest(BaseModel):
    version: str | None = Field(None, description="Model version to run in shadow mode, or null to disable")
