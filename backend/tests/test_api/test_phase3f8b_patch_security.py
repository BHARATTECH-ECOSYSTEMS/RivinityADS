import pytest
from decimal import Decimal
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from liteads.models.ad import Advertiser, Campaign, Creative, TargetingRule
from liteads.models.domain import AdvertiserDomain
from liteads.models.base import ModerationStatus
from liteads.models import User
from liteads.common.security import create_access_token
from liteads.ad_server.services.tools import update_campaign as tool_update_campaign
from liteads.ad_server.services.tools import activate_campaign as tool_activate_campaign
from liteads.ad_server.services.tools import ToolValidationError, ToolAuthorizationError


@pytest.fixture
def auth_headers():
    def _get_headers(user: User):
        token = create_access_token(user)
        return {"Authorization": f"Bearer {token}"}
    return _get_headers


@pytest.fixture
async def setup_data(test_db: AsyncSession):
    user = User(id=300, email="adv300@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Test Adv 300", owner_user_id=user.id, balance=Decimal("100.0"))
    test_db.add_all([user, adv])
    await test_db.commit()
    user.advertiser_id = adv.id
    
    user2 = User(id=301, email="adv301@test.com", role="advertiser", name="test", password_hash="test")
    adv2 = Advertiser(name="Test Adv 301", owner_user_id=user2.id, balance=Decimal("0.0"))
    test_db.add_all([user2, adv2])
    await test_db.commit()
    user2.advertiser_id = adv2.id

    camp1 = Campaign(advertiser_id=adv.id, name="Camp 1", status=5, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0")) 
    camp2 = Campaign(advertiser_id=adv.id, name="Camp 2", status=1) 
    camp3 = Campaign(advertiser_id=adv.id, name="Camp 3", status=2) 
    camp4 = Campaign(advertiser_id=adv2.id, name="Camp 4", status=5, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0"))
    
    test_db.add_all([camp1, camp2, camp3, camp4])
    await test_db.flush()
    
    creative1 = Creative(campaign_id=camp1.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.APPROVED.value)
    targeting1 = TargetingRule(campaign_id=camp1.id, rule_type="geo", rule_value={"countries": ["US"]})
    domain1 = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test_token")
    creative4 = Creative(campaign_id=camp4.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.APPROVED.value)
    targeting4 = TargetingRule(campaign_id=camp4.id, rule_type="geo", rule_value={"countries": ["US"]})
    domain4 = AdvertiserDomain(advertiser_id=adv2.id, domain="example.com", status="VERIFIED", verification_token="test_token")
    test_db.add_all([creative1, targeting1, domain1, creative4, targeting4, domain4])
    await test_db.commit()
    
    await test_db.refresh(user)
    await test_db.refresh(user2)
    await test_db.refresh(adv)
    await test_db.refresh(adv2)
    await test_db.refresh(camp1)
    await test_db.refresh(camp2)
    await test_db.refresh(camp3)
    await test_db.refresh(camp4)

    return {
        "user1": user,
        "adv1": adv,
        "user2": user2,
        "adv2": adv2,
        "camp1": camp1,
        "camp2": camp2,
        "camp3": camp3,
        "camp4": camp4,
    }


@pytest.mark.asyncio
async def test_patch_status_cannot_activate(client: AsyncClient, test_db: AsyncSession, auth_headers, setup_data):
    user = setup_data["user1"]
    camp = setup_data["camp1"]
    headers = auth_headers(user)
    
    resp = await client.patch(f"/api/v1/campaign/{camp.id}", json={"status": 1}, headers=headers)
    assert resp.status_code == 422 # Pydantic extra=forbid should reject it
    
    await test_db.refresh(camp)
    assert camp.status == 5


@pytest.mark.asyncio
async def test_patch_status_draft_rejected(client: AsyncClient, test_db: AsyncSession, auth_headers, setup_data):
    user = setup_data["user1"]
    camp = setup_data["camp1"]
    headers = auth_headers(user)
    
    resp = await client.patch(f"/api/v1/campaign/{camp.id}", json={"status": 5}, headers=headers)
    assert resp.status_code == 422
    
    await test_db.refresh(camp)
    assert camp.status == 5


@pytest.mark.asyncio
async def test_patch_status_active_rejected(client: AsyncClient, test_db: AsyncSession, auth_headers, setup_data):
    user = setup_data["user1"]
    camp = setup_data["camp2"]
    headers = auth_headers(user)
    
    resp = await client.patch(f"/api/v1/campaign/{camp.id}", json={"status": 2}, headers=headers)
    assert resp.status_code == 422
    
    await test_db.refresh(camp)
    assert camp.status == 1


@pytest.mark.asyncio
async def test_patch_status_paused_rejected(client: AsyncClient, test_db: AsyncSession, auth_headers, setup_data):
    user = setup_data["user1"]
    camp = setup_data["camp3"]
    headers = auth_headers(user)
    
    resp = await client.patch(f"/api/v1/campaign/{camp.id}", json={"status": 1}, headers=headers)
    assert resp.status_code == 422
    
    await test_db.refresh(camp)
    assert camp.status == 2


@pytest.mark.asyncio
async def test_patch_legitimate_fields_allowed(client: AsyncClient, test_db: AsyncSession, auth_headers, setup_data):
    user = setup_data["user1"]
    camp = setup_data["camp1"]
    headers = auth_headers(user)
    
    resp = await client.patch(f"/api/v1/campaign/{camp.id}", json={"name": "Updated Name", "budget_daily": "10.0"}, headers=headers)
    assert resp.status_code == 200
    
    await test_db.refresh(camp)
    assert camp.name == "Updated Name"
    assert camp.budget_daily == Decimal("10.0")
    assert camp.status == 5


@pytest.mark.asyncio
async def test_dedicated_activation_success(client: AsyncClient, test_db: AsyncSession, auth_headers, setup_data):
    user = setup_data["user1"]
    camp = setup_data["camp1"] # DRAFT, balance > 0
    headers = auth_headers(user)
    
    resp = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert resp.status_code == 200
    
    await test_db.refresh(camp)
    assert camp.status == 1


@pytest.mark.asyncio
async def test_dedicated_activation_rejects_no_balance(client: AsyncClient, test_db: AsyncSession, auth_headers, setup_data):
    user = setup_data["user2"]
    camp = setup_data["camp4"] # DRAFT, balance <= 0
    headers = auth_headers(user)
    
    resp = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert resp.status_code == 400
    assert "balance" in resp.json()["detail"].lower()
    
    await test_db.refresh(camp)
    assert camp.status == 5


@pytest.mark.asyncio
async def test_cross_user_campaign_access_rejected(client: AsyncClient, test_db: AsyncSession, auth_headers, setup_data):
    user = setup_data["user2"] # adv2
    camp = setup_data["camp1"] # owned by adv1
    headers = auth_headers(user)
    
    # Try PATCH
    resp_patch = await client.patch(f"/api/v1/campaign/{camp.id}", json={"name": "Hack"}, headers=headers)
    assert resp_patch.status_code == 403
    
    # Try Activate
    resp_act = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert resp_act.status_code == 403


@pytest.mark.asyncio
async def test_chat_update_tool_cannot_modify_status(test_db: AsyncSession, setup_data):
    user = setup_data["user1"]
    camp = setup_data["camp1"]
    
    # The signature of update_campaign doesn't have status, 
    # but we can try updating other fields and verify status remains.
    res = await tool_update_campaign(
        user=user,
        session=test_db,
        campaign_id=camp.id,
        name="Tool Name",
        budget_daily=Decimal("50.0")
    )
    
    assert res.status == "DRAFT"
    
    await test_db.refresh(camp)
    assert camp.name == "Tool Name"
    assert camp.budget_daily == Decimal("50.0")
    assert camp.status == 5
    
    # Passing an unknown kwarg (like status) would raise TypeError in Python,
    # so we don't even need to test it at runtime, it's structurally impossible.
    with pytest.raises(TypeError):
        await tool_update_campaign(
            user=user,
            session=test_db,
            campaign_id=camp.id,
            status=1 # This will throw TypeError
        )


@pytest.mark.asyncio
async def test_chat_activate_tool_success(test_db: AsyncSession, setup_data):
    user = setup_data["user1"]
    camp = setup_data["camp1"] # balance > 0, DRAFT
    
    res = await tool_activate_campaign(
        user=user,
        session=test_db,
        campaign_id=camp.id
    )
    
    assert res.status == "ACTIVE"
    await test_db.refresh(camp)
    assert camp.status == 1


@pytest.mark.asyncio
async def test_chat_activate_tool_rejects_no_balance(test_db: AsyncSession, setup_data):
    user = setup_data["user2"]
    camp = setup_data["camp4"] # balance <= 0, DRAFT
    
    with pytest.raises(ToolValidationError, match="balance"):
        await tool_activate_campaign(
            user=user,
            session=test_db,
            campaign_id=camp.id
        )
    
    await test_db.refresh(camp)
    assert camp.status == 5
