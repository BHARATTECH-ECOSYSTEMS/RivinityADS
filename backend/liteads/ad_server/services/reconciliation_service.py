"""
Reconciliation service comparing ad delivery events, billed spend, advertiser wallet debits,
publisher credits, and rollups (Phase 3 Task D).

Invariants:
- PostgreSQL transactions table is the permanent authoritative financial source of truth.
- Gross billable delivery (non-IVT, non-test ad_events.cost) must reconcile with transaction ledger debits.
- Publisher balance must reconcile with credited rev_share earnings minus completed payouts.
- Any discrepancy exceeding ₹0.01 generates an actionable alert.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
import logging
from typing import Any

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.ad import AdEvent, Advertiser, Campaign
from liteads.models.base import EventType, TransactionType
from liteads.models.finance import Transaction, PublisherPayout
from liteads.models.publisher import Publisher, PublisherDailyStat

logger = logging.getLogger(__name__)

DISCREPANCY_THRESHOLD = Decimal("0.01")  # ₹0.01 tolerance for minor rounding


@dataclass
class DiscrepancyItem:
    entity_type: str  # "advertiser", "publisher", "campaign", "global"
    entity_id: int | None
    name: str
    metric: str
    expected_value: Decimal
    actual_value: Decimal
    delta: Decimal
    severity: str  # "WARNING", "CRITICAL"
    details: str


@dataclass
class ReconciliationReport:
    status: str  # "BALANCED" or "DISCREPANCY_DETECTED"
    start_date: date
    end_date: date
    total_event_spend: Decimal
    total_ledger_spend: Decimal
    spend_delta: Decimal
    total_ivt_events: int
    ivt_suppressed_spend: Decimal
    discrepancies: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)


class ReconciliationService:
    """Service auditing and reconciling financial ledgers against delivery telemetry."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def reconcile(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        advertiser_id: int | None = None,
        publisher_id: int | None = None,
    ) -> ReconciliationReport:
        today = datetime.now(timezone.utc).date()
        start = start_date or today
        end = end_date or today

        start_dt = datetime.combine(start, time.min)
        end_dt = datetime.combine(end, time.max)

        discrepancy_list: list[DiscrepancyItem] = []
        alerts: list[str] = []

        # 1. Total Billable Event Delivery vs Financial Ledger SPEND
        event_spend_query = select(
            func.coalesce(func.sum(AdEvent.cost), Decimal("0"))
        ).where(
            AdEvent.event_time >= start_dt,
            AdEvent.event_time <= end_dt,
            AdEvent.is_test == False,
            AdEvent.is_invalid == False,
        )
        if advertiser_id:
            event_spend_query = event_spend_query.join(Campaign, Campaign.id == AdEvent.campaign_id).where(
                Campaign.advertiser_id == advertiser_id
            )

        event_spend = Decimal(str((await self.session.execute(event_spend_query)).scalar() or 0))

        ledger_spend_query = select(
            func.coalesce(func.sum(func.abs(Transaction.amount)), Decimal("0"))
        ).where(
            Transaction.created_at >= start_dt,
            Transaction.created_at <= end_dt,
            Transaction.type == TransactionType.SPEND,
        )
        if advertiser_id:
            ledger_spend_query = ledger_spend_query.where(Transaction.advertiser_id == advertiser_id)

        ledger_spend = Decimal(str((await self.session.execute(ledger_spend_query)).scalar() or 0))

        spend_delta = abs(event_spend - ledger_spend)
        if spend_delta > DISCREPANCY_THRESHOLD:
            severity = "CRITICAL" if spend_delta > Decimal("100.00") else "WARNING"
            discrepancy_list.append(
                DiscrepancyItem(
                    entity_type="global" if not advertiser_id else "advertiser",
                    entity_id=advertiser_id,
                    name="Delivery vs Ledger Spend",
                    metric="gross_spend",
                    expected_value=event_spend,
                    actual_value=ledger_spend,
                    delta=spend_delta,
                    severity=severity,
                    details=(
                        f"Event delivery spend ({event_spend}) does not match ledger debits ({ledger_spend}). "
                        f"Drift: {spend_delta}"
                    ),
                )
            )
            alerts.append(
                f"Gross spend reconciliation drift detected: Events ₹{event_spend} vs Ledger ₹{ledger_spend} (Δ ₹{spend_delta})"
            )

        # 2. Advertiser Wallet Balance vs Ledger Transactions
        adv_query = select(Advertiser)
        if advertiser_id:
            adv_query = adv_query.where(Advertiser.id == advertiser_id)
        advertisers = (await self.session.execute(adv_query)).scalars().all()

        for adv in advertisers:
            # Expected balance = Deposits + Refunds - Spends
            tx_query = select(
                func.coalesce(func.sum(Transaction.amount), Decimal("0"))
            ).where(Transaction.advertiser_id == adv.id)
            expected_balance = Decimal(str((await self.session.execute(tx_query)).scalar() or 0))
            actual_balance = Decimal(str(adv.balance or 0))
            balance_delta = abs(actual_balance - expected_balance)

            if balance_delta > DISCREPANCY_THRESHOLD:
                discrepancy_list.append(
                    DiscrepancyItem(
                        entity_type="advertiser",
                        entity_id=adv.id,
                        name=adv.name,
                        metric="wallet_balance",
                        expected_value=expected_balance,
                        actual_value=actual_balance,
                        delta=balance_delta,
                        severity="CRITICAL",
                        details=(
                            f"Advertiser {adv.name} (ID: {adv.id}) wallet balance ₹{actual_balance} "
                            f"differs from transaction ledger sum ₹{expected_balance} by ₹{balance_delta}"
                        ),
                    )
                )
                alerts.append(f"Advertiser ID {adv.id} wallet balance mismatch: Δ ₹{balance_delta}")

        # 3. Publisher Balance Reconciliation
        pub_query = select(Publisher)
        if publisher_id:
            pub_query = pub_query.where(Publisher.id == publisher_id)
        publishers = (await self.session.execute(pub_query)).scalars().all()

        for pub in publishers:
            # Expected publisher balance from PublisherDailyStat or payout transactions
            stat_earnings_query = select(
                func.coalesce(func.sum(PublisherDailyStat.earnings), Decimal("0"))
            ).where(PublisherDailyStat.publisher_id == pub.id)
            total_earned = Decimal(str((await self.session.execute(stat_earnings_query)).scalar() or 0))

            payouts_query = select(
                func.coalesce(func.sum(PublisherPayout.amount), Decimal("0"))
            ).where(
                PublisherPayout.publisher_id == pub.id,
                PublisherPayout.status.in_(["requested", "approved", "processing", "paid"]),
            )
            total_payouts = Decimal(str((await self.session.execute(payouts_query)).scalar() or 0))

            expected_pub_balance = total_earned - total_payouts
            actual_pub_balance = Decimal(str(pub.balance or 0))
            pub_delta = abs(actual_pub_balance - expected_pub_balance)

            if pub_delta > DISCREPANCY_THRESHOLD:
                discrepancy_list.append(
                    DiscrepancyItem(
                        entity_type="publisher",
                        entity_id=pub.id,
                        name=pub.name,
                        metric="publisher_balance",
                        expected_value=expected_pub_balance,
                        actual_value=actual_pub_balance,
                        delta=pub_delta,
                        severity="WARNING",
                        details=(
                            f"Publisher {pub.name} (ID: {pub.id}) balance ₹{actual_pub_balance} "
                            f"differs from earned minus payouts ₹{expected_pub_balance} by ₹{pub_delta}"
                        ),
                    )
                )

        # 4. IVT Metrics Summary
        ivt_count_query = select(
            func.count()
        ).where(
            AdEvent.event_time >= start_dt,
            AdEvent.event_time <= end_dt,
            AdEvent.is_invalid == True,
        )
        total_ivt_events = int((await self.session.execute(ivt_count_query)).scalar() or 0)

        # Calculate would-be cost of IVT (if it hadn't been zeroed) or count
        status = "BALANCED" if len(discrepancy_list) == 0 else "DISCREPANCY_DETECTED"

        return ReconciliationReport(
            status=status,
            start_date=start,
            end_date=end,
            total_event_spend=event_spend,
            total_ledger_spend=ledger_spend,
            spend_delta=spend_delta,
            total_ivt_events=total_ivt_events,
            ivt_suppressed_spend=Decimal("0.00"),
            discrepancies=[
                {
                    "entity_type": d.entity_type,
                    "entity_id": d.entity_id,
                    "name": d.name,
                    "metric": d.metric,
                    "expected_value": float(d.expected_value),
                    "actual_value": float(d.actual_value),
                    "delta": float(d.delta),
                    "severity": d.severity,
                    "details": d.details,
                }
                for d in discrepancy_list
            ],
            alerts=alerts,
        )

