import pytest
from httpx import AsyncClient
from liteads.models.publisher import PublisherApiKey, Publisher
from liteads.models.auth import User
from liteads.common.database import get_session
from liteads.ad_server.main import app

@pytest.fixture(autouse=True)
async def auth_override(test_db):
    from liteads.common.security import current_user
    async def mock_user():
        return User(id=1, role="admin", email="admin@test.com", is_active=True, email_verified=True)
    app.dependency_overrides[current_user] = mock_user
    
    publisher = Publisher(id=1, name="test_pub", status=1)
    test_db.add(publisher)
    await test_db.commit()
    
    yield
    app.dependency_overrides.pop(current_user, None)


@pytest.mark.asyncio
async def test_create_api_key(client: AsyncClient, test_db) -> None:
    """Test creating an API key."""
    response = await client.post("/api/v1/publisher/1/keys", json={"name": "Test Key"})
    assert response.status_code == 201
    data = response.json()
    assert "raw_key" in data
    assert data["raw_key"].startswith("pk_live_")
    assert data["id"] is not None
    assert data["name"] == "Test Key"
    assert data["publisher_id"] == 1


@pytest.mark.asyncio
async def test_list_api_keys(client: AsyncClient, test_db) -> None:
    """Test listing API keys."""
    await client.post("/api/v1/publisher/1/keys", json={"name": "Test Key 1"})
    await client.post("/api/v1/publisher/1/keys", json={"name": "Test Key 2"})
    
    response = await client.get("/api/v1/publisher/1/keys")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "Test Key 1"
    assert data[1]["name"] == "Test Key 2"


@pytest.mark.asyncio
async def test_revoke_api_key(client: AsyncClient, test_db) -> None:
    """Test revoking an API key."""
    resp1 = await client.post("/api/v1/publisher/1/keys", json={"name": "Key to Revoke"})
    key_id = resp1.json()["id"]
    
    response = await client.delete(f"/api/v1/publisher/1/keys/{key_id}")
    assert response.status_code == 204
    
    # List keys and verify it's not active or revoked
    resp2 = await client.get("/api/v1/publisher/1/keys")
    data = resp2.json()
    assert len(data) == 1
    assert data[0]["revoked_at"] is not None
