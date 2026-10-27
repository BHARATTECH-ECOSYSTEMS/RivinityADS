"""CRUD and publisher reporting endpoints."""

import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.database import get_session
from liteads.common.security import current_user, verified_user, require_roles
from liteads.ad_server.services.campaign_activation import activate_campaign_logic, CampaignActivationError
from liteads.ad_server.services.creative_moderation import CreativeModerationService
from liteads.ad_server.services.domain_verification import DomainVerificationService
from liteads.ad_server.services.placement_analyzer import PlacementAnalyzerService
from liteads.models import (
    AdSet,
    AdSlot,
    Advertiser,
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
from liteads.schemas.management import (
    AdSetCreate,
    AdSetReportingItem,
    AdSetReportingResponse,
    AdSetResponse,
    AdSetUpdate,
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
    FormatBenchmarkSchema,
    PlacementHealthItemSchema,
    PlacementHealthResponse,
)
from liteads.ad_server.services.optimization_executor import OptimizationExecutor
from liteads.ad_server.services.optimization_policy import OptimizationPolicyResult
from liteads.ad_server.services.rate_limit_service import RateLimitService
from liteads.models.ad import Suggestion

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
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, key, value)
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

    for key, value in updates.items():
        setattr(creative, key, value)
        
    if "landing_url" in updates and user.role == "advertiser":
        domain_service = DomainVerificationService(session)
        is_verified = await domain_service.is_url_verified_for_advertiser(user.advertiser_id, creative.landing_url)
        if not is_verified:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Landing URL domain is not verified")
        
    # If sensitive fields changed, re-evaluate moderation
    if any(k in updates for k in ("title", "description", "image_url", "video_url", "landing_url")):
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
    session: AsyncSession = Depends(get_session),
) -> Publisher:
    publisher = await session.get(Publisher, publisher_id)
    if publisher is None:
        raise _not_found("Publisher", publisher_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(publisher, key, value)
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
    
    today = date.today()
    start = start_date or today
    end = end_date or today
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
        
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())
    
    query = select(
        func.date(AdEvent.event_time).label('stat_date'),
        func.count().filter(AdEvent.event_type == 1).label('impressions'),
        func.count().filter(AdEvent.event_type == 2).label('clicks'),
        func.count().filter(AdEvent.event_type == 3).label('conversions'),
        func.sum(AdEvent.cost).label('spend'),
    ).join(Campaign, Campaign.id == AdEvent.campaign_id).where(
        Campaign.advertiser_id == advertiser_id,
        AdEvent.event_time >= start_dt,
        AdEvent.event_time <= end_dt,
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
    total_spend = Decimal("0")
    breakdown = []
    
    for row in result.all():
        d_impressions = int(row.impressions or 0)
        d_clicks = int(row.clicks or 0)
        d_conversions = int(row.conversions or 0)
        d_spend = Decimal(row.spend or 0)
        
        # In SQLite func.date returns a string 'YYYY-MM-DD', we need a date object
        stat_date = datetime.strptime(str(row.stat_date), "%Y-%m-%d").date()
        
        breakdown.append(DailyStatResponse(
            stat_date=stat_date,
            impressions=d_impressions,
            clicks=d_clicks,
            conversions=d_conversions,
            spend=d_spend,
        ))
        
        total_impressions += d_impressions
        total_clicks += d_clicks
        total_conversions += d_conversions
        total_spend += d_spend
        
    ctr_val = (Decimal(total_clicks) / Decimal(total_impressions) * Decimal(100)) if total_impressions else Decimal("0")
    cvr_val = (Decimal(total_conversions) / Decimal(total_clicks) * Decimal(100)) if total_clicks else Decimal("0")
    
    return AdvertiserReportingResponse(
        advertiser_id=advertiser_id,
        campaign_id=campaign_id,
        ad_set_id=ad_set_id,
        start_date=start,
        end_date=end,
        impressions=total_impressions,
        clicks=total_clicks,
        conversions=total_conversions,
        spend=total_spend,
        ctr=ctr_val,
        cvr=cvr_val,
        daily_breakdown=breakdown,
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
    today = date.today()
    start = start_date or today
    end = end_date or today
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())

    ad_sets_result = await session.execute(
        select(AdSet).where(AdSet.campaign_id == campaign_id).order_by(AdSet.id)
    )
    ad_sets = list(ad_sets_result.scalars().all())

    events_query = (
        select(
            Creative.ad_set_id,
            func.count().filter(AdEvent.event_type == 1).label('impressions'),
            func.count().filter(AdEvent.event_type == 2).label('clicks'),
            func.count().filter(AdEvent.event_type == 3).label('conversions'),
            func.sum(AdEvent.cost).label('spend'),
        )
        .join(Creative, Creative.id == AdEvent.creative_id)
        .where(
            AdEvent.campaign_id == campaign_id,
            AdEvent.event_time >= start_dt,
            AdEvent.event_time <= end_dt,
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
    total_spend = Decimal("0")

    for ad_set in ad_sets:
        row = ad_set_stats_map.get(ad_set.id)
        impr = int(row.impressions or 0) if row else 0
        clk = int(row.clicks or 0) if row else 0
        conv = int(row.conversions or 0) if row else 0
        sp = Decimal(row.spend or 0) if row else Decimal("0")

        ctr = (Decimal(clk) / Decimal(impr) * Decimal(100)) if impr else Decimal("0")
        cvr = (Decimal(conv) / Decimal(clk) * Decimal(100)) if clk else Decimal("0")

        ad_set_items.append(
            AdSetReportingItem(
                ad_set_id=ad_set.id,
                ad_set_name=ad_set.name,
                status=ad_set.status,
                impressions=impr,
                clicks=clk,
                conversions=conv,
                spend=sp,
                ctr=ctr,
                cvr=cvr,
            )
        )
        total_impressions += impr
        total_clicks += clk
        total_conversions += conv
        total_spend += sp

    return AdSetReportingResponse(
        campaign_id=campaign_id,
        start_date=start,
        end_date=end,
        ad_sets=ad_set_items,
        total_impressions=total_impressions,
        total_clicks=total_clicks,
        total_conversions=total_conversions,
        total_spend=total_spend,
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


@router.get("/publisher/{publisher_id}/property", response_model=list[PublisherPropertyResponse], dependencies=[Depends(require_roles("admin", "publisher"))])
async def list_publisher_properties(
    publisher_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> list[PublisherProperty]:
    _assert_publisher_access(user, publisher_id)
    query = select(PublisherProperty).where(PublisherProperty.publisher_id == publisher_id).order_by(PublisherProperty.id)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("/publisher/{publisher_id}/property", response_model=PublisherPropertyResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "publisher"))])
async def create_publisher_property(
    publisher_id: int, payload: PublisherPropertyCreate, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> PublisherProperty:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    data = payload.model_dump()
    data["publisher_id"] = publisher_id
    property_obj = PublisherProperty(**data)
    session.add(property_obj)
    await session.commit()
    await session.refresh(property_obj)
    return property_obj


@router.get("/publisher/property/{property_id}", response_model=PublisherPropertyResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def get_publisher_property(
    property_id: int, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> PublisherProperty:
    property_obj = await session.get(PublisherProperty, property_id)
    if property_obj is None:
        raise _not_found("PublisherProperty", property_id)
    _assert_publisher_access(user, property_obj.publisher_id)
    return property_obj


@router.patch("/publisher/property/{property_id}", response_model=PublisherPropertyResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def update_publisher_property(
    property_id: int, payload: PublisherPropertyUpdate, user: User = Depends(verified_user), session: AsyncSession = Depends(get_session)
) -> PublisherProperty:
    property_obj = await session.get(PublisherProperty, property_id)
    if property_obj is None:
        raise _not_found("PublisherProperty", property_id)
    _assert_publisher_access(user, property_obj.publisher_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(property_obj, key, value)
    await session.commit()
    await session.refresh(property_obj)
    return property_obj


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
