from datetime import datetime
from decimal import Decimal
from typing import Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from pydantic import BaseModel

from liteads.models.auth import User
from liteads.models.ad import Campaign, Advertiser, TargetingRule, AdSet, Creative
from liteads.models.base import Status, ModerationStatus, CreativeType
from liteads.schemas.targeting import TargetingSet
from liteads.schemas.finance import PaymentOrderResponse
from liteads.ad_server.services.razorpay_client import get_razorpay_client, RazorpayClientError
from liteads.ad_server.services.campaign_activation import activate_campaign_logic, CampaignActivationError
from liteads.ad_server.services.domain_verification import DomainVerificationService
from liteads.ad_server.services.creative_moderation import CreativeModerationService


class ToolError(Exception):
    """Base class for tool execution errors."""
    pass

class ToolAuthorizationError(ToolError):
    """Raised when a user lacks permission to use a tool or access a resource."""
    pass

class ToolValidationError(ToolError):
    """Raised when tool inputs are invalid."""
    pass


class CreateDraftCampaignResult(BaseModel):
    """Minimal safe response object for orchestration."""
    campaign_id: int
    name: str
    status: str
    message: str


async def create_draft_campaign(
    user: User,
    session: AsyncSession,
    name: str,
    budget_daily: Optional[Decimal] = None,
    budget_total: Optional[Decimal] = None,
) -> CreateDraftCampaignResult:
    """
    Creates a new campaign for the authenticated advertiser.
    The campaign is always created in DRAFT status.
    
    Raises:
        ToolAuthorizationError: If the user is not authenticated as an advertiser.
        ToolValidationError: If the inputs are invalid.
        ToolError: If a database operation fails.
    """
    # 1. Resolve and validate authenticated advertiser context
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    advertiser = await session.get(Advertiser, user.advertiser_id)
    if advertiser is None:
        raise ToolAuthorizationError("Advertiser account not found")

    # 2. Validate input
    if not name or len(name.strip()) == 0:
        raise ToolValidationError("Campaign name cannot be empty")
        
    bd = budget_daily or Decimal("0")
    bt = budget_total or Decimal("0")
    
    if bd < Decimal("0"):
        raise ToolValidationError("Daily budget cannot be negative")
    if bt < Decimal("0"):
        raise ToolValidationError("Total budget cannot be negative")

    # 3. Construct campaign & explicitly force DRAFT status
    campaign = Campaign(
        advertiser_id=user.advertiser_id,
        name=name.strip(),
        budget_daily=bd,
        budget_total=bt,
        status=Status.DRAFT,  # Ensure status is always 5
    )
    
    # 4. Persist
    try:
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)
    except Exception as e:
        await session.rollback()
        raise ToolError(f"Failed to save campaign to database: {str(e)}") from e

    # 5. Return safe structured result
    return CreateDraftCampaignResult(
        campaign_id=campaign.id,
        name=campaign.name,
        status="DRAFT",
        message="Draft campaign created successfully."
    )


class UpdateCampaignResult(BaseModel):
    """Minimal safe response object for orchestration."""
    campaign_id: int
    name: str
    status: str
    updated_fields: list[str]


async def update_campaign(
    user: User,
    session: AsyncSession,
    campaign_id: int,
    name: Optional[str] = None,
    budget_daily: Optional[Decimal] = None,
    budget_total: Optional[Decimal] = None,
    bid_amount: Optional[Decimal] = None,
    freq_cap_daily: Optional[int] = None,
    freq_cap_hourly: Optional[int] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> UpdateCampaignResult:
    """
    Updates permitted fields of an existing DRAFT campaign.
    
    Raises:
        ToolAuthorizationError: If the user lacks permission or the campaign isn't found/owned by them.
        ToolValidationError: If the inputs are invalid or campaign is not in DRAFT status.
        ToolError: If a database operation fails.
    """
    # 1. Resolve advertiser context
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    # 2. Fetch campaign
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise ToolValidationError(f"Campaign {campaign_id} not found")

    # 3. Verify ownership
    if campaign.advertiser_id != user.advertiser_id:
        raise ToolAuthorizationError("Campaign belongs to another advertiser")

    # 4. Enforce lifecycle rules: only DRAFT can be conversationally updated
    if campaign.status != Status.DRAFT:
        raise ToolValidationError("Only DRAFT campaigns can be updated via conversation")

    updates = {}
    
    # 5. Apply explicit permitted fields & validate
    if name is not None:
        if not name or len(name.strip()) == 0:
            raise ToolValidationError("Campaign name cannot be empty")
        updates["name"] = name.strip()
        
    if budget_daily is not None:
        if budget_daily < Decimal("0"):
            raise ToolValidationError("Daily budget cannot be negative")
        updates["budget_daily"] = budget_daily
        
    if budget_total is not None:
        if budget_total < Decimal("0"):
            raise ToolValidationError("Total budget cannot be negative")
        updates["budget_total"] = budget_total
        
    if bid_amount is not None:
        if bid_amount < Decimal("0"):
            raise ToolValidationError("Bid amount cannot be negative")
        updates["bid_amount"] = bid_amount
        
    if freq_cap_daily is not None:
        if freq_cap_daily < 0:
            raise ToolValidationError("Daily frequency cap cannot be negative")
        updates["freq_cap_daily"] = freq_cap_daily
        
    if freq_cap_hourly is not None:
        if freq_cap_hourly < 0:
            raise ToolValidationError("Hourly frequency cap cannot be negative")
        updates["freq_cap_hourly"] = freq_cap_hourly
        
    if start_time is not None:
        updates["start_time"] = start_time
        
    if end_time is not None:
        updates["end_time"] = end_time

    # 6. Apply updates conceptually
    for k, v in updates.items():
        setattr(campaign, k, v)

    # 7. Commit to DB
    if updates:
        try:
            await session.commit()
            await session.refresh(campaign)
        except Exception as e:
            await session.rollback()
            raise ToolError(f"Failed to update campaign in database: {str(e)}") from e

    # 8. Return safe structured result
    return UpdateCampaignResult(
        campaign_id=campaign.id,
        name=campaign.name,
        status="DRAFT", # Always returns DRAFT, since we enforce it above
        updated_fields=list(updates.keys())
    )


class SelectCampaignResult(BaseModel):
    """Safe response object for campaign selection."""
    campaign_id: int
    name: str
    status: str
    budget_daily: Decimal
    budget_total: Decimal
    default_ad_set_id: Optional[int] = None
    ad_sets_count: int = 0


async def select_campaign(
    user: User,
    session: AsyncSession,
    campaign_id: int,
) -> SelectCampaignResult:
    """Select or switch to an existing campaign for the authenticated advertiser."""
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise ToolValidationError(f"Campaign {campaign_id} not found")
    if campaign.advertiser_id != user.advertiser_id:
        raise ToolAuthorizationError("Campaign belongs to another advertiser")

    res = await session.execute(
        select(AdSet).where(AdSet.campaign_id == campaign_id, AdSet.status != Status.DELETED).order_by(AdSet.id.asc())
    )
    ad_sets = list(res.scalars().all())
    default_as_id = ad_sets[0].id if ad_sets else None

    return SelectCampaignResult(
        campaign_id=campaign.id,
        name=campaign.name,
        status="DRAFT" if campaign.status == Status.DRAFT else "ACTIVE",
        budget_daily=campaign.budget_daily,
        budget_total=campaign.budget_total,
        default_ad_set_id=default_as_id,
        ad_sets_count=len(ad_sets),
    )


class CreateAdSetResult(BaseModel):
    """Safe response object for ad set creation."""
    ad_set_id: int
    campaign_id: int
    name: str
    daily_budget: Optional[Decimal]
    bid_amount: Decimal
    status: str
    message: str


async def create_draft_ad_set(
    user: User,
    session: AsyncSession,
    campaign_id: int,
    name: str,
    daily_budget: Optional[Decimal] = None,
    bid_amount: Optional[Decimal] = None,
    bid_type: Optional[int] = None,
) -> CreateAdSetResult:
    """Creates a new ad set under a DRAFT campaign."""
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise ToolValidationError(f"Campaign {campaign_id} not found")
    if campaign.advertiser_id != user.advertiser_id:
        raise ToolAuthorizationError("Campaign belongs to another advertiser")
    if campaign.status != Status.DRAFT:
        raise ToolValidationError("Ad sets can only be added to DRAFT campaigns")

    if not name or len(name.strip()) == 0:
        raise ToolValidationError("Ad set name cannot be empty")

    db = daily_budget
    if db is not None:
        if db < Decimal("0"):
            raise ToolValidationError("Daily sub-budget cannot be negative")
        if campaign.budget_daily > Decimal("0") and db > campaign.budget_daily:
            raise ToolValidationError(
                f"Ad set daily sub-budget (₹{db}) cannot exceed campaign daily budget (₹{campaign.budget_daily})"
            )

    ba = bid_amount if bid_amount is not None else campaign.bid_amount
    if ba < Decimal("0"):
        raise ToolValidationError("Bid amount cannot be negative")

    bt = bid_type if bid_type is not None else campaign.bid_type

    ad_set = AdSet(
        campaign_id=campaign.id,
        name=name.strip(),
        daily_budget=db,
        bid_amount=ba,
        bid_type=bt,
        status=Status.ACTIVE,
    )
    try:
        session.add(ad_set)
        await session.commit()
        await session.refresh(ad_set)
    except Exception as e:
        await session.rollback()
        raise ToolError(f"Failed to save ad set: {str(e)}") from e

    return CreateAdSetResult(
        ad_set_id=ad_set.id,
        campaign_id=campaign.id,
        name=ad_set.name,
        daily_budget=ad_set.daily_budget,
        bid_amount=ad_set.bid_amount,
        status="ACTIVE",
        message="Ad set created successfully.",
    )


class SelectAdSetResult(BaseModel):
    """Safe response object for ad set selection."""
    ad_set_id: int
    campaign_id: int
    name: str
    status: str
    daily_budget: Optional[Decimal]
    bid_amount: Decimal


async def select_ad_set(
    user: User,
    session: AsyncSession,
    campaign_id: int,
    ad_set_id: Optional[int] = None,
    ad_set_name: Optional[str] = None,
) -> SelectAdSetResult:
    """Select or switch to an active ad set within the selected campaign."""
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise ToolValidationError(f"Campaign {campaign_id} not found")
    if campaign.advertiser_id != user.advertiser_id:
        raise ToolAuthorizationError("Campaign belongs to another advertiser")

    ad_set = None
    if ad_set_id is not None:
        ad_set = await session.get(AdSet, ad_set_id)
        if not ad_set or ad_set.status == Status.DELETED:
            raise ToolValidationError(f"Ad set {ad_set_id} not found")
        if ad_set.campaign_id != campaign_id:
            raise ToolValidationError(f"Ad set {ad_set_id} does not belong to campaign {campaign_id}")
    elif ad_set_name:
        query = select(AdSet).where(
            AdSet.campaign_id == campaign_id,
            AdSet.name.ilike(ad_set_name.strip()),
            AdSet.status != Status.DELETED,
        )
        res = await session.execute(query)
        ad_set = res.scalars().first()
        if not ad_set:
            raise ToolValidationError(f"Ad set with name '{ad_set_name}' not found under campaign {campaign_id}")
    else:
        raise ToolValidationError("Must specify ad_set_id or ad_set_name to select an ad set")

    return SelectAdSetResult(
        ad_set_id=ad_set.id,
        campaign_id=campaign.id,
        name=ad_set.name,
        status="ACTIVE" if ad_set.status == Status.ACTIVE else "PAUSED",
        daily_budget=ad_set.daily_budget,
        bid_amount=ad_set.bid_amount,
    )


class UpdateAdSetResult(BaseModel):
    """Safe response object for ad set updates."""
    ad_set_id: int
    campaign_id: int
    name: str
    status: str
    updated_fields: list[str]


async def update_ad_set(
    user: User,
    session: AsyncSession,
    campaign_id: int,
    ad_set_id: int,
    name: Optional[str] = None,
    daily_budget: Optional[Decimal] = None,
    bid_amount: Optional[Decimal] = None,
    bid_type: Optional[int] = None,
    freq_cap_daily: Optional[int] = None,
    freq_cap_hourly: Optional[int] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> UpdateAdSetResult:
    """Updates permitted configuration on an existing ad set."""
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise ToolValidationError(f"Campaign {campaign_id} not found")
    if campaign.advertiser_id != user.advertiser_id:
        raise ToolAuthorizationError("Campaign belongs to another advertiser")
    if campaign.status != Status.DRAFT:
        raise ToolValidationError("Ad sets can only be updated in DRAFT campaigns")

    ad_set = await session.get(AdSet, ad_set_id)
    if not ad_set or ad_set.status == Status.DELETED:
        raise ToolValidationError(f"Ad set {ad_set_id} not found")
    if ad_set.campaign_id != campaign_id:
        raise ToolValidationError(f"Ad set {ad_set_id} does not belong to campaign {campaign_id}")

    updates = {}
    if name is not None:
        if not name or len(name.strip()) == 0:
            raise ToolValidationError("Ad set name cannot be empty")
        updates["name"] = name.strip()

    if daily_budget is not None:
        if daily_budget < Decimal("0"):
            raise ToolValidationError("Daily sub-budget cannot be negative")
        if campaign.budget_daily > Decimal("0") and daily_budget > campaign.budget_daily:
            raise ToolValidationError(
                f"Ad set daily sub-budget (₹{daily_budget}) cannot exceed campaign daily budget (₹{campaign.budget_daily})"
            )
        updates["daily_budget"] = daily_budget

    if bid_amount is not None:
        if bid_amount < Decimal("0"):
            raise ToolValidationError("Bid amount cannot be negative")
        updates["bid_amount"] = bid_amount

    if bid_type is not None:
        updates["bid_type"] = bid_type

    if freq_cap_daily is not None:
        if freq_cap_daily < 0:
            raise ToolValidationError("Daily frequency cap cannot be negative")
        updates["freq_cap_daily"] = freq_cap_daily

    if freq_cap_hourly is not None:
        if freq_cap_hourly < 0:
            raise ToolValidationError("Hourly frequency cap cannot be negative")
        updates["freq_cap_hourly"] = freq_cap_hourly

    if start_time is not None:
        updates["start_time"] = start_time

    if end_time is not None:
        updates["end_time"] = end_time

    for k, v in updates.items():
        setattr(ad_set, k, v)

    if updates:
        try:
            await session.commit()
            await session.refresh(ad_set)
        except Exception as e:
            await session.rollback()
            raise ToolError(f"Failed to update ad set: {str(e)}") from e

    return UpdateAdSetResult(
        ad_set_id=ad_set.id,
        campaign_id=campaign_id,
        name=ad_set.name,
        status="ACTIVE" if ad_set.status == Status.ACTIVE else "PAUSED",
        updated_fields=list(updates.keys()),
    )


class UpdateTargetingResult(BaseModel):
    """Minimal safe response object for orchestration."""
    campaign_id: int
    ad_set_id: Optional[int] = None
    status: str
    targeting: TargetingSet


async def update_targeting(
    user: User,
    session: AsyncSession,
    campaign_id: int,
    targeting: TargetingSet,
    ad_set_id: Optional[int] = None,
) -> UpdateTargetingResult:
    """
    Updates the targeting configuration scoped to a specific ad set (or default ad set) in a DRAFT campaign.
    Replaces existing targeting rules fully for the specified scope.
    """
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise ToolValidationError(f"Campaign {campaign_id} not found")

    if campaign.advertiser_id != user.advertiser_id:
        raise ToolAuthorizationError("Campaign belongs to another advertiser")

    if campaign.status != Status.DRAFT:
        raise ToolValidationError("Only DRAFT campaigns can be updated via conversation")

    target_ad_set_id = ad_set_id
    if target_ad_set_id is not None:
        ad_set = await session.get(AdSet, target_ad_set_id)
        if not ad_set or ad_set.status == Status.DELETED:
            raise ToolValidationError(f"Ad set {target_ad_set_id} not found")
        if ad_set.campaign_id != campaign_id:
            raise ToolValidationError(f"Ad set {target_ad_set_id} does not belong to campaign {campaign_id}")
    else:
        # Fallback to campaign's default ad set
        res = await session.execute(
            select(AdSet).where(AdSet.campaign_id == campaign_id, AdSet.status != Status.DELETED).order_by(AdSet.id.asc())
        )
        default_as = res.scalars().first()
        if not default_as:
            default_as = AdSet(
                campaign_id=campaign_id,
                name="Default Ad Set",
                status=Status.ACTIVE,
                daily_budget=campaign.budget_daily,
                bid_amount=campaign.bid_amount,
                bid_type=campaign.bid_type,
            )
            session.add(default_as)
            await session.flush()
        target_ad_set_id = default_as.id

    try:
        # Delete existing targeting rules for geo and sector scoped to target_ad_set_id
        if ad_set_id is None:
            scope_condition = or_(
                TargetingRule.ad_set_id == target_ad_set_id,
                TargetingRule.ad_set_id.is_(None),
            )
        else:
            scope_condition = TargetingRule.ad_set_id == target_ad_set_id

        query = select(TargetingRule).where(
            TargetingRule.campaign_id == campaign_id,
            scope_condition,
            TargetingRule.rule_type.in_(["geo", "sector"])
        )
        result = await session.execute(query)
        existing_rules = result.scalars().all()
        for rule in existing_rules:
            await session.delete(rule)

        # Insert new rules scoped to target_ad_set_id
        if targeting.geo_include:
            session.add(
                TargetingRule(
                    campaign_id=campaign_id,
                    ad_set_id=target_ad_set_id,
                    rule_type="geo",
                    is_include=True,
                    rule_value={"entries": [e.model_dump(exclude_none=True) for e in targeting.geo_include]}
                )
            )
        if targeting.geo_exclude:
            session.add(
                TargetingRule(
                    campaign_id=campaign_id,
                    ad_set_id=target_ad_set_id,
                    rule_type="geo",
                    is_include=False,
                    rule_value={"entries": [e.model_dump(exclude_none=True) for e in targeting.geo_exclude]}
                )
            )

        if targeting.sector_include:
            session.add(
                TargetingRule(
                    campaign_id=campaign_id,
                    ad_set_id=target_ad_set_id,
                    rule_type="sector",
                    is_include=True,
                    rule_value={"sectors": targeting.sector_include}
                )
            )
        if targeting.sector_exclude:
            session.add(
                TargetingRule(
                    campaign_id=campaign_id,
                    ad_set_id=target_ad_set_id,
                    rule_type="sector",
                    is_include=False,
                    rule_value={"sectors": targeting.sector_exclude}
                )
            )

        await session.commit()
    except Exception as e:
        await session.rollback()
        raise ToolError(f"Failed to update targeting in database: {str(e)}") from e

    return UpdateTargetingResult(
        campaign_id=campaign_id,
        ad_set_id=target_ad_set_id,
        status="DRAFT",
        targeting=targeting
    )


class CreateAdResult(BaseModel):
    """Safe response object for creative/ad creation."""
    creative_id: int
    campaign_id: int
    ad_set_id: int
    title: str
    status: str
    moderation_status: int
    moderation_reason: Optional[str] = None
    landing_url: str
    message: str


async def create_ad(
    user: User,
    session: AsyncSession,
    campaign_id: int,
    ad_set_id: int,
    title: str,
    landing_url: str,
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
    creative_type: int = 1,
    width: int = 300,
    height: int = 250,
) -> CreateAdResult:
    """Creates a new creative/ad associated with the specified ad set under a DRAFT campaign."""
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise ToolValidationError(f"Campaign {campaign_id} not found")
    if campaign.advertiser_id != user.advertiser_id:
        raise ToolAuthorizationError("Campaign belongs to another advertiser")
    if campaign.status != Status.DRAFT:
        raise ToolValidationError("Ads can only be added to DRAFT campaigns")

    ad_set = await session.get(AdSet, ad_set_id)
    if not ad_set or ad_set.status == Status.DELETED:
        raise ToolValidationError(f"Ad set {ad_set_id} not found")
    if ad_set.campaign_id != campaign_id:
        raise ToolValidationError(f"Ad set {ad_set_id} does not belong to campaign {campaign_id}")

    if not title or len(title.strip()) == 0:
        raise ToolValidationError("Ad title cannot be empty")
    if not landing_url or len(landing_url.strip()) == 0:
        raise ToolValidationError("Landing URL cannot be empty")

    # Domain verification check for advertiser
    domain_service = DomainVerificationService(session)
    is_verified = await domain_service.is_url_verified_for_advertiser(user.advertiser_id, landing_url.strip())
    if not is_verified:
        raise ToolValidationError(f"Landing URL domain is not verified for your account: {landing_url.strip()}")

    # Moderation check
    decision = await CreativeModerationService.moderate_creative(
        title=title.strip(),
        description=None,
        image_url=image_url,
        video_url=video_url,
        landing_url=landing_url.strip(),
    )

    ad_status = Status.ACTIVE if decision.status == ModerationStatus.APPROVED.value else Status.PENDING

    creative = Creative(
        campaign_id=campaign_id,
        ad_set_id=ad_set_id,
        title=title.strip(),
        landing_url=landing_url.strip(),
        image_url=image_url,
        video_url=video_url,
        creative_type=creative_type,
        width=width,
        height=height,
        status=ad_status,
        moderation_status=decision.status,
        moderation_reason=decision.reason,
    )
    try:
        session.add(creative)
        await session.commit()
        await session.refresh(creative)
    except Exception as e:
        await session.rollback()
        raise ToolError(f"Failed to save creative: {str(e)}") from e

    msg = "Creative created and approved." if decision.status == ModerationStatus.APPROVED.value else (
        f"Creative created but awaiting review: {decision.reason or 'Pending moderation'}"
    )

    return CreateAdResult(
        creative_id=creative.id,
        campaign_id=campaign_id,
        ad_set_id=ad_set_id,
        title=creative.title,
        status="ACTIVE" if creative.status == Status.ACTIVE else "PENDING",
        moderation_status=creative.moderation_status,
        moderation_reason=creative.moderation_reason,
        landing_url=creative.landing_url,
        message=msg,
    )


class ReviewAdSetSummary(BaseModel):
    """Safe summary of an ad set within campaign review."""
    id: int
    name: str
    status: str
    daily_budget: Optional[Decimal]
    bid_amount: Decimal
    creatives_count: int
    has_targeting: bool


class ReviewCampaignResult(BaseModel):
    """Complete campaign structure review and missing launch prerequisites."""
    campaign_id: int
    campaign_name: str
    status: str
    wallet_balance: Decimal
    budget_daily: Decimal
    budget_total: Decimal
    ad_sets: list[ReviewAdSetSummary]
    total_creatives: int
    total_ads: int = 0
    approved_creatives: int
    is_ready_for_launch: bool
    can_activate: bool = False
    missing_requirements: list[str]


async def review_campaign(
    user: User,
    session: AsyncSession,
    campaign_id: int,
) -> ReviewCampaignResult:
    """Retrieves full campaign hierarchy and validates readiness for activation."""
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    advertiser = await session.get(Advertiser, user.advertiser_id)
    if not advertiser:
        raise ToolAuthorizationError("Advertiser not found")

    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise ToolValidationError(f"Campaign {campaign_id} not found")
    if campaign.advertiser_id != user.advertiser_id:
        raise ToolAuthorizationError("Campaign belongs to another advertiser")

    res = await session.execute(
        select(AdSet).where(AdSet.campaign_id == campaign_id, AdSet.status != Status.DELETED).order_by(AdSet.id.asc())
    )
    ad_sets = list(res.scalars().all())

    res = await session.execute(
        select(Creative).where(Creative.campaign_id == campaign_id, Creative.status != Status.DELETED).order_by(Creative.id.asc())
    )
    creatives = list(res.scalars().all())

    res = await session.execute(
        select(TargetingRule).where(TargetingRule.campaign_id == campaign_id)
    )
    targeting_rules = list(res.scalars().all())

    domain_service = DomainVerificationService(session)

    missing = []
    if advertiser.balance <= Decimal("0"):
        missing.append(f"Insufficient wallet balance: current balance is ₹{advertiser.balance:.2f}. Please add funds.")
    elif campaign.budget_daily > Decimal("0") and advertiser.balance < campaign.budget_daily:
        missing.append(f"Wallet balance (₹{advertiser.balance:.2f}) is less than daily budget (₹{campaign.budget_daily:.2f}).")

    if not ad_sets:
        missing.append("Campaign has no ad sets. Create at least one ad set.")
    else:
        active_ad_sets = [a for a in ad_sets if a.status == Status.ACTIVE]
        if not active_ad_sets:
            missing.append("All ad sets are paused. At least one ad set must be active.")

    if not creatives:
        missing.append("Campaign has no ads. Create at least one ad under an ad set.")
    else:
        active_creatives = [c for c in creatives if c.status == Status.ACTIVE]
        approved_creatives = [c for c in active_creatives if c.moderation_status == ModerationStatus.APPROVED.value]
        pending_creatives = [c for c in creatives if c.moderation_status == ModerationStatus.PENDING_REVIEW.value]
        rejected_creatives = [c for c in creatives if c.moderation_status == ModerationStatus.REJECTED.value]

        if not approved_creatives:
            if pending_creatives:
                missing.append(f"{len(pending_creatives)} creative(s) are awaiting moderation review.")
            elif rejected_creatives:
                missing.append(f"{len(rejected_creatives)} creative(s) were rejected by moderation.")
            else:
                missing.append("No active approved creatives found.")

        # Check domain verification for all active creatives
        for c in active_creatives:
            is_ver = await domain_service.is_url_verified_for_advertiser(user.advertiser_id, c.landing_url)
            if not is_ver:
                missing.append(f"Landing URL domain for '{c.title}' is not verified.")

    ad_set_rules_count = {a.id: 0 for a in ad_sets}
    for r in targeting_rules:
        if r.ad_set_id in ad_set_rules_count:
            ad_set_rules_count[r.ad_set_id] += 1

    ad_set_creatives_count = {a.id: 0 for a in ad_sets}
    for c in creatives:
        if c.ad_set_id in ad_set_creatives_count:
            ad_set_creatives_count[c.ad_set_id] += 1

    ad_set_summaries = [
        ReviewAdSetSummary(
            id=a.id,
            name=a.name,
            status="ACTIVE" if a.status == Status.ACTIVE else "PAUSED",
            daily_budget=a.daily_budget,
            bid_amount=a.bid_amount,
            creatives_count=ad_set_creatives_count.get(a.id, 0),
            has_targeting=ad_set_rules_count.get(a.id, 0) > 0,
        )
        for a in ad_sets
    ]

    approved_count = len([c for c in creatives if c.moderation_status == ModerationStatus.APPROVED.value])

    return ReviewCampaignResult(
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        status="DRAFT" if campaign.status == Status.DRAFT else "ACTIVE",
        wallet_balance=advertiser.balance,
        budget_daily=campaign.budget_daily,
        budget_total=campaign.budget_total,
        ad_sets=ad_set_summaries,
        total_creatives=len(creatives),
        total_ads=len(creatives),
        approved_creatives=approved_count,
        is_ready_for_launch=len(missing) == 0,
        can_activate=len(missing) == 0,
        missing_requirements=missing,
    )


async def request_payment(
    user: User,
    session: AsyncSession,
    amount: Decimal,
    advertiser_id: int | None = None,
) -> PaymentOrderResponse:
    """
    Creates a Razorpay payment order for wallet top-up.
    Does NOT credit the wallet. Wallet credit happens only after webhook verification.

    Args:
        user: The authenticated User. Must have an advertiser_id or have advertiser_id passed.
        amount: The payment amount in INR. Minimum ₹100.
        advertiser_id: Optional advertiser ID to override/fallback for authorized callers.
        
    Raises:
        ToolAuthorizationError: If no associated advertiser account can be found.
        ToolValidationError: If the amount is less than ₹100.
        ToolError: If the Razorpay API fails.
    """
    target_adv_id = advertiser_id if advertiser_id is not None else user.advertiser_id
    if not target_adv_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    if amount < Decimal("100"):
        raise ToolValidationError("Amount to top up must be at least ₹100")

    import uuid
    from liteads.models.finance import PaymentRequest
    
    internal_reference = f"adv_{target_adv_id}_qr_{uuid.uuid4().hex[:12]}"
    
    razorpay_client = get_razorpay_client()
    try:
        # Create Razorpay QR code instead of regular order
        qr = razorpay_client.create_qr_code(
            amount=amount,
            receipt=internal_reference,
            advertiser_id=target_adv_id
        )
        qr_id = qr["id"]
        qr_image_url = qr.get("image_url")

        # Persist the request
        payment_request = PaymentRequest(
            advertiser_id=target_adv_id,
            amount=amount,
            currency="INR",
            provider_order_id=qr_id,
            status="pending"
        )
        session.add(payment_request)
        await session.commit()

        return PaymentOrderResponse(
            order_id=qr_id,
            amount=amount,
            currency="INR",
            qr_image_url=qr_image_url
        )
    except RazorpayClientError as e:
        raise ToolError(f"Payment gateway error: {str(e)}") from e


class ActivateCampaignResult(BaseModel):
    """Minimal safe response object for orchestration."""
    campaign_id: int
    status: str


async def activate_campaign(
    user: User,
    session: AsyncSession,
    campaign_id: int,
) -> ActivateCampaignResult:
    """
    Activates a DRAFT campaign after enforcing server-side ownership, DRAFT status, and balance checks.
    
    Raises:
        ToolAuthorizationError: If the user lacks permission or the campaign isn't owned by them.
        ToolValidationError: If the campaign is not in DRAFT or if wallet balance is insufficient.
        ToolError: If a database operation fails.
    """
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    try:
        campaign = await activate_campaign_logic(
            session=session,
            campaign_id=campaign_id,
            advertiser_id=user.advertiser_id
        )
        await session.commit()
    except CampaignActivationError as e:
        await session.rollback()
        msg = str(e)
        if "belongs to another advertiser" in msg:
            raise ToolAuthorizationError(msg)
        raise ToolValidationError(msg)
    except Exception as e:
        await session.rollback()
        raise ToolError(f"Failed to activate campaign: {str(e)}") from e

    return ActivateCampaignResult(
        campaign_id=campaign.id,
        status="ACTIVE"
    )
