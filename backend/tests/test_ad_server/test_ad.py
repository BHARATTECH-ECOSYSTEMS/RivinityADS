"""
Tests for ad serving endpoints.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from liteads.ad_server.main import app
from liteads.ad_server.services.rate_limit_service import RateLimitService
from liteads.common.security import AuthenticatedAdCaller, authenticate_ad_request
from liteads.models import AdSlot, Publisher
from liteads.common.database import get_session

@pytest_asyncio.fixture(autouse=True)
async def override_auth(test_db):
    async def mock_ad_auth():
        return AuthenticatedAdCaller(
            is_authenticated=True,
            auth_type="api_key",
            publisher_id=1,
            api_key={"id": 1, "is_sandbox": False, "rate_limit_per_minute": 1000, "daily_quota": 10000},
        )
    app.dependency_overrides[authenticate_ad_request] = mock_ad_auth
    
    # Create dummy slot for tests
    publisher = Publisher(id=1, name="test_pub", status=1)
    slot = AdSlot(id=1, publisher_id=1, slot_id="test_slot", name="test", format=1, status=1)
    test_db.add(publisher)
    test_db.add(slot)
    await test_db.commit()
    
    with patch.object(RateLimitService, "check_rate_limit", return_value=(True, 1000, 60)), \
         patch.object(RateLimitService, "check_daily_quota", return_value=(True, 10000, 86400)), \
         patch.object(RateLimitService, "record_usage_metric", new_callable=AsyncMock):
        try:
            yield
        finally:
            app.dependency_overrides.pop(authenticate_ad_request, None)

@pytest.mark.asyncio
async def test_ad_request_empty(
    client: AsyncClient,
    sample_ad_request: dict[str, Any],
) -> None:
    """Test ad request with empty database returns empty ads."""
    response = await client.post("/api/v1/ad/request", json=sample_ad_request)

    assert response.status_code == 200
    data = response.json()
    assert "request_id" in data
    assert data["ads"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_ad_request_validation_error(client: AsyncClient) -> None:
    """Test ad request with invalid data returns validation error."""
    # Missing required field 'slot_id'
    invalid_request = {
        "device": {"os": "android"},
    }

    response = await client.post("/api/v1/ad/request", json=invalid_request)
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_ad_request_with_user_features(
    client: AsyncClient,
    sample_ad_request: dict[str, Any],
) -> None:
    """Test ad request with user features."""
    sample_ad_request["user_features"] = {
        "age": 25,
        "gender": "male",
        "interests": ["sports", "technology"],
    }

    response = await client.post("/api/v1/ad/request", json=sample_ad_request)
    assert response.status_code == 200
    data = response.json()
    assert "request_id" in data
