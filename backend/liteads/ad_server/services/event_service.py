"""
Event tracking service.

Handles recording ad events (impressions, clicks, conversions).
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.cache import CacheKeys, redis_client
from liteads.common.logger import get_logger
from liteads.common.utils import current_date, current_hour
from liteads.models import AdEvent, AdSet, AdSlot, Campaign, Creative, EventType, PublisherDailyStat, Advertiser, Status, Publisher
from liteads.ad_server.services.billing import BillingService

logger = get_logger(__name__)


class EventService:
    """Event tracking service."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def track_event(
        self,
        request_id: str,
        ad_id: str,
        event_type: str,
        user_id: str | None = None,
        timestamp: int | None = None,
        extra: dict[str, Any] | None = None,
        seq: int = 1,
        raise_on_duplicate: bool = False,
    ) -> bool:
        """
        Track an ad event.

        Events are:
        1. Persisted to database for billing/reporting
        2. Cached in Redis for real-time stats
        3. Used for frequency control updates
        """
        try:
            # Parse ad ID to get campaign/creative IDs
            campaign_id, creative_id = self._parse_ad_id(ad_id)

            # Convert event type
            event_type_enum = self._get_event_type(event_type)
            if event_type_enum is None:
                logger.warning(f"Unknown event type: {event_type}")
                return False

            # Create event record with canonical row lock order: Advertiser -> Campaign
            if campaign_id:
                camp_peek = await self.session.get(Campaign, campaign_id)
                if camp_peek and camp_peek.advertiser_id:
                    await self.session.get(Advertiser, camp_peek.advertiser_id, with_for_update=True)
                result = await self.session.execute(
                    select(Campaign).where(Campaign.id == campaign_id).with_for_update()
                )
                campaign = result.scalar_one_or_none()
            else:
                campaign = None

            # Derive ad_set_id server-side from creative
            ad_set = None
            ad_set_id = None
            if creative_id:
                creative_obj = await self.session.get(Creative, creative_id)
                if creative_obj and creative_obj.ad_set_id:
                    ad_set = await self.session.get(AdSet, creative_obj.ad_set_id)
                    if ad_set:
                        ad_set_id = ad_set.id

            cost = self._calculate_cost(event_type_enum, campaign, ad_set)
            
            slot_id = extra.get("slot_id") if extra else None
            publisher_id = None
            slot = None
            if slot_id:
                slot_res = await self.session.execute(select(AdSlot).where(AdSlot.slot_id == slot_id))
                slot = slot_res.scalar_one_or_none()
                if slot:
                    publisher_id = slot.publisher_id

            # Phase 10F Financial Safeguards:
            # 1. Non-billable preview or sandbox traffic generates zero billable spend
            if (extra and (extra.get("is_preview") or extra.get("is_sandbox"))) or request_id.startswith(("prev_", "test_preview_")):
                cost = Decimal("0")

            # 2. Placements bound to unverified or inactive properties generate zero billable spend
            if slot and slot.property_id:
                from liteads.models.publisher import PublisherProperty
                prop_res = await self.session.execute(
                    select(PublisherProperty).where(PublisherProperty.id == slot.property_id)
                )
                prop_obj = prop_res.scalar_one_or_none()
                if not prop_obj or prop_obj.status != Status.ACTIVE or prop_obj.verification_status != "VERIFIED":
                    logger.warning("Event on unverified or inactive property; billable charge suppressed", slot_id=slot_id)
                    cost = Decimal("0")

            # 3. Placements owned by inactive or suspended publishers generate zero billable spend
            if publisher_id:
                pub_res = await self.session.execute(select(Publisher).where(Publisher.id == publisher_id))
                pub_obj = pub_res.scalar_one_or_none()
                if not pub_obj or pub_obj.status != Status.ACTIVE:
                    logger.warning("Event on inactive publisher; billable charge suppressed", publisher_id=publisher_id)
                    cost = Decimal("0")
            
            logical_event_key = f"{request_id}:{creative_id or 0}:{event_type_enum}:{seq}"

            if campaign and campaign.advertiser_id and publisher_id and cost > 0:
                success_bill = await BillingService.process_impression_charge(
                    self.session,
                    advertiser_id=campaign.advertiser_id,
                    campaign_id=campaign.id,
                    publisher_id=publisher_id,
                    cost=cost,
                    reference_id=logical_event_key,
                )
                if not success_bill:
                    logger.warning(f"Billing failed for event {request_id}")
                    return False
            elif campaign and campaign.updated_at.date() < datetime.now(timezone.utc).date():
                campaign.spent_today = 0

            event = AdEvent(
                request_id=request_id,
                campaign_id=campaign_id,
                creative_id=creative_id,
                event_type=event_type_enum,
                event_time=datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
                if timestamp
                else datetime.now(timezone.utc).replace(tzinfo=None),
                user_id=user_id,
                cost=cost,
                idempotency_key=logical_event_key,
            )

            self.session.add(event)
            await self.session.flush()

            await self._update_campaign_stats(campaign, creative_id, event_type_enum)
            await self._update_publisher_stats(event_type_enum, cost, extra)
            await self._update_user_profile(user_id, campaign_id, event_type_enum)

            # Update AdSet daily budget in Redis if applicable
            if ad_set_id and cost > 0:
                today = current_date()
                as_key = f"adset_budget:{ad_set_id}:{today}"
                try:
                    await redis_client.hincrbyfloat(as_key, "spent_today", float(cost))
                    await redis_client.expire(as_key, 86400 * 2)
                except Exception as e:
                    logger.error(f"Failed to update ad set spent for ad_set {ad_set_id}: {e}")

            # Update real-time stats in Redis
            await self._update_stats(campaign_id, event_type_enum, ad_set_id=ad_set_id)

            # Update frequency counter for impressions
            if event_type_enum == EventType.IMPRESSION and user_id:
                await self._update_frequency(user_id, campaign_id, slot_id=slot_id, ad_set_id=ad_set_id)

            # ── Webhook dispatch (fire-and-forget background tasks) ──────
            await self._dispatch_webhooks(
                event_type=event_type,
                request_id=request_id,
                ad_id=ad_id,
                campaign_id=campaign_id,
                creative_id=creative_id,
                user_id=user_id,
                cost=cost,
                advertiser_id=campaign.advertiser_id if campaign else None,
                publisher_id=publisher_id,
                extra=extra,
            )

            logger.info(
                "Event tracked",
                event_id=event.id,
                campaign_id=campaign_id,
            )

            return True

        except IntegrityError as ie:
            await self.session.rollback()
            err_msg = str(ie).lower()
            if (
                "idempotency_key" in err_msg
                or "uq_ad_events" in err_msg
                or "reference_id" in err_msg
                or "uq_transactions" in err_msg
            ):
                logger.info(f"Duplicate event rejected by database: {ie}")
                if raise_on_duplicate:
                    raise HTTPException(status_code=429, detail="Duplicate event rejected")
                return False
            logger.error(f"IntegrityError tracking event: {ie}")
            return False
        except HTTPException:
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            logger.error(f"Failed to track event: {e}")
            return False


    def _parse_ad_id(self, ad_id: str) -> tuple[int | None, int | None]:
        """Parse ad ID to extract campaign and creative IDs."""
        # Expected format: ad_{campaign_id}_{creative_id}
        try:
            parts = ad_id.split("_")
            if len(parts) >= 3:
                return int(parts[1]), int(parts[2])
            elif len(parts) >= 2:
                return int(parts[1]), None
            else:
                return int(ad_id), None
        except (ValueError, IndexError):
            logger.warning(f"Invalid ad_id format: {ad_id}")
            return None, None

    def _get_event_type(self, event_type: str) -> int | None:
        """Convert event type string to enum."""
        mapping = {
            "impression": EventType.IMPRESSION,
            "imp": EventType.IMPRESSION,
            "click": EventType.CLICK,
            "clk": EventType.CLICK,
            "conversion": EventType.CONVERSION,
            "conv": EventType.CONVERSION,
        }
        return mapping.get(event_type.lower())

    def _calculate_cost(self, event_type: int, campaign: Campaign | None, ad_set: AdSet | None = None) -> Decimal:
        """Calculate cost for the event, respecting ad_set bid if specified."""
        if campaign is None:
            return Decimal("0.000000")
        bid_type = campaign.bid_type
        bid_amount = campaign.bid_amount
        if ad_set and ad_set.bid_amount and Decimal(ad_set.bid_amount) > 0:
            bid_type = ad_set.bid_type
            bid_amount = ad_set.bid_amount

        if bid_type == 1 and event_type == EventType.IMPRESSION:
            return Decimal(bid_amount) / Decimal("1000")
        if bid_type == 2 and event_type == EventType.CLICK:
            return Decimal(bid_amount)
        if bid_type == 3 and event_type == EventType.CONVERSION:
            return Decimal(bid_amount)
        return Decimal("0.000000")

    async def _update_campaign_stats(
        self,
        campaign: Campaign | None,
        creative_id: int | None,
        event_type: int,
    ) -> None:
        """Update cached campaign and creative counters."""
        if campaign is None:
            return
        if event_type == EventType.IMPRESSION:
            campaign.impressions += 1
        elif event_type == EventType.CLICK:
            campaign.clicks += 1
        elif event_type == EventType.CONVERSION:
            campaign.conversions += 1
        if creative_id:
            creative = await self.session.get(Creative, creative_id)
            if creative:
                if event_type == EventType.IMPRESSION:
                    creative.impressions += 1
                elif event_type == EventType.CLICK:
                    creative.clicks += 1
                elif event_type == EventType.CONVERSION:
                    creative.conversions += 1

    async def _update_publisher_stats(
        self,
        event_type: int,
        cost: Decimal,
        extra: dict[str, Any] | None,
    ) -> None:
        """Attribute tracked delivery to a slot and persist publisher earnings."""
        slot_id = extra.get("slot_id") if extra else None
        if not slot_id:
            return
        result = await self.session.execute(select(AdSlot).where(AdSlot.slot_id == slot_id))
        slot = result.scalar_one_or_none()
        if slot is None:
            return
        stat_date = datetime.now(timezone.utc).date()
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        delta_impressions = 1 if event_type == EventType.IMPRESSION else 0
        delta_clicks = 1 if event_type == EventType.CLICK else 0
        delta_conversions = 1 if event_type == EventType.CONVERSION else 0
        delta_earnings = cost * Decimal("0.70")

        # Database-atomic upsert across supported dialects
        dialect_name = self.session.get_bind().dialect.name
        if dialect_name == "postgresql":
            stmt = pg_insert(PublisherDailyStat).values(
                publisher_id=slot.publisher_id,
                slot_id=slot.id,
                stat_date=stat_date,
                impressions=delta_impressions,
                clicks=delta_clicks,
                conversions=delta_conversions,
                earnings=delta_earnings,
                created_at=now_utc,
                updated_at=now_utc,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_pub_daily_stats_slot_date",
                set_={
                    "impressions": PublisherDailyStat.impressions + stmt.excluded.impressions,
                    "clicks": PublisherDailyStat.clicks + stmt.excluded.clicks,
                    "conversions": PublisherDailyStat.conversions + stmt.excluded.conversions,
                    "earnings": PublisherDailyStat.earnings + stmt.excluded.earnings,
                    "updated_at": now_utc,
                },
            )
            await self.session.execute(stmt)
        elif dialect_name == "sqlite":
            stmt = sqlite_insert(PublisherDailyStat).values(
                publisher_id=slot.publisher_id,
                slot_id=slot.id,
                stat_date=stat_date,
                impressions=delta_impressions,
                clicks=delta_clicks,
                conversions=delta_conversions,
                earnings=delta_earnings,
                created_at=now_utc,
                updated_at=now_utc,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["publisher_id", "slot_id", "stat_date"],
                set_={
                    "impressions": PublisherDailyStat.impressions + stmt.excluded.impressions,
                    "clicks": PublisherDailyStat.clicks + stmt.excluded.clicks,
                    "conversions": PublisherDailyStat.conversions + stmt.excluded.conversions,
                    "earnings": PublisherDailyStat.earnings + stmt.excluded.earnings,
                    "updated_at": now_utc,
                },
            )
            await self.session.execute(stmt)
        else:
            result = await self.session.execute(
                select(PublisherDailyStat).where(
                    PublisherDailyStat.publisher_id == slot.publisher_id,
                    PublisherDailyStat.slot_id == slot.id,
                    PublisherDailyStat.stat_date == stat_date,
                ).with_for_update()
            )
            stat = result.scalar_one_or_none()
            if stat is None:
                stat = PublisherDailyStat(
                    publisher_id=slot.publisher_id,
                    slot_id=slot.id,
                    stat_date=stat_date,
                    impressions=0,
                    clicks=0,
                    conversions=0,
                    earnings=Decimal("0.000000"),
                )
                self.session.add(stat)
            stat.impressions += delta_impressions
            stat.clicks += delta_clicks
            stat.conversions += delta_conversions
            stat.earnings += delta_earnings

    async def _update_stats(self, campaign_id: int | None, event_type: int, ad_set_id: int | None = None) -> None:
        """Update real-time statistics in Redis."""
        if campaign_id is None:
            return

        hour = current_hour()
        key = CacheKeys.stat_hourly(campaign_id, hour)

        # Increment appropriate counter
        if event_type == EventType.IMPRESSION:
            await redis_client.hincrby(key, "impressions", 1)
        elif event_type == EventType.CLICK:
            await redis_client.hincrby(key, "clicks", 1)
        elif event_type == EventType.CONVERSION:
            await redis_client.hincrby(key, "conversions", 1)

        # Set TTL (48 hours)
        await redis_client.expire(key, 48 * 3600)

        # Increment counter for ad set
        if ad_set_id is not None:
            as_stat_key = f"stat:adset:{ad_set_id}:{hour}"
            if event_type == EventType.IMPRESSION:
                await redis_client.hincrby(as_stat_key, "impressions", 1)
            elif event_type == EventType.CLICK:
                await redis_client.hincrby(as_stat_key, "clicks", 1)
            elif event_type == EventType.CONVERSION:
                await redis_client.hincrby(as_stat_key, "conversions", 1)
            await redis_client.expire(as_stat_key, 48 * 3600)

    async def _update_frequency(
        self,
        user_id: str,
        campaign_id: int | None,
        slot_id: str | None = None,
        ad_set_id: int | None = None,
    ) -> None:
        """Update frequency counter atomically with bounded TTL."""
        if campaign_id is None or not user_id:
            return

        today = current_date()
        hour = current_hour()

        daily_key = CacheKeys.freq_daily(user_id, campaign_id, today, slot_id=slot_id)
        hourly_key = CacheKeys.freq_hourly(user_id, campaign_id, hour, slot_id=slot_id)

        try:
            if not redis_client.connected:
                try:
                    await redis_client.connect()
                except Exception:
                    pass
            if redis_client.connected:
                client = redis_client.client
                _LUA = """
                local current = redis.call('INCR', KEYS[1])
                if current == 1 then
                    redis.call('EXPIRE', KEYS[1], ARGV[1])
                end
                return current
                """
                await client.eval(_LUA, 1, daily_key, 86400)
                await client.eval(_LUA, 1, hourly_key, 3600)
                if ad_set_id is not None:
                    as_daily_key = f"freq:adset:{user_id}:{ad_set_id}:{today}"
                    as_hourly_key = f"freq:adset:{user_id}:{ad_set_id}:{hour}"
                    await client.eval(_LUA, 1, as_daily_key, 86400)
                    await client.eval(_LUA, 1, as_hourly_key, 3600)
        except Exception as e:
            logger.error("failed_to_update_frequency", campaign_id=campaign_id, error=str(e))

    async def _update_user_profile(self, user_id: str | None, campaign_id: int | None, event_type: int) -> None:
        """Update the user's affinity profile based on ad interaction."""
        if not user_id or not campaign_id:
            return

        try:
            from liteads.ad_server.services import user_profile
            from liteads.models import TargetingRule
            
            result = await self.session.execute(
                select(TargetingRule.rule_value).where(
                    TargetingRule.campaign_id == campaign_id,
                    TargetingRule.rule_type == "sector",
                    TargetingRule.is_include.is_(True),
                )
            )
            
            sectors: list[str] = []
            for (rv,) in result.all():
                if rv and "entries" in rv:
                    sectors.extend(rv["entries"])
                    
            if sectors:
                await user_profile.record_activity(user_id, sectors, event_type)
        except Exception as e:
            logger.error(f"Failed to update user profile: {e}")

    async def _dispatch_webhooks(
        self,
        event_type: str,
        request_id: str,
        ad_id: str,
        campaign_id: int | None,
        creative_id: int | None,
        user_id: str | None,
        cost: float,
        advertiser_id: int | None,
        publisher_id: int | None,
        extra: dict[str, Any] | None,
    ) -> None:
        """Helper to invoke the async webhook dispatcher."""
        try:
            from liteads.ad_server.services.webhook_dispatcher import build_event_payload, dispatch_webhooks
            from liteads.common.database import db

            payload = build_event_payload(
                event_type=event_type,
                request_id=request_id,
                ad_id=ad_id,
                campaign_id=campaign_id,
                creative_id=creative_id,
                user_id=user_id,
                cost=cost,
                extra=extra,
            )

            await dispatch_webhooks(
                session=self.session,
                event_type=event_type,
                payload=payload,
                advertiser_id=advertiser_id,
                publisher_id=publisher_id,
                session_factory=db.session_factory,
            )
        except Exception as e:
            logger.error(f"Webhook dispatch failed: {e}")
