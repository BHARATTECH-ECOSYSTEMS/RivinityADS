"""Authoritative Campaign Activation and Readiness Verification Service (Phase 4).

Single source of truth for campaign launch prerequisites across:
- Campaign master configuration (budgets, schedules, status)
- Ad Sets hierarchy (active ad set presence, sub-budgets, targeting)
- Creative assets (ad set assignment, moderation status, destination verification)
- Advertiser wallet balance & billing integrity
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from liteads.ad_server.services.domain_verification import DomainVerificationService
from liteads.models.ad import AdSet, Campaign, Creative, TargetingRule, Advertiser, Status
from liteads.models.base import ModerationStatus


class ReadinessIssue(BaseModel):
    """Structured blocker or warning preventing or advising on campaign activation."""
    code: str
    category: str  # "blocker" | "warning"
    entity_type: str  # "campaign" | "ad_set" | "creative" | "advertiser"
    entity_id: int | None = None
    entity_name: str | None = None
    field: str | None = None
    message: str


class CampaignReadinessResponse(BaseModel):
    """Server-authoritative readiness state and pre-flight launch summary."""
    campaign_id: int
    campaign_name: str
    status: int
    status_name: str
    lifecycle_state: str = "Draft"
    is_ready: bool
    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []
    summary: dict[str, Any] = {}


class CampaignActivationError(Exception):
    """Exception raised when a campaign fails authoritative activation validation."""
    def __init__(self, message: str, blockers: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.message = message
        self.blockers = blockers or []


async def validate_campaign_readiness(
    session: AsyncSession,
    campaign_id: int,
    advertiser_id: int,
) -> CampaignReadinessResponse:
    """
    Authoritatively evaluate campaign readiness for activation.
    Returns structured blockers, warnings, and a comprehensive pre-flight summary.
    """
    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []

    # 1. Advertiser checks
    advertiser = await session.get(Advertiser, advertiser_id)
    if advertiser is None:
        blockers.append(
            ReadinessIssue(
                code="ADVERTISER_NOT_FOUND",
                category="blocker",
                entity_type="advertiser",
                entity_id=advertiser_id,
                message="Advertiser not found",
            )
        )
        return CampaignReadinessResponse(
            campaign_id=campaign_id,
            campaign_name="Unknown",
            status=0,
            status_name="UNKNOWN",
            lifecycle_state="Draft",
            is_ready=False,
            blockers=blockers,
            warnings=warnings,
            summary={},
        )

    # 2. Campaign checks
    stmt = (
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(
            selectinload(Campaign.creatives),
            selectinload(Campaign.targeting_rules),
            selectinload(Campaign.ad_sets).selectinload(AdSet.targeting_rules),
            selectinload(Campaign.ad_sets).selectinload(AdSet.creatives),
        )
        .execution_options(populate_existing=True)
    )
    result = await session.execute(stmt)
    campaign = result.scalar_one_or_none()

    if campaign is None:
        blockers.append(
            ReadinessIssue(
                code="CAMPAIGN_NOT_FOUND",
                category="blocker",
                entity_type="campaign",
                entity_id=campaign_id,
                message="Campaign not found",
            )
        )
        return CampaignReadinessResponse(
            campaign_id=campaign_id,
            campaign_name="Unknown",
            status=0,
            status_name="UNKNOWN",
            lifecycle_state="Draft",
            is_ready=False,
            blockers=blockers,
            warnings=warnings,
            summary={},
        )

    if campaign.advertiser_id != advertiser_id:
        blockers.append(
            ReadinessIssue(
                code="UNAUTHORIZED",
                category="blocker",
                entity_type="campaign",
                entity_id=campaign_id,
                message="Campaign belongs to another advertiser",
            )
        )
        return CampaignReadinessResponse(
            campaign_id=campaign_id,
            campaign_name=campaign.name,
            status=campaign.status,
            status_name="UNAUTHORIZED",
            lifecycle_state="Draft",
            is_ready=False,
            blockers=blockers,
            warnings=warnings,
            summary={},
        )

    # Check status (Must be DRAFT to activate)
    status_map = {
        Status.DRAFT: "DRAFT",
        Status.ACTIVE: "ACTIVE",
        Status.PAUSED: "PAUSED",
        Status.DELETED: "DELETED",
        Status.INACTIVE: "INACTIVE",
        Status.PENDING: "PENDING",
    }
    status_name = status_map.get(campaign.status, f"STATUS_{campaign.status}")

    if campaign.status != Status.DRAFT:
        if campaign.status == Status.ACTIVE:
            blockers.append(
                ReadinessIssue(
                    code="ALREADY_ACTIVE",
                    category="blocker",
                    entity_type="campaign",
                    entity_id=campaign.id,
                    entity_name=campaign.name,
                    field="status",
                    message="Only DRAFT campaigns can be activated",
                )
            )
        else:
            blockers.append(
                ReadinessIssue(
                    code="DISALLOWED_STATUS",
                    category="blocker",
                    entity_type="campaign",
                    entity_id=campaign.id,
                    entity_name=campaign.name,
                    field="status",
                    message="Only DRAFT campaigns can be activated",
                )
            )

    # Wallet Balance
    if advertiser.balance <= Decimal("0"):
        blockers.append(
            ReadinessIssue(
                code="INSUFFICIENT_BALANCE",
                category="blocker",
                entity_type="advertiser",
                entity_id=advertiser.id,
                entity_name=advertiser.name,
                field="balance",
                message="Insufficient wallet balance to activate campaign",
            )
        )
    elif campaign.budget_daily > Decimal("0") and advertiser.balance < campaign.budget_daily:
        warnings.append(
            ReadinessIssue(
                code="BALANCE_BELOW_DAILY_BUDGET",
                category="warning",
                entity_type="advertiser",
                entity_id=advertiser.id,
                entity_name=advertiser.name,
                field="balance",
                message=f"Wallet balance (₹{advertiser.balance:.2f}) is less than daily budget (₹{campaign.budget_daily:.2f}). Campaign will pause once funds are depleted.",
            )
        )

    # Budgets
    if campaign.budget_daily <= Decimal("0"):
        blockers.append(
            ReadinessIssue(
                code="INVALID_DAILY_BUDGET",
                category="blocker",
                entity_type="campaign",
                entity_id=campaign.id,
                entity_name=campaign.name,
                field="budget_daily",
                message="Campaign must have a daily budget greater than zero",
            )
        )

    if campaign.budget_total < campaign.budget_daily:
        blockers.append(
            ReadinessIssue(
                code="BUDGET_MISMATCH",
                category="blocker",
                entity_type="campaign",
                entity_id=campaign.id,
                entity_name=campaign.name,
                field="budget_total",
                message="Total budget cannot be less than daily budget",
            )
        )

    if campaign.bid_amount <= Decimal("0"):
        warnings.append(
            ReadinessIssue(
                code="ZERO_BID_AMOUNT",
                category="warning",
                entity_type="campaign",
                entity_id=campaign.id,
                entity_name=campaign.name,
                field="bid_amount",
                message="Campaign master bid amount is zero; ensure Ad Sets define positive bid amounts.",
            )
        )

    # Schedule validation
    now_utc = datetime.now(timezone.utc)
    if campaign.start_time and campaign.end_time and campaign.end_time <= campaign.start_time:
        blockers.append(
            ReadinessIssue(
                code="INVALID_SCHEDULE",
                category="blocker",
                entity_type="campaign",
                entity_id=campaign.id,
                entity_name=campaign.name,
                field="end_time",
                message="Campaign schedule end time must be after start time",
            )
        )
    if campaign.end_time:
        end_tz = campaign.end_time if campaign.end_time.tzinfo else campaign.end_time.replace(tzinfo=timezone.utc)
        if end_tz < now_utc:
            blockers.append(
                ReadinessIssue(
                    code="SCHEDULE_EXPIRED",
                    category="blocker",
                    entity_type="campaign",
                    entity_id=campaign.id,
                    entity_name=campaign.name,
                    field="end_time",
                    message="Campaign schedule has already ended",
                )
            )

    # 3. Ad Sets Hierarchy & Sub-budgets
    all_ad_sets = [a for a in campaign.ad_sets if a.status != Status.DELETED]
    active_ad_sets = [a for a in all_ad_sets if a.status == Status.ACTIVE]

    if all_ad_sets and not active_ad_sets:
        blockers.append(
            ReadinessIssue(
                code="NO_ACTIVE_AD_SETS",
                category="blocker",
                entity_type="ad_set",
                message="All ad sets in this campaign are paused. At least one ad set must be active.",
            )
        )

    # Validate AdSet schedules and sub-budgets
    for ad_set in active_ad_sets:
        if ad_set.start_time and ad_set.end_time and ad_set.end_time <= ad_set.start_time:
            blockers.append(
                ReadinessIssue(
                    code="INVALID_AD_SET_SCHEDULE",
                    category="blocker",
                    entity_type="ad_set",
                    entity_id=ad_set.id,
                    entity_name=ad_set.name,
                    field="end_time",
                    message=f"Ad set '{ad_set.name}' end time must be after start time",
                )
            )
        if ad_set.end_time:
            as_end_tz = ad_set.end_time if ad_set.end_time.tzinfo else ad_set.end_time.replace(tzinfo=timezone.utc)
            if as_end_tz < now_utc:
                blockers.append(
                    ReadinessIssue(
                        code="AD_SET_SCHEDULE_EXPIRED",
                        category="blocker",
                        entity_type="ad_set",
                        entity_id=ad_set.id,
                        entity_name=ad_set.name,
                        field="end_time",
                        message=f"Ad set '{ad_set.name}' schedule has already expired",
                    )
                )

    # Warn if sum of active ad set sub-budgets exceeds campaign ceiling
    total_sub_budgets = sum(
        (a.daily_budget for a in active_ad_sets if a.daily_budget is not None),
        Decimal("0"),
    )
    if campaign.budget_daily > Decimal("0") and total_sub_budgets > campaign.budget_daily:
        warnings.append(
            ReadinessIssue(
                code="SUB_BUDGETS_EXCEED_CAMPAIGN_CEILING",
                category="warning",
                entity_type="campaign",
                entity_id=campaign.id,
                entity_name=campaign.name,
                field="budget_daily",
                message=(
                    f"Sum of active Ad Set daily sub-budgets (${total_sub_budgets:.2f}) exceeds campaign daily ceiling "
                    f"(${campaign.budget_daily:.2f}). Campaign spend will be throttled at the master ceiling."
                ),
            )
        )

    # Advisory warnings for inactive ad sets and empty ad sets
    for ad_set in all_ad_sets:
        if ad_set.status != Status.ACTIVE:
            warnings.append(
                ReadinessIssue(
                    code="AD_SET_INACTIVE",
                    category="warning",
                    entity_type="ad_set",
                    entity_id=ad_set.id,
                    entity_name=ad_set.name,
                    field="status",
                    message=f"Ad set '{ad_set.name}' is paused or inactive. Ads in this set will not be served.",
                )
            )
        if not [c for c in ad_set.creatives if c.status != Status.DELETED]:
            warnings.append(
                ReadinessIssue(
                    code="AD_SET_NO_CREATIVES",
                    category="warning",
                    entity_type="ad_set",
                    entity_id=ad_set.id,
                    entity_name=ad_set.name,
                    message=f"Ad set '{ad_set.name}' has no creatives assigned.",
                )
            )

    # 4. Creatives & Ads Checks
    all_creatives = [c for c in campaign.creatives if c.status != Status.DELETED]
    if not all_creatives:
        blockers.append(
            ReadinessIssue(
                code="NO_CREATIVES",
                category="blocker",
                entity_type="campaign",
                entity_id=campaign.id,
                entity_name=campaign.name,
                message="Campaign must have at least one creative",
            )
        )

    active_creatives = [c for c in all_creatives if c.status == Status.ACTIVE]
    if all_creatives and not active_creatives:
        blockers.append(
            ReadinessIssue(
                code="NO_ACTIVE_CREATIVES",
                category="blocker",
                entity_type="campaign",
                entity_id=campaign.id,
                entity_name=campaign.name,
                message="Campaign must have at least one active creative",
            )
        )

    # Check active creatives belong to an active AdSet (when AdSets are used)
    if all_ad_sets and active_creatives:
        active_set_ids = set(a.id for a in active_ad_sets)
        active_ads_in_active_sets = [c for c in active_creatives if c.ad_set_id in active_set_ids]
        if not active_ads_in_active_sets:
            blockers.append(
                ReadinessIssue(
                    code="NO_ACTIVE_ADS_IN_ACTIVE_ADSETS",
                    category="blocker",
                    entity_type="creative",
                    message="None of the active creatives belong to an active ad set.",
                )
            )

    # Moderation & Landing Domain Verification
    domain_service = DomainVerificationService(session)
    verified_domains_cache: dict[str, bool] = {}

    for c in active_creatives:
        # Destination URL syntax validation
        if not c.landing_url or not (c.landing_url.startswith("https://") or c.landing_url.startswith("http://")):
            blockers.append(
                ReadinessIssue(
                    code="INVALID_DESTINATION",
                    category="blocker",
                    entity_type="creative",
                    entity_id=c.id,
                    entity_name=c.title,
                    field="landing_url",
                    message=f"Creative '{c.title}' has an invalid destination URL '{c.landing_url}'.",
                )
            )
            continue

        try:
            parsed = urlparse(c.landing_url)
            host = parsed.netloc.split(":")[0].lower()
            if not host:
                blockers.append(
                    ReadinessIssue(
                        code="INVALID_DESTINATION",
                        category="blocker",
                        entity_type="creative",
                        entity_id=c.id,
                        entity_name=c.title,
                        field="landing_url",
                        message=f"Creative '{c.title}' landing URL must specify a valid domain.",
                    )
                )
                continue
        except Exception:
            blockers.append(
                ReadinessIssue(
                    code="INVALID_DESTINATION",
                    category="blocker",
                    entity_type="creative",
                    entity_id=c.id,
                    entity_name=c.title,
                    field="landing_url",
                    message=f"Creative '{c.title}' landing URL could not be parsed.",
                )
            )
            continue

        # Destination safety & SSRF / policy check
        from liteads.ad_server.services.destination_validator import DestinationValidator
        dest_val = await DestinationValidator.check_destination(c.landing_url, perform_fetch=False)
        if not dest_val.is_valid:
            blockers.append(
                ReadinessIssue(
                    code="UNSAFE_OR_MALICIOUS_DESTINATION",
                    category="blocker",
                    entity_type="creative",
                    entity_id=c.id,
                    entity_name=c.title,
                    field="landing_url",
                    message=f"Creative '{c.title}' destination failed safety check: {dest_val.reason}",
                )
            )
            continue

        # Moderation approval check
        if c.moderation_status != ModerationStatus.APPROVED.value:
            blockers.append(
                ReadinessIssue(
                    code="MODERATION_NOT_APPROVED",
                    category="blocker",
                    entity_type="creative",
                    entity_id=c.id,
                    entity_name=c.title,
                    field="moderation_status",
                    message=f"Active creative '{c.title}' is not approved by moderation",
                )
            )

        # Domain verification check
        if host not in verified_domains_cache:
            verified_domains_cache[host] = await domain_service.is_url_verified_for_advertiser(advertiser_id, c.landing_url)
        if not verified_domains_cache[host]:
            blockers.append(
                ReadinessIssue(
                    code="UNVERIFIED_DOMAIN",
                    category="blocker",
                    entity_type="creative",
                    entity_id=c.id,
                    entity_name=c.title,
                    field="landing_url",
                    message=f"Landing URL domain for creative '{c.title}' is not verified",
                )
            )

    # 5. Targeting Rules Checks
    has_campaign_targeting = bool(campaign.targeting_rules)
    has_ad_set_targeting = any(bool(a.targeting_rules) for a in active_ad_sets)

    if not has_campaign_targeting and not has_ad_set_targeting:
        blockers.append(
            ReadinessIssue(
                code="MISSING_TARGETING",
                category="blocker",
                entity_type="campaign",
                entity_id=campaign.id,
                entity_name=campaign.name,
                message="Campaign must have targeting configured",
            )
        )
    elif all_ad_sets and not has_campaign_targeting:
        # Check if any active ad set is missing targeting
        for a in active_ad_sets:
            if not a.targeting_rules:
                warnings.append(
                    ReadinessIssue(
                        code="AD_SET_MISSING_TARGETING",
                        category="warning",
                        entity_type="ad_set",
                        entity_id=a.id,
                        entity_name=a.name,
                        message=f"Ad set '{a.name}' has no specific targeting rules configured.",
                    )
                )

    # Pre-flight launch summary
    ad_set_summaries = [
        {
            "id": a.id,
            "name": a.name,
            "status": a.status,
            "daily_budget": float(a.daily_budget) if a.daily_budget is not None else None,
            "bid_amount": float(a.bid_amount),
            "ads_count": len([c for c in a.creatives if c.status != Status.DELETED]),
            "active_ads_count": len([c for c in a.creatives if c.status == Status.ACTIVE]),
            "has_targeting": bool(a.targeting_rules),
        }
        for a in all_ad_sets
    ]

    approved_count = len([c for c in all_creatives if c.moderation_status == ModerationStatus.APPROVED.value])
    verified_count = sum(1 for c in all_creatives if verified_domains_cache.get(urlparse(c.landing_url).netloc.split(":")[0].lower(), False))

    # Determine user-visible lifecycle state
    if campaign.status == Status.DELETED:
        lifecycle_state = "Archived"
    elif campaign.status in (Status.PAUSED, Status.INACTIVE):
        lifecycle_state = "Paused"
    elif campaign.status == Status.ACTIVE:
        if campaign.end_time:
            end_tz = campaign.end_time if campaign.end_time.tzinfo else campaign.end_time.replace(tzinfo=timezone.utc)
            if end_tz < now_utc:
                lifecycle_state = "Completed"
            else:
                start_tz = campaign.start_time if campaign.start_time and campaign.start_time.tzinfo else (campaign.start_time.replace(tzinfo=timezone.utc) if campaign.start_time else None)
                if start_tz and start_tz > now_utc:
                    lifecycle_state = "Scheduled"
                else:
                    lifecycle_state = "Active"
        else:
            start_tz = campaign.start_time if campaign.start_time and campaign.start_time.tzinfo else (campaign.start_time.replace(tzinfo=timezone.utc) if campaign.start_time else None)
            if start_tz and start_tz > now_utc:
                lifecycle_state = "Scheduled"
            else:
                lifecycle_state = "Active"
    elif campaign.status in (Status.DRAFT, Status.PENDING):
        has_rejected = any(c.moderation_status == ModerationStatus.REJECTED.value for c in all_creatives)
        has_pending = any(c.moderation_status == ModerationStatus.PENDING_REVIEW.value for c in active_creatives)
        if has_rejected:
            lifecycle_state = "Rejected"
        elif has_pending:
            lifecycle_state = "Pending Review"
        elif blockers:
            missing_setup_codes = {"NO_CREATIVES", "NO_ACTIVE_CREATIVES", "NO_ACTIVE_AD_SETS", "MISSING_TARGETING"}
            if any(b.code in missing_setup_codes for b in blockers):
                lifecycle_state = "Needs Setup"
            else:
                lifecycle_state = "Needs Changes"
        else:
            start_tz = campaign.start_time if campaign.start_time and campaign.start_time.tzinfo else (campaign.start_time.replace(tzinfo=timezone.utc) if campaign.start_time else None)
            if start_tz and start_tz > now_utc:
                lifecycle_state = "Scheduled"
            else:
                lifecycle_state = "Approved / Eligible"
    else:
        lifecycle_state = "Draft"

    summary = {
        "wallet_balance": float(advertiser.balance),
        "budget_daily": float(campaign.budget_daily),
        "budget_total": float(campaign.budget_total),
        "bid_type": campaign.bid_type,
        "bid_amount": float(campaign.bid_amount),
        "freq_cap_daily": campaign.freq_cap_daily,
        "freq_cap_hourly": campaign.freq_cap_hourly,
        "start_time": campaign.start_time.isoformat() if campaign.start_time else None,
        "end_time": campaign.end_time.isoformat() if campaign.end_time else None,
        "total_ad_sets": len(all_ad_sets),
        "active_ad_sets": len(active_ad_sets),
        "total_creatives": len(all_creatives),
        "active_creatives": len(active_creatives),
        "approved_creatives": approved_count,
        "unapproved_creatives": len(all_creatives) - approved_count,
        "verified_creatives": verified_count,
        "unverified_creatives": len(all_creatives) - verified_count,
        "has_targeting": has_campaign_targeting or has_ad_set_targeting,
        "lifecycle_state": lifecycle_state,
        "ad_sets": ad_set_summaries,
    }

    return CampaignReadinessResponse(
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        status=campaign.status,
        status_name=status_name,
        lifecycle_state=lifecycle_state,
        is_ready=len(blockers) == 0,
        blockers=blockers,
        warnings=warnings,
        summary=summary,
    )


async def activate_campaign_logic(
    session: AsyncSession,
    campaign_id: int,
    advertiser_id: int,
) -> Campaign:
    """
    Server-authoritative activation logic.
    Locks records in canonical order (Advertiser -> Campaign), verifies all
    launch prerequisites, and transitions DRAFT campaigns to ACTIVE.
    """
    # 1. Canonical lock order: Advertiser -> Campaign
    advertiser = await session.get(Advertiser, advertiser_id, with_for_update=True)
    if advertiser is None:
        raise CampaignActivationError("Advertiser not found")

    # 2. Lock Campaign with relationships loaded
    stmt = (
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(
            selectinload(Campaign.creatives),
            selectinload(Campaign.targeting_rules),
            selectinload(Campaign.ad_sets).selectinload(AdSet.targeting_rules),
            selectinload(Campaign.ad_sets).selectinload(AdSet.creatives),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    result = await session.execute(stmt)
    campaign = result.scalar_one_or_none()

    if campaign is None:
        raise CampaignActivationError("Campaign not found")

    # 3. Authoritative readiness check
    readiness = await validate_campaign_readiness(session, campaign_id, advertiser_id)
    if readiness.blockers:
        primary = readiness.blockers[0].message
        raise CampaignActivationError(primary, blockers=[b.model_dump() for b in readiness.blockers])

    # 4. State transition
    campaign.status = Status.ACTIVE
    return campaign
