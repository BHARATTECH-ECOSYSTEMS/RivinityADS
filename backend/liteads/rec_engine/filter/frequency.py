"""
Frequency filter for controlling ad exposure per user.
"""

from typing import Any

from fastapi import HTTPException, status

from liteads.common.cache import CacheKeys, redis_client
from liteads.common.config import get_settings
from liteads.common.logger import get_logger
from liteads.common.utils import current_date, current_hour
from liteads.rec_engine.filter.base import BaseFilter
from liteads.schemas.internal import AdCandidate, FrequencyInfo, UserContext

logger = get_logger(__name__)

_ATOMIC_INCR_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class FrequencyFilter(BaseFilter):
    """
    Filter candidates by frequency cap.

    Prevents showing the same ad too many times to the same user.
    Supports both daily and hourly caps.
    Enforces privacy-safe user hashing and fail-closed security.
    """

    def __init__(
        self,
        default_daily_cap: int | None = None,
        default_hourly_cap: int | None = None,
    ):
        """
        Initialize frequency filter.

        Args:
            default_daily_cap: Default daily frequency cap
            default_hourly_cap: Default hourly frequency cap
        """
        settings = get_settings()
        self.default_daily_cap = default_daily_cap or settings.frequency.default_daily_cap
        self.default_hourly_cap = default_hourly_cap or settings.frequency.default_hourly_cap

    async def filter(
        self,
        candidates: list[AdCandidate],
        user_context: UserContext,
        **kwargs: Any,
    ) -> list[AdCandidate]:
        """Filter candidates by frequency cap."""
        if not candidates:
            return []

        # Skip if no user ID (requests without user hash are served without cap)
        if not user_context.user_id:
            return candidates

        # Build campaign cap maps from candidate metadata if present
        caps_map: dict[int, tuple[int, int]] = {}
        for c in candidates:
            c_daily = c.metadata.get("freq_cap_daily") if c.metadata else None
            c_hourly = c.metadata.get("freq_cap_hourly") if c.metadata else None
            daily_cap = int(c_daily) if c_daily and int(c_daily) > 0 else self.default_daily_cap
            hourly_cap = int(c_hourly) if c_hourly and int(c_hourly) > 0 else self.default_hourly_cap
            caps_map[c.campaign_id] = (daily_cap, hourly_cap)

        # Batch get frequency info
        campaign_ids = list(set(c.campaign_id for c in candidates))
        slot_id = kwargs.get("slot_id")
        freq_infos = await self._get_frequency_batch(
            user_context.user_id, campaign_ids, caps_map=caps_map, slot_id=slot_id
        )

        # Filter
        result = []
        for candidate in candidates:
            freq_info = freq_infos.get(candidate.campaign_id)
            if freq_info and not freq_info.is_capped:
                result.append(candidate)

        filtered_count = len(candidates) - len(result)
        if filtered_count > 0:
            logger.debug(
                f"Frequency filter removed {filtered_count} candidates"
            )

        return result

    async def filter_single(
        self,
        candidate: AdCandidate,
        user_context: UserContext,
        **kwargs: Any,
    ) -> bool:
        """Check if single candidate passes frequency filter."""
        if not user_context.user_id:
            return True

        freq_info = await self._get_frequency(
            user_context.user_id, candidate.campaign_id, slot_id=kwargs.get("slot_id")
        )
        return not freq_info.is_capped

    async def _get_frequency_batch(
        self,
        user_id: str,
        campaign_ids: list[int],
        caps_map: dict[int, tuple[int, int]] | None = None,
        slot_id: str | None = None,
    ) -> dict[int, FrequencyInfo]:
        """Get frequency info for multiple campaigns with fail-closed error handling."""
        result: dict[int, FrequencyInfo] = {}
        today = current_date()
        hour = current_hour()

        # Build keys
        daily_keys = [
            CacheKeys.freq_daily(user_id, cid, today, slot_id=slot_id) for cid in campaign_ids
        ]
        hourly_keys = [
            CacheKeys.freq_hourly(user_id, cid, hour, slot_id=slot_id) for cid in campaign_ids
        ]

        if not redis_client.connected:
            try:
                await redis_client.connect()
            except Exception:
                pass

        if not redis_client.connected:
            logger.error("frequency_cap_fail_closed_redis_unavailable")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Frequency capping service unavailable",
            )

        try:
            pipeline = redis_client.pipeline()
            for key in daily_keys + hourly_keys:
                pipeline.get(key)

            values = await pipeline.execute()
            daily_values = values[: len(campaign_ids)]
            hourly_values = values[len(campaign_ids) :]

            for i, campaign_id in enumerate(campaign_ids):
                daily_count = int(daily_values[i]) if daily_values[i] else 0
                hourly_count = int(hourly_values[i]) if hourly_values[i] else 0

                daily_cap, hourly_cap = (
                    caps_map.get(campaign_id, (self.default_daily_cap, self.default_hourly_cap))
                    if caps_map
                    else (self.default_daily_cap, self.default_hourly_cap)
                )

                result[campaign_id] = FrequencyInfo(
                    user_id=user_id,
                    campaign_id=campaign_id,
                    daily_count=daily_count,
                    hourly_count=hourly_count,
                    daily_cap=daily_cap,
                    hourly_cap=hourly_cap,
                )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("frequency_cap_redis_error_fail_closed", error=str(e))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Frequency capping service unavailable",
            )

        return result

    async def _get_frequency(
        self,
        user_id: str,
        campaign_id: int,
        slot_id: str | None = None,
    ) -> FrequencyInfo:
        """Get frequency info for a single campaign."""
        result = await self._get_frequency_batch(user_id, [campaign_id], slot_id=slot_id)
        return result.get(
            campaign_id,
            FrequencyInfo(user_id=user_id, campaign_id=campaign_id),
        )

    async def increment(
        self,
        user_id: str,
        campaign_id: int,
        slot_id: str | None = None,
    ) -> None:
        """
        Increment frequency counter atomically after ad impression.

        Uses atomic Lua script to guarantee key expiration (bounded TTL).
        """
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
                await client.eval(_ATOMIC_INCR_LUA, 1, daily_key, 86400)
                await client.eval(_ATOMIC_INCR_LUA, 1, hourly_key, 3600)
        except Exception as e:
            logger.error(
                f"Failed to increment frequency for campaign {campaign_id}: {e}"
            )

    async def reset(
        self,
        user_id: str,
        campaign_id: int | None = None,
        slot_id: str | None = None,
    ) -> None:
        """
        Reset frequency counter for user.

        Args:
            user_id: User identifier
            campaign_id: Optional campaign ID. If None, resets all.
            slot_id: Optional slot ID.
        """
        today = current_date()
        hour = current_hour()

        if campaign_id:
            keys = [
                CacheKeys.freq_daily(user_id, campaign_id, today, slot_id=slot_id),
                CacheKeys.freq_hourly(user_id, campaign_id, hour, slot_id=slot_id),
            ]
        else:
            logger.warning("Resetting all frequency for user is expensive")
            return

        try:
            if redis_client.connected:
                await redis_client.delete(*keys)
        except Exception as e:
            logger.error(f"Failed to reset frequency: {e}")
