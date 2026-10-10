from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles
from liteads.models import User, Creative
from liteads.models.base import ModerationStatus, Status
from liteads.schemas.management import CreativeResponse


router = APIRouter(prefix="/moderation/creatives", tags=["Moderation"])


class ModerationRejectPayload(BaseModel):
    reason: str


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
        
    creative.moderation_status = ModerationStatus.APPROVED
    creative.moderated_at = datetime.now(timezone.utc)
    creative.moderated_by_id = user.id
    
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
    """Reject a creative."""
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise HTTPException(status_code=404, detail="Creative not found")
        
    if creative.moderation_status == ModerationStatus.REJECTED:
        raise HTTPException(status_code=400, detail="Creative is already rejected")
        
    creative.moderation_status = ModerationStatus.REJECTED
    creative.moderation_reason = payload.reason
    creative.moderated_at = datetime.now(timezone.utc)
    creative.moderated_by_id = user.id
    
    # Ensure it's not served
    creative.status = Status.PENDING
    
    await session.commit()
    await session.refresh(creative)
    return creative


@router.post("/{creative_id}/reopen", response_model=CreativeResponse, dependencies=[Depends(require_roles("admin"))])
async def reopen_creative(
    creative_id: int,
    session: AsyncSession = Depends(get_session)
) -> Creative:
    """Reopen a rejected creative for review."""
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise HTTPException(status_code=404, detail="Creative not found")
        
    if creative.moderation_status != ModerationStatus.REJECTED:
        raise HTTPException(status_code=400, detail="Only rejected creatives can be reopened")
        
    creative.moderation_status = ModerationStatus.PENDING_REVIEW
    creative.moderation_reason = None
    creative.moderated_at = None
    creative.moderated_by_id = None
    
    await session.commit()
    await session.refresh(creative)
    return creative
