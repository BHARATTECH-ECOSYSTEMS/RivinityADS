from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from liteads.models.auth import User
from liteads.models.ad import Campaign, Advertiser, TargetingRule
from liteads.models.base import Status
from liteads.schemas.targeting import TargetingSet
from liteads.schemas.finance import PaymentOrderResponse
from liteads.ad_server.services.razorpay_client import get_razorpay_client, RazorpayClientError
from liteads.ad_server.services.campaign_activation import activate_campaign_logic, CampaignActivationError
from sqlalchemy import select


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


class UpdateTargetingResult(BaseModel):
    """Minimal safe response object for orchestration."""
    campaign_id: int
    status: str
    targeting: TargetingSet


async def update_targeting(
    user: User,
    session: AsyncSession,
    campaign_id: int,
    targeting: TargetingSet,
) -> UpdateTargetingResult:
    """
    Updates the targeting configuration of an existing DRAFT campaign.
    Replaces existing targeting rules fully.
    
    Raises:
        ToolAuthorizationError: If the user lacks permission or the campaign isn't found/owned by them.
        ToolValidationError: If the campaign is not in DRAFT status.
        ToolError: If a database operation fails.
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

    # The input targeting is already validated by Pydantic (TargetingSet)

    try:
        # Delete existing targeting rules for geo and sector
        query = select(TargetingRule).where(
            TargetingRule.campaign_id == campaign_id,
            TargetingRule.rule_type.in_(["geo", "sector"])
        )
        result = await session.execute(query)
        existing_rules = result.scalars().all()
        for rule in existing_rules:
            await session.delete(rule)

        # Insert new rules
        if targeting.geo_include:
            session.add(
                TargetingRule(
                    campaign_id=campaign_id,
                    rule_type="geo",
                    is_include=True,
                    rule_value={"entries": [e.model_dump(exclude_none=True) for e in targeting.geo_include]}
                )
            )
        if targeting.geo_exclude:
            session.add(
                TargetingRule(
                    campaign_id=campaign_id,
                    rule_type="geo",
                    is_include=False,
                    rule_value={"entries": [e.model_dump(exclude_none=True) for e in targeting.geo_exclude]}
                )
            )

        if targeting.sector_include:
            session.add(
                TargetingRule(
                    campaign_id=campaign_id,
                    rule_type="sector",
                    is_include=True,
                    rule_value={"sectors": targeting.sector_include}
                )
            )
        if targeting.sector_exclude:
            session.add(
                TargetingRule(
                    campaign_id=campaign_id,
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
        status="DRAFT",
        targeting=targeting
    )


async def request_payment(
    user: User,
    session: AsyncSession,
    amount: Decimal,
) -> PaymentOrderResponse:
    """
    Creates a Razorpay payment order for wallet top-up.
    Does NOT credit the wallet. Wallet credit happens only after webhook verification.

    Args:
        user: The authenticated User. Must have an advertiser_id.
        amount: The payment amount in INR. Minimum ₹100.
        
    Raises:
        ToolAuthorizationError: If the user lacks an advertiser account.
        ToolValidationError: If the amount is less than ₹100.
        ToolError: If the Razorpay API fails.
    """
    if not user.advertiser_id:
        raise ToolAuthorizationError("User does not have an associated advertiser account")
    if amount < Decimal("100"):
        raise ToolValidationError("Amount to top up must be at least ₹100")

    import uuid
    from liteads.models.finance import PaymentRequest
    
    internal_reference = f"adv_{user.advertiser_id}_qr_{uuid.uuid4().hex[:12]}"
    
    razorpay_client = get_razorpay_client()
    try:
        # Create Razorpay QR code instead of regular order
        qr = razorpay_client.create_qr_code(
            amount=amount,
            receipt=internal_reference,
            advertiser_id=user.advertiser_id
        )
        qr_id = qr["id"]
        qr_image_url = qr.get("image_url")

        # Persist the request
        payment_request = PaymentRequest(
            advertiser_id=user.advertiser_id,
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
