"""Financial billing and transaction service."""

from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models import Advertiser, Campaign, Publisher
from liteads.models.base import TransactionType
from liteads.models.finance import PlatformSettings, PublisherPayout, Transaction

logger = logging.getLogger(__name__)


class BillingService:
    """Service for handling financial transactions with row-level locks."""

    @staticmethod
    async def get_rev_share(session: AsyncSession) -> Decimal:
        """Get the global publisher revenue share (e.g. 0.700)."""
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
        reference_id: str | None = None,
        currency: str = "INR",
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
            currency=currency,
            advertiser_id=advertiser_id,
            campaign_id=campaign_id,
            publisher_id=publisher_id,
            reference_id=reference_id,
            description="Ad Impression Spend",
        )
        session.add(spend_tx)

        return True

    @staticmethod
    async def process_cpa_charge(
        session: AsyncSession,
        advertiser_id: int,
        campaign_id: int,
        cost: Decimal,
        currency: str = "INR",
        publisher_id: int | None = None,
        order_id: str | None = None,
        request_id: str | None = None,
    ) -> bool:
        """
        Atomically process CPA conversion charge.
        Canonical billing path ensuring:
        1. DB uniqueness on Transaction.reference_id (cpa:{campaign_id}:{order_id or request_id})
        2. Row-level locks on Advertiser and Campaign
        3. Real publisher credit if publisher_id provided; direct inventory treatment if None
        4. Centralized configurable revenue share calculation
        """
        if cost <= Decimal("0"):
            return True

        canonical_key = f"cpa:{campaign_id}:{order_id}" if order_id else f"cpa:{campaign_id}:{request_id}"

        # 1. Check if already billed
        existing_stmt = select(Transaction).where(Transaction.reference_id == canonical_key)
        existing_tx = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing_tx:
            logger.info("CPA conversion charge already billed idempotently", reference_id=canonical_key)
            return True

        # 2. Lock Advertiser and Campaign
        adv_stmt = select(Advertiser).where(Advertiser.id == advertiser_id).with_for_update()
        camp_stmt = select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        adv = (await session.execute(adv_stmt)).scalar_one_or_none()
        camp = (await session.execute(camp_stmt)).scalar_one_or_none()

        if not adv or not camp:
            logger.error("Billing failed: adv or camp not found", advertiser_id=advertiser_id, campaign_id=campaign_id)
            return False

        if adv.balance < cost:
            logger.warning("Billing failed: insufficient funds", balance=adv.balance, cost=cost)
            return False

        # 3. Handle publisher credit if real publisher resolved
        if publisher_id:
            pub_stmt = select(Publisher).where(Publisher.id == publisher_id).with_for_update()
            pub = (await session.execute(pub_stmt)).scalar_one_or_none()
            if pub:
                rev_share = await BillingService.get_rev_share(session)
                publisher_earnings = cost * rev_share
                pub.balance += publisher_earnings

        # 4. Update balances
        adv.balance -= cost
        camp.spent_today += cost
        camp.spent_total += cost

        # 5. Record Transaction
        spend_tx = Transaction(
            type=TransactionType.SPEND,
            amount=-cost,
            currency=currency,
            advertiser_id=advertiser_id,
            campaign_id=campaign_id,
            publisher_id=publisher_id,
            reference_id=canonical_key,
            description=f"CPA Conversion Charge (Order: {order_id or request_id})",
        )
        session.add(spend_tx)
        return True

    @staticmethod
    async def record_deposit(
        session: AsyncSession,
        advertiser_id: int,
        amount: Decimal,
        reference_id: str | None = None,
        description: str = "Deposit",
        currency: str = "INR",
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
            currency=currency,
            advertiser_id=advertiser_id,
            reference_id=reference_id,
            description=description,
        )
        session.add(tx)
        return tx

    @staticmethod
    async def request_payout(
        session: AsyncSession,
        publisher_id: int,
        amount: Decimal,
        currency: str = "INR",
        reference_id: str | None = None,
        notes: str | None = None,
    ) -> tuple[PublisherPayout, Transaction]:
        """
        Record a publisher payout request and hold/reserve funds.
        Does NOT claim payment succeeded; clearly records a reservation in 'requested' state.
        """
        if amount <= Decimal("0"):
            raise ValueError("Payout amount must be positive")

        pub_stmt = select(Publisher).where(Publisher.id == publisher_id).with_for_update()
        pub = (await session.execute(pub_stmt)).scalar_one_or_none()
        if not pub:
            raise ValueError(f"Publisher {publisher_id} not found")

        if pub.balance < amount:
            raise ValueError("Insufficient balance for payout")

        ref_id = reference_id or f"payout_{publisher_id}_{int(datetime.now(timezone.utc).timestamp())}"

        # Hold the requested funds
        pub.balance -= amount

        payout = PublisherPayout(
            publisher_id=publisher_id,
            amount=amount,
            currency=currency,
            status="requested",
            reference_id=ref_id,
            notes=notes or "Payout requested by publisher; funds held in reservation",
        )
        session.add(payout)

        tx = Transaction(
            type=TransactionType.PAYOUT,
            amount=-amount,
            currency=currency,
            publisher_id=publisher_id,
            reference_id=ref_id,
            description=f"Payout Reservation (Status: Requested, Ref: {ref_id})",
        )
        session.add(tx)
        return payout, tx

    @staticmethod
    async def reject_payout(
        session: AsyncSession,
        payout_id: int,
        reason: str = "Payout rejected by admin",
    ) -> tuple[PublisherPayout, Transaction]:
        """
        Reject a pending payout request and restore the reserved funds to the publisher's balance.
        """
        payout_stmt = select(PublisherPayout).where(PublisherPayout.id == payout_id).with_for_update()
        payout = (await session.execute(payout_stmt)).scalar_one_or_none()
        if not payout:
            raise ValueError(f"Payout record {payout_id} not found")

        if payout.status not in ("requested", "approved"):
            raise ValueError(f"Cannot reject payout in status '{payout.status}'")

        pub_stmt = select(Publisher).where(Publisher.id == payout.publisher_id).with_for_update()
        pub = (await session.execute(pub_stmt)).scalar_one_or_none()
        if not pub:
            raise ValueError(f"Publisher {payout.publisher_id} not found")

        # Restore the held funds
        pub.balance += payout.amount
        payout.status = "rejected"
        payout.notes = f"{payout.notes or ''} | Rejected: {reason}".strip(" |")

        # Record refund/reversal transaction
        reversal_ref = f"rev_{payout.reference_id}"
        reversal_tx = Transaction(
            type=TransactionType.REFUND,
            amount=payout.amount,
            currency=payout.currency,
            publisher_id=payout.publisher_id,
            reference_id=reversal_ref,
            description=f"Payout Reservation Reversed: {reason}",
        )
        session.add(reversal_tx)
        return payout, reversal_tx

    @staticmethod
    async def confirm_payout(
        session: AsyncSession,
        payout_id: int,
        provider_payout_id: str,
        notes: str | None = None,
    ) -> PublisherPayout:
        """
        Confirm that external payment provider has executed the payout.
        Transitions status from 'requested'/'processing' to 'paid'.
        """
        payout_stmt = select(PublisherPayout).where(PublisherPayout.id == payout_id).with_for_update()
        payout = (await session.execute(payout_stmt)).scalar_one_or_none()
        if not payout:
            raise ValueError(f"Payout record {payout_id} not found")

        if payout.status not in ("requested", "approved", "processing"):
            raise ValueError(f"Cannot confirm payout in status '{payout.status}'")

        payout.status = "paid"
        payout.provider_payout_id = provider_payout_id
        payout.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if notes:
            payout.notes = f"{payout.notes or ''} | {notes}".strip(" |")
        return payout

    @staticmethod
    async def record_payout(
        session: AsyncSession,
        publisher_id: int,
        amount: Decimal,
        reference_id: str | None = None,
        description: str = "Payout",
        currency: str = "INR",
    ) -> Transaction:
        """Record a payout reservation (delegates to request_payout)."""
        _, tx = await BillingService.request_payout(
            session=session,
            publisher_id=publisher_id,
            amount=amount,
            currency=currency,
            reference_id=reference_id,
            notes=description,
        )
        return tx

    @staticmethod
    async def process_dispute_resolution(
        session: AsyncSession,
        dispute_id: int,
        status: str,
        amount_credited: Decimal,
        notes: str,
        admin_user_id: int,
    ) -> Any:
        """
        Process dispute resolution atomically:
        - If RESOLVED_REFUNDED (advertiser): credits advertiser wallet and writes REFUND ledger entry.
        - If RESOLVED_CREDITED (publisher): credits publisher balance and writes adjustment ledger entry.
        - If REJECTED: marks dispute rejected with notes.
        """
        from liteads.models.finance import BillingDispute
        dispute_stmt = select(BillingDispute).where(BillingDispute.id == dispute_id).with_for_update()
        dispute = (await session.execute(dispute_stmt)).scalar_one_or_none()
        if not dispute:
            raise ValueError(f"Dispute {dispute_id} not found")

        if dispute.status not in ("OPEN", "UNDER_REVIEW"):
            raise ValueError(f"Cannot resolve dispute in status '{dispute.status}'")

        if status == "RESOLVED_REFUNDED" and amount_credited > Decimal("0"):
            if not dispute.advertiser_id:
                raise ValueError("Cannot refund advertiser: dispute has no advertiser_id")
            adv_stmt = select(Advertiser).where(Advertiser.id == dispute.advertiser_id).with_for_update()
            adv = (await session.execute(adv_stmt)).scalar_one_or_none()
            if not adv:
                raise ValueError(f"Advertiser {dispute.advertiser_id} not found")

            adv.balance += amount_credited
            refund_tx = Transaction(
                type=TransactionType.REFUND,
                amount=amount_credited,
                currency=dispute.currency,
                advertiser_id=dispute.advertiser_id,
                campaign_id=dispute.campaign_id,
                reference_id=f"dispute_refund_{dispute.id}",
                description=f"Dispute Refund: {notes}",
            )
            session.add(refund_tx)

        elif status == "RESOLVED_CREDITED" and amount_credited > Decimal("0"):
            if not dispute.publisher_id:
                raise ValueError("Cannot credit publisher: dispute has no publisher_id")
            pub_stmt = select(Publisher).where(Publisher.id == dispute.publisher_id).with_for_update()
            pub = (await session.execute(pub_stmt)).scalar_one_or_none()
            if not pub:
                raise ValueError(f"Publisher {dispute.publisher_id} not found")

            pub.balance += amount_credited
            credit_tx = Transaction(
                type=TransactionType.REFUND,
                amount=amount_credited,
                currency=dispute.currency,
                publisher_id=dispute.publisher_id,
                reference_id=f"dispute_credit_{dispute.id}",
                description=f"Publisher Traffic Appeal Credit: {notes}",
            )
            session.add(credit_tx)

        dispute.status = status
        dispute.amount_credited = amount_credited
        dispute.resolution_notes = notes
        dispute.resolved_by_id = admin_user_id
        dispute.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)

        return dispute
