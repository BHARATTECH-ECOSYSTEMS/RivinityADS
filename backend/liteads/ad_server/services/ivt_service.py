"""
First-party Invalid Traffic (IVT) detection service (Phase 3).

Standard AdTech IVT Safeguards:
1. Replay / Duplicate detection: Prevents replaying identical event sequences.
2. Impossible timing: Rejects clicks received < 50ms after impression (inhuman speed)
   or > 24h after impression (expired attribution).
3. Orphan clicks: Flags clicks referencing a non-existent impression.
4. Click bursts: Limits clicks to <= 3 per 5-second window per user/IP/creative.
5. Publisher CTR spikes: Flags slots with CTR > 25% over 50+ impressions in an hour.
6. Automated/bot user agents: Flags scrapers/crawlers not running in test/sandbox.

Inviolable Rule:
Events flagged as IVT are NEVER silently dropped. They are persisted to `ad_events`
with `is_invalid=True`, `invalid_reason=...`, `invalid_details=...`, and `cost=0`,
ensuring zero billable charge to advertiser and zero publisher earnings, while
providing an immutable audit log for investigations and appeals.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import re
from typing import Any

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.cache import redis_client
from liteads.common.logger import get_logger
from liteads.models.ad import AdEvent, Creative
from liteads.models.base import EventType
from liteads.models.publisher import AdSlot

logger = get_logger(__name__)

# Known bot patterns (case-insensitive)
BOT_UA_REGEX = re.compile(
    r"(googlebot|bingbot|yandexbot|duckduckbot|slurp|baiduspider|curl|wget|python-requests|"
    r"scrapy|headlesschrome|selenium|puppeteer|phantomjs|aiohttp|httpclient)",
    re.IGNORECASE,
)

MIN_CLICK_DELTA_SECONDS = 0.050  # 50 ms minimum reaction/network time
MAX_CLICK_DELTA_SECONDS = 86400.0  # 24 hours maximum click delay from impression
CLICK_BURST_MAX_COUNT = 3  # Maximum clicks allowed within burst window
CLICK_BURST_WINDOW_SECONDS = 5  # 5-second burst detection window
PUBLISHER_CTR_SPIKE_THRESHOLD = 0.25  # 25% CTR
PUBLISHER_CTR_MIN_IMPRESSIONS = 50  # Minimum sample size to evaluate CTR spike


@dataclass
class IVTResult:
    """Outcome of an IVT risk assessment."""
    is_invalid: bool
    reason: str | None = None
    details: dict[str, Any] | None = None


class IVTService:
    """Service detecting and cataloging invalid traffic patterns."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def evaluate_event(
        self,
        *,
        event_type: int,
        request_id: str,
        campaign_id: int | None = None,
        creative_id: int | None = None,
        user_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        event_time: datetime | None = None,
        slot_id: str | None = None,
        seq: int = 1,
        is_sandbox: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> IVTResult:
        """
        Evaluate an incoming event for invalid traffic signals.
        Returns IVTResult with invalid status, reason code, and diagnostic details.
        """
        now_utc = datetime.now(timezone.utc)
        cur_event_time = event_time or now_utc.replace(tzinfo=None)

        # 1. Automated / Bot User-Agent detection (bypassed only in documented sandbox/test mode)
        if user_agent and not is_sandbox:
            if BOT_UA_REGEX.search(user_agent):
                logger.warning("IVT detected: Automated user-agent", user_agent=user_agent, request_id=request_id)
                return IVTResult(
                    is_invalid=True,
                    reason="AUTOMATED_USER_AGENT",
                    details={"user_agent": user_agent[:128]},
                )

        # 2. Check Replay / Duplicate for the same logical event sequence
        logical_key = f"{request_id}:{creative_id or 0}:{event_type}:{seq}"
        dup_query = select(AdEvent.id).where(AdEvent.idempotency_key == logical_key).limit(1)
        dup_res = await self.session.execute(dup_query)
        if dup_res.scalar_one_or_none() is not None:
            logger.warning("IVT detected: Duplicate event replay", logical_key=logical_key)
            return IVTResult(
                is_invalid=True,
                reason="DUPLICATE_REPLAY",
                details={"idempotency_key": logical_key},
            )

        # 3. CLICK specific checks
        if event_type == EventType.CLICK:
            # A. Click Burst Rate Limiting (sliding window per request_id or ip_address/user_id)
            burst_identifier = f"{request_id}:{ip_address or user_id or 'anon'}"
            burst_key = f"ivt:burst:{burst_identifier}"
            burst_count = 0
            if redis_client.connected:
                try:
                    burst_count = await redis_client.incr(burst_key)
                    if burst_count == 1:
                        await redis_client.expire(burst_key, CLICK_BURST_WINDOW_SECONDS)
                except Exception as e:
                    logger.warning(f"Redis unavailable for burst tracking, falling back: {e}")
                    burst_count = 0

            if burst_count == 0:
                # Database fallback: count clicks recorded for this request_id in the last 5 seconds
                burst_window_start = cur_event_time - timedelta(seconds=CLICK_BURST_WINDOW_SECONDS)
                db_count_query = select(func.count(AdEvent.id)).where(
                    AdEvent.request_id == request_id,
                    AdEvent.event_type == EventType.CLICK,
                    AdEvent.event_time >= burst_window_start,
                )
                db_burst_res = await self.session.execute(db_count_query)
                burst_count = int(db_burst_res.scalar() or 0) + 1

            if burst_count > CLICK_BURST_MAX_COUNT:
                logger.warning(
                    "IVT detected: Click burst rate exceeded",
                    identifier=burst_identifier,
                    count=burst_count,
                )
                return IVTResult(
                    is_invalid=True,
                    reason="CLICK_BURST",
                    details={
                        "burst_count": burst_count,
                        "window_seconds": CLICK_BURST_WINDOW_SECONDS,
                        "identifier": burst_identifier,
                    },
                )

            # B. Timing validation: compare click time against initial impression time
            imp_query = (
                select(AdEvent)
                .where(AdEvent.request_id == request_id, AdEvent.event_type == EventType.IMPRESSION)
                .order_by(AdEvent.id.asc())
                .limit(1)
            )
            imp_res = await self.session.execute(imp_query)
            impression_event = imp_res.scalar_one_or_none()

            if not impression_event and not is_sandbox:
                # Click without impression
                logger.warning("IVT detected: Orphan click without preceding impression", request_id=request_id)
                return IVTResult(
                    is_invalid=True,
                    reason="ORPHAN_CLICK",
                    details={"request_id": request_id},
                )

            if impression_event:
                imp_time = impression_event.event_time
                delta = (cur_event_time - imp_time).total_seconds()
                if delta < MIN_CLICK_DELTA_SECONDS:
                    delta_ms = round(delta * 1000, 2)
                    logger.warning(
                        "IVT detected: Impossible click timing (<50ms)",
                        delta_ms=delta_ms,
                        request_id=request_id,
                    )
                    return IVTResult(
                        is_invalid=True,
                        reason="IMPOSSIBLE_TIMING",
                        details={"delta_ms": delta_ms, "min_threshold_ms": MIN_CLICK_DELTA_SECONDS * 1000},
                    )
                if delta > MAX_CLICK_DELTA_SECONDS:
                    logger.warning(
                        "IVT detected: Expired click timing (>24h)",
                        delta_seconds=delta,
                        request_id=request_id,
                    )
                    return IVTResult(
                        is_invalid=True,
                        reason="EXPIRED_CLICK",
                        details={"delta_seconds": round(delta, 2), "max_threshold_seconds": MAX_CLICK_DELTA_SECONDS},
                    )

            # C. Publisher Slot CTR Spike Detection
            if slot_id:
                try:
                    slot_stat_key = f"stat:slot_ivt:{slot_id}:{now_utc.strftime('%Y%m%d%H')}"
                    slot_imps = int(await redis_client.hget(slot_stat_key, "imps") or 0)
                    slot_clks = int(await redis_client.hget(slot_stat_key, "clks") or 0) + 1
                    await redis_client.hincrby(slot_stat_key, "clks", 1)
                    await redis_client.expire(slot_stat_key, 7200)

                    if slot_imps >= PUBLISHER_CTR_MIN_IMPRESSIONS:
                        ctr = slot_clks / slot_imps
                        if ctr > PUBLISHER_CTR_SPIKE_THRESHOLD:
                            logger.warning(
                                "IVT detected: Publisher slot CTR spike",
                                slot_id=slot_id,
                                ctr=ctr,
                                imps=slot_imps,
                                clks=slot_clks,
                            )
                            return IVTResult(
                                is_invalid=True,
                                reason="PUBLISHER_CTR_SPIKE",
                                details={
                                    "slot_id": slot_id,
                                    "ctr": round(ctr, 4),
                                    "impressions": slot_imps,
                                    "clicks": slot_clks,
                                },
                            )
                except Exception as e:
                    logger.warning(f"Redis slot stats error: {e}")

        # Record slot impression count in Redis for CTR spike detection
        if event_type == EventType.IMPRESSION and slot_id:
            try:
                slot_stat_key = f"stat:slot_ivt:{slot_id}:{now_utc.strftime('%Y%m%d%H')}"
                await redis_client.hincrby(slot_stat_key, "imps", 1)
                await redis_client.expire(slot_stat_key, 7200)
            except Exception as e:
                logger.warning(f"Redis slot imps incr error: {e}")

        # All checks passed
        return IVTResult(is_invalid=False)

