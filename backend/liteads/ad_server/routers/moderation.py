import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles, verified_user
from liteads.models import User, Creative, Campaign
from liteads.models.auth import AuditLog
from liteads.models.base import ModerationStatus, Status
from liteads.schemas.management import CreativeResponse
from liteads.ad_server.services.creative_moderation import (
    ModerationCategory,
    MODERATION_CATEGORY_DESCRIPTIONS,
)


router = APIRouter(prefix="/moderation/creatives", tags=["Moderation"], dependencies=[Depends(verified_user)])


class ModerationRejectPayload(BaseModel):
    category: str = Field(default=ModerationCategory.POLICY_VIOLATION.value, description="Moderation category")
    reason: str = Field(..., min_length=3, max_length=1000, description="Specific reason for rejection")


class ModerationCategoryItem(BaseModel):
    category: str
    description: str


@router.get("/categories", response_model=list[ModerationCategoryItem])
async def list_moderation_categories() -> list[ModerationCategoryItem]:
    """List available moderation rejection categories."""
    return [
        ModerationCategoryItem(category=k, description=v)
        for k, v in MODERATION_CATEGORY_DESCRIPTIONS.items()
    ]


@router.get("/pending", response_model=list[CreativeResponse], dependencies=[Depends(require_roles("admin"))])
async def list_pending_creatives(session: AsyncSession = Depends(get_session)) -> list[Creative]:
    """List all creatives awaiting review."""
    query = select(Creative).where(Creative.moderation_status == ModerationStatus.PENDING_REVIEW).order_by(Creative.created_at)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.get("/{creative_id}", response_model=CreativeResponse, dependencies=[Depends(require_roles("admin"))])
async def get_creative_for_moderation(creative_id: int, session: AsyncSession = Depends(get_session)) -> Creative:
    """Retrieve details of a creative for moderation."""
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise HTTPException(status_code=404, detail="Creative not found")
    return creative


@router.post("/{creative_id}/approve", response_model=CreativeResponse, dependencies=[Depends(require_roles("admin"))])
async def approve_creative(
    creative_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session)
) -> Creative:
    """Approve a creative."""
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise HTTPException(status_code=404, detail="Creative not found")
        
    if creative.moderation_status == ModerationStatus.APPROVED:
        raise HTTPException(status_code=400, detail="Creative is already approved")
        
    prev_status = creative.moderation_status
    creative.moderation_status = ModerationStatus.APPROVED
    creative.moderated_at = datetime.now(timezone.utc)
    creative.moderated_by_id = user.id
    creative.moderation_reason = None

    # Immutable Audit Log
    audit = AuditLog(
        user_id=user.id,
        action="creative_approved",
        details=json.dumps({
            "creative_id": creative.id,
            "campaign_id": creative.campaign_id,
            "target": f"creative:{creative.id}",
            "actor_email": user.email,
            "actor_role": user.role,
            "previous_status": int(prev_status),
            "new_status": int(ModerationStatus.APPROVED),
            "previous_state": int(prev_status),
            "new_state": int(ModerationStatus.APPROVED),
            "reason": "Administrative approval",
            "moderator_email": user.email,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    )
    session.add(audit)
    
    await session.commit()
    await session.refresh(creative)
    return creative


@router.post("/{creative_id}/reject", response_model=CreativeResponse, dependencies=[Depends(require_roles("admin"))])
async def reject_creative(
    creative_id: int,
    payload: ModerationRejectPayload,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session)
) -> Creative:
    """Reject a creative with categorized reason."""
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise HTTPException(status_code=404, detail="Creative not found")
        
    if creative.moderation_status == ModerationStatus.REJECTED:
        raise HTTPException(status_code=400, detail="Creative is already rejected")

    category = payload.category.upper()
    valid_categories = {c.value for c in ModerationCategory}
    if category not in valid_categories:
        category = ModerationCategory.OTHER.value

    full_reason = f"[{category}] {payload.reason}"
    prev_status = creative.moderation_status
    creative.moderation_status = ModerationStatus.REJECTED
    creative.moderation_reason = full_reason
    creative.moderated_at = datetime.now(timezone.utc)
    creative.moderated_by_id = user.id
    
    # Ensure it's not served
    creative.status = Status.PENDING

    # Immutable Audit Log
    audit = AuditLog(
        user_id=user.id,
        action="creative_rejected",
        details=json.dumps({
            "creative_id": creative.id,
            "campaign_id": creative.campaign_id,
            "target": f"creative:{creative.id}",
            "actor_email": user.email,
            "actor_role": user.role,
            "previous_status": int(prev_status),
            "new_status": int(ModerationStatus.REJECTED),
            "previous_state": int(prev_status),
            "new_state": int(ModerationStatus.REJECTED),
            "category": category,
            "reason": payload.reason,
            "moderator_email": user.email,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }),
    )
    session.add(audit)
    
    await session.commit()
    await session.refresh(creative)
    return creative


@router.post("/{creative_id}/reopen", response_model=CreativeResponse, dependencies=[Depends(require_roles("admin"))])
async def reopen_creative(
    creative_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session)
) -> Creative:
    """Reopen a rejected creative for review."""
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise HTTPException(status_code=404, detail="Creative not found")
        
    if creative.moderation_status != ModerationStatus.REJECTED:
        raise HTTPException(status_code=400, detail="Only rejected creatives can be reopened")
        
    prev_status = creative.moderation_status
    creative.moderation_status = ModerationStatus.PENDING_REVIEW
    creative.moderation_reason = "Resubmitted for review"
    creative.moderated_at = None
    creative.moderated_by_id = None

    # Immutable Audit Log
    audit = AuditLog(
        user_id=user.id,
        action="creative_reopened",
        details=json.dumps({
            "creative_id": creative.id,
            "campaign_id": creative.campaign_id,
            "previous_status": int(prev_status),
            "new_status": int(ModerationStatus.PENDING_REVIEW),
            "operator_email": user.email,
        }),
    )
    session.add(audit)
    
    await session.commit()
    await session.refresh(creative)
    return creative


@router.post("/{creative_id}/resubmit", response_model=CreativeResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def resubmit_creative(
    creative_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session)
) -> Creative:
    """Resubmit a rejected creative for review."""
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise HTTPException(status_code=404, detail="Creative not found")

    # Authorize advertiser ownership
    if user.role != "admin":
        camp = await session.get(Campaign, creative.campaign_id)
        if not camp or camp.advertiser_id != user.advertiser_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this creative")

    if creative.moderation_status != ModerationStatus.REJECTED:
        raise HTTPException(status_code=400, detail="Only rejected creatives can be resubmitted for moderation")

    prev_status = creative.moderation_status
    creative.moderation_status = ModerationStatus.PENDING_REVIEW
    creative.moderation_reason = "Resubmitted for review"
    creative.moderated_at = None
    creative.moderated_by_id = None

    # Immutable Audit Log
    audit = AuditLog(
        user_id=user.id,
        action="creative_resubmitted",
        details=json.dumps({
            "creative_id": creative.id,
            "campaign_id": creative.campaign_id,
            "previous_status": int(prev_status),
            "new_status": int(ModerationStatus.PENDING_REVIEW),
            "resubmitted_by_email": user.email,
        }),
    )
    session.add(audit)

    await session.commit()
    await session.refresh(creative)
    return creative
