from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select
from liteads.models.ad import Campaign, Advertiser
from liteads.models.base import ModerationStatus
from liteads.ad_server.services.domain_verification import DomainVerificationService

class CampaignActivationError(Exception):
    pass

async def activate_campaign_logic(
    session: AsyncSession,
    campaign_id: int,
    advertiser_id: int
) -> Campaign:
    # 1. Canonical lock order: Advertiser -> Campaign
    advertiser = await session.get(Advertiser, advertiser_id, with_for_update=True)
    if advertiser is None:
        raise CampaignActivationError("Advertiser not found")

    if advertiser.balance <= Decimal("0"):
        raise CampaignActivationError("Insufficient wallet balance to activate campaign")

    # 2. Lock Campaign with relationships loaded
    stmt = (
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.creatives), selectinload(Campaign.targeting_rules))
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    result = await session.execute(stmt)
    campaign = result.scalar_one_or_none()
    
    if campaign is None:
        raise CampaignActivationError("Campaign not found")
    
    if campaign.advertiser_id != advertiser_id:
        raise CampaignActivationError("Campaign belongs to another advertiser")
    
    if campaign.status != 5:  # Status.DRAFT
        raise CampaignActivationError("Only DRAFT campaigns can be activated")
        
    if campaign.budget_daily <= Decimal("0"):
        raise CampaignActivationError("Campaign must have a daily budget greater than zero")
        
    if campaign.budget_total < campaign.budget_daily:
        raise CampaignActivationError("Total budget cannot be less than daily budget")
        
    if not campaign.creatives:
        raise CampaignActivationError("Campaign must have at least one creative")
        
    if not any(c.status == 1 for c in campaign.creatives):  # Status.ACTIVE
        raise CampaignActivationError("Campaign must have at least one active creative")
        
    domain_service = DomainVerificationService(session)
    for c in campaign.creatives:
        if c.status == 1: # Only check active creatives
            if c.moderation_status != ModerationStatus.APPROVED.value:
                raise CampaignActivationError(f"Active creative '{c.title}' is not approved by moderation")
            is_verified = await domain_service.is_url_verified_for_advertiser(advertiser_id, c.landing_url)
            if not is_verified:
                raise CampaignActivationError(f"Landing URL domain for creative '{c.title}' is not verified")
        
    if not campaign.targeting_rules:
        raise CampaignActivationError("Campaign must have targeting configured")

    campaign.status = 1  # Status.ACTIVE
    return campaign
