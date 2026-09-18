"""
Tests for ad serving endpoints.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from liteads.ad_server.main import app
from liteads.common.security import authenticate_s2s_or_user
from liteads.models import User, AdSlot, Publisher
from liteads.common.database import get_session

@pytest.fixture(autouse=True)
async def override_auth(test_db):
    async def mock_auth():
        return {"type": "user", "entity": User(id=1, role="admin", email="test@test.com")}
    app.dependency_overrides[authenticate_s2s_or_user] = mock_auth
    
    # Create dummy slot for tests
    publisher = Publisher(id=1, name="test_pub", status=1)
    slot = AdSlot(id=1, publisher_id=1, slot_id="test_slot", name="test", format=1, status=1)
    test_db.add(publisher)
    test_db.add(slot)
    await test_db.commit()
    
    yield
    app.dependency_overrides.pop(authenticate_s2s_or_user, None)

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
