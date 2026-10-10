import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from liteads.models.ad import Creative, Advertiser, Campaign
from liteads.models.domain import AdvertiserDomain
from liteads.models import User
from liteads.models.base import ModerationStatus, Status
from liteads.common.security import create_access_token
from decimal import Decimal

@pytest.fixture
def auth_headers():
    def _get_headers(user: User):
        token = create_access_token(user)
        return {"Authorization": f"Bearer {token}"}
    return _get_headers


@pytest.mark.asyncio
async def test_creative_creation_defaults_to_pending_and_overrides_status(
    client: AsyncClient,
    test_db: AsyncSession,
    auth_headers,
):
    # Setup Advertiser
    adv_user = User(name="Adv User", email="adv@test.com", password_hash="hash", role="advertiser", email_verified=True)
    test_db.add(adv_user)
    await test_db.flush()
    
    advertiser = Advertiser(name="Test Adv", owner_user_id=adv_user.id, balance=Decimal("1000"))
    test_db.add(advertiser)
    await test_db.flush()
    adv_user.advertiser_id = advertiser.id
    
    # Setup Admin
    admin_user = User(name="Admin User", email="admin@test.com", password_hash="hash", role="admin", email_verified=True)
    test_db.add(admin_user)
    await test_db.flush()
    
    # Setup Campaign
    campaign = Campaign(
        advertiser_id=advertiser.id,
        name="Test Campaign",
        budget_daily=Decimal("100"),
        budget_total=Decimal("1000"),
        bid_amount=Decimal("1"),
        status=Status.DRAFT
    )
    test_db.add(campaign)
    await test_db.flush()
    
    domain = AdvertiserDomain(advertiser_id=advertiser.id, domain="example.com", status="VERIFIED", verification_token="test_token")
    test_db.add(domain)
    await test_db.commit()

    adv_headers = auth_headers(adv_user)
    admin_headers = auth_headers(admin_user)

    # Advertiser creates a creative, attempting to set it to ACTIVE (1)
    create_payload = {
        "campaign_id": campaign.id,
        "title": "Test Creative",
        "landing_url": "https://example.com",
        "status": 1,  # Try to make it ACTIVE
    }
    
    response = await client.post(
        "/api/v1/creative",
        json=create_payload,
        headers=adv_headers
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test Creative"
    assert data["moderation_status"] == 1  # PENDING_REVIEW
    assert data["status"] == 4  # PENDING (force-changed from ACTIVE)
    creative_id = data["id"]
    
    # Admin can see it in pending
    response = await client.get(
        "/api/v1/moderation/creatives/pending",
        headers=admin_headers
    )
    assert response.status_code == 200
    pending_list = response.json()
    assert any(c["id"] == creative_id for c in pending_list)
    
    # Admin approves it
    response = await client.post(
        f"/api/v1/moderation/creatives/{creative_id}/approve",
        headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["moderation_status"] == 2  # APPROVED
    
    # Now advertiser can update status to ACTIVE
    update_payload = {"status": 1}
    response = await client.patch(
        f"/api/v1/creative/{creative_id}",
        json=update_payload,
        headers=adv_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == 1  # ACTIVE
    
    # If advertiser updates sensitive field, it goes back to PENDING_REVIEW and status PENDING
    update_payload = {"title": "Updated Creative"}
    response = await client.patch(
        f"/api/v1/creative/{creative_id}",
        json=update_payload,
        headers=adv_headers
    )
    assert response.status_code == 200
    assert response.json()["moderation_status"] == 1  # PENDING_REVIEW
    assert response.json()["status"] == 4  # PENDING
    
    # Admin rejects it
    response = await client.post(
        f"/api/v1/moderation/creatives/{creative_id}/reject",
        json={"reason": "Inappropriate title"},
        headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["moderation_status"] == 3  # REJECTED
    assert response.json()["moderation_reason"] == "Inappropriate title"
    assert response.json()["status"] == 4  # PENDING
    
    # Admin reopens it
    response = await client.post(
        f"/api/v1/moderation/creatives/{creative_id}/reopen",
        headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["moderation_status"] == 1  # PENDING_REVIEW
