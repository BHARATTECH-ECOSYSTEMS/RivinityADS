"""
User Profile service.

Manages user sector affinities in Redis for personalized ranking.
"""

import time
from typing import Dict, List

import redis.exceptions

from liteads.common.cache import redis_client
from liteads.common.logger import get_logger
from liteads.models import EventType

logger = get_logger(__name__)

# Constants for affinity scoring
WEIGHTS = {
    EventType.IMPRESSION: 0.1,
    EventType.CLICK: 1.0,
    EventType.CONVERSION: 3.0,
}

# 90 days in seconds
TTL_SECONDS = 90 * 24 * 3600
# Decay threshold: 6 hours
DECAY_INTERVAL_SECONDS = 6 * 3600
# Decay multiplier
DECAY_FACTOR = 0.95


async def record_activity(user_id: str, sectors: List[str], event_type: int) -> None:
    """
    Record user activity to update sector affinities.
    
    Args:
        user_id: The ID of the user.
        sectors: List of targeted sectors for the ad they interacted with.
        event_type: Type of event (impression, click, conversion).
    """
    if not user_id or not sectors:
        return

    weight = float(WEIGHTS.get(event_type, 0.1))
    now = int(time.time())

    profile_key = f"oas:profile:{user_id}:affinity"
    recency_key = f"oas:profile:{user_id}:recency"

    try:
        # Get the last updated time to determine if we should apply decay
        last_update_raw = await redis_client.get(recency_key)
        last_update = int(last_update_raw) if last_update_raw else 0

        # If it's been more than DECAY_INTERVAL_SECONDS since the last update, decay existing scores
        if last_update > 0 and (now - last_update) > DECAY_INTERVAL_SECONDS:
            # We decay all current affinities
            current_affinities = await redis_client.hgetall(profile_key)
            if current_affinities:
                for s, val_str in current_affinities.items():
                    try:
                        val = float(val_str)
                        decayed = val * DECAY_FACTOR
                        if decayed < 0.01:
                            await redis_client.hdel(profile_key, s)
                        else:
                            await redis_client.hset(profile_key, s, str(decayed))
                    except ValueError:
                        pass
        
        # Increment scores for the interacted sectors
        for sector in sectors:
            await redis_client.hincrbyfloat(profile_key, sector, weight)
        
        # Update recency and set TTLs
        await redis_client.set(recency_key, str(now))
        await redis_client.expire(profile_key, TTL_SECONDS)
        await redis_client.expire(recency_key, TTL_SECONDS)

    except redis.exceptions.RedisError as e:
        logger.error(f"Redis error in user_profile.record_activity: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in user_profile.record_activity: {e}")


async def get_affinities(user_id: str | None) -> Dict[str, float]:
    """
    Retrieve a user's sector affinities.
    
    Args:
        user_id: The ID of the user.
        
    Returns:
        Dict mapping sector string to affinity float score.
    """
    if not user_id:
        return {}

    profile_key = f"oas:profile:{user_id}:affinity"
    
    try:
        raw_affinities = await redis_client.hgetall(profile_key)
        if not raw_affinities:
            return {}
            
        result = {}
        for sector, val_str in raw_affinities.items():
            try:
                result[sector] = float(val_str)
            except ValueError:
                pass
        return result
        
    except redis.exceptions.RedisError as e:
        logger.error(f"Redis error in user_profile.get_affinities: {e}")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error in user_profile.get_affinities: {e}")
        return {}
