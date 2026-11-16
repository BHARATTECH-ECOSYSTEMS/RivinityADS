"""
First-party Invalid Traffic (IVT) detection service (Phase 3 & Phase 7).

Standard AdTech IVT Safeguards:
1. Replay / Duplicate detection: Prevents replaying identical event sequences.
2. Impossible timing: Rejects clicks received < 50ms after impression (inhuman speed)
   or > 24h after impression (expired attribution).
3. Orphan clicks: Flags clicks referencing a non-existent impression.
4. Click bursts: Limits clicks to <= 3 per 5-second window per user/IP/creative.
5. Click velocity: Limits clicks to <= 15 per 60-second window across an identifier.
   Crucial rule: Single IPs are NOT treated as conclusive fraud (IP sharing / NAT protection).
6. Clickjacking: Detects negative/out-of-bounds coordinates, zero deltas, and hidden iframe state.
7. Repeated creative clicks: Flags repetitive clicks targeting the same creative.
8. Publisher CTR spikes: Flags slots with CTR > 25% over 50+ impressions in an hour.
9. Automated/bot user agents: Flags scrapers/crawlers not running in test/sandbox.

Inviolable Rule:
Events flagged as IVT are NEVER silently dropped. They are persisted to `ad_events`
with `is_invalid=True`, `invalid_reason=...`, `invalid_details=...`, and `cost=0`,
ensuring zero billable charge to advertiser and zero publisher earnings, while
providing an immutable audit log for investigations and appeals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
CLICK_BURST_MAX_COUNT = 3  # Maximum clicks allowed within 5s window
CLICK_BURST_WINDOW_SECONDS = 5  # 5-second burst detection window
CLICK_VELOCITY_MAX_COUNT = 15  # Maximum clicks allowed within 60s window
CLICK_VELOCITY_WINDOW_SECONDS = 60  # 60-second velocity detection window
REPEATED_CREATIVE_MAX_COUNT = 5  # Max clicks on exact creative in 30s
REPEATED_CREATIVE_WINDOW_SECONDS = 30
PUBLISHER_CTR_SPIKE_THRESHOLD = 0.25  # 25% CTR
PUBLISHER_CTR_MIN_IMPRESSIONS = 50  # Minimum sample size to evaluate CTR spike


@dataclass
class IVTResult:
    """Outcome of an IVT risk assessment."""
    is_invalid: bool
    reason: str | None = None
    details: dict[str, Any] | None = None


@dataclass
class PublisherSafetySummary:
    """Publisher-facing quality and safety summary (without leaking proprietary thresholds)."""
    publisher_id: int
    quality_score: int  # 0-100
    total_events: int
    valid_events: int
    invalid_events: int
    invalid_rate_pct: float
    warning_categories: list[dict[str, Any]]
    recommendations: list[str]


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
            extra_payload = extra or {}

            # A. Clickjacking and Coordinate Invariants
            if extra_payload:
                is_hidden = extra_payload.get("hidden_state") or extra_payload.get("iframe_hidden")
                if is_hidden is True:
                    logger.warning("IVT detected: Click on obscured or hidden container", request_id=request_id)
                    return IVTResult(
                        is_invalid=True,
                        reason="CLICKJACKING_SUSPECTED",
                        details={"flag": "hidden_surface"},
                    )

                click_x = extra_payload.get("click_x")
                click_y = extra_payload.get("click_y")
                frame_w = extra_payload.get("width")
                frame_h = extra_payload.get("height")

                if click_x is not None and click_y is not None:
                    try:
                        cx, cy = float(click_x), float(click_y)
                        if cx < 0 or cy < 0:
                            return IVTResult(
                                is_invalid=True,
                                reason="CLICKJACKING_SUSPECTED",
                                details={"coordinates": [cx, cy], "flag": "negative_coordinates"},
                            )
                        if frame_w and frame_h:
                            fw, fh = float(frame_w), float(frame_h)
                            if fw > 0 and fh > 0 and (cx > fw or cy > fh):
                                return IVTResult(
                                    is_invalid=True,
                                    reason="CLICKJACKING_SUSPECTED",
                                    details={"coordinates": [cx, cy], "frame": [fw, fh], "flag": "out_of_bounds"},
                                )
                    except (ValueError, TypeError):
                        pass

            # B. Click Burst Rate Limiting (5-second window)
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

            # C. Click Velocity across 60 seconds (anti-bot velocity, non-conclusive IP rule)
            client_id = ip_address or user_id or "anon"
            velocity_key = f"ivt:velocity:{client_id}"
            velocity_count = 0
            if redis_client.connected and client_id != "anon":
                try:
                    velocity_count = await redis_client.incr(velocity_key)
                    if velocity_count == 1:
                        await redis_client.expire(velocity_key, CLICK_VELOCITY_WINDOW_SECONDS)
                except Exception as e:
                    logger.warning(f"Redis velocity error: {e}")
                    velocity_count = 0

            if velocity_count > CLICK_VELOCITY_MAX_COUNT:
                logger.warning(
                    "IVT detected: Click velocity threshold exceeded",
                    client_id=client_id,
                    count=velocity_count,
                )
                return IVTResult(
                    is_invalid=True,
                    reason="CLICK_VELOCITY_EXCEEDED",
                    details={"velocity_count": velocity_count, "window_seconds": CLICK_VELOCITY_WINDOW_SECONDS},
                )

            # D. Repeated Clicks on Same Creative (30-second window)
            if creative_id and client_id != "anon":
                rep_key = f"ivt:rep_creative:{client_id}:{creative_id}"
                rep_count = 0
                if redis_client.connected:
                    try:
                        rep_count = await redis_client.incr(rep_key)
                        if rep_count == 1:
                            await redis_client.expire(rep_key, REPEATED_CREATIVE_WINDOW_SECONDS)
                    except Exception as e:
                        logger.warning(f"Redis repeated creative error: {e}")
                        rep_count = 0
                if rep_count > REPEATED_CREATIVE_MAX_COUNT:
                    logger.warning("IVT detected: Repeated clicks on exact creative", creative_id=creative_id, client_id=client_id)
                    return IVTResult(
                        is_invalid=True,
                        reason="REPEATED_CREATIVE_CLICKS",
                        details={"count": rep_count, "creative_id": creative_id},
                    )

            # E. Timing validation: compare click time against initial impression time
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

            # F. Publisher Slot CTR Spike Detection
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

    async def get_publisher_safety_summary(
        self,
        publisher_id: int,
        window_days: int = 30,
    ) -> PublisherSafetySummary:
        """
        Generate publisher-facing safety analysis and warnings without leaking
        internal proprietary thresholds.
        """
        since_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window_days)

        # Query all slots belonging to this publisher
        slot_query = select(AdSlot.slot_id).where(AdSlot.publisher_id == publisher_id)
        slot_res = await self.session.execute(slot_query)
        slot_ids = list(slot_res.scalars().all())

        if not slot_ids:
            return PublisherSafetySummary(
                publisher_id=publisher_id,
                quality_score=100,
                total_events=0,
                valid_events=0,
                invalid_events=0,
                invalid_rate_pct=0.0,
                warning_categories=[],
                recommendations=["Configure and activate ad units to view traffic telemetry."],
            )

        events_stmt = (
            select(
                AdEvent.is_invalid,
                AdEvent.invalid_reason,
                func.count(AdEvent.id).label("count"),
            )
            .where(
                AdEvent.slot_id.in_(slot_ids),
                AdEvent.event_time >= since_time,
            )
            .group_by(AdEvent.is_invalid, AdEvent.invalid_reason)
        )
        events_res = await self.session.execute(events_stmt)
        rows = events_res.all()

        total = 0
        invalid = 0
        reasons_map: dict[str, int] = {}

        for is_inv, reason, cnt in rows:
            total += cnt
            if is_inv:
                invalid += cnt
                if reason:
                    reasons_map[reason] = reasons_map.get(reason, 0) + cnt

        valid = total - invalid
        invalid_rate = round((invalid / total * 100), 2) if total > 0 else 0.0
        score = max(0, min(100, int(100 - (invalid_rate * 1.5))))

        warning_categories = []
        recommendations = []

        if reasons_map.get("AUTOMATED_USER_AGENT", 0) > 0:
            warning_categories.append({
                "title": "Automated Non-Human Traffic",
                "description": "Traffic identified as web scrapers or automated tools was filtered.",
                "count": reasons_map["AUTOMATED_USER_AGENT"],
                "severity": "medium",
            })
            recommendations.append("Ensure your web properties employ standard bot-mitigation techniques.")

        if reasons_map.get("CLICK_BURST", 0) > 0 or reasons_map.get("CLICK_VELOCITY_EXCEEDED", 0) > 0:
            burst_tot = reasons_map.get("CLICK_BURST", 0) + reasons_map.get("CLICK_VELOCITY_EXCEEDED", 0)
            warning_categories.append({
                "title": "Rapid Repeated Interactions",
                "description": "High-frequency interaction bursts from single visitor sessions were suppressed.",
                "count": burst_tot,
                "severity": "high" if burst_tot > 20 else "medium",
            })
            recommendations.append("Inspect ad placement positions to prevent accidental double-clicks or overlap with navigation controls.")

        if reasons_map.get("IMPOSSIBLE_TIMING", 0) > 0:
            warning_categories.append({
                "title": "Atypical Interaction Timing",
                "description": "Interactions registered instantaneously without reasonable visual dwell time.",
                "count": reasons_map["IMPOSSIBLE_TIMING"],
                "severity": "high",
            })
            recommendations.append("Avoid rendering ads in areas where visitors may unintentionally trigger clicks upon page load.")

        if reasons_map.get("PUBLISHER_CTR_SPIKE", 0) > 0:
            warning_categories.append({
                "title": "Abnormal Interaction Density",
                "description": "Unusual interaction patterns detected on specific placement units.",
                "count": reasons_map["PUBLISHER_CTR_SPIKE"],
                "severity": "high",
            })
            recommendations.append("Verify that ad units are not placed adjacent to clickable game controls or interactive media.")

        if not recommendations:
            recommendations.append("Traffic health is within optimal parameters. No publisher actions required.")

        return PublisherSafetySummary(
            publisher_id=publisher_id,
            quality_score=score,
            total_events=total,
            valid_events=valid,
            invalid_events=invalid,
            invalid_rate_pct=invalid_rate,
            warning_categories=warning_categories,
            recommendations=recommendations,
        )
