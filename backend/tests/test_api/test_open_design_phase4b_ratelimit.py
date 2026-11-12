"""Unit and regression tests for RivinityADS Open Design — Phase 4B:
Redis Rate Limiting & Daily Quota Management.

Covers:
1. Daily quota enforcement below, at, and above 50 limit.
2. UTC midnight reset and namespaced daily key rollover.
3. Atomic concurrent reservations for daily quota.
4. Burst limit enforcement below, at, and above 5 requests/minute.
5. Rolling 60-second window expiry behavior and retry-after calculation.
6. Atomic concurrent burst reservations.
7. Independent quotas and burst buckets across different authenticated user IDs.
8. Redis key isolation between publisher AI and existing API keys.
9. Combined reservation semantics: burst rejection prevents daily quota consumption.
10. Failure accounting: non-refundable reservation semantics.
11. Non-mutating quota status inspection.
12. Validation of user_id (positive integer only, no untrusted IDs).
13. Redis unavailable/error fail-closed policy (HTTP 503).
14. Regression verification of existing API key rate limit and daily quota methods.
"""

import asyncio
import math
import time
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException

from liteads.ad_server.services.rate_limit_service import (
    RateLimitService,
    PublisherAIQuotaResult,
    PublisherAIBurstResult,
    PublisherAIReservationResult,
    PUBLISHER_AI_DAILY_LIMIT,
    PUBLISHER_AI_BURST_LIMIT,
    PUBLISHER_AI_BURST_WINDOW_SECONDS,
    _DAILY_QUOTA_LUA,
    _SLIDING_WINDOW_LUA,
)
from liteads.common.cache import redis_client


class InMemoryRedisClient:
    """High-fidelity in-memory Redis client simulating atomic Lua script evaluation."""

    def __init__(self):
        self.strings: dict[str, str] = {}
        self.zsets: dict[str, list[tuple[float, str]]] = {}
        self.expiries: dict[str, float] = {}
        self.lock = asyncio.Lock()
        self.simulated_time: float | None = None

    def _get_time(self) -> float:
        return self.simulated_time if self.simulated_time is not None else time.time()

    async def get(self, key: str) -> str | None:
        async with self.lock:
            now = self._get_time()
            if key in self.expiries and now > self.expiries[key]:
                self.strings.pop(key, None)
                self.expiries.pop(key, None)
                return None
            return self.strings.get(key)

    async def delete(self, *keys: str) -> int:
        async with self.lock:
            count = 0
            for k in keys:
                if k in self.strings:
                    del self.strings[k]
                    count += 1
                if k in self.zsets:
                    del self.zsets[k]
                    count += 1
                self.expiries.pop(k, None)
            return count

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> list[int]:
        async with self.lock:
            keys = list(keys_and_args[:numkeys])
            args = list(keys_and_args[numkeys:])

            # Simulation of _DAILY_QUOTA_LUA
            if "max_quota" in script or script == _DAILY_QUOTA_LUA:
                key = keys[0]
                max_quota = int(args[0])
                ttl = int(args[1])
                seconds_to_midnight = int(args[2])

                current = int(self.strings.get(key, "0"))
                if current < max_quota:
                    new_val = current + 1
                    self.strings[key] = str(new_val)
                    if new_val == 1:
                        self.expiries[key] = self._get_time() + ttl
                    remaining = max_quota - new_val
                    return [1, remaining, seconds_to_midnight]
                else:
                    return [0, 0, seconds_to_midnight]

            # Simulation of _SLIDING_WINDOW_LUA
            elif "window" in script or script == _SLIDING_WINDOW_LUA:
                key = keys[0]
                now = float(args[0])
                window = float(args[1])
                max_limit = int(args[2])
                member_id = str(args[3])

                zset = self.zsets.setdefault(key, [])
                # Remove timestamps older than (now - window)
                zset[:] = [entry for entry in zset if entry[0] > (now - window)]

                current_count = len(zset)
                if current_count < max_limit:
                    zset.append((now, member_id))
                    zset.sort(key=lambda x: x[0])
                    self.expiries[key] = now + math.ceil(window) + 5
                    remaining = max_limit - current_count - 1
                    return [1, remaining, math.ceil(window)]
                else:
                    reset_ttl = math.ceil(window)
                    if zset:
                        oldest_ts = zset[0][0]
                        reset_ttl = max(1, math.ceil(oldest_ts + window - now))
                    return [0, 0, reset_ttl]

            raise NotImplementedError(f"Unsupported Lua script in test mock: {script[:30]}...")


@pytest.fixture
def mock_redis():
    """Fixture providing an isolated in-memory Redis client connected state."""
    in_memory = InMemoryRedisClient()
    mock_wrapper = MagicMock()
    mock_wrapper.connected = True
    mock_wrapper.client = in_memory
    mock_wrapper._client = in_memory

    with patch("liteads.ad_server.services.rate_limit_service.redis_client", mock_wrapper):
        yield in_memory


# ── 1. Daily Quota Tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_quota_below_limit(mock_redis):
    """Reservations from 1 to 49 succeed and decrement remaining quota."""
    user_id = 101

    for i in range(1, 50):
        res = await RateLimitService.check_publisher_ai_daily_quota(user_id=user_id, daily_limit=50)
        assert res.allowed is True
        assert res.limit == 50
        assert res.remaining == 50 - i
        assert res.reset_seconds > 0
        assert "T" in res.reset_timestamp_utc


@pytest.mark.asyncio
async def test_daily_quota_at_limit(mock_redis):
    """The 50th request succeeds with 0 remaining quota."""
    user_id = 102

    for _ in range(49):
        await RateLimitService.check_publisher_ai_daily_quota(user_id=user_id, daily_limit=50)

    res_50 = await RateLimitService.check_publisher_ai_daily_quota(user_id=user_id, daily_limit=50)
    assert res_50.allowed is True
    assert res_50.remaining == 0


@pytest.mark.asyncio
async def test_daily_quota_above_limit_denied(mock_redis):
    """The 51st request is rejected with allowed=False and remaining=0."""
    user_id = 103

    for _ in range(50):
        await RateLimitService.check_publisher_ai_daily_quota(user_id=user_id, daily_limit=50)

    res_51 = await RateLimitService.check_publisher_ai_daily_quota(user_id=user_id, daily_limit=50)
    assert res_51.allowed is False
    assert res_51.limit == 50
    assert res_51.remaining == 0
    assert res_51.reset_seconds > 0

    # Ensure counter in Redis was not incremented past 50
    date_str = RateLimitService.get_utc_date_str()
    key = f"quota:publisher_ai:{user_id}:{date_str}"
    assert mock_redis.strings[key] == "50"


@pytest.mark.asyncio
async def test_daily_quota_utc_midnight_reset(mock_redis):
    """Quota automatically resets when date advances past 00:00 UTC."""
    user_id = 104

    # Day 1: Exhaust quota on 2026-11-10
    day1_date = "2026-11-10"
    for _ in range(50):
        await RateLimitService.check_publisher_ai_daily_quota(user_id=user_id, daily_limit=50, utc_date=day1_date)

    res_day1_blocked = await RateLimitService.check_publisher_ai_daily_quota(
        user_id=user_id, daily_limit=50, utc_date=day1_date
    )
    assert res_day1_blocked.allowed is False

    # Day 2: Advance to 2026-11-11 -> fresh key and full quota
    day2_date = "2026-11-11"
    res_day2_fresh = await RateLimitService.check_publisher_ai_daily_quota(
        user_id=user_id, daily_limit=50, utc_date=day2_date
    )
    assert res_day2_fresh.allowed is True
    assert res_day2_fresh.remaining == 49

    # Day 1 key is intact in Redis
    assert mock_redis.strings[f"quota:publisher_ai:{user_id}:{day1_date}"] == "50"
    assert mock_redis.strings[f"quota:publisher_ai:{user_id}:{day2_date}"] == "1"


@pytest.mark.asyncio
async def test_atomic_concurrent_daily_quota_reservations(mock_redis):
    """Concurrent requests cannot exceed the daily quota limit."""
    user_id = 105
    limit = 10

    # Fire 25 concurrent reservation attempts
    tasks = [
        RateLimitService.check_publisher_ai_daily_quota(user_id=user_id, daily_limit=limit)
        for _ in range(25)
    ]
    results = await asyncio.gather(*tasks)

    allowed = [r for r in results if r.allowed]
    denied = [r for r in results if not r.allowed]

    assert len(allowed) == 10
    assert len(denied) == 15
    # The remaining values for allowed requests must be unique numbers from 0 to 9
    assert sorted([r.remaining for r in allowed]) == list(range(10))


# ── 2. Burst Rate Limit Tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_burst_limit_below_limit(mock_redis):
    """Requests 1 to 4 in a rolling 60s window succeed."""
    user_id = 201

    for i in range(1, 5):
        res = await RateLimitService.check_publisher_ai_burst_limit(user_id=user_id, max_limit=5)
        assert res.allowed is True
        assert res.limit == 5
        assert res.remaining == 5 - i
        assert res.retry_after_seconds == 60


@pytest.mark.asyncio
async def test_burst_limit_at_limit(mock_redis):
    """The 5th burst request succeeds with 0 remaining."""
    user_id = 202

    for _ in range(4):
        await RateLimitService.check_publisher_ai_burst_limit(user_id=user_id, max_limit=5)

    res_5 = await RateLimitService.check_publisher_ai_burst_limit(user_id=user_id, max_limit=5)
    assert res_5.allowed is True
    assert res_5.remaining == 0


@pytest.mark.asyncio
async def test_burst_limit_above_limit_denied(mock_redis):
    """The 6th request within 60 seconds is rejected with retry timing."""
    user_id = 203

    for _ in range(5):
        await RateLimitService.check_publisher_ai_burst_limit(user_id=user_id, max_limit=5)

    res_6 = await RateLimitService.check_publisher_ai_burst_limit(user_id=user_id, max_limit=5)
    assert res_6.allowed is False
    assert res_6.limit == 5
    assert res_6.remaining == 0
    assert 1 <= res_6.retry_after_seconds <= 60


@pytest.mark.asyncio
async def test_burst_limit_rolling_window_expiry(mock_redis):
    """Requests older than 60 seconds roll out of the sliding window."""
    user_id = 204
    t0 = 1000.0

    mock_redis.simulated_time = t0
    # Make 5 requests at t0
    with patch("time.time", return_value=t0):
        for _ in range(5):
            res = await RateLimitService.check_publisher_ai_burst_limit(user_id=user_id, max_limit=5)
            assert res.allowed is True

        # 6th request at t0 + 10s is blocked
        mock_redis.simulated_time = t0 + 10.0
        with patch("time.time", return_value=t0 + 10.0):
            res_blocked = await RateLimitService.check_publisher_ai_burst_limit(user_id=user_id, max_limit=5)
            assert res_blocked.allowed is False
            assert res_blocked.retry_after_seconds == 50  # 60 - 10

    # Advance time to t0 + 61s: all initial 5 requests have rolled out
    t_after = t0 + 61.0
    mock_redis.simulated_time = t_after
    with patch("time.time", return_value=t_after):
        res_after = await RateLimitService.check_publisher_ai_burst_limit(user_id=user_id, max_limit=5)
        assert res_after.allowed is True
        assert res_after.remaining == 4


@pytest.mark.asyncio
async def test_atomic_concurrent_burst_reservations(mock_redis):
    """Concurrent burst requests cannot exceed the rolling window limit."""
    user_id = 205
    limit = 5

    tasks = [
        RateLimitService.check_publisher_ai_burst_limit(user_id=user_id, max_limit=limit)
        for _ in range(15)
    ]
    results = await asyncio.gather(*tasks)

    allowed = [r for r in results if r.allowed]
    denied = [r for r in results if not r.allowed]

    assert len(allowed) == 5
    assert len(denied) == 10
    assert sorted([r.remaining for r in allowed]) == [0, 1, 2, 3, 4]


# ── 3. Quota & Identity Isolation Tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_independent_publisher_quotas(mock_redis):
    """User A exhausting quota does not affect User B."""
    user_a = 301
    user_b = 302

    # Exhaust user A's 50 requests
    for _ in range(50):
        await RateLimitService.check_publisher_ai_daily_quota(user_id=user_a, daily_limit=50)

    res_a = await RateLimitService.check_publisher_ai_daily_quota(user_id=user_a, daily_limit=50)
    assert res_a.allowed is False

    # User B should still have full quota
    res_b = await RateLimitService.check_publisher_ai_daily_quota(user_id=user_b, daily_limit=50)
    assert res_b.allowed is True
    assert res_b.remaining == 49


@pytest.mark.asyncio
async def test_independent_publisher_burst_buckets(mock_redis):
    """User A exhausting burst does not affect User B."""
    user_a = 303
    user_b = 304

    # Exhaust user A's 5 burst requests
    for _ in range(5):
        await RateLimitService.check_publisher_ai_burst_limit(user_id=user_a, max_limit=5)

    res_a = await RateLimitService.check_publisher_ai_burst_limit(user_id=user_a, max_limit=5)
    assert res_a.allowed is False

    # User B should still have full burst bucket
    res_b = await RateLimitService.check_publisher_ai_burst_limit(user_id=user_b, max_limit=5)
    assert res_b.allowed is True
    assert res_b.remaining == 4


@pytest.mark.asyncio
async def test_namespaced_redis_key_isolation_from_api_keys(mock_redis):
    """Publisher AI keys and API key quota keys do not collide."""
    user_id = 401
    date_str = RateLimitService.get_utc_date_str()

    ai_key = f"quota:publisher_ai:{user_id}:{date_str}"
    api_key = f"quota:apikey:{user_id}:{date_str}"

    # Perform AI quota reservation
    await RateLimitService.check_publisher_ai_daily_quota(user_id=user_id, daily_limit=50)
    # Perform API key daily quota check on same numerical ID
    await RateLimitService.check_daily_quota(key_id=user_id, daily_quota=100)

    assert mock_redis.strings[ai_key] == "1"
    assert mock_redis.strings[api_key] == "1"
    assert ai_key != api_key


# ── 4. Combined Reservation Accounting Semantics ──────────────────────────────

@pytest.mark.asyncio
async def test_burst_rejection_prevents_daily_quota_consumption(mock_redis):
    """If burst limit is exceeded, daily quota must NOT be incremented."""
    user_id = 501
    date_str = RateLimitService.get_utc_date_str()
    daily_key = f"quota:publisher_ai:{user_id}:{date_str}"

    # Consume all 5 burst slots
    for _ in range(5):
        res = await RateLimitService.reserve_publisher_ai_quota(user_id=user_id)
        assert res.allowed is True

    assert mock_redis.strings[daily_key] == "5"

    # 6th request fails burst check
    res_6 = await RateLimitService.reserve_publisher_ai_quota(user_id=user_id)
    assert res_6.allowed is False
    assert res_6.reason == "burst_limit_exceeded"
    assert res_6.quota is None
    assert res_6.burst.allowed is False

    # CRITICAL: Daily quota must remain at 5, NOT 6!
    assert mock_redis.strings[daily_key] == "5"


@pytest.mark.asyncio
async def test_daily_quota_exhausted_reports_correct_reason(mock_redis):
    """When burst is available but daily quota is exhausted, daily_quota_exceeded is returned."""
    user_id = 502
    daily_limit = 2

    res1 = await RateLimitService.reserve_publisher_ai_quota(user_id=user_id, daily_limit=daily_limit)
    assert res1.allowed is True
    res2 = await RateLimitService.reserve_publisher_ai_quota(user_id=user_id, daily_limit=daily_limit)
    assert res2.allowed is True

    # 3rd request passes burst (limit 5) but fails daily quota (limit 2)
    res3 = await RateLimitService.reserve_publisher_ai_quota(user_id=user_id, daily_limit=daily_limit)
    assert res3.allowed is False
    assert res3.reason == "daily_quota_exceeded"
    assert res3.quota.allowed is False
    assert res3.burst.allowed is True


@pytest.mark.asyncio
async def test_ensure_publisher_ai_quota_raises_http_429_burst(mock_redis):
    """ensure_publisher_ai_quota raises HTTP 429 with Retry-After when burst exceeded."""
    user_id = 503

    for _ in range(5):
        await RateLimitService.ensure_publisher_ai_quota(user_id=user_id)

    with pytest.raises(HTTPException) as exc_info:
        await RateLimitService.ensure_publisher_ai_quota(user_id=user_id)

    assert exc_info.value.status_code == 429
    assert "burst limit exceeded" in exc_info.value.detail
    assert "Retry-After" in exc_info.value.headers
    assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"


@pytest.mark.asyncio
async def test_ensure_publisher_ai_quota_raises_http_429_daily(mock_redis):
    """ensure_publisher_ai_quota raises HTTP 429 with RFC headers when daily quota exceeded."""
    user_id = 504

    for _ in range(2):
        await RateLimitService.ensure_publisher_ai_quota(user_id=user_id, daily_limit=2)

    with pytest.raises(HTTPException) as exc_info:
        await RateLimitService.ensure_publisher_ai_quota(user_id=user_id, daily_limit=2)

    assert exc_info.value.status_code == 429
    assert "Daily AI generation quota exceeded" in exc_info.value.detail
    assert "Retry-After" in exc_info.value.headers
    assert exc_info.value.headers["X-Quota-Limit"] == "2"
    assert exc_info.value.headers["X-Quota-Remaining"] == "0"
    assert "X-Quota-Reset" in exc_info.value.headers


# ── 5. Non-Mutating Inspection & Validation Tests ─────────────────────────────

@pytest.mark.asyncio
async def test_non_mutating_get_publisher_ai_quota_status(mock_redis):
    """get_publisher_ai_quota_status reads quota without consuming it."""
    user_id = 601
    date_str = RateLimitService.get_utc_date_str()
    key = f"quota:publisher_ai:{user_id}:{date_str}"

    # Consume 3 reservations
    for _ in range(3):
        await RateLimitService.check_publisher_ai_daily_quota(user_id=user_id, daily_limit=50)

    assert mock_redis.strings[key] == "3"

    # Inspect status 5 times
    for _ in range(5):
        status_res = await RateLimitService.get_publisher_ai_quota_status(user_id=user_id, daily_limit=50)
        assert status_res.allowed is True
        assert status_res.limit == 50
        assert status_res.remaining == 47

    # Counter in Redis must still be 3
    assert mock_redis.strings[key] == "3"


@pytest.mark.asyncio
async def test_invalid_user_id_rejected():
    """Invalid, missing, zero, or negative user_ids are immediately rejected."""
    invalid_ids = [0, -1, -99, "10", None, 3.14, {}]

    for uid in invalid_ids:
        with pytest.raises(ValueError):
            await RateLimitService.check_publisher_ai_burst_limit(user_id=uid)

        with pytest.raises(ValueError):
            await RateLimitService.check_publisher_ai_daily_quota(user_id=uid)

        with pytest.raises(ValueError):
            await RateLimitService.get_publisher_ai_quota_status(user_id=uid)


# ── 6. Fail-Closed Error Handling Tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_unavailable_fail_closed_burst():
    """HTTP 503 is raised when Redis is down during burst check."""
    mock_wrapper = MagicMock()
    mock_wrapper.connected = False
    mock_wrapper.connect = MagicMock(side_effect=Exception("Connection refused"))

    with patch("liteads.ad_server.services.rate_limit_service.redis_client", mock_wrapper):
        with pytest.raises(HTTPException) as exc_info:
            await RateLimitService.check_publisher_ai_burst_limit(user_id=701)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Rate limit service unavailable"


@pytest.mark.asyncio
async def test_redis_unavailable_fail_closed_daily_quota():
    """HTTP 503 is raised when Redis is down during daily quota check."""
    mock_wrapper = MagicMock()
    mock_wrapper.connected = False
    mock_wrapper.connect = MagicMock(side_effect=Exception("Connection refused"))

    with patch("liteads.ad_server.services.rate_limit_service.redis_client", mock_wrapper):
        with pytest.raises(HTTPException) as exc_info:
            await RateLimitService.check_publisher_ai_daily_quota(user_id=702)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Quota service unavailable"


@pytest.mark.asyncio
async def test_redis_eval_exception_fail_closed_no_secret_leaks():
    """Redis eval errors raise 503 without exposing internal trace or connection string."""
    mock_inner = MagicMock()
    mock_inner.eval = MagicMock(side_effect=Exception("redis://pass:secret@redis-internal:6379 crashed"))
    mock_wrapper = MagicMock()
    mock_wrapper.connected = True
    mock_wrapper.client = mock_inner

    with patch("liteads.ad_server.services.rate_limit_service.redis_client", mock_wrapper):
        with pytest.raises(HTTPException) as exc_info:
            await RateLimitService.check_publisher_ai_daily_quota(user_id=703)
        assert exc_info.value.status_code == 503
        assert "secret" not in exc_info.value.detail
        assert "redis-internal" not in exc_info.value.detail


# ── 7. Regression Tests for Existing API Key Methods ─────────────────────────

@pytest.mark.asyncio
async def test_existing_api_key_check_rate_limit_regression(mock_redis):
    """Existing RateLimitService.check_rate_limit behaves identically."""
    key_id = 801
    rate_limit = 3

    # 1st request
    allowed, rem, reset = await RateLimitService.check_rate_limit(key_id=key_id, rate_limit_per_minute=rate_limit)
    assert allowed is True
    assert rem == 2
    assert reset == 60

    # 2nd request
    allowed, rem, reset = await RateLimitService.check_rate_limit(key_id=key_id, rate_limit_per_minute=rate_limit)
    assert allowed is True
    assert rem == 1

    # 3rd request
    allowed, rem, reset = await RateLimitService.check_rate_limit(key_id=key_id, rate_limit_per_minute=rate_limit)
    assert allowed is True
    assert rem == 0

    # 4th request exceeds rate limit
    allowed, rem, reset = await RateLimitService.check_rate_limit(key_id=key_id, rate_limit_per_minute=rate_limit)
    assert allowed is False
    assert rem == 0
    assert 1 <= reset <= 60


@pytest.mark.asyncio
async def test_existing_api_key_check_daily_quota_regression(mock_redis):
    """Existing RateLimitService.check_daily_quota behaves identically."""
    key_id = 802
    daily_quota = 3

    # 1st request
    allowed, rem, reset = await RateLimitService.check_daily_quota(key_id=key_id, daily_quota=daily_quota)
    assert allowed is True
    assert rem == 2
    assert reset > 0

    # 2nd request
    allowed, rem, reset = await RateLimitService.check_daily_quota(key_id=key_id, daily_quota=daily_quota)
    assert allowed is True
    assert rem == 1

    # 3rd request
    allowed, rem, reset = await RateLimitService.check_daily_quota(key_id=key_id, daily_quota=daily_quota)
    assert allowed is True
    assert rem == 0

    # 4th request exceeds daily quota
    allowed, rem, reset = await RateLimitService.check_daily_quota(key_id=key_id, daily_quota=daily_quota)
    assert allowed is False
    assert rem == 0
    assert reset > 0

