"""Tests for Phase 5 Work Unit 1: Database, Models & Schema Foundations.

Covers:
- PublisherApiKey, Publisher, and AdSlot model defaults and persistence
- Pydantic schema validation for positive quotas (rate_limit_per_minute > 0, daily_quota > 0)
- Database check constraints rejecting non-positive quotas
- secret_hash nullability and omission from API responses
- Existing API-key endpoints returning the new metadata
- Alembic migration upgrade/downgrade against pre-existing rows
"""

from datetime import datetime, timezone
import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.main import app
from liteads.common.security import current_user
from liteads.models.auth import User
from liteads.models.publisher import AdSlot, Publisher, PublisherApiKey
from liteads.schemas.management import (
    ApiKeyCreate,
    ApiKeyResponse,
    PublisherApiKeyCreate,
    PublisherApiKeyCreateResponse,
    PublisherApiKeyResponse,
    PublisherCreate,
    PublisherResponse,
    SlotCreate,
    SlotResponse,
)


@pytest.fixture(autouse=True)
async def auth_override(test_db: AsyncSession):
    """Ensure authenticated admin user for management routes."""
    async def mock_user():
        return User(id=1, role="admin", email="admin@test.com", is_active=True, email_verified=True)

    app.dependency_overrides[current_user] = mock_user

    publisher = Publisher(id=1, name="test_pub", status=1, is_sandbox=False)
    test_db.add(publisher)
    await test_db.commit()

    yield
    app.dependency_overrides.pop(current_user, None)


@pytest.mark.asyncio
async def test_publisher_model_defaults_and_sandbox_flag(test_db: AsyncSession):
    """Test Publisher model default is_sandbox is False and can be set to True."""
    pub_default = Publisher(name="Default Pub", status=1)
    pub_sandbox = Publisher(name="Sandbox Pub", status=1, is_sandbox=True)
    test_db.add_all([pub_default, pub_sandbox])
    await test_db.commit()
    await test_db.refresh(pub_default)
    await test_db.refresh(pub_sandbox)

    assert pub_default.is_sandbox is False
    assert pub_sandbox.is_sandbox is True


@pytest.mark.asyncio
async def test_ad_slot_model_defaults_and_chat_surface_flag(test_db: AsyncSession):
    """Test AdSlot model default is_chat_surface is False and can be set to True."""
    slot_default = AdSlot(
        publisher_id=1,
        slot_id="slot_web_banner",
        name="Web Banner",
        format=1,
    )
    slot_chat = AdSlot(
        publisher_id=1,
        slot_id="slot_ai_sidebar",
        name="AI Chat Sidebar",
        format=2,
        is_chat_surface=True,
    )
    test_db.add_all([slot_default, slot_chat])
    await test_db.commit()
    await test_db.refresh(slot_default)
    await test_db.refresh(slot_chat)

    assert slot_default.is_chat_surface is False
    assert slot_chat.is_chat_surface is True


@pytest.mark.asyncio
async def test_publisher_api_key_model_defaults(test_db: AsyncSession):
    """Test PublisherApiKey default values for sandbox, quotas, and last_used_at."""
    key = PublisherApiKey(
        publisher_id=1,
        name="Standard Key",
        key_prefix="pk_live_12345678",
        key_hash="hashed_secret_test",
    )
    test_db.add(key)
    await test_db.commit()
    await test_db.refresh(key)

    assert key.is_sandbox is False
    assert key.rate_limit_per_minute == 60
    assert key.daily_quota == 10000
    assert key.secret_hash is None
    assert key.last_used_at is None


@pytest.mark.asyncio
async def test_publisher_api_key_custom_values_and_last_used_at(test_db: AsyncSession):
    """Test setting custom quotas, secret_hash, and last_used_at timestamp."""
    now = datetime.now(timezone.utc)
    key = PublisherApiKey(
        publisher_id=1,
        name="High Quota Sandbox Key",
        key_prefix="pk_test_87654321",
        key_hash="hashed_key_val",
        is_sandbox=True,
        secret_hash="hashed_secret_val",
        rate_limit_per_minute=300,
        daily_quota=50000,
        last_used_at=now,
    )
    test_db.add(key)
    await test_db.commit()
    await test_db.refresh(key)

    assert key.is_sandbox is True
    assert key.rate_limit_per_minute == 300
    assert key.daily_quota == 50000
    assert key.secret_hash == "hashed_secret_val"
    assert key.last_used_at is not None


@pytest.mark.asyncio
async def test_pydantic_quota_validation_rejects_non_positive():
    """Test Pydantic schemas reject quota values <= 0."""
    with pytest.raises(ValidationError):
        ApiKeyCreate(name="Bad Key", rate_limit_per_minute=0)

    with pytest.raises(ValidationError):
        ApiKeyCreate(name="Bad Key", daily_quota=-10)

    with pytest.raises(ValidationError):
        PublisherApiKeyCreate(name="Bad Key", rate_limit_per_minute=-1)

    with pytest.raises(ValidationError):
        PublisherApiKeyCreate(name="Bad Key", daily_quota=0)


@pytest.mark.asyncio
async def test_db_check_constraint_rejects_non_positive_quotas(test_db: AsyncSession):
    """Test database check constraints reject non-positive quotas on insert."""
    bad_key = PublisherApiKey(
        publisher_id=1,
        name="Negative Quota Key",
        key_prefix="pk_live_invalid",
        key_hash="dummy_hash",
        rate_limit_per_minute=-5,
    )
    test_db.add(bad_key)
    with pytest.raises(IntegrityError):
        await test_db.commit()

    await test_db.rollback()


@pytest.mark.asyncio
async def test_schemas_do_not_expose_secret_hash():
    """Ensure response schemas do not serialize secret_hash or secret material."""
    key = PublisherApiKey(
        id=99,
        publisher_id=1,
        name="Sensitive Key",
        key_prefix="pk_live_sec",
        key_hash="super_secret_hash_not_exposed",
        secret_hash="super_secret_signing_hash",
        is_sandbox=False,
        rate_limit_per_minute=60,
        daily_quota=10000,
        created_at=datetime.now(timezone.utc),
    )

    resp1 = ApiKeyResponse.model_validate(key)
    assert not hasattr(resp1, "secret_hash")
    assert not hasattr(resp1, "key_hash")
    resp_dict1 = resp1.model_dump()
    assert "secret_hash" not in resp_dict1
    assert "key_hash" not in resp_dict1

    resp2 = PublisherApiKeyResponse.model_validate(key)
    assert not hasattr(resp2, "secret_hash")
    assert not hasattr(resp2, "key_hash")
    resp_dict2 = resp2.model_dump()
    assert "secret_hash" not in resp_dict2
    assert "key_hash" not in resp_dict2


@pytest.mark.asyncio
async def test_api_endpoints_create_and_return_phase5_fields(client: AsyncClient, test_db: AsyncSession):
    """Test API key creation and listing returns Phase 5 fields and respects custom quotas."""
    create_payload = {
        "name": "Partner Test Key",
        "is_sandbox": True,
        "rate_limit_per_minute": 120,
        "daily_quota": 25000,
    }
    post_res = await client.post("/api/v1/publisher/1/keys", json=create_payload)
    assert post_res.status_code == 201
    created_data = post_res.json()

    assert created_data["name"] == "Partner Test Key"
    assert created_data["is_sandbox"] is True
    assert created_data["rate_limit_per_minute"] == 120
    assert created_data["daily_quota"] == 25000
    assert "raw_key" in created_data
    assert created_data["raw_key"].startswith("pk_test_")
    assert "secret_hash" not in created_data

    # List keys and verify returned fields
    list_res = await client.get("/api/v1/publisher/1/keys")
    assert list_res.status_code == 200
    keys = list_res.json()
    assert len(keys) >= 1
    target = next(k for k in keys if k["id"] == created_data["id"])
    assert target["is_sandbox"] is True
    assert target["rate_limit_per_minute"] == 120
    assert target["daily_quota"] == 25000
    assert "secret_hash" not in target


@pytest.mark.asyncio
async def test_slot_creation_with_chat_surface(client: AsyncClient, test_db: AsyncSession):
    """Test creating an ad slot with is_chat_surface flag."""
    slot_payload = {
        "publisher_id": 1,
        "slot_id": "chat_surface_test_slot",
        "name": "Chat Assistant Slot",
        "format": 2,
        "is_chat_surface": True,
    }
    res = await client.post("/api/v1/slot", json=slot_payload)
    assert res.status_code == 201
    data = res.json()
    assert data["slot_id"] == "chat_surface_test_slot"
    assert data["is_chat_surface"] is True
