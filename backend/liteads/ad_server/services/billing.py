"""Financial billing and transaction service."""

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models import Advertiser, Campaign, Publisher
from liteads.models.base import TransactionType
from liteads.models.finance import PlatformSettings, Transaction

logger = logging.getLogger(__name__)


class BillingService:
    """Service for handling financial transactions with row-level locks."""

    @staticmethod
    async def get_rev_share(session: AsyncSession) -> Decimal:
        """Get the global publisher revenue share (e.g. 0.70)."""
        stmt = select(PlatformSettings).order_by(PlatformSettings.id.desc()).limit(1)
        result = await session.execute(stmt)
        settings = result.scalar_one_or_none()
        if settings:
            return settings.publisher_rev_share
        return Decimal("0.700")

    @staticmethod
    async def process_impression_charge(
        session: AsyncSession,
        advertiser_id: int,
        campaign_id: int,
        publisher_id: int,
        cost: Decimal,
        reference_id: str | None = None
    ) -> bool:
        """
        Atomically deduct cost from advertiser and campaign, and credit publisher.
        Uses SELECT FOR UPDATE to prevent concurrent race conditions.
        Returns True if successful, False if advertiser has insufficient funds.
        """
        if cost <= Decimal("0"):
            return True

        # Lock Advertiser and Campaign
        adv_stmt = select(Advertiser).where(Advertiser.id == advertiser_id).with_for_update()
        camp_stmt = select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        
        adv_res = await session.execute(adv_stmt)
        camp_res = await session.execute(camp_stmt)
        
        advertiser = adv_res.scalar_one_or_none()
        campaign = camp_res.scalar_one_or_none()

        if not advertiser or not campaign:
            logger.error(f"Billing failed: adv {advertiser_id} or camp {campaign_id} not found")
            return False

        if advertiser.balance < cost:
            logger.warning(f"Billing failed: adv {advertiser_id} insufficient funds ({advertiser.balance} < {cost})")
            return False

        # Lock Publisher
        pub_stmt = select(Publisher).where(Publisher.id == publisher_id).with_for_update()
        pub_res = await session.execute(pub_stmt)
        publisher = pub_res.scalar_one_or_none()
        
        if not publisher:
            logger.error(f"Billing failed: pub {publisher_id} not found")
            return False

        rev_share = await BillingService.get_rev_share(session)
        publisher_earnings = cost * rev_share

        # 1. Update balances
        advertiser.balance -= cost
        campaign.spent_today += cost
        campaign.spent_total += cost
        publisher.balance += publisher_earnings

        # 2. Record Spend Transaction (Deduction from Advertiser)
        spend_tx = Transaction(
            type=TransactionType.SPEND,
            amount=-cost,
            advertiser_id=advertiser_id,
            campaign_id=campaign_id,
            publisher_id=publisher_id,
            reference_id=reference_id,
            description="Ad Impression Spend"
        )
        session.add(spend_tx)

        return True

    @staticmethod
    async def record_deposit(
        session: AsyncSession,
        advertiser_id: int,
        amount: Decimal,
        reference_id: str | None = None,
        description: str = "Deposit"
    ) -> Transaction:
        """Record a deposit for an advertiser."""
        adv_stmt = select(Advertiser).where(Advertiser.id == advertiser_id).with_for_update()
        adv_res = await session.execute(adv_stmt)
        advertiser = adv_res.scalar_one_or_none()
        
        if not advertiser:
            raise ValueError(f"Advertiser {advertiser_id} not found")

        advertiser.balance += amount
        
        tx = Transaction(
            type=TransactionType.DEPOSIT,
            amount=amount,
            advertiser_id=advertiser_id,
            reference_id=reference_id,
            description=description
        )
        session.add(tx)
        return tx

    @staticmethod
    async def record_payout(
        session: AsyncSession,
        publisher_id: int,
        amount: Decimal,
        reference_id: str | None = None,
        description: str = "Payout"
    ) -> Transaction:
        """Record a payout to a publisher."""
        pub_stmt = select(Publisher).where(Publisher.id == publisher_id).with_for_update()
        pub_res = await session.execute(pub_stmt)
        publisher = pub_res.scalar_one_or_none()
        
        if not publisher:
            raise ValueError(f"Publisher {publisher_id} not found")

        if publisher.balance < amount:
            raise ValueError("Insufficient balance for payout")

        publisher.balance -= amount
        
        tx = Transaction(
            type=TransactionType.PAYOUT,
            amount=-amount,
            publisher_id=publisher_id,
            reference_id=reference_id,
            description=description
        )
        session.add(tx)
        return tx
