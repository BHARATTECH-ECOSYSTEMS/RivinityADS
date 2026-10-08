import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from liteads.models.ad import Advertiser, Campaign
from liteads.models.auth import User
from liteads.models.base import Status
from liteads.common.security import create_access_token

def get_auth_headers(user: User):
    token = create_access_token(user)
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
async def setup_advertiser(test_db: AsyncSession):
    user = User(
        email="draftcreator@test.com", 
        role="advertiser", 
        name="Test Advertiser", 
        password_hash="test"
    )
    test_db.add(user)
    await test_db.flush()
    
    adv = Advertiser(
        name="Test Adv 3E",
        owner_user_id=user.id,
    )
    test_db.add(adv)
    await test_db.flush()
    
    user.advertiser_id = adv.id
    await test_db.commit()
    return user, adv

@pytest.mark.asyncio
async def test_draft_campaign_creation(client: AsyncClient, test_db: AsyncSession, setup_advertiser):
    user, adv = setup_advertiser
    headers = get_auth_headers(user)
    
    # TEST 1: DRAFT CREATION
    payload = {
        "advertiser_id": adv.id,
        "name": "Phase 3E Campaign",
        "budget_daily": 1000
    }
    
    resp = await client.post("/api/v1/campaign", json=payload, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    
    assert data["name"] == "Phase 3E Campaign"
    assert data["status"] == Status.DRAFT
    
    # Verify in database
    campaign = await test_db.get(Campaign, data["id"])
    assert campaign is not None
    assert campaign.status == Status.DRAFT

@pytest.mark.asyncio
async def test_active_creation_safety(client: AsyncClient, test_db: AsyncSession, setup_advertiser):
    user, adv = setup_advertiser
    headers = get_auth_headers(user)
    
    # TEST 3: ACTIVE CREATION SAFETY (Client tries to force ACTIVE)
    payload = {
        "advertiser_id": adv.id,
        "name": "Forced Active Campaign",
        "status": 1  # Should be ignored or fail if we didn't remove it from schema. Since we removed it, it's ignored.
    }
    
    resp = await client.post("/api/v1/campaign", json=payload, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    
    # Must still be created as DRAFT
    assert data["status"] == Status.DRAFT
    
    # Verify in database
    campaign = await test_db.get(Campaign, data["id"])
    assert campaign.status == Status.DRAFT

@pytest.mark.asyncio
async def test_ownership_validation_preserved(client: AsyncClient, test_db: AsyncSession, setup_advertiser):
    user, adv = setup_advertiser
    headers = get_auth_headers(user)
    
    # Create another advertiser not owned by this user
    other_user = User(email="other@test.com", role="advertiser", name="Other", password_hash="test")
    test_db.add(other_user)
    await test_db.flush()
    other_adv = Advertiser(name="Other Adv", owner_user_id=other_user.id)
    test_db.add(other_adv)
    await test_db.commit()
    
    # TEST 4: OWNERSHIP
    payload = {
        "advertiser_id": other_adv.id,
        "name": "Stolen Campaign",
    }
    
    resp = await client.post("/api/v1/campaign", json=payload, headers=headers)
    assert resp.status_code == 403
