import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import redis.exceptions

from liteads.models import EventType
from liteads.ad_server.services.user_profile import record_activity, get_affinities, DECAY_INTERVAL_SECONDS

@pytest.mark.asyncio
@patch('liteads.ad_server.services.user_profile.redis_client')
async def test_get_affinities_success(mock_redis):
    mock_redis.hgetall = AsyncMock(return_value={"gaming": "1.5", "education": "0.1"})
    
    result = await get_affinities("user1")
    assert result == {"gaming": 1.5, "education": 0.1}
    mock_redis.hgetall.assert_called_once_with("oas:profile:user1:affinity")

@pytest.mark.asyncio
@patch('liteads.ad_server.services.user_profile.redis_client')
async def test_get_affinities_redis_failure(mock_redis):
    # Should gracefully fail and return empty dict without throwing exception
    mock_redis.hgetall.side_effect = redis.exceptions.ConnectionError("Redis down")
    
    result = await get_affinities("user1")
    assert result == {}

@pytest.mark.asyncio
@patch('liteads.ad_server.services.user_profile.time.time')
@patch('liteads.ad_server.services.user_profile.redis_client')
async def test_record_activity_no_decay(mock_redis, mock_time):
    mock_time.return_value = 10000
    # Recency says it was updated just now (no decay needed)
    mock_redis.get = AsyncMock(return_value="9900")
    mock_redis.hincrbyfloat = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.expire = AsyncMock()
    
    await record_activity("user1", ["gaming"], EventType.CLICK)
    
    # Should increment by 1.0 (click weight)
    mock_redis.hincrbyfloat.assert_called_once_with("oas:profile:user1:affinity", "gaming", 1.0)
    # mock_redis.hgetall.assert_not_called()  # No decay happened

@pytest.mark.asyncio
@patch('liteads.ad_server.services.user_profile.time.time')
@patch('liteads.ad_server.services.user_profile.redis_client')
async def test_record_activity_with_decay(mock_redis, mock_time):
    # Time is far in the future compared to last update
    mock_time.return_value = 10000 + DECAY_INTERVAL_SECONDS + 100
    mock_redis.get = AsyncMock(return_value="10000")
    mock_redis.hgetall = AsyncMock(return_value={"gaming": "2.0", "tiny_sector": "0.001"})
    mock_redis.hset = AsyncMock()
    mock_redis.hdel = AsyncMock()
    mock_redis.hincrbyfloat = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.expire = AsyncMock()
    
    await record_activity("user1", ["education"], EventType.IMPRESSION)
    
    # Should have decayed gaming from 2.0 to 1.9 (0.95 factor)
    mock_redis.hset.assert_called_with("oas:profile:user1:affinity", "gaming", "1.9")
    
    # tiny_sector < 0.01 after decay, so it should be deleted
    mock_redis.hdel.assert_called_with("oas:profile:user1:affinity", "tiny_sector")
    
    # And then record the new impression (0.1 weight)
    mock_redis.hincrbyfloat.assert_called_once_with("oas:profile:user1:affinity", "education", 0.1)

@pytest.mark.asyncio
@patch('liteads.ad_server.services.user_profile.redis_client')
async def test_record_activity_redis_failure(mock_redis):
    # Should fail gracefully
    mock_redis.get = AsyncMock(side_effect=redis.exceptions.TimeoutError("Timeout"))
    
    await record_activity("user1", ["gaming"], EventType.CLICK)
    # Reaches here without exception
    assert True
