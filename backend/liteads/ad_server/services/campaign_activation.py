from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from liteads.models.ad import Campaign, Advertiser

class CampaignActivationError(Exception):
    pass

async def activate_campaign_logic(
    session: AsyncSession,
    campaign_id: int,
    advertiser_id: int
) -> Campaign:
    campaign = await session.get(Campaign, campaign_id, with_for_update=True)
    if campaign is None:
        raise CampaignActivationError("Campaign not found")
    
    if campaign.advertiser_id != advertiser_id:
        raise CampaignActivationError("Campaign belongs to another advertiser")
    
    if campaign.status != 5:  # Status.DRAFT
        raise CampaignActivationError("Only DRAFT campaigns can be activated")
        
    advertiser = await session.get(Advertiser, campaign.advertiser_id, with_for_update=True)
    if advertiser is None:
        raise CampaignActivationError("Advertiser not found")
        
    if advertiser.balance <= Decimal("0"):
        raise CampaignActivationError("Insufficient wallet balance to activate campaign")
        
    campaign.status = 1  # Status.ACTIVE
    return campaign
