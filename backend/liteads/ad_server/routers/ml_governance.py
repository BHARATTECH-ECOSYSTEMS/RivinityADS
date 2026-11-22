"""
Admin ML Governance Endpoints (Phase 8).

Admin-only endpoints for:
- Model registry inspection
- Model registration & artifact validation
- Multi-gate promotion evaluation
- Rollout percentage control
- Emergency rollback to last-known-good or baseline
- Operational inference telemetry & drift monitoring
"""

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.database import get_session
from liteads.common.logger import get_logger
from liteads.common.security import require_roles
from liteads.ml_engine.governance.manifest import ModelArtifactManifest
from liteads.ml_engine.governance.registry import (
    PromotionGateConfig,
    get_model_registry,
)
from liteads.ml_engine.governance.telemetry import get_ml_telemetry
from liteads.models.auth import AuditLog, User
from liteads.schemas.ml_governance import (
    ModelPromoteRequest,
    ModelRegisterRequest,
    ModelRollbackRequest,
    ModelRolloutRequest,
    SetShadowModelRequest,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/ml", tags=["admin-ml"])


@router.get(
    "/models",
    summary="List all registered ML models and lifecycle status",
    response_model=dict[str, Any],
)
async def list_models(
    current_user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    """List all registered models, current production, shadow, and last-known-good."""
    registry = get_model_registry()
    return registry.get_registry_summary()


@router.post(
    "/models/register",
    summary="Register a new ML model artifact with manifest",
    status_code=status.HTTP_201_CREATED,
    response_model=dict[str, Any],
)
async def register_model(
    req: ModelRegisterRequest,
    current_user: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Validate and register a new model artifact."""
    registry = get_model_registry()
    try:
        manifest = ModelArtifactManifest.from_dict(req.manifest)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid manifest schema: {e}",
        )

    try:
        record = registry.register_model(
            manifest=manifest,
            artifact_path=req.artifact_path,
            registered_by=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Immutable Audit Log
    audit = AuditLog(
        user_id=current_user.id,
        action="ml_model_registered",
        details=json.dumps({
            "version": record.version,
            "model_name": record.model_name,
            "architecture": manifest.model_architecture,
            "actor_email": current_user.email,
            "sha256": manifest.sha256_checksum,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    )
    session.add(audit)
    await session.commit()

    return record.to_dict()


@router.post(
    "/models/{version}/promote",
    summary="Promote model to production via multi-gate evaluation",
    response_model=dict[str, Any],
)
async def promote_model(
    version: str,
    req: ModelPromoteRequest | None = None,
    current_user: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Evaluate candidate model against multi-gate criteria and promote to production."""
    registry = get_model_registry()
    record = registry.get_model(version)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model version '{version}' not found in registry",
        )

    gate_config = PromotionGateConfig()
    if req:
        gate_config.min_auc = req.min_auc
        gate_config.max_ece = req.max_ece
        gate_config.min_pcoctr = req.min_pcoctr
        gate_config.max_pcoctr = req.max_pcoctr
        gate_config.min_business_value_lift = req.min_business_value_lift
        gate_config.min_shadow_agreement = req.min_shadow_agreement

    try:
        promoted_record = registry.promote_model(
            version=version,
            gates=gate_config,
            promoted_by=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Audit log
    audit = AuditLog(
        user_id=current_user.id,
        action="ml_model_promoted",
        details=json.dumps({
            "version": version,
            "model_name": promoted_record.model_name,
            "actor_email": current_user.email,
            "promoted_at": promoted_record.promoted_at,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    )
    session.add(audit)
    await session.commit()

    return promoted_record.to_dict()


@router.post(
    "/models/{version}/rollout",
    summary="Set gradual rollout percentage for a model",
    response_model=dict[str, Any],
)
async def set_rollout(
    version: str,
    req: ModelRolloutRequest,
    current_user: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Adjust traffic rollout percentage for a registered model."""
    registry = get_model_registry()
    try:
        record = registry.set_rollout_percentage(
            version=version,
            percentage=req.rollout_percentage,
            updated_by=current_user.email,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model version '{version}' not found",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    audit = AuditLog(
        user_id=current_user.id,
        action="ml_model_rollout_updated",
        details=json.dumps({
            "version": version,
            "rollout_percentage": req.rollout_percentage,
            "actor_email": current_user.email,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    )
    session.add(audit)
    await session.commit()

    return record.to_dict()


@router.post(
    "/models/shadow",
    summary="Set model version for shadow mode evaluation",
    response_model=dict[str, Any],
)
async def set_shadow_model(
    req: SetShadowModelRequest,
    current_user: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Designate shadow evaluation model or disable shadow mode."""
    registry = get_model_registry()
    try:
        registry.set_shadow_model(req.version)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    audit = AuditLog(
        user_id=current_user.id,
        action="ml_shadow_model_updated",
        details=json.dumps({
            "shadow_version": req.version,
            "actor_email": current_user.email,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    )
    session.add(audit)
    await session.commit()

    return {"status": "ok", "shadow_version": req.version}


@router.post(
    "/models/rollback",
    summary="Emergency rollback to last-known-good model or baseline",
    response_model=dict[str, Any],
)
async def rollback_model(
    req: ModelRollbackRequest,
    current_user: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Execute fast atomic rollback away from failing model."""
    registry = get_model_registry()
    result = registry.rollback(
        reason=req.reason,
        rolled_back_by=current_user.email,
        target_version=req.target_version,
    )

    audit = AuditLog(
        user_id=current_user.id,
        action="ml_model_rollback",
        details=json.dumps({
            "rolled_back_version": result.get("rolled_back_version"),
            "active_production_version": result.get("active_production_version"),
            "reason": req.reason,
            "actor_email": current_user.email,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    )
    session.add(audit)
    await session.commit()

    return result


@router.get(
    "/telemetry",
    summary="Operational ML inference latency and drift telemetry",
    response_model=dict[str, Any],
)
async def get_telemetry(
    current_user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    """Fetch live operational ML inference telemetry."""
    telemetry = get_ml_telemetry()
    return telemetry.get_telemetry_summary()
