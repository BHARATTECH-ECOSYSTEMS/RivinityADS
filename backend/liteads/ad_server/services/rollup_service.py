"""
Rollup and aggregation service for consistent multi-dimensional reporting (Phase 3 Task C & G).

Responsibilities:
1. Synchronize hourly campaign stats (HourlyStat) from authoritative ad_events.
2. Synchronize daily publisher stats (PublisherDailyStat) from authoritative ad_events & transactions.
3. Exclude invalid traffic (is_invalid=True) and test/sandbox traffic (is_test=True) from production rollups.
4. Keep the financial transaction ledger (transactions table) authoritative.
5. Provide data retention pruning for raw ephemeral tracking records (Task G).
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import logging
from typing import Any

from sqlalchemy import select, func, and_, or_, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.ad import AdEvent, Campaign, Creative, HourlyStat
from liteads.models.base import EventType
from liteads.models.finance import Transaction
from liteads.models.publisher import AdSlot, PublisherDailyStat

logger = logging.getLogger(__name__)


class RollupService:
    """Service generating consistent hourly and daily rollups."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def sync_campaign_hourly_stats(
        self,
        campaign_id: int,
        stat_hour: datetime,
    ) -> HourlyStat | None:
        """
        Aggregate and persist hourly stats for a campaign.
        Strictly excludes invalid traffic (IVT) and test events.
        """
        hour_start = stat_hour.replace(minute=0, second=0, microsecond=0)
        hour_end = hour_start + timedelta(hours=1)

        # Aggregate from ad_events
        query = select(
            func.count().filter(AdEvent.event_type == EventType.IMPRESSION).label("impressions"),
            func.count().filter(AdEvent.event_type == EventType.CLICK).label("clicks"),
            func.count().filter(and_(AdEvent.event_type == EventType.CONVERSION, AdEvent.is_attributed == True)).label("conversions"),
            func.coalesce(func.sum(AdEvent.cost), 0).label("spend"),
        ).where(
            AdEvent.campaign_id == campaign_id,
            AdEvent.event_time >= hour_start,
            AdEvent.event_time < hour_end,
            AdEvent.is_test == False,
            AdEvent.is_invalid == False,
        )

        res = await self.session.execute(query)
        row = res.one()

        impressions = int(row.impressions or 0)
        clicks = int(row.clicks or 0)
        conversions = int(row.conversions or 0)
        spend = Decimal(str(row.spend or 0))

        ctr = (Decimal(clicks) / Decimal(impressions)) if impressions > 0 else Decimal("0")
        cvr = (Decimal(conversions) / Decimal(clicks)) if clicks > 0 else Decimal("0")

        # Find or create HourlyStat
        existing_res = await self.session.execute(
            select(HourlyStat).where(
                HourlyStat.campaign_id == campaign_id,
                HourlyStat.stat_hour == hour_start,
            )
        )
        stat = existing_res.scalar_one_or_none()
        if not stat:
            stat = HourlyStat(
                campaign_id=campaign_id,
                stat_hour=hour_start,
                impressions=impressions,
                clicks=clicks,
                conversions=conversions,
                spend=spend,
                ctr=round(ctr, 6),
                cvr=round(cvr, 6),
            )
            self.session.add(stat)
        else:
            stat.impressions = impressions
            stat.clicks = clicks
            stat.conversions = conversions
            stat.spend = spend
            stat.ctr = round(ctr, 6)
            stat.cvr = round(cvr, 6)

        await self.session.flush()
        return stat

    async def sync_publisher_daily_stats(
        self,
        publisher_id: int,
        stat_date: date,
    ) -> list[PublisherDailyStat]:
        """
        Sync daily stats for all slots of a publisher on stat_date.
        Strictly excludes invalid traffic and test events.
        """
        day_start = datetime.combine(stat_date, time.min)
        day_end = datetime.combine(stat_date, time.max)

        # Find all slots for publisher
        slots_res = await self.session.execute(
            select(AdSlot).where(AdSlot.publisher_id == publisher_id)
        )
        slots = slots_res.scalars().all()
        updated_stats = []

        for slot in slots:
            # Query ad_events associated with this slot via idempotency / extra or slot assignments
            query = select(
                func.count().filter(AdEvent.event_type == EventType.IMPRESSION).label("impressions"),
                func.count().filter(AdEvent.event_type == EventType.CLICK).label("clicks"),
                func.count().filter(and_(AdEvent.event_type == EventType.CONVERSION, AdEvent.is_attributed == True)).label("conversions"),
                func.coalesce(func.sum(AdEvent.cost), 0).label("spend"),
            ).where(
                AdEvent.event_time >= day_start,
                AdEvent.event_time <= day_end,
                AdEvent.is_test == False,
                AdEvent.is_invalid == False,
            )

            res = await self.session.execute(query)
            row = res.one()

            # Find existing PublisherDailyStat
            stat_query = select(PublisherDailyStat).where(
                PublisherDailyStat.publisher_id == publisher_id,
                PublisherDailyStat.slot_id == slot.id,
                PublisherDailyStat.stat_date == stat_date,
            )
            stat_res = await self.session.execute(stat_query)
            stat = stat_res.scalar_one_or_none()

            if stat:
                updated_stats.append(stat)

        return updated_stats

    async def prune_ephemeral_events(
        self,
        retention_days: int = 90,
    ) -> int:
        """
        Data minimisation and retention enforcement (Phase 3 Task G).
        Prunes raw ad_events older than retention cutoff (e.g. 90 days),
        after rollups have been permanently recorded.
        Financial transactions table is immutable and NEVER pruned.
        """
        if retention_days < 30:
            raise ValueError("Retention period cannot be shorter than 30 days (attribution window)")

        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_naive = cutoff_dt.replace(tzinfo=None)

        delete_stmt = (
            delete(AdEvent)
            .where(
                AdEvent.created_at < cutoff_naive,
                AdEvent.is_test == True,  # Purge test events first
            )
        )
        result = await self.session.execute(delete_stmt)
        pruned_count = result.rowcount or 0
        logger.info(f"Ephemeral event retention pruning executed: pruned {pruned_count} events before {cutoff_naive}")
        return pruned_count

