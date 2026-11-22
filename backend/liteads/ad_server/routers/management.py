"""CRUD and publisher reporting endpoints."""

import csv
import hashlib
import io
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.security import current_user, verified_user, require_roles
from liteads.ad_server.services.campaign_activation import (
    activate_campaign_logic,
    CampaignActivationError,
    validate_campaign_readiness,
    CampaignReadinessResponse,
)
from liteads.ad_server.services.creative_moderation import CreativeModerationService
from liteads.ad_server.services.domain_verification import DomainVerificationService
from liteads.ad_server.services.placement_analyzer import PlacementAnalyzerService
from liteads.models import (
    AdSet,
    AdSlot,
    Advertiser,
    AdvertiserApiKey,
    AdEvent,
    Campaign,
    CampaignAssignment,
    Creative,
    CreativeType,
    Publisher,
    PublisherApiKey,
    PublisherDailyStat,
    PublisherProperty,
    User,
    Status,
    ModerationStatus,
)
from liteads.management.api_key_service import ApiKeyService
from liteads.management.advertiser_api_key_service import AdvertiserApiKeyService
from liteads.schemas.management import (
    AdSetCreate,
    AdSetReportingItem,
    AdSetReportingResponse,
    AdSetResponse,
    AdSetUpdate,
    AdvertiserApiKeyCreate,
    AdvertiserApiKeyCreateResponse,
    AdvertiserApiKeyResponse,
    AdvertiserCreate,
    AdvertiserResponse,
    AdvertiserUpdate,
    ApiKeyCreate,
    ApiKeyResponse,
    ApiKeyUsageResponse,
    CampaignAssignmentCreate,
    CampaignAssignmentResponse,
    CampaignCreate,
    CampaignResponse,
    CampaignUpdate,
    CreativeCreate,
    CreativeResponse,
    CreativeUpdate,
    PublisherApiKeyCreate,
    PublisherApiKeyCreateResponse,
    PublisherApiKeyResponse,
    PublisherCreate,
    PublisherPropertyCreate,
    PublisherPropertyResponse,
    PublisherPropertyUpdate,
    PropertyChallengeRequest,
    PropertyChallengeResponse,
    PublisherReportingResponse,
    AdvertiserReportingResponse,
    DailyStatResponse,
    PublisherResponse,
    PublisherUpdate,
    SlotCreate,
    SlotResponse,
    SlotUpdate,
    PublisherAssignmentResponse,
    SuggestionResponse,
    SuggestionStatusPatch,
    OptimizationStatusResponse,
    OptimizationDecisionResponse,
    CampaignOptimizationStateResponse,
    DecisionRevertResponse,
    FormatBenchmarkSchema,
    PlacementHealthItemSchema,
    PlacementHealthResponse,
    AuditLogResponse,
    SLAItem,
    OperationalAlert,
    OperationalExceptionsResponse,
    PublisherSafetySummaryResponse,
    WarningCategorySchema,
)
from liteads.schemas.finance import PublisherEarningsBreakdownResponse
from liteads.ad_server.services.destination_validator import DestinationValidator
from liteads.ad_server.services.ivt_service import IVTService
from liteads.ad_server.services.billing import BillingService
from liteads.models.auth import AuditLog
from liteads.models.finance import BillingDispute, PublisherPayout
from liteads.ad_server.services.optimization_executor import OptimizationExecutor, OptimizationExecutionError
from liteads.ad_server.services.optimization_policy import OptimizationPolicyResult
from liteads.ad_server.services.rate_limit_service import RateLimitService
from liteads.models.ad import Suggestion, OptimizationDecision, CampaignChangeLog

router = APIRouter(dependencies=[Depends(verified_user)])


def _not_found(entity: str, entity_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{entity} {entity_id} not found")


def _assert_advertiser_access(user: User, advertiser_id: int) -> None:
    if user.role != "admin" and user.advertiser_id != advertiser_id:
        raise HTTPException(status_code=403, detail="Advertiser resource is outside your account")


def _assert_publisher_access(user: User, publisher_id: int) -> None:
    if user.role != "admin" and user.publisher_id != publisher_id:
        raise HTTPException(status_code=403, detail="Publisher resource is outside your account")


async def _assert_campaign_access(user: User, campaign_id: int, session: AsyncSession) -> Campaign:
    """Admin: any campaign. Advertiser: only own campaigns. Publisher: never."""
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if user.role == "advertiser" and campaign.advertiser_id != user.advertiser_id:
        raise HTTPException(status_code=403, detail="Campaign belongs to another advertiser")
    if user.role == "publisher":
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return campaign


async def _assert_ad_set_access(user: User, ad_set_id: int, session: AsyncSession) -> tuple[AdSet, Campaign]:
    """Verify that user has access to the ad set via its parent campaign."""
    ad_set = await session.get(AdSet, ad_set_id)
    if ad_set is None:
        raise _not_found("AdSet", ad_set_id)
    campaign = await _assert_campaign_access(user, ad_set.campaign_id, session)
    return ad_set, campaign


@router.get("/advertiser", response_model=list[AdvertiserResponse], dependencies=[Depends(require_roles("admin"))])
async def list_advertisers(session: AsyncSession = Depends(get_session)) -> list[Advertiser]:
    result = await session.execute(select(Advertiser).order_by(Advertiser.id))
    return list(result.scalars().all())


@router.post("/advertiser", response_model=AdvertiserResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin"))])
async def create_advertiser(
    payload: AdvertiserCreate, session: AsyncSession = Depends(get_session)
) -> Advertiser:
    advertiser = Advertiser(**payload.model_dump())
    session.add(advertiser)
    await session.commit()
    await session.refresh(advertiser)
    return advertiser


@router.get("/advertiser/{advertiser_id}", response_model=AdvertiserResponse, dependencies=[Depends(require_roles("admin"))])
async def get_advertiser(advertiser_id: int, session: AsyncSession = Depends(get_session)) -> Advertiser:
    advertiser = await session.get(Advertiser, advertiser_id)
    if advertiser is None:
        raise _not_found("Advertiser", advertiser_id)
    return advertiser


@router.patch("/advertiser/{advertiser_id}", response_model=AdvertiserResponse, dependencies=[Depends(require_roles("admin"))])
async def update_advertiser(
    advertiser_id: int,
    payload: AdvertiserUpdate,
    session: AsyncSession = Depends(get_session),
) -> Advertiser:
    advertiser = await session.get(Advertiser, advertiser_id)
    if advertiser is None:
        raise _not_found("Advertiser", advertiser_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(advertiser, key, value)
    await session.commit()
    await session.refresh(advertiser)
    return advertiser


@router.get("/campaign", response_model=list[CampaignResponse], dependencies=[Depends(require_roles("admin", "advertiser"))])
async def list_campaigns(
    advertiser_id: int | None = Query(default=None),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> list[Campaign]:
    query = select(Campaign).order_by(Campaign.id)
    if user.role == "advertiser":
        advertiser_id = user.advertiser_id
    if advertiser_id is not None:
        query = query.where(Campaign.advertiser_id == advertiser_id)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("/campaign", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def create_campaign(
    payload: CampaignCreate, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> Campaign:
    _assert_advertiser_access(user, payload.advertiser_id)
    if await session.get(Advertiser, payload.advertiser_id) is None:
        raise _not_found("Advertiser", payload.advertiser_id)
    
    # Phase 3E: All campaigns must start as DRAFT (Status.DRAFT = 5)
    data = payload.model_dump()
    data["status"] = 5
    
    campaign = Campaign(**data)
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    return campaign


@router.get("/campaign/{campaign_id}", response_model=CampaignResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_campaign(campaign_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    _assert_advertiser_access(user, campaign.advertiser_id)
    return campaign


@router.patch("/campaign/{campaign_id}", response_model=CampaignResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    _assert_advertiser_access(user, campaign.advertiser_id)
    updates = payload.model_dump(exclude_unset=True)
    prev_status = campaign.status
    for key, value in updates.items():
        setattr(campaign, key, value)
    if "status" in updates and campaign.status != prev_status:
        session.add(
            AuditLog(
                user_id=user.id,
                action="campaign_status_updated",
                details=json.dumps({
                    "campaign_id": campaign.id,
                    "advertiser_id": campaign.advertiser_id,
                    "previous_status": int(prev_status) if prev_status is not None else None,
                    "new_status": int(campaign.status) if campaign.status is not None else None,
                    "actor_email": user.email,
                }),
            )
        )
    await session.commit()
    await session.refresh(campaign)
    return campaign


@router.post("/campaign/{campaign_id}/activate", response_model=CampaignResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def activate_campaign(
    campaign_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> Campaign:
    """Activate a DRAFT campaign."""
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
        
    _assert_advertiser_access(user, campaign.advertiser_id)

    try:
        campaign = await activate_campaign_logic(session, campaign_id, campaign.advertiser_id)
    except CampaignActivationError as e:
        msg = str(e)
        if msg in ("Campaign not found", "Advertiser not found"):
            raise _not_found(msg.split()[0], campaign_id)
        raise HTTPException(status_code=400, detail=msg)
        
    await session.commit()
    await session.refresh(campaign)
    return campaign


@router.get(
    "/campaign/{campaign_id}/readiness",
    response_model=CampaignReadinessResponse,
    dependencies=[Depends(require_roles("admin", "advertiser"))],
)
@router.get(
    "/campaign/{campaign_id}/validate",
    response_model=CampaignReadinessResponse,
    dependencies=[Depends(require_roles("admin", "advertiser"))],
)
async def get_campaign_readiness_endpoint(
    campaign_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> CampaignReadinessResponse:
    """Retrieve server-authoritative campaign readiness state, blockers, warnings, and summary."""
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    _assert_advertiser_access(user, campaign.advertiser_id)
    return await validate_campaign_readiness(session, campaign_id, campaign.advertiser_id)



@router.get("/campaign/{campaign_id}/assignments", response_model=list[CampaignAssignmentResponse], dependencies=[Depends(require_roles("admin"))])
async def list_campaign_assignments(
    campaign_id: int, session: AsyncSession = Depends(get_session)
) -> list[CampaignAssignment]:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    query = select(CampaignAssignment).where(CampaignAssignment.campaign_id == campaign_id).order_by(CampaignAssignment.id)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("/campaign/{campaign_id}/assignments", response_model=CampaignAssignmentResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin"))])
async def create_campaign_assignment(
    campaign_id: int, payload: CampaignAssignmentCreate, session: AsyncSession = Depends(get_session)
) -> CampaignAssignment:
    if payload.campaign_id != campaign_id:
        raise HTTPException(status_code=400, detail="Campaign ID mismatch")
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    publisher = await session.get(Publisher, payload.publisher_id)
    if publisher is None:
        raise _not_found("Publisher", payload.publisher_id)
    if payload.slot_id:
        query = select(AdSlot).where(AdSlot.slot_id == payload.slot_id, AdSlot.publisher_id == payload.publisher_id)
        result = await session.execute(query)
        slot = result.scalar_one_or_none()
        if slot is None:
            raise HTTPException(status_code=400, detail="AdSlot does not exist or does not belong to the publisher")
    
    assignment = CampaignAssignment(**payload.model_dump())
    session.add(assignment)
    await session.commit()
    await session.refresh(assignment)
    return assignment


@router.delete("/campaign/{campaign_id}/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_roles("admin"))])
async def delete_campaign_assignment(
    campaign_id: int, assignment_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    assignment = await session.get(CampaignAssignment, assignment_id)
    if assignment is None or assignment.campaign_id != campaign_id:
        raise _not_found("CampaignAssignment", assignment_id)
    await session.delete(assignment)
    await session.commit()


# ── Phase 7B: AdSet Endpoints ───────────────────────────────────────────────

@router.post("/campaign/{campaign_id}/ad-sets", response_model=AdSetResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def create_ad_set(
    campaign_id: int,
    payload: AdSetCreate,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> AdSet:
    campaign = await _assert_campaign_access(user, campaign_id, session)
    if payload.daily_budget is not None and campaign.budget_daily is not None and campaign.budget_daily > 0:
        if payload.daily_budget > campaign.budget_daily:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ad set daily budget cannot exceed campaign daily budget",
            )
    data = payload.model_dump()
    ad_set = AdSet(campaign_id=campaign_id, **data)
    session.add(ad_set)
    await session.commit()
    await session.refresh(ad_set)
    return ad_set


@router.get("/campaign/{campaign_id}/ad-sets", response_model=list[AdSetResponse], dependencies=[Depends(require_roles("admin", "advertiser"))])
async def list_ad_sets(
    campaign_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> list[AdSet]:
    await _assert_campaign_access(user, campaign_id, session)
    query = select(AdSet).where(AdSet.campaign_id == campaign_id).order_by(AdSet.id)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.get("/ad-set/{ad_set_id}", response_model=AdSetResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_ad_set(
    ad_set_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> AdSet:
    ad_set, _ = await _assert_ad_set_access(user, ad_set_id, session)
    return ad_set


@router.patch("/ad-set/{ad_set_id}", response_model=AdSetResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def update_ad_set(
    ad_set_id: int,
    payload: AdSetUpdate,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> AdSet:
    ad_set, campaign = await _assert_ad_set_access(user, ad_set_id, session)
    updates = payload.model_dump(exclude_unset=True)
    if "daily_budget" in updates and updates["daily_budget"] is not None:
        if campaign.budget_daily is not None and campaign.budget_daily > 0 and updates["daily_budget"] > campaign.budget_daily:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ad set daily budget cannot exceed campaign daily budget",
            )
    for key, value in updates.items():
        setattr(ad_set, key, value)
    await session.commit()
    await session.refresh(ad_set)
    return ad_set


@router.put("/ad-set/{ad_set_id}", response_model=AdSetResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def replace_ad_set(
    ad_set_id: int,
    payload: AdSetUpdate,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> AdSet:
    return await update_ad_set(ad_set_id, payload, user, session)


@router.patch("/ad-set/{ad_set_id}/pause", response_model=AdSetResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def pause_ad_set(
    ad_set_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> AdSet:
    ad_set, _ = await _assert_ad_set_access(user, ad_set_id, session)
    ad_set.status = Status.PAUSED
    await session.commit()
    await session.refresh(ad_set)
    return ad_set


@router.patch("/ad-set/{ad_set_id}/resume", response_model=AdSetResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def resume_ad_set(
    ad_set_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> AdSet:
    ad_set, _ = await _assert_ad_set_access(user, ad_set_id, session)
    ad_set.status = Status.ACTIVE
    await session.commit()
    await session.refresh(ad_set)
    return ad_set


@router.delete("/ad-set/{ad_set_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def delete_ad_set(
    ad_set_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    ad_set, _ = await _assert_ad_set_access(user, ad_set_id, session)
    ad_set.status = Status.DELETED
    await session.commit()


@router.get("/creative", response_model=list[CreativeResponse], dependencies=[Depends(require_roles("admin", "advertiser"))])
async def list_creatives(
    campaign_id: int,
    ad_set_id: int | None = Query(default=None),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> list[Creative]:
    await _assert_campaign_access(user, campaign_id, session)
    query = select(Creative).order_by(Creative.id).where(Creative.campaign_id == campaign_id)
    if ad_set_id is not None:
        query = query.where(Creative.ad_set_id == ad_set_id)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("/creative", response_model=CreativeResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def create_creative(
    payload: CreativeCreate,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session)
) -> Creative:
    campaign = await _assert_campaign_access(user, payload.campaign_id, session)

    data = payload.model_dump()
    if payload.ad_set_id is not None:
        ad_set = await session.get(AdSet, payload.ad_set_id)
        if ad_set is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="AdSet not found")
        if ad_set.campaign_id != payload.campaign_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="AdSet does not belong to specified campaign")
    else:
        # Default / legacy fallback: assign campaign's default ad set
        res = await session.execute(
            select(AdSet).where(AdSet.campaign_id == payload.campaign_id).order_by(AdSet.id.asc())
        )
        default_ad_set = res.scalars().first()
        if default_ad_set is None:
            default_ad_set = AdSet(
                campaign_id=payload.campaign_id,
                name="Default Ad Set",
                status=Status.ACTIVE,
                daily_budget=campaign.budget_daily,
                bid_type=campaign.bid_type,
                bid_amount=campaign.bid_amount,
            )
            session.add(default_ad_set)
            await session.flush()
        data["ad_set_id"] = default_ad_set.id
    
    if payload.landing_url:
        dest_val = await DestinationValidator.check_destination(payload.landing_url, perform_fetch=False)
        if not dest_val.is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid landing destination: {dest_val.reason}")

    if payload.landing_url and user.role == "advertiser":
        domain_service = DomainVerificationService(session)
        is_verified = await domain_service.is_url_verified_for_advertiser(user.advertiser_id, payload.landing_url)
        if not is_verified:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Landing URL domain is not verified")
    
    decision = await CreativeModerationService.moderate_creative(
        title=payload.title,
        description=payload.description,
        image_url=payload.image_url,
        video_url=payload.video_url,
        landing_url=payload.landing_url
    )
    
    data["moderation_status"] = decision.status
    data["moderation_reason"] = decision.reason
    
    # If not approved, force delivery status to PENDING so it cannot be served
    if decision.status != ModerationStatus.APPROVED and data.get("status") == Status.ACTIVE:
        data["status"] = Status.PENDING
        
    creative = Creative(**data)
    session.add(creative)
    await session.commit()
    await session.refresh(creative)
    return creative


@router.get("/creative/{creative_id}", response_model=CreativeResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_creative(creative_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)) -> Creative:
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise _not_found("Creative", creative_id)
    await _assert_campaign_access(user, creative.campaign_id, session)
    return creative


@router.patch("/creative/{creative_id}", response_model=CreativeResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def update_creative(
    creative_id: int,
    payload: CreativeUpdate,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> Creative:
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise _not_found("Creative", creative_id)
    await _assert_campaign_access(user, creative.campaign_id, session)

    updates = payload.model_dump(exclude_unset=True)
    if "ad_set_id" in updates and updates["ad_set_id"] is not None:
        ad_set = await session.get(AdSet, updates["ad_set_id"])
        if ad_set is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="AdSet not found")
        if ad_set.campaign_id != creative.campaign_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="AdSet does not belong to creative campaign")

    orig_title = creative.title
    orig_description = creative.description
    orig_image_url = creative.image_url
    orig_video_url = creative.video_url
    orig_landing_url = creative.landing_url
    orig_mod_status = creative.moderation_status

    for key, value in updates.items():
        setattr(creative, key, value)
        
    if "landing_url" in updates and creative.landing_url:
        dest_val = await DestinationValidator.check_destination(creative.landing_url, perform_fetch=False)
        if not dest_val.is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid landing destination: {dest_val.reason}")

    if "landing_url" in updates and user.role == "advertiser":
        domain_service = DomainVerificationService(session)
        is_verified = await domain_service.is_url_verified_for_advertiser(user.advertiser_id, creative.landing_url)
        if not is_verified:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Landing URL domain is not verified")
        
    # Content equivalence check: if substantive fields changed
    sensitive_fields_changed = any(k in updates for k in ("title", "description", "image_url", "video_url", "landing_url"))
    if sensitive_fields_changed:
        is_equivalent = CreativeModerationService.are_contents_equivalent(
            orig_title, orig_description, orig_image_url, orig_video_url, orig_landing_url,
            creative.title, creative.description, creative.image_url, creative.video_url, creative.landing_url,
        )
        if orig_mod_status == ModerationStatus.APPROVED and not is_equivalent:
            creative.moderation_status = ModerationStatus.PENDING_REVIEW
            creative.moderation_reason = "Content modified; re-review required"
            creative.moderated_at = None
            creative.moderated_by_id = None
            session.add(
                AuditLog(
                    user_id=user.id,
                    action="creative_content_modified_reset",
                    details=json.dumps({
                        "creative_id": creative.id,
                        "campaign_id": creative.campaign_id,
                        "previous_status": int(orig_mod_status),
                        "new_status": int(ModerationStatus.PENDING_REVIEW),
                        "actor_email": user.email,
                    }),
                )
            )
        elif not is_equivalent or orig_mod_status != ModerationStatus.APPROVED:
            decision = await CreativeModerationService.moderate_creative(
                title=creative.title,
                description=creative.description,
                image_url=creative.image_url,
                video_url=creative.video_url,
                landing_url=creative.landing_url
            )
            creative.moderation_status = decision.status
            creative.moderation_reason = decision.reason
            creative.moderated_at = None
            creative.moderated_by_id = None
        
    # Force status to PENDING if it's not approved and user wants it ACTIVE
    if creative.moderation_status != ModerationStatus.APPROVED and creative.status == Status.ACTIVE:
        creative.status = Status.PENDING
        
    await session.commit()
    await session.refresh(creative)
    return creative

@router.delete("/creative/{creative_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def delete_creative(
    creative_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise _not_found("Creative", creative_id)
    await _assert_campaign_access(user, creative.campaign_id, session)
    await session.delete(creative)
    await session.commit()


@router.get("/publisher", response_model=list[PublisherResponse], dependencies=[Depends(require_roles("admin"))])
async def list_publishers(session: AsyncSession = Depends(get_session)) -> list[Publisher]:
    result = await session.execute(select(Publisher).order_by(Publisher.id))
    return list(result.scalars().all())


@router.post("/publisher", response_model=PublisherResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin"))])
async def create_publisher(
    payload: PublisherCreate, session: AsyncSession = Depends(get_session)
) -> Publisher:
    publisher = Publisher(**payload.model_dump())
    session.add(publisher)
    await session.commit()
    await session.refresh(publisher)
    return publisher


@router.get("/publisher/assignments", response_model=list[PublisherAssignmentResponse], dependencies=[Depends(require_roles("publisher"))])
async def get_publisher_assignments(
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> list[PublisherAssignmentResponse]:
    if not user.publisher_id:
        raise HTTPException(status_code=403, detail="Not a publisher")

    query = (
        select(
            CampaignAssignment.id,
            CampaignAssignment.campaign_id,
            Campaign.name.label("campaign_name"),
            Advertiser.name.label("advertiser_name"),
            CampaignAssignment.slot_id,
            AdSlot.name.label("slot_name"),
            Campaign.status
        )
        .join(Campaign, CampaignAssignment.campaign_id == Campaign.id)
        .join(Advertiser, Campaign.advertiser_id == Advertiser.id)
        .outerjoin(
            AdSlot,
            (CampaignAssignment.slot_id == AdSlot.slot_id) &
            (CampaignAssignment.publisher_id == AdSlot.publisher_id)
        )
        .where(CampaignAssignment.publisher_id == user.publisher_id)
        .order_by(CampaignAssignment.id)
    )
    result = await session.execute(query)
    
    status_map = {0: "Paused", 1: "Active", 2: "Completed", 3: "Archived"}
    
    response = []
    for row in result.all():
        status_str = status_map.get(row.status, "Unknown")
        response.append(PublisherAssignmentResponse(
            id=row.id,
            campaign_id=row.campaign_id,
            campaign_name=row.campaign_name,
            advertiser_name=row.advertiser_name,
            slot_id=row.slot_id,
            slot_name=row.slot_name,
            status=status_str
        ))
    return response


@router.get("/publisher/{publisher_id}", response_model=PublisherResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def get_publisher(publisher_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)) -> Publisher:
    publisher = await session.get(Publisher, publisher_id)
    if publisher is None:
        raise _not_found("Publisher", publisher_id)
    _assert_publisher_access(user, publisher_id)
    return publisher


@router.patch("/publisher/{publisher_id}", response_model=PublisherResponse, dependencies=[Depends(require_roles("admin"))])
async def update_publisher(
    publisher_id: int,
    payload: PublisherUpdate,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> Publisher:
    publisher = await session.get(Publisher, publisher_id)
    if publisher is None:
        raise _not_found("Publisher", publisher_id)
    updates = payload.model_dump(exclude_unset=True)
    prev_status = publisher.status
    for key, value in updates.items():
        setattr(publisher, key, value)
    if "status" in updates and publisher.status != prev_status:
        session.add(
            AuditLog(
                user_id=user.id,
                action="publisher_status_updated",
                details=json.dumps({
                    "publisher_id": publisher.id,
                    "previous_status": int(prev_status) if prev_status is not None else None,
                    "new_status": int(publisher.status) if publisher.status is not None else None,
                    "actor_email": user.email,
                }),
            )
        )
    await session.commit()
    await session.refresh(publisher)
    return publisher


@router.get("/publisher/{publisher_id}/keys", response_model=list[ApiKeyResponse], dependencies=[Depends(require_roles("admin", "publisher"))])
async def list_publisher_keys(
    publisher_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> list[ApiKeyResponse]:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    return await ApiKeyService.list_api_keys(session, publisher_id)


@router.post("/publisher/{publisher_id}/keys", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "publisher"))])
async def create_publisher_key(
    publisher_id: int, payload: ApiKeyCreate, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> ApiKeyResponse:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    
    db_key, raw_key = await ApiKeyService.create_api_key(session, publisher_id, payload)
    response_model = ApiKeyResponse.model_validate(db_key)
    response_model.raw_key = raw_key
    return response_model


@router.delete("/publisher/{publisher_id}/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_roles("admin", "publisher"))])
async def revoke_publisher_key(
    publisher_id: int, key_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> None:
    _assert_publisher_access(user, publisher_id)
    if not await ApiKeyService.revoke_api_key(session, publisher_id, key_id):
        raise HTTPException(status_code=404, detail="API Key not found or already revoked")


@router.get("/slot", response_model=list[SlotResponse], dependencies=[Depends(require_roles("admin", "publisher"))])
async def list_slots(
    publisher_id: int | None = Query(default=None),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> list[AdSlot]:
    query = select(AdSlot).order_by(AdSlot.id)
    if user.role == "publisher":
        publisher_id = user.publisher_id
    if publisher_id is not None:
        query = query.where(AdSlot.publisher_id == publisher_id)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("/slot", response_model=SlotResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "publisher"))])
async def create_slot(payload: SlotCreate, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)) -> AdSlot:
    publisher_id = payload.publisher_id or user.publisher_id
    if not publisher_id:
        raise HTTPException(status_code=400, detail="publisher_id is required")
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    data = payload.model_dump()
    data["publisher_id"] = publisher_id
    slot = AdSlot(**data)
    session.add(slot)
    await session.commit()
    await session.refresh(slot)
    return slot


@router.get("/slot/{slot_id}", response_model=SlotResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def get_slot(slot_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)) -> AdSlot:
    slot = await session.get(AdSlot, slot_id)
    if slot is None:
        raise _not_found("Slot", slot_id)
    _assert_publisher_access(user, slot.publisher_id)
    return slot


@router.patch("/slot/{slot_id}", response_model=SlotResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def update_slot(
    slot_id: int, payload: SlotUpdate, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> AdSlot:
    slot = await session.get(AdSlot, slot_id)
    if slot is None:
        raise _not_found("Slot", slot_id)
    _assert_publisher_access(user, slot.publisher_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(slot, key, value)
    await session.commit()
    await session.refresh(slot)
    return slot


@router.get("/advertiser/{advertiser_id}/reporting", response_model=AdvertiserReportingResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def advertiser_reporting(
    advertiser_id: int,
    user: User = Depends(verified_user),
    campaign_id: int | None = Query(default=None),
    ad_set_id: int | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> AdvertiserReportingResponse:
    if user.role == "advertiser" and user.advertiser_id != advertiser_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this advertiser")
    if await session.get(Advertiser, advertiser_id) is None:
        raise _not_found("Advertiser", advertiser_id)

    if ad_set_id:
        ad_set = await session.get(AdSet, ad_set_id)
        if ad_set is None:
            raise _not_found("AdSet", ad_set_id)
        ad_set_camp = await session.get(Campaign, ad_set.campaign_id)
        if user.role == "advertiser" and (not ad_set_camp or ad_set_camp.advertiser_id != user.advertiser_id):
            raise HTTPException(status_code=403, detail="AdSet belongs to another advertiser")
        if campaign_id and ad_set.campaign_id != campaign_id:
            raise HTTPException(status_code=400, detail="AdSet does not belong to specified campaign")
    
    today = datetime.now(timezone.utc).date()
    start = start_date or today
    end = end_date or today
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
        
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())

    # Check for currency consistency across the queried campaigns/events
    curr_query = (
        select(AdEvent.currency)
        .distinct()
        .join(Campaign, Campaign.id == AdEvent.campaign_id)
        .where(
            Campaign.advertiser_id == advertiser_id,
            AdEvent.event_time >= start_dt,
            AdEvent.event_time <= end_dt,
            AdEvent.is_test == False,
            AdEvent.currency.isnot(None),
        )
    )
    if campaign_id:
        curr_query = curr_query.where(AdEvent.campaign_id == campaign_id)
    if ad_set_id:
        curr_query = curr_query.join(Creative, Creative.id == AdEvent.creative_id).where(Creative.ad_set_id == ad_set_id)

    currs_res = await session.execute(curr_query)
    distinct_currs = [c for c in currs_res.scalars().all() if c]
    if len(set(distinct_currs)) > 1:
        raise HTTPException(
            status_code=422,
            detail=f"Incompatible currencies detected in reporting period: {sorted(list(set(distinct_currs)))}",
        )
    report_currency = distinct_currs[0] if distinct_currs else "INR"
    
    query = select(
        func.date(AdEvent.event_time).label('stat_date'),
        func.count().filter(AdEvent.event_type == 1).label('impressions'),
        func.count().filter(AdEvent.event_type == 2).label('clicks'),
        func.count().filter(AdEvent.event_type == 3).label('total_conversions'),
        func.count().filter(and_(AdEvent.event_type == 3, AdEvent.is_attributed == True)).label('attributed_conversions'),
        func.coalesce(func.sum(case((and_(AdEvent.event_type == 3, AdEvent.is_attributed == True), AdEvent.conversion_value), else_=0)), 0).label('conversion_value'),
        func.coalesce(func.sum(AdEvent.cost), 0).label('spend'),
    ).join(Campaign, Campaign.id == AdEvent.campaign_id).where(
        Campaign.advertiser_id == advertiser_id,
        AdEvent.event_time >= start_dt,
        AdEvent.event_time <= end_dt,
        AdEvent.is_test == False,
    )
    if campaign_id:
        query = query.where(AdEvent.campaign_id == campaign_id)
    if ad_set_id:
        query = query.join(Creative, Creative.id == AdEvent.creative_id).where(Creative.ad_set_id == ad_set_id)
        
    query = query.group_by(func.date(AdEvent.event_time)).order_by(func.date(AdEvent.event_time))
    result = await session.execute(query)
    
    total_impressions = 0
    total_clicks = 0
    total_conversions = 0
    total_attributed_conversions = 0
    total_conversion_value = Decimal("0")
    total_spend = Decimal("0")
    breakdown = []
    
    for row in result.all():
        d_impressions = int(row.impressions or 0)
        d_clicks = int(row.clicks or 0)
        d_total_conversions = int(row.total_conversions or 0)
        d_attr_conversions = int(row.attributed_conversions or 0)
        d_conv_value = Decimal(str(row.conversion_value or 0))
        d_spend = Decimal(str(row.spend or 0))
        
        stat_date = datetime.strptime(str(row.stat_date), "%Y-%m-%d").date()
        d_cpa = (d_spend / Decimal(d_attr_conversions)) if d_attr_conversions > 0 else None
        d_roas = (d_conv_value / d_spend) if d_spend > Decimal("0") else None
        
        breakdown.append(DailyStatResponse(
            stat_date=stat_date,
            impressions=d_impressions,
            clicks=d_clicks,
            conversions=d_total_conversions,
            total_conversions=d_total_conversions,
            attributed_conversions=d_attr_conversions,
            conversion_value=d_conv_value,
            spend=d_spend,
            cpa=d_cpa,
            roas=d_roas,
        ))
        
        total_impressions += d_impressions
        total_clicks += d_clicks
        total_conversions += d_total_conversions
        total_attributed_conversions += d_attr_conversions
        total_conversion_value += d_conv_value
        total_spend += d_spend
        
    ctr_val = (Decimal(total_clicks) / Decimal(total_impressions) * Decimal(100)) if total_impressions else Decimal("0")
    cvr_val = (Decimal(total_attributed_conversions) / Decimal(total_clicks) * Decimal(100)) if total_clicks else Decimal("0")
    cpa_val = (total_spend / Decimal(total_attributed_conversions)) if total_attributed_conversions > 0 else None
    roas_val = (total_conversion_value / total_spend) if total_spend > Decimal("0") else None
    
    return AdvertiserReportingResponse(
        advertiser_id=advertiser_id,
        campaign_id=campaign_id,
        ad_set_id=ad_set_id,
        start_date=start,
        end_date=end,
        impressions=total_impressions,
        clicks=total_clicks,
        conversions=total_conversions,
        total_conversions=total_conversions,
        attributed_conversions=total_attributed_conversions,
        conversion_value=total_conversion_value,
        spend=total_spend,
        currency=report_currency,
        ctr=ctr_val,
        cvr=cvr_val,
        cpa=cpa_val,
        roas=roas_val,
        daily_breakdown=breakdown,
    )


@router.get("/advertiser/{advertiser_id}/reporting/export", dependencies=[Depends(require_roles("admin", "advertiser"))])
async def export_advertiser_reporting(
    advertiser_id: int,
    user: User = Depends(verified_user),
    campaign_id: int | None = Query(default=None),
    ad_set_id: int | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Export advertiser campaign reporting to RFC 4180 CSV or JSON (Phase 3 Task H)."""
    report = await advertiser_reporting(
        advertiser_id=advertiser_id,
        user=user,
        campaign_id=campaign_id,
        ad_set_id=ad_set_id,
        start_date=start_date,
        end_date=end_date,
        session=session,
    )
    if format == "json":
        return report

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow([
        "Date",
        "Impressions",
        "Clicks",
        "TotalConversions",
        "AttributedConversions",
        "ConversionValue",
        "Spend",
        "CTR_Percent",
        "CPA",
        "ROAS",
        "Currency",
    ])
    for d in report.daily_breakdown:
        d_ctr = (Decimal(d.clicks) / Decimal(d.impressions) * Decimal(100)) if d.impressions else Decimal("0")
        writer.writerow([
            d.stat_date.isoformat(),
            d.impressions,
            d.clicks,
            d.total_conversions,
            d.attributed_conversions,
            f"{d.conversion_value:.2f}",
            f"{d.spend:.4f}",
            f"{d_ctr:.2f}%",
            f"{d.cpa:.4f}" if d.cpa is not None else "N/A",
            f"{d.roas:.4f}" if d.roas is not None else "N/A",
            report.currency,
        ])
    csv_bytes = output.getvalue().encode("utf-8")
    filename = f"advertiser_{advertiser_id}_report_{report.start_date}_{report.end_date}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/campaign/{campaign_id}/ad-set-reporting", response_model=AdSetReportingResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def campaign_ad_set_reporting(
    campaign_id: int,
    user: User = Depends(verified_user),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> AdSetReportingResponse:
    campaign = await _assert_campaign_access(user, campaign_id, session)
    today = datetime.now(timezone.utc).date()
    start = start_date or today
    end = end_date or today
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())

    # Check currency consistency
    curr_query = (
        select(AdEvent.currency)
        .distinct()
        .where(
            AdEvent.campaign_id == campaign_id,
            AdEvent.event_time >= start_dt,
            AdEvent.event_time <= end_dt,
            AdEvent.is_test == False,
            AdEvent.currency.isnot(None),
        )
    )
    currs_res = await session.execute(curr_query)
    distinct_currs = [c for c in currs_res.scalars().all() if c]
    if len(set(distinct_currs)) > 1:
        raise HTTPException(
            status_code=422,
            detail=f"Incompatible currencies detected in reporting period: {sorted(list(set(distinct_currs)))}",
        )
    report_currency = distinct_currs[0] if distinct_currs else "INR"

    ad_sets_result = await session.execute(
        select(AdSet).where(AdSet.campaign_id == campaign_id).order_by(AdSet.id)
    )
    ad_sets = list(ad_sets_result.scalars().all())

    events_query = (
        select(
            Creative.ad_set_id,
            func.count().filter(AdEvent.event_type == 1).label('impressions'),
            func.count().filter(AdEvent.event_type == 2).label('clicks'),
            func.count().filter(AdEvent.event_type == 3).label('total_conversions'),
            func.count().filter(and_(AdEvent.event_type == 3, AdEvent.is_attributed == True)).label('attributed_conversions'),
            func.coalesce(func.sum(case((and_(AdEvent.event_type == 3, AdEvent.is_attributed == True), AdEvent.conversion_value), else_=0)), 0).label('conversion_value'),
            func.coalesce(func.sum(AdEvent.cost), 0).label('spend'),
        )
        .join(Creative, Creative.id == AdEvent.creative_id)
        .where(
            AdEvent.campaign_id == campaign_id,
            AdEvent.event_time >= start_dt,
            AdEvent.event_time <= end_dt,
            AdEvent.is_test == False,
            Creative.ad_set_id.isnot(None),
        )
        .group_by(Creative.ad_set_id)
    )
    events_result = await session.execute(events_query)
    ad_set_stats_map = {row.ad_set_id: row for row in events_result.all()}

    ad_set_items: list[AdSetReportingItem] = []
    total_impressions = 0
    total_clicks = 0
    total_conversions = 0
    total_attributed_conversions = 0
    total_conversion_value = Decimal("0")
    total_spend = Decimal("0")

    for ad_set in ad_sets:
        row = ad_set_stats_map.get(ad_set.id)
        impr = int(row.impressions or 0) if row else 0
        clk = int(row.clicks or 0) if row else 0
        tot_conv = int(row.total_conversions or 0) if row else 0
        attr_conv = int(row.attributed_conversions or 0) if row else 0
        conv_val = Decimal(str(row.conversion_value or 0)) if row else Decimal("0")
        sp = Decimal(str(row.spend or 0)) if row else Decimal("0")

        ctr = (Decimal(clk) / Decimal(impr) * Decimal(100)) if impr else Decimal("0")
        cvr = (Decimal(attr_conv) / Decimal(clk) * Decimal(100)) if clk else Decimal("0")
        cpa = (sp / Decimal(attr_conv)) if attr_conv > 0 else None
        roas = (conv_val / sp) if sp > Decimal("0") else None

        ad_set_items.append(
            AdSetReportingItem(
                ad_set_id=ad_set.id,
                ad_set_name=ad_set.name,
                status=ad_set.status,
                impressions=impr,
                clicks=clk,
                conversions=tot_conv,
                total_conversions=tot_conv,
                attributed_conversions=attr_conv,
                conversion_value=conv_val,
                spend=sp,
                currency=report_currency,
                ctr=ctr,
                cvr=cvr,
                cpa=cpa,
                roas=roas,
            )
        )
        total_impressions += impr
        total_clicks += clk
        total_conversions += tot_conv
        total_attributed_conversions += attr_conv
        total_conversion_value += conv_val
        total_spend += sp

    total_ctr = (Decimal(total_clicks) / Decimal(total_impressions) * Decimal(100)) if total_impressions else Decimal("0")
    total_cvr = (Decimal(total_attributed_conversions) / Decimal(total_clicks) * Decimal(100)) if total_clicks else Decimal("0")
    total_cpa = (total_spend / Decimal(total_attributed_conversions)) if total_attributed_conversions > 0 else None
    total_roas = (total_conversion_value / total_spend) if total_spend > Decimal("0") else None

    return AdSetReportingResponse(
        campaign_id=campaign_id,
        start_date=start,
        end_date=end,
        ad_sets=ad_set_items,
        total_impressions=total_impressions,
        total_clicks=total_clicks,
        total_conversions=total_conversions,
        total_attributed_conversions=total_attributed_conversions,
        total_conversion_value=total_conversion_value,
        total_spend=total_spend,
        currency=report_currency,
        ctr=total_ctr,
        cvr=total_cvr,
        cpa=total_cpa,
        roas=total_roas,
    )


@router.get("/publisher/{publisher_id}/reporting", response_model=PublisherReportingResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def publisher_reporting(
    publisher_id: int,
    user: User = Depends(verified_user),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> PublisherReportingResponse:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    today = date.today()
    start = start_date or today
    end = end_date or today
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    result = await session.execute(
        select(
            func.coalesce(func.sum(PublisherDailyStat.impressions), 0),
            func.coalesce(func.sum(PublisherDailyStat.clicks), 0),
            func.coalesce(func.sum(PublisherDailyStat.conversions), 0),
            func.coalesce(func.sum(PublisherDailyStat.earnings), Decimal("0")),
        ).where(
            PublisherDailyStat.publisher_id == publisher_id,
            PublisherDailyStat.stat_date >= start,
            PublisherDailyStat.stat_date <= end,
        )
    )
    impressions, clicks, conversions, earnings = result.one()
    earnings = Decimal(earnings or 0)
    ecpm = (earnings / Decimal(impressions) * Decimal(1000)) if impressions else Decimal("0")
    
    # Fetch daily breakdown aggregated across all slots per date
    daily_result = await session.execute(
        select(
            PublisherDailyStat.stat_date,
            func.coalesce(func.sum(PublisherDailyStat.impressions), 0).label("impressions"),
            func.coalesce(func.sum(PublisherDailyStat.clicks), 0).label("clicks"),
            func.coalesce(func.sum(PublisherDailyStat.conversions), 0).label("conversions"),
            func.coalesce(func.sum(PublisherDailyStat.earnings), Decimal("0")).label("earnings"),
        ).where(
            PublisherDailyStat.publisher_id == publisher_id,
            PublisherDailyStat.stat_date >= start,
            PublisherDailyStat.stat_date <= end,
        ).group_by(PublisherDailyStat.stat_date).order_by(PublisherDailyStat.stat_date)
    )
    daily_rows = daily_result.all()
    
    breakdown = [
        DailyStatResponse(
            stat_date=row.stat_date,
            impressions=int(row.impressions),
            clicks=int(row.clicks),
            conversions=int(row.conversions),
            earnings=Decimal(row.earnings or 0),
        )
        for row in daily_rows
    ]
    
    return PublisherReportingResponse(
        publisher_id=publisher_id,
        start_date=start,
        end_date=end,
        impressions=int(impressions),
        clicks=int(clicks),
        conversions=int(conversions),
        earnings=earnings,
        ecpm=ecpm,
        daily_breakdown=breakdown,
    )


@router.get("/publisher/{publisher_id}/reporting/export", dependencies=[Depends(require_roles("admin", "publisher"))])
async def export_publisher_reporting(
    publisher_id: int,
    user: User = Depends(verified_user),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Export publisher earnings and delivery reporting to RFC 4180 CSV or JSON (Phase 3 Task H)."""
    report = await publisher_reporting(
        publisher_id=publisher_id,
        user=user,
        start_date=start_date,
        end_date=end_date,
        session=session,
    )
    if format == "json":
        return report

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow([
        "Date",
        "Impressions",
        "Clicks",
        "Conversions",
        "Earnings",
        "ECPM",
        "Currency",
    ])
    for d in report.daily_breakdown:
        d_ecpm = (Decimal(d.earnings) / Decimal(d.impressions) * Decimal(1000)) if d.impressions else Decimal("0")
        writer.writerow([
            d.stat_date.isoformat(),
            d.impressions,
            d.clicks,
            d.conversions,
            f"{d.earnings:.4f}",
            f"{d_ecpm:.4f}",
            "INR",
        ])
    csv_bytes = output.getvalue().encode("utf-8")
    filename = f"publisher_{publisher_id}_report_{report.start_date}_{report.end_date}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/publisher/{publisher_id}/placements/health",
    response_model=PlacementHealthResponse,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Get placement health analysis and platform benchmarks for publisher",
)
async def get_publisher_placement_health(
    publisher_id: int,
    window_days: int = Query(default=30, ge=1, le=90),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> PlacementHealthResponse:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)

    analyzer = PlacementAnalyzerService(session)
    results = await analyzer.analyze_publisher_placements(
        publisher_id=publisher_id,
        window_days=window_days,
    )
    raw_benchmarks = await analyzer.calculate_platform_benchmarks(window_days=window_days)

    benchmarks_schema: dict[str, FormatBenchmarkSchema] = {}
    for fmt_int, bm in raw_benchmarks.items():
        try:
            fmt_name = CreativeType(fmt_int).name
        except Exception:
            fmt_name = str(fmt_int)
        benchmarks_schema[fmt_name] = FormatBenchmarkSchema(
            format=bm.format,
            format_name=fmt_name,
            eligible_slots_count=bm.eligible_slots_count,
            median_ctr=bm.median_ctr,
            median_ecpm=bm.median_ecpm,
        )

    items: list[PlacementHealthItemSchema] = []
    start_date_str = ""
    end_date_str = ""
    for r in results:
        start_date_str = r.start_date
        end_date_str = r.end_date
        try:
            r_fmt_name = CreativeType(r.format).name
        except Exception:
            r_fmt_name = str(r.format)

        bm_dict = r.benchmark
        bm_schema = FormatBenchmarkSchema(
            format=bm_dict.get("format", r.format),
            format_name=r_fmt_name,
            eligible_slots_count=bm_dict.get("eligible_slots_count", 0),
            median_ctr=bm_dict.get("median_ctr"),
            median_ecpm=bm_dict.get("median_ecpm"),
        )
        items.append(
            PlacementHealthItemSchema(
                slot_id=r.slot_id,
                slot_name=r.slot_name,
                format=r.format,
                format_name=r_fmt_name,
                impressions=r.impressions,
                clicks=r.clicks,
                conversions=r.conversions,
                earnings=r.earnings,
                ctr=r.ctr,
                ecpm=r.ecpm,
                verdict=r.verdict,
                recommendation=r.recommendation,
                benchmark=bm_schema,
            )
        )

    if not items:
        today = date.today()
        start = today - timedelta(days=window_days - 1)
        start_date_str = start.isoformat()
        end_date_str = today.isoformat()

    return PlacementHealthResponse(
        publisher_id=publisher_id,
        window_days=window_days,
        start_date=start_date_str,
        end_date=end_date_str,
        total_placements=len(items),
        placements=items,
        benchmarks=benchmarks_schema,
    )


async def _enrich_property_telemetry(session: AsyncSession, properties: list[PublisherProperty]) -> None:
    """Enrich property models with operational last_impression_at and last_request_at."""
    if not properties:
        return
    prop_ids = [p.id for p in properties]
    slots_res = await session.execute(
        select(AdSlot.property_id, AdSlot.slot_id).where(AdSlot.property_id.in_(prop_ids))
    )
    prop_to_slot_ids: dict[int, list[str]] = {}
    for prop_id, slot_id_str in slots_res.all():
        if prop_id:
            prop_to_slot_ids.setdefault(prop_id, []).append(slot_id_str)

    all_slot_ids = [sid for sids in prop_to_slot_ids.values() for sid in sids]
    if all_slot_ids:
        stats_res = await session.execute(
            select(PublisherDailyStat.slot_id, func.max(PublisherDailyStat.updated_at))
            .where(
                PublisherDailyStat.slot_id.in_(all_slot_ids),
                PublisherDailyStat.impressions > 0,
            )
            .group_by(PublisherDailyStat.slot_id)
        )
        slot_last_imp: dict[str, datetime] = {row[0]: row[1] for row in stats_res.all() if row[1]}
        for prop in properties:
            sids = prop_to_slot_ids.get(prop.id, [])
            last_imps = [slot_last_imp[sid] for sid in sids if sid in slot_last_imp]
            prop.last_impression_at = max(last_imps) if last_imps else None
            prop.last_request_at = None
    else:
        for prop in properties:
            prop.last_impression_at = None
            prop.last_request_at = None


@router.get("/publisher/{publisher_id}/property", response_model=list[PublisherPropertyResponse], dependencies=[Depends(require_roles("admin", "publisher"))])
async def list_publisher_properties(
    publisher_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> list[PublisherProperty]:
    _assert_publisher_access(user, publisher_id)
    query = (
        select(PublisherProperty)
        .options(selectinload(PublisherProperty.slots))
        .where(PublisherProperty.publisher_id == publisher_id)
        .order_by(PublisherProperty.id)
    )
    result = await session.execute(query)
    props = list(result.scalars().all())
    await _enrich_property_telemetry(session, props)
    return props


@router.post("/publisher/{publisher_id}/property", response_model=PublisherPropertyResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "publisher"))])
async def create_publisher_property(
    publisher_id: int, payload: PublisherPropertyCreate, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> PublisherProperty:
    from liteads.ad_server.services.publisher_verification import (
        normalize_property_domain,
        generate_property_verification_token,
    )

    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)

    try:
        norm_domain = normalize_property_domain(payload.domain)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Reject if this domain is actively verified by a different publisher
    other_ver_stmt = select(PublisherProperty).where(
        PublisherProperty.domain == norm_domain,
        PublisherProperty.verification_status == "VERIFIED",
        PublisherProperty.publisher_id != publisher_id,
    )
    other_res = await session.execute(other_ver_stmt)
    if other_res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Domain is currently verified by another publisher account. Contact support to dispute ownership.",
        )

    # Check if this publisher already has this property registered
    existing_stmt = (
        select(PublisherProperty)
        .options(selectinload(PublisherProperty.slots))
        .where(
            PublisherProperty.domain == norm_domain,
            PublisherProperty.publisher_id == publisher_id,
        )
    )
    existing_res = await session.execute(existing_stmt)
    existing = existing_res.scalar_one_or_none()
    if existing is not None:
        await _enrich_property_telemetry(session, [existing])
        return existing

    data = payload.model_dump()
    data["publisher_id"] = publisher_id
    data["domain"] = norm_domain
    data["verification_status"] = "PENDING"
    data["verification_method"] = "DNS_TXT"
    data["verification_token"] = generate_property_verification_token()
    data["challenge_expires_at"] = datetime.now(timezone.utc) + timedelta(hours=48)
    data["verified_at"] = None
    data["last_error"] = None

    property_obj = PublisherProperty(**data)
    session.add(property_obj)
    await session.commit()

    # Eagerly reload with slots so serialization never triggers lazy load
    stmt_reload = (
        select(PublisherProperty)
        .options(selectinload(PublisherProperty.slots))
        .where(PublisherProperty.id == property_obj.id)
    )
    res_reload = await session.execute(stmt_reload)
    property_obj = res_reload.scalar_one()
    await _enrich_property_telemetry(session, [property_obj])
    return property_obj


@router.get("/publisher/property/{property_id}", response_model=PublisherPropertyResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def get_publisher_property(
    property_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> PublisherProperty:
    stmt = (
        select(PublisherProperty)
        .options(selectinload(PublisherProperty.slots))
        .where(PublisherProperty.id == property_id)
    )
    res = await session.execute(stmt)
    property_obj = res.scalar_one_or_none()
    if property_obj is None:
        raise _not_found("PublisherProperty", property_id)
    _assert_publisher_access(user, property_obj.publisher_id)
    await _enrich_property_telemetry(session, [property_obj])
    return property_obj


@router.patch("/publisher/property/{property_id}", response_model=PublisherPropertyResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def update_publisher_property(
    property_id: int, payload: PublisherPropertyUpdate, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> PublisherProperty:
    from liteads.ad_server.services.publisher_verification import (
        normalize_property_domain,
        generate_property_verification_token,
    )

    stmt = (
        select(PublisherProperty)
        .options(selectinload(PublisherProperty.slots))
        .where(PublisherProperty.id == property_id)
    )
    res = await session.execute(stmt)
    property_obj = res.scalar_one_or_none()
    if property_obj is None:
        raise _not_found("PublisherProperty", property_id)
    _assert_publisher_access(user, property_obj.publisher_id)

    updates = payload.model_dump(exclude_unset=True)

    # Strip client attempts to manipulate verification state
    for forbidden_field in ("verification_status", "verified_at", "last_checked_at", "verification_token"):
        updates.pop(forbidden_field, None)

    if "domain" in updates and updates["domain"] is not None:
        try:
            norm_domain = normalize_property_domain(updates["domain"])
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

        if norm_domain != property_obj.domain:
            # Check collision
            other_ver_stmt = select(PublisherProperty).where(
                PublisherProperty.domain == norm_domain,
                PublisherProperty.verification_status == "VERIFIED",
                PublisherProperty.publisher_id != property_obj.publisher_id,
            )
            other_res = await session.execute(other_ver_stmt)
            if other_res.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Domain is currently verified by another publisher account.",
                )

            property_obj.domain = norm_domain
            property_obj.verification_status = "PENDING"
            property_obj.verification_token = generate_property_verification_token()
            property_obj.challenge_expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
            property_obj.verified_at = None
            property_obj.last_error = None

    if "name" in updates and updates["name"] is not None:
        property_obj.name = updates["name"]
    if "status" in updates and updates["status"] is not None:
        prev_status = property_obj.status
        property_obj.status = updates["status"]
        if property_obj.status != prev_status:
            session.add(
                AuditLog(
                    user_id=user.id,
                    action="property_status_updated",
                    details=json.dumps({
                        "property_id": property_obj.id,
                        "publisher_id": property_obj.publisher_id,
                        "previous_status": int(prev_status) if prev_status is not None else None,
                        "new_status": int(property_obj.status) if property_obj.status is not None else None,
                        "actor_email": user.email,
                    }),
                )
            )

    await session.commit()

    # Eagerly reload with slots
    stmt_reload = (
        select(PublisherProperty)
        .options(selectinload(PublisherProperty.slots))
        .where(PublisherProperty.id == property_obj.id)
    )
    res_reload = await session.execute(stmt_reload)
    property_obj = res_reload.scalar_one()
    await _enrich_property_telemetry(session, [property_obj])
    return property_obj


@router.post("/publisher/property/{property_id}/challenge", response_model=PropertyChallengeResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def create_property_challenge(
    property_id: int,
    payload: PropertyChallengeRequest,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> PropertyChallengeResponse:
    """Generate or renew an unpredictable verification challenge on a property."""
    from liteads.ad_server.services.publisher_verification import PublisherVerificationService

    property_obj = await session.get(PublisherProperty, property_id)
    if property_obj is None:
        raise _not_found("PublisherProperty", property_id)
    _assert_publisher_access(user, property_obj.publisher_id)

    service = PublisherVerificationService(session)
    updated_prop = await service.create_challenge(property_obj, method=payload.method)

    rec_name = f"_rivinity-challenge.{updated_prop.domain}"
    meta_tag = f'<meta name="rivinity-site-verification" content="{updated_prop.verification_token}">'
    if payload.method == "DNS_TXT":
        instructions = f"Add a DNS TXT record for '{rec_name}' with value '{updated_prop.verification_token}'."
    else:
        instructions = f"Add the meta tag '{meta_tag}' into the <head> section of your website homepage."

    return PropertyChallengeResponse(
        property_id=updated_prop.id,
        domain=updated_prop.domain,
        verification_method=updated_prop.verification_method,
        verification_token=updated_prop.verification_token or "",
        dns_record_name=rec_name,
        dns_record_value=updated_prop.verification_token or "",
        html_meta_tag=meta_tag,
        challenge_expires_at=updated_prop.challenge_expires_at or (datetime.now(timezone.utc) + timedelta(hours=48)),
        instructions=instructions,
    )


@router.post("/publisher/property/{property_id}/verify", response_model=PublisherPropertyResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def verify_publisher_property(
    property_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> PublisherProperty:
    """Trigger server-side verification of domain ownership for a publisher property."""
    from liteads.ad_server.services.publisher_verification import PublisherVerificationService

    property_obj = await session.get(PublisherProperty, property_id)
    if property_obj is None:
        raise _not_found("PublisherProperty", property_id)
    _assert_publisher_access(user, property_obj.publisher_id)

    service = PublisherVerificationService(session)
    result_prop = await service.verify_property(property_obj)

    # Eagerly reload with slots so serialization never triggers lazy load / MissingGreenlet
    stmt_reload = (
        select(PublisherProperty)
        .options(selectinload(PublisherProperty.slots))
        .where(PublisherProperty.id == result_prop.id)
    )
    res_reload = await session.execute(stmt_reload)
    result_prop = res_reload.scalar_one()
    await _enrich_property_telemetry(session, [result_prop])
    return result_prop


@router.get("/slot/{slot_id}/tag", dependencies=[Depends(require_roles("admin", "publisher"))])
async def generate_slot_tag(
    slot_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
):
    slot = await session.get(AdSlot, slot_id)
    if slot is None:
        raise _not_found("Slot", slot_id)
    _assert_publisher_access(user, slot.publisher_id)
    
    script = f'''<script async src="http://localhost:8000/api/v1/sdk.js"></script>
<div id="liteads-ad-{slot.slot_id}" class="liteads-ad-slot" data-slot-id="{slot.slot_id}"></div>
<script>
  (window.LiteAds = window.LiteAds || []).push({{
    slotId: "{slot.slot_id}",
    containerId: "liteads-ad-{slot.slot_id}"
  }});
</script>'''
    return {"html": script}


# ── Phase 4.2 — Publisher API key management ────────────────────────────────────

@router.post(
    "/publisher/{publisher_id}/keys",
    response_model=ApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Create an API key for a publisher",
)
async def create_api_key_for_publisher(
    publisher_id: int,
    payload: ApiKeyCreate,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyResponse:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)

    db_key, raw_key = await ApiKeyService.create_api_key(session, publisher_id, payload)
    return ApiKeyResponse(
        id=db_key.id,
        publisher_id=db_key.publisher_id,
        name=db_key.name,
        key_prefix=db_key.key_prefix,
        is_sandbox=db_key.is_sandbox,
        rate_limit_per_minute=db_key.rate_limit_per_minute,
        daily_quota=db_key.daily_quota,
        raw_key=raw_key,
        revoked_at=db_key.revoked_at,
        created_at=db_key.created_at,
        updated_at=db_key.updated_at,
    )


@router.post(
    "/publisher/keys",
    response_model=PublisherApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Create a publisher API key",
    description=(
        "Generates a new API key for authenticating ad/event API calls from "
        "third-party integrations and AI-platform server-to-server callers. "
        "**The raw key is returned exactly once** — copy it now."
    ),
)
async def create_publisher_api_key(
    payload: PublisherApiKeyCreate,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> PublisherApiKeyCreateResponse:
    pub_id = payload.publisher_id or user.id
    _assert_publisher_access(user, pub_id)
    if await session.get(Publisher, pub_id) is None:
        raise _not_found("Publisher", pub_id)

    key_name = payload.name or payload.label or "Default Key"
    db_key, raw_key = await ApiKeyService.create_api_key(
        session,
        pub_id,
        ApiKeyCreate(
            name=key_name,
            is_sandbox=payload.is_sandbox,
            rate_limit_per_minute=payload.rate_limit_per_minute,
            daily_quota=payload.daily_quota,
        ),
    )
    return PublisherApiKeyCreateResponse(
        id=db_key.id,
        publisher_id=db_key.publisher_id,
        name=db_key.name,
        label=db_key.name,
        key_prefix=db_key.key_prefix,
        is_sandbox=db_key.is_sandbox,
        rate_limit_per_minute=db_key.rate_limit_per_minute,
        daily_quota=db_key.daily_quota,
        status=db_key.status,
        created_at=db_key.created_at,
        raw_key=raw_key,
    )


@router.get(
    "/publisher/{publisher_id}/keys",
    response_model=list[ApiKeyResponse],
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="List publisher API keys",
)
async def list_publisher_api_keys(
    publisher_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyResponse]:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)

    keys = await ApiKeyService.list_api_keys(session, publisher_id)
    return [
        ApiKeyResponse(
            id=k.id,
            publisher_id=k.publisher_id,
            name=k.name,
            key_prefix=k.key_prefix,
            is_sandbox=k.is_sandbox,
            rate_limit_per_minute=k.rate_limit_per_minute,
            daily_quota=k.daily_quota,
            last_used_at=k.last_used_at,
            revoked_at=k.revoked_at,
            created_at=k.created_at,
            updated_at=k.updated_at,
        )
        for k in keys
    ]


@router.delete(
    "/publisher/{publisher_id}/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Revoke a publisher API key by publisher and key id",
)
async def revoke_publisher_api_key_by_pub(
    publisher_id: int,
    key_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _assert_publisher_access(user, publisher_id)
    success = await ApiKeyService.revoke_api_key(session, publisher_id, key_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"API key {key_id} not found or already revoked")


@router.delete(
    "/publisher/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Revoke a publisher API key",
)
async def revoke_publisher_api_key(
    key_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    api_key = await session.get(PublisherApiKey, key_id)
    if api_key is None:
        raise HTTPException(status_code=404, detail=f"API key {key_id} not found")
    _assert_publisher_access(user, api_key.publisher_id)
    api_key.status = 0  # revoked
    await session.commit()


@router.post(
    "/publisher/{publisher_id}/keys/{key_id}/rotate",
    response_model=PublisherApiKeyCreateResponse,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Rotate a publisher API key",
    description="Revokes the existing key and generates a new active key with the same configuration. The raw key is returned once.",
)
async def rotate_publisher_api_key_by_pub(
    publisher_id: int,
    key_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> PublisherApiKeyCreateResponse:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)

    new_key, raw_key = await ApiKeyService.rotate_api_key(session, publisher_id, key_id)
    return PublisherApiKeyCreateResponse(
        id=new_key.id,
        publisher_id=new_key.publisher_id,
        name=new_key.name,
        label=new_key.name,
        key_prefix=new_key.key_prefix,
        is_sandbox=new_key.is_sandbox,
        rate_limit_per_minute=new_key.rate_limit_per_minute,
        daily_quota=new_key.daily_quota,
        status=new_key.status,
        created_at=new_key.created_at,
        raw_key=raw_key,
    )


@router.post(
    "/publisher/keys/{key_id}/rotate",
    response_model=PublisherApiKeyCreateResponse,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Rotate a publisher API key for current user",
)
async def rotate_publisher_api_key(
    key_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> PublisherApiKeyCreateResponse:
    api_key = await session.get(PublisherApiKey, key_id)
    if api_key is None:
        raise HTTPException(status_code=404, detail=f"API key {key_id} not found")
    _assert_publisher_access(user, api_key.publisher_id)

    new_key, raw_key = await ApiKeyService.rotate_api_key(session, api_key.publisher_id, key_id)
    return PublisherApiKeyCreateResponse(
        id=new_key.id,
        publisher_id=new_key.publisher_id,
        name=new_key.name,
        label=new_key.name,
        key_prefix=new_key.key_prefix,
        is_sandbox=new_key.is_sandbox,
        rate_limit_per_minute=new_key.rate_limit_per_minute,
        daily_quota=new_key.daily_quota,
        status=new_key.status,
        created_at=new_key.created_at,
        raw_key=raw_key,
    )


@router.get(
    "/publisher/{publisher_id}/keys/usage",
    response_model=list[ApiKeyUsageResponse],
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Get real-time and aggregate usage for all publisher API keys",
)
async def get_publisher_keys_usage(
    publisher_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyUsageResponse]:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    return await RateLimitService.get_publisher_keys_usage(session, publisher_id)


@router.get(
    "/publisher/{publisher_id}/keys/{key_id}/usage",
    response_model=ApiKeyUsageResponse,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Get real-time and aggregate usage for a specific publisher API key",
)
async def get_publisher_key_usage(
    publisher_id: int,
    key_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyUsageResponse:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    api_key = await session.get(PublisherApiKey, key_id)
    if not api_key or api_key.publisher_id != publisher_id:
        raise HTTPException(status_code=404, detail=f"API key {key_id} not found")
    return await RateLimitService.get_key_usage(session, api_key)


@router.get(
    "/publisher/keys/usage",
    response_model=list[ApiKeyUsageResponse],
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Get usage for current authenticated publisher's API keys",
)
async def get_current_publisher_keys_usage(
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyUsageResponse]:
    if not user.publisher_id:
        raise HTTPException(status_code=400, detail="User is not associated with a publisher")
    return await RateLimitService.get_publisher_keys_usage(session, user.publisher_id)



@router.get("/campaign/{campaign_id}/suggestions", response_model=list[SuggestionResponse], dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_campaign_suggestions(
    campaign_id: int, 
    user: User = Depends(verified_user), 
    session: AsyncSession = Depends(get_session)
) -> list[Suggestion]:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    _assert_advertiser_access(user, campaign.advertiser_id)
    result = await session.execute(
        select(Suggestion).where(Suggestion.campaign_id == campaign_id).where(Suggestion.status == "PENDING").order_by(Suggestion.created_at.desc())
    )
    return list(result.scalars().all())


@router.patch("/suggestion/{suggestion_id}", response_model=SuggestionResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def update_suggestion_status(
    suggestion_id: int,
    payload: SuggestionStatusPatch,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session)
) -> Suggestion:
    if payload.status not in ("APPLIED", "REJECTED"):
        raise HTTPException(status_code=400, detail="Invalid status transition")
        
    # 1. Peek suggestion and campaign without lock to determine advertiser ownership
    result_peek = await session.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id)
    )
    suggestion_peek = result_peek.scalar_one_or_none()
    if suggestion_peek is None:
        raise _not_found("Suggestion", suggestion_id)
        
    camp_peek = await session.get(Campaign, suggestion_peek.campaign_id)
    if camp_peek is None:
        raise _not_found("Campaign", suggestion_peek.campaign_id)
        
    _assert_advertiser_access(user, camp_peek.advertiser_id)

    # 2. Acquire canonical row locks: Advertiser -> Campaign -> Suggestion
    advertiser = await session.get(Advertiser, camp_peek.advertiser_id, with_for_update=True, populate_existing=True)
    if advertiser is None:
        raise _not_found("Advertiser", camp_peek.advertiser_id)

    campaign = await session.get(Campaign, suggestion_peek.campaign_id, with_for_update=True, populate_existing=True)
    if campaign is None or campaign.advertiser_id != advertiser.id:
        raise _not_found("Campaign", suggestion_peek.campaign_id)

    result = await session.execute(
        select(Suggestion).where(Suggestion.id == suggestion_id).with_for_update().execution_options(populate_existing=True)
    )
    suggestion = result.scalar_one_or_none()
    if suggestion is None:
        raise _not_found("Suggestion", suggestion_id)
    
    if suggestion.status != "PENDING":
        raise HTTPException(status_code=400, detail="Suggestion is no longer PENDING")
        
    if payload.status == "REJECTED":
        suggestion.status = "REJECTED"
        suggestion.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
        await session.refresh(suggestion)
        return suggestion

    action_name = suggestion.action.get("action")
    if action_name == "improve_creative":
        suggestion.status = "APPLIED"
        suggestion.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
        await session.refresh(suggestion)
        return suggestion

    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="Manual approval",
        action=action_name,
        confidence=suggestion.action.get("confidence", 1.0),
        autopilot=campaign.autopilot,
        proposed_budget_delta=suggestion.action.get("proposed_budget_delta"),
        operation_type=suggestion.action.get("operation_type")
    )
    
    executor = OptimizationExecutor(session)
    try:
        await executor._execute_autopilot_on(
            campaign=campaign,
            policy_result=policy_result,
            action_payload=suggestion.action,
            target_payload=suggestion.action.get("target_payload"),
            actor=f"user:{user.id}",
            enforce_cooldown=False,
            auto_commit=False,
            suggestion=suggestion,
        )
        await session.commit()
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
        
    await session.refresh(suggestion)
    return suggestion


# ── Advertiser API Key Endpoints (Phase 1) ───────────────────────────────────

@router.post(
    "/advertiser/{advertiser_id}/keys",
    response_model=AdvertiserApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin", "advertiser"))],
    summary="Create an advertiser API key / signing credential",
    description="Generates an API key and HMAC secret for S2S conversion postbacks. Returned raw exactly once.",
)
async def create_advertiser_api_key(
    advertiser_id: int,
    payload: AdvertiserApiKeyCreate,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> AdvertiserApiKeyCreateResponse:
    _assert_advertiser_access(user, advertiser_id)
    adv = await session.get(Advertiser, advertiser_id)
    if not adv:
        raise _not_found("Advertiser", advertiser_id)

    db_key, raw_key, raw_secret = await AdvertiserApiKeyService.create_api_key(
        db=session,
        advertiser_id=advertiser_id,
        name=payload.name,
        campaign_id=payload.campaign_id,
        is_sandbox=payload.is_sandbox,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        daily_quota=payload.daily_quota,
        user_id=user.id,
    )
    return AdvertiserApiKeyCreateResponse(
        id=db_key.id,
        advertiser_id=db_key.advertiser_id,
        campaign_id=db_key.campaign_id,
        name=db_key.name,
        key_prefix=db_key.key_prefix,
        is_sandbox=db_key.is_sandbox,
        rate_limit_per_minute=db_key.rate_limit_per_minute,
        daily_quota=db_key.daily_quota,
        created_at=db_key.created_at,
        raw_key=raw_key,
        raw_secret=raw_secret,
    )


@router.get(
    "/advertiser/{advertiser_id}/keys",
    response_model=list[AdvertiserApiKeyResponse],
    dependencies=[Depends(require_roles("admin", "advertiser"))],
    summary="List advertiser API keys",
)
async def list_advertiser_api_keys(
    advertiser_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> list[AdvertiserApiKeyResponse]:
    _assert_advertiser_access(user, advertiser_id)
    adv = await session.get(Advertiser, advertiser_id)
    if not adv:
        raise _not_found("Advertiser", advertiser_id)

    keys = await AdvertiserApiKeyService.list_api_keys(session, advertiser_id)
    return [AdvertiserApiKeyResponse.model_validate(k) for k in keys]


@router.post(
    "/advertiser/{advertiser_id}/keys/{key_id}/rotate",
    response_model=AdvertiserApiKeyCreateResponse,
    dependencies=[Depends(require_roles("admin", "advertiser"))],
    summary="Rotate an advertiser API key",
)
async def rotate_advertiser_api_key(
    advertiser_id: int,
    key_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> AdvertiserApiKeyCreateResponse:
    _assert_advertiser_access(user, advertiser_id)
    adv = await session.get(Advertiser, advertiser_id)
    if not adv:
        raise _not_found("Advertiser", advertiser_id)

    new_key, raw_key, raw_secret = await AdvertiserApiKeyService.rotate_api_key(
        db=session,
        advertiser_id=advertiser_id,
        key_id=key_id,
        user_id=user.id,
    )
    return AdvertiserApiKeyCreateResponse(
        id=new_key.id,
        advertiser_id=new_key.advertiser_id,
        campaign_id=new_key.campaign_id,
        name=new_key.name,
        key_prefix=new_key.key_prefix,
        is_sandbox=new_key.is_sandbox,
        rate_limit_per_minute=new_key.rate_limit_per_minute,
        daily_quota=new_key.daily_quota,
        created_at=new_key.created_at,
        raw_key=raw_key,
        raw_secret=raw_secret,
    )


@router.delete(
    "/advertiser/{advertiser_id}/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin", "advertiser"))],
    summary="Revoke an advertiser API key",
)
async def revoke_advertiser_api_key(
    advertiser_id: int,
    key_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _assert_advertiser_access(user, advertiser_id)
    success = await AdvertiserApiKeyService.revoke_api_key(
        db=session,
        advertiser_id=advertiser_id,
        key_id=key_id,
        user_id=user.id,
    )
    if not success:
        raise HTTPException(status_code=404, detail="API key not found or already revoked")


# ── Phase 6: Optimization & Laya Operator Governance Endpoints ───────────────

@router.get(
    "/optimization/status",
    response_model=OptimizationStatusResponse,
    dependencies=[Depends(require_roles("admin"))],
    summary="Get global optimization and Laya sidecar status",
)
async def get_optimization_status() -> OptimizationStatusResponse:
    settings = get_settings()
    from liteads.ad_server.services.optimization_scheduler import optimization_loop
    return OptimizationStatusResponse(
        global_mode=settings.optimization.mode,
        kill_switch=settings.optimization.kill_switch,
        enabled=settings.optimization.enabled,
        pilot_campaign_ids=settings.optimization.pilot_campaign_ids,
        pilot_count=len(settings.optimization.pilot_campaign_ids),
        cooldown_seconds=settings.optimization.cooldown_seconds,
        max_daily_changes=settings.optimization.max_daily_changes_per_campaign,
        pilot_max_budget_delta_amount=settings.optimization.pilot_max_budget_delta_amount,
        laya_base_url=settings.laya.base_url,
        laya_enabled=settings.laya.enabled,
        loop_is_running=optimization_loop.is_running,
    )


@router.get(
    "/optimization/decisions",
    response_model=list[OptimizationDecisionResponse],
    dependencies=[Depends(require_roles("admin", "advertiser"))],
    summary="List optimization decision audit records",
)
async def list_optimization_decisions(
    campaign_id: int | None = Query(default=None),
    outcome: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> list[OptimizationDecision]:
    stmt = select(OptimizationDecision).order_by(OptimizationDecision.id.desc()).limit(limit)

    if campaign_id is not None:
        camp = await session.get(Campaign, campaign_id)
        if not camp:
            raise _not_found("Campaign", campaign_id)
        _assert_advertiser_access(user, camp.advertiser_id)
        stmt = stmt.where(OptimizationDecision.campaign_id == campaign_id)
    elif user.role != "admin":
        adv_stmt = select(Campaign.id).where(Campaign.advertiser_id == user.advertiser_id)
        adv_camps = (await session.execute(adv_stmt)).scalars().all()
        stmt = stmt.where(OptimizationDecision.campaign_id.in_(adv_camps))

    if outcome is not None:
        stmt = stmt.where(OptimizationDecision.outcome == outcome)
    if mode is not None:
        stmt = stmt.where(OptimizationDecision.mode == mode)

    res = await session.execute(stmt)
    return list(res.scalars().all())


@router.post(
    "/optimization/decisions/{decision_id}/revert",
    response_model=DecisionRevertResponse,
    dependencies=[Depends(require_roles("admin", "advertiser"))],
    summary="Revert an applied optimization action",
)
async def revert_optimization_decision_endpoint(
    decision_id: str,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> DecisionRevertResponse:
    stmt = select(OptimizationDecision).where(OptimizationDecision.decision_id == decision_id)
    res = await session.execute(stmt)
    decision = res.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail=f"Optimization decision {decision_id} not found")

    camp = await session.get(Campaign, decision.campaign_id)
    if not camp:
        raise _not_found("Campaign", decision.campaign_id)
    _assert_advertiser_access(user, camp.advertiser_id)

    executor = OptimizationExecutor(session)
    try:
        detail = await executor.revert_decision(
            decision_id=decision_id,
            actor=user.email or f"user_{user.id}",
            auto_commit=True,
        )
        return DecisionRevertResponse(
            decision_id=decision_id,
            campaign_id=camp.id,
            status="reverted",
            detail=detail,
        )
    except OptimizationExecutionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/campaign/{campaign_id}/optimization-state",
    response_model=CampaignOptimizationStateResponse,
    dependencies=[Depends(require_roles("admin", "advertiser"))],
    summary="Get detailed optimization and autopilot state for a campaign",
)
async def get_campaign_optimization_state(
    campaign_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> CampaignOptimizationStateResponse:
    camp = await session.get(Campaign, campaign_id)
    if not camp:
        raise _not_found("Campaign", campaign_id)
    _assert_advertiser_access(user, camp.advertiser_id)

    settings = get_settings()
    pilot_ids = settings.optimization.pilot_campaign_ids
    is_pilot_eligible = bool(pilot_ids and camp.id in pilot_ids)
    effective_mode = settings.optimization.mode

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cooldown = timedelta(seconds=settings.optimization.cooldown_seconds)
    day_ago = now - timedelta(hours=24)

    recent_log_stmt = select(CampaignChangeLog).where(
        CampaignChangeLog.campaign_id == camp.id,
        CampaignChangeLog.created_at >= now - cooldown,
    )
    in_cooldown = (await session.execute(recent_log_stmt)).scalars().first() is not None

    count_stmt = select(func.count(CampaignChangeLog.id)).where(
        CampaignChangeLog.campaign_id == camp.id,
        CampaignChangeLog.created_at >= day_ago,
    )
    daily_changes_count = (await session.execute(count_stmt)).scalar() or 0

    last_dec_stmt = (
        select(OptimizationDecision)
        .where(OptimizationDecision.campaign_id == camp.id)
        .order_by(OptimizationDecision.id.desc())
        .limit(1)
    )
    last_dec = (await session.execute(last_dec_stmt)).scalar_one_or_none()

    can_revert = bool(last_dec and last_dec.outcome == "applied")
    last_dec_resp = OptimizationDecisionResponse.model_validate(last_dec) if last_dec else None

    return CampaignOptimizationStateResponse(
        campaign_id=camp.id,
        campaign_name=camp.name,
        autopilot=bool(camp.autopilot),
        is_pilot_eligible=is_pilot_eligible,
        effective_mode=effective_mode,
        in_cooldown=in_cooldown,
        daily_changes_count=daily_changes_count,
        max_daily_changes=settings.optimization.max_daily_changes_per_campaign,
        impressions=camp.impressions,
        clicks=camp.clicks,
        conversions=camp.conversions,
        spent_today=camp.spent_today,
        spent_total=camp.spent_total,
        daily_budget=camp.budget_daily,
        last_decision=last_dec_resp,
        can_revert=can_revert,
    )


# ── Phase 7 Operations, Exceptions & Publisher Safety Endpoints ───────────────

@router.get(
    "/admin/audit-logs",
    response_model=list[AuditLogResponse],
    dependencies=[Depends(require_roles("admin"))],
    summary="Query immutable audit log of administrative and sensitive actions",
)
async def list_audit_logs(
    action: str | None = Query(default=None, description="Filter by action name"),
    user_id: int | None = Query(default=None, description="Filter by actor user ID"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    stmt = stmt.offset(offset).limit(limit)
    res = await session.execute(stmt)
    return list(res.scalars().all())


@router.get(
    "/admin/dashboard/operational-exceptions",
    response_model=OperationalExceptionsResponse,
    dependencies=[Depends(require_roles("admin"))],
    summary="Operational exception queues and SLA status tracking",
)
async def get_operational_exceptions(
    session: AsyncSession = Depends(get_session),
) -> OperationalExceptionsResponse:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_integrations: list[OperationalAlert] = []
    no_fill_slots: list[OperationalAlert] = []
    unusual_ctrs: list[OperationalAlert] = []
    spend_spikes: list[OperationalAlert] = []
    webhook_payout_failures: list[OperationalAlert] = []
    moderation_queue: list[SLAItem] = []
    finance_queue: list[SLAItem] = []

    # 1. Moderation Exception Queue with SLA (24h window)
    pending_creatives_stmt = (
        select(Creative)
        .where(Creative.moderation_status == ModerationStatus.PENDING_REVIEW)
        .order_by(Creative.created_at.asc())
        .limit(50)
    )
    pending_creatives = (await session.execute(pending_creatives_stmt)).scalars().all()
    for cr in pending_creatives:
        created = cr.created_at.replace(tzinfo=None) if cr.created_at else now
        deadline = created + timedelta(hours=24)
        rem_sec = (deadline - now).total_seconds()
        rem_min = int(rem_sec // 60)
        if rem_sec < 0:
            sla_status = "BREACHED"
        elif rem_sec <= 6 * 3600:
            sla_status = "AT_RISK"
        else:
            sla_status = "ON_TRACK"

        moderation_queue.append(
            SLAItem(
                id=cr.id,
                type="creative_moderation",
                title=f"Creative #{cr.id}: {cr.title}",
                entity_name=cr.title,
                created_at=created,
                sla_deadline=deadline,
                sla_status=sla_status,
                time_remaining_minutes=rem_min,
                details={
                    "campaign_id": cr.campaign_id,
                    "landing_url": cr.landing_url,
                    "moderation_reason": cr.moderation_reason,
                },
            )
        )

    # 2. Finance Exception Queue with SLA (48h window)
    pending_payouts_stmt = (
        select(PublisherPayout)
        .where(PublisherPayout.status == "requested")
        .order_by(PublisherPayout.created_at.asc())
        .limit(50)
    )
    pending_payouts = (await session.execute(pending_payouts_stmt)).scalars().all()
    for po in pending_payouts:
        created = po.created_at.replace(tzinfo=None) if po.created_at else now
        deadline = created + timedelta(hours=48)
        rem_sec = (deadline - now).total_seconds()
        rem_min = int(rem_sec // 60)
        if rem_sec < 0:
            sla_status = "BREACHED"
        elif rem_sec <= 12 * 3600:
            sla_status = "AT_RISK"
        else:
            sla_status = "ON_TRACK"

        finance_queue.append(
            SLAItem(
                id=po.id,
                type="payout_review",
                title=f"Payout #{po.id} (Publisher {po.publisher_id})",
                entity_name=f"₹{po.amount}",
                created_at=created,
                sla_deadline=deadline,
                sla_status=sla_status,
                time_remaining_minutes=rem_min,
                details={
                    "publisher_id": po.publisher_id,
                    "amount": str(po.amount),
                    "reference_id": po.reference_id,
                },
            )
        )

    open_disputes_stmt = (
        select(BillingDispute)
        .where(BillingDispute.status == "OPEN")
        .order_by(BillingDispute.created_at.asc())
        .limit(50)
    )
    open_disputes = (await session.execute(open_disputes_stmt)).scalars().all()
    for d in open_disputes:
        created = d.created_at.replace(tzinfo=None) if d.created_at else now
        deadline = created + timedelta(hours=48)
        rem_sec = (deadline - now).total_seconds()
        rem_min = int(rem_sec // 60)
        if rem_sec < 0:
            sla_status = "BREACHED"
        elif rem_sec <= 12 * 3600:
            sla_status = "AT_RISK"
        else:
            sla_status = "ON_TRACK"

        finance_queue.append(
            SLAItem(
                id=d.id,
                type="dispute",
                title=f"Dispute #{d.id}: {d.dispute_type}",
                entity_name=f"₹{d.disputed_amount}",
                created_at=created,
                sla_deadline=deadline,
                sla_status=sla_status,
                time_remaining_minutes=rem_min,
                details={
                    "dispute_type": d.dispute_type,
                    "advertiser_id": d.advertiser_id,
                    "publisher_id": d.publisher_id,
                },
            )
        )

    # 3. Stale Integrations
    stale_props_stmt = (
        select(PublisherProperty)
        .where(
            PublisherProperty.verification_status == "PENDING",
            PublisherProperty.challenge_expires_at != None,
            PublisherProperty.challenge_expires_at < now,
        )
        .limit(20)
    )
    stale_props = (await session.execute(stale_props_stmt)).scalars().all()
    for p in stale_props:
        stale_integrations.append(
            OperationalAlert(
                code="EXPIRED_PROPERTY_VERIFICATION",
                severity="medium",
                title=f"Expired Verification: {p.domain}",
                description=f"Property {p.name} (#{p.id}) verification challenge expired without completion.",
                entity_type="property",
                entity_id=p.id,
            )
        )

    # 4. Unusual CTRs
    high_ctr_stmt = (
        select(Campaign)
        .where(
            Campaign.status == Status.ACTIVE,
            Campaign.impressions >= 50,
        )
        .limit(20)
    )
    high_ctr_camps = (await session.execute(high_ctr_stmt)).scalars().all()
    for c in high_ctr_camps:
        ctr = (c.clicks / c.impressions) if c.impressions > 0 else 0
        if ctr > 0.20:
            unusual_ctrs.append(
                OperationalAlert(
                    code="UNUSUAL_CAMPAIGN_CTR",
                    severity="high" if ctr > 0.35 else "medium",
                    title=f"Elevated CTR: {c.name} ({round(ctr * 100, 1)}%)",
                    description=f"Campaign #{c.id} recorded {c.clicks} clicks out of {c.impressions} impressions.",
                    entity_type="campaign",
                    entity_id=c.id,
                )
            )

    # 5. Spend Spikes (burned >= 80% of daily budget)
    active_camps_stmt = (
        select(Campaign)
        .where(
            Campaign.status == Status.ACTIVE,
            Campaign.budget_daily > Decimal("0"),
        )
        .limit(50)
    )
    active_camps = (await session.execute(active_camps_stmt)).scalars().all()
    for c in active_camps:
        if c.spent_today >= (c.budget_daily * Decimal("0.8")):
            spend_spikes.append(
                OperationalAlert(
                    code="BUDGET_PACING_SPIKE",
                    severity="high" if c.spent_today >= c.budget_daily else "medium",
                    title=f"Budget Burn Spike: {c.name}",
                    description=f"Campaign #{c.id} reached {round(float(c.spent_today / c.budget_daily) * 100)}% of daily budget (₹{c.spent_today}/₹{c.budget_daily}).",
                    entity_type="campaign",
                    entity_id=c.id,
                )
            )

    # 6. Webhook / Payout Failures
    failed_payouts_stmt = (
        select(PublisherPayout)
        .where(PublisherPayout.status.in_(("failed", "rejected")))
        .order_by(PublisherPayout.created_at.desc())
        .limit(20)
    )
    failed_payouts = (await session.execute(failed_payouts_stmt)).scalars().all()
    for fp in failed_payouts:
        webhook_payout_failures.append(
            OperationalAlert(
                code="PAYOUT_EXECUTION_FAILURE",
                severity="high" if fp.status == "failed" else "medium",
                title=f"Payout {fp.status.capitalize()}: #{fp.id}",
                description=f"Payout of ₹{fp.amount} to Publisher #{fp.publisher_id} {fp.status}: {fp.notes or 'No reason provided'}",
                entity_type="payout",
                entity_id=fp.id,
            )
        )

    # 7. No-fill slots
    slots_stmt = select(AdSlot).where(AdSlot.status == Status.ACTIVE).limit(20)
    slots = (await session.execute(slots_stmt)).scalars().all()
    for s in slots:
        ev_stmt = select(func.count(AdEvent.id)).where(AdEvent.slot_id == s.slot_id)
        ev_count = (await session.execute(ev_stmt)).scalar() or 0
        if ev_count == 0 and s.created_at and (now - s.created_at.replace(tzinfo=None)).days >= 3:
            no_fill_slots.append(
                OperationalAlert(
                    code="ZERO_FILL_PLACEMENT",
                    severity="low",
                    title=f"Zero Impressions: {s.name} ({s.slot_id})",
                    description=f"Slot '{s.name}' has recorded zero impressions since creation.",
                    entity_type="slot",
                    entity_id=s.id,
                )
            )

    total_count = (
        len(stale_integrations)
        + len(no_fill_slots)
        + len(unusual_ctrs)
        + len(spend_spikes)
        + len(webhook_payout_failures)
        + len(moderation_queue)
        + len(finance_queue)
    )

    return OperationalExceptionsResponse(
        stale_integrations=stale_integrations,
        no_fill_slots=no_fill_slots,
        unusual_ctrs=unusual_ctrs,
        spend_spikes=spend_spikes,
        webhook_payout_failures=webhook_payout_failures,
        moderation_queue=moderation_queue,
        finance_queue=finance_queue,
        total_exceptions_count=total_count,
    )


@router.get(
    "/publisher/{publisher_id}/safety-summary",
    response_model=PublisherSafetySummaryResponse,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Publisher-facing traffic quality and safety score summary",
)
async def get_publisher_safety_summary(
    publisher_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> PublisherSafetySummaryResponse:
    _assert_publisher_access(user, publisher_id)
    pub = await session.get(Publisher, publisher_id)
    if not pub:
        raise _not_found("Publisher", publisher_id)

    ivt_service = IVTService(session)
    summary = await ivt_service.get_publisher_safety_summary(publisher_id)
    return PublisherSafetySummaryResponse(
        publisher_id=summary.publisher_id,
        quality_score=summary.quality_score,
        total_events=summary.total_events,
        valid_events=summary.valid_events,
        invalid_events=summary.invalid_events,
        invalid_rate_pct=summary.invalid_rate_pct,
        warning_categories=[
            WarningCategorySchema(
                title=w["title"],
                description=w["description"],
                count=w["count"],
                severity=w["severity"],
            )
            for w in summary.warning_categories
        ],
        recommendations=summary.recommendations,
    )


@router.get(
    "/publisher/{publisher_id}/earnings-breakdown",
    response_model=PublisherEarningsBreakdownResponse,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Detailed publisher earnings breakdown",
)
async def get_publisher_earnings_breakdown_management(
    publisher_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> PublisherEarningsBreakdownResponse:
    _assert_publisher_access(user, publisher_id)
    pub = await session.get(Publisher, publisher_id)
    if not pub:
        raise _not_found("Publisher", publisher_id)

    data = await BillingService.get_publisher_earnings_breakdown(session, publisher_id)
    return PublisherEarningsBreakdownResponse(**data)

