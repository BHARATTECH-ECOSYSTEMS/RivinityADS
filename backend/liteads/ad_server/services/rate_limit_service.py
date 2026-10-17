"""Rate limiting, daily quota enforcement, and usage accounting service."""

import math
import time
from datetime import date, datetime, timezone, timedelta
from typing import Any
import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.cache import redis_client
from liteads.models.publisher import PublisherApiKey, ApiKeyDailyStat
from liteads.schemas.management import ApiKeyUsageResponse, ApiKeyUsageBreakdown

logger = structlog.get_logger(__name__)

# Atomic sliding window rate limit Lua script (ZSET)
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_limit = tonumber(ARGV[3])
local member_id = ARGV[4]

-- Remove timestamps older than (now - window)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)

-- Count entries within the sliding window
local current_count = redis.call('ZCARD', key)

if current_count < max_limit then
    -- Admitted: record current timestamp with unique member ID
    redis.call('ZADD', key, now, member_id)
    redis.call('EXPIRE', key, math.ceil(window) + 5)
    local remaining = max_limit - current_count - 1
    return {1, remaining, math.ceil(window)}
else
    -- Rejected: calculate reset TTL until the oldest entry in window expires
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset_ttl = math.ceil(window)
    if oldest and #oldest >= 2 then
        local oldest_ts = tonumber(oldest[2])
        reset_ttl = math.max(1, math.ceil(oldest_ts + window - now))
    end
    return {0, 0, reset_ttl}
end
"""

# Atomic daily quota check and increment Lua script
_DAILY_QUOTA_LUA = """
local key = KEYS[1]
local max_quota = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local seconds_to_midnight = tonumber(ARGV[3])

local current = tonumber(redis.call('GET', key) or '0')

if current < max_quota then
    local new_val = redis.call('INCR', key)
    if new_val == 1 then
        redis.call('EXPIRE', key, ttl)
    end
    local remaining = max_quota - new_val
    return {1, remaining, seconds_to_midnight}
else
    return {0, 0, seconds_to_midnight}
end
"""


class RateLimitService:
    """Service enforcing per-key rate limits, daily quotas, and usage accounting."""

    @staticmethod
    def get_utc_date_str() -> str:
        """Return the current UTC date string YYYY-MM-DD."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def get_seconds_until_utc_midnight() -> int:
        """Calculate seconds remaining until the next 00:00:00 UTC."""
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1, int((tomorrow - now).total_seconds()))

    @staticmethod
    def get_next_utc_midnight_iso() -> str:
        """Return the ISO string for next UTC midnight."""
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return tomorrow.isoformat()

    @classmethod
    async def check_rate_limit(
        cls,
        key_id: int,
        rate_limit_per_minute: int,
    ) -> tuple[bool, int, int]:
        """Check and enforce the per-minute rate limit for an API key.

        Returns:
            tuple[bool, int, int]: (is_allowed, remaining_quota, reset_seconds)

        Raises:
            HTTPException(503): When Redis is unavailable (fail-closed policy).
        """
        if not redis_client.connected:
            try:
                await redis_client.connect()
            except Exception:
                pass

        if not redis_client.connected:
            logger.error("rate_limit_fail_closed_redis_unavailable", key_id=key_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limit service unavailable",
            )

        window_seconds = 60
        now_ts = time.time()
        redis_key = f"ratelimit:apikey:{key_id}:minute"
        member_id = f"{now_ts}:{time.monotonic_ns()}"

        try:
            client = redis_client.client
            result = await client.eval(
                _SLIDING_WINDOW_LUA,
                1,
                redis_key,
                str(now_ts),
                str(window_seconds),
                str(rate_limit_per_minute),
                member_id,
            )
            # result: [allowed, remaining, reset_ttl]
            is_allowed = bool(result[0])
            remaining = int(result[1])
            reset_seconds = int(result[2])
            return is_allowed, remaining, reset_seconds
        except HTTPException:
            raise
        except Exception as e:
            logger.error("rate_limit_redis_error_fail_closed", key_id=key_id, error=str(e))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limit service unavailable",
            )

    @classmethod
    async def check_daily_quota(
        cls,
        key_id: int,
        daily_quota: int,
        utc_date: str | None = None,
    ) -> tuple[bool, int, int]:
        """Check and enforce the daily request quota for an API key.

        Returns:
            tuple[bool, int, int]: (is_allowed, remaining_quota, reset_seconds)

        Raises:
            HTTPException(503): When Redis is unavailable (fail-closed policy).
        """
        if not redis_client.connected:
            try:
                await redis_client.connect()
            except Exception:
                pass

        if not redis_client.connected:
            logger.error("daily_quota_fail_closed_redis_unavailable", key_id=key_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Quota service unavailable",
            )

        utc_date = utc_date or cls.get_utc_date_str()
        redis_key = f"quota:apikey:{key_id}:{utc_date}"
        seconds_to_midnight = cls.get_seconds_until_utc_midnight()
        ttl_seconds = 172800  # 48 hours to preserve yesterday's quota for reconciliation

        try:
            client = redis_client.client
            result = await client.eval(
                _DAILY_QUOTA_LUA,
                1,
                redis_key,
                str(daily_quota),
                str(ttl_seconds),
                str(seconds_to_midnight),
            )
            is_allowed = bool(result[0])
            remaining = int(result[1])
            reset_seconds = int(result[2])
            return is_allowed, remaining, reset_seconds
        except HTTPException:
            raise
        except Exception as e:
            logger.error("daily_quota_redis_error_fail_closed", key_id=key_id, error=str(e))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Quota service unavailable",
            )

    @classmethod
    async def record_usage_metric(
        cls,
        key_id: int,
        metric: str,
        amount: int = 1,
        utc_date: str | None = None,
    ) -> None:
        """Safely record usage metrics into Redis hash.

        Metrics tracked:
        - total_requests
        - admitted_requests
        - rate_limited_requests
        - quota_exceeded_requests
        - served_ads
        """
        if not redis_client.connected:
            try:
                await redis_client.connect()
            except Exception:
                pass

        if not redis_client.connected:
            return

        utc_date = utc_date or cls.get_utc_date_str()
        redis_key = f"usage:apikey:{key_id}:{utc_date}"
        try:
            client = redis_client.client
            pipe = client.pipeline()
            pipe.hincrby(redis_key, metric, amount)
            pipe.expire(redis_key, 2592000)  # 30 days retention
            await pipe.execute()
        except Exception as e:
            logger.warning("failed_to_record_usage_metric", key_id=key_id, metric=metric, error=str(e))

    @classmethod
    async def get_key_usage(
        cls,
        session: AsyncSession,
        key: PublisherApiKey,
        utc_date: str | None = None,
    ) -> ApiKeyUsageResponse:
        """Retrieve real-time and aggregate usage statistics for an API key."""
        utc_date = utc_date or cls.get_utc_date_str()
        seconds_to_midnight = cls.get_seconds_until_utc_midnight()
        next_midnight_iso = cls.get_next_utc_midnight_iso()

        breakdown_data = {
            "total_requests": 0,
            "admitted_requests": 0,
            "rate_limited_requests": 0,
            "quota_exceeded_requests": 0,
            "served_ads": 0,
        }
        current_minute_usage = 0

        if not redis_client.connected:
            try:
                await redis_client.connect()
            except Exception:
                pass

        if redis_client.connected or redis_client._client is not None:
            try:
                client = redis_client.client
                # 1. Fetch daily usage hash
                usage_key = f"usage:apikey:{key.id}:{utc_date}"
                hash_vals = await client.hgetall(usage_key)
                if hash_vals:
                    for k, v in hash_vals.items():
                        decoded_k = k.decode() if isinstance(k, bytes) else k
                        decoded_v = int(v.decode() if isinstance(v, bytes) else v)
                        if decoded_k in breakdown_data:
                            breakdown_data[decoded_k] = decoded_v

                # 2. Fetch current minute window count
                now_ts = time.time()
                minute_key = f"ratelimit:apikey:{key.id}:minute"
                # Clean stale and count
                await client.zremrangebyscore(minute_key, "-inf", now_ts - 60)
                current_minute_usage = await client.zcard(minute_key)
            except Exception as e:
                logger.warning("failed_to_fetch_redis_usage", key_id=key.id, error=str(e))

        today_usage = breakdown_data["admitted_requests"]
        daily_quota_remaining = max(0, key.daily_quota - today_usage)
        rate_limit_remaining = max(0, key.rate_limit_per_minute - current_minute_usage)

        return ApiKeyUsageResponse(
            key_id=key.id,
            publisher_id=key.publisher_id,
            name=key.name,
            key_prefix=key.key_prefix,
            is_sandbox=key.is_sandbox,
            rate_limit_per_minute=key.rate_limit_per_minute,
            daily_quota=key.daily_quota,
            current_minute_usage=current_minute_usage,
            rate_limit_remaining=rate_limit_remaining,
            rate_limit_reset_seconds=60,
            today_usage=today_usage,
            daily_quota_remaining=daily_quota_remaining,
            daily_reset_time_utc=next_midnight_iso,
            daily_reset_seconds=seconds_to_midnight,
            breakdown=ApiKeyUsageBreakdown(**breakdown_data),
            last_used_at=key.last_used_at,
            status=key.status,
            revoked_at=key.revoked_at,
            created_at=key.created_at,
        )

    @classmethod
    async def get_publisher_keys_usage(
        cls,
        session: AsyncSession,
        publisher_id: int,
    ) -> list[ApiKeyUsageResponse]:
        """Retrieve usage statistics for all API keys owned by a publisher."""
        stmt = (
            select(PublisherApiKey)
            .where(PublisherApiKey.publisher_id == publisher_id)
            .order_by(PublisherApiKey.id.desc())
        )
        result = await session.execute(stmt)
        keys = list(result.scalars().all())

        responses: list[ApiKeyUsageResponse] = []
        for key in keys:
            resp = await cls.get_key_usage(session, key)
            responses.append(resp)
        return responses

    @classmethod
    async def sync_daily_stat_to_db(
        cls,
        session: AsyncSession,
        key: PublisherApiKey,
        stat_date: date | None = None,
    ) -> None:
        """Periodically or idempotently upsert Redis daily counters to PostgreSQL."""
        if not redis_client.connected and redis_client._client is None:
            return

        date_obj = stat_date or datetime.now(timezone.utc).date()
        date_str = date_obj.strftime("%Y-%m-%d")
        usage_key = f"usage:apikey:{key.id}:{date_str}"

        try:
            client = redis_client.client
            hash_vals = await client.hgetall(usage_key)
            if not hash_vals:
                return

            def _get(field: str) -> int:
                val = hash_vals.get(field.encode() if isinstance(list(hash_vals.keys())[0], bytes) else field)
                if val is None:
                    return 0
                return int(val.decode() if isinstance(val, bytes) else val)

            stmt = select(ApiKeyDailyStat).where(
                ApiKeyDailyStat.api_key_id == key.id,
                ApiKeyDailyStat.stat_date == date_obj,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                existing.total_requests = _get("total_requests")
                existing.admitted_requests = _get("admitted_requests")
                existing.rate_limited_requests = _get("rate_limited_requests")
                existing.quota_exceeded_requests = _get("quota_exceeded_requests")
                existing.served_ads = _get("served_ads")
                existing.is_sandbox = key.is_sandbox
            else:
                new_stat = ApiKeyDailyStat(
                    api_key_id=key.id,
                    publisher_id=key.publisher_id,
                    stat_date=date_obj,
                    total_requests=_get("total_requests"),
                    admitted_requests=_get("admitted_requests"),
                    rate_limited_requests=_get("rate_limited_requests"),
                    quota_exceeded_requests=_get("quota_exceeded_requests"),
                    served_ads=_get("served_ads"),
                    is_sandbox=key.is_sandbox,
                )
                session.add(new_stat)
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.warning("sync_daily_stat_to_db_failed", key_id=key.id, error=str(e))
