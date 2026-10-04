from fastapi import HTTPException
from liteads.common.cache import redis_client

_LIMITS = {
    "impression": 1,
    "imp": 1,
    "click": 3,
    "clk": 3,
    "conversion": 1,
    "conv": 1,
}

async def enforce_replay_guard(event_type: str, request_id: str, ad_id: str):
    """
    Enforce replay protection to prevent tracking URL reuse.
    Raises HTTPException 429 if the limit is exceeded.
    """
    event_key = event_type.lower()
    limit = _LIMITS.get(event_key, 1)

    key = f"trk:nonce:{event_key}:{request_id}:{ad_id}"

    # Atomically increment the count
    count = await redis_client.incr(key)

    # Set expiration ONLY on the first increment
    if count == 1:
        await redis_client.expire(key, 900)  # 15 minutes TTL

    if count > limit:
        raise HTTPException(status_code=429, detail="Duplicate event rejected")
