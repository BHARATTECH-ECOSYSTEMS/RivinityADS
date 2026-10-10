import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from liteads.models.ad import Advertiser, Campaign, Creative, TargetingRule
from liteads.models.domain import AdvertiserDomain
from liteads.models import User
from liteads.models.base import ModerationStatus
from liteads.common.security import create_access_token
from decimal import Decimal


@pytest.fixture
def auth_headers():
    def _get_headers(user: User):
        token = create_access_token(user)
        return {"Authorization": f"Bearer {token}"}
    return _get_headers


@pytest.mark.asyncio
async def test_activation_rejects_pending_creative(client: AsyncClient, test_db: AsyncSession, auth_headers):
    user = User(id=401, email="verified401@test.com", role="advertiser", name="test", password_hash="test", email_verified=True)
    adv = Advertiser(name="Test Adv", owner_user_id=user.id, balance=Decimal("100.0"))
    test_db.add_all([user, adv])
    await test_db.commit()
    user.advertiser_id = adv.id

    campaign = Campaign(advertiser_id=adv.id, name="Test Campaign", status=5, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0"))
    test_db.add(campaign)
    await test_db.flush()
    
    # PENDING creative
    creative = Creative(campaign_id=campaign.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.PENDING_REVIEW.value)
    targeting = TargetingRule(campaign_id=campaign.id, rule_type="geo", rule_value={"countries": ["US"]})
    domain = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test")
    test_db.add_all([creative, targeting, domain])
    await test_db.commit()

    headers = auth_headers(user)
    resp = await client.post(f"/api/v1/campaign/{campaign.id}/activate", headers=headers)
    assert resp.status_code == 400
    assert "not approved by moderation" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_activation_rejects_unverified_domain(client: AsyncClient, test_db: AsyncSession, auth_headers):
    user = User(id=402, email="verified402@test.com", role="advertiser", name="test", password_hash="test", email_verified=True)
    adv = Advertiser(name="Test Adv", owner_user_id=user.id, balance=Decimal("100.0"))
    test_db.add_all([user, adv])
    await test_db.commit()
    user.advertiser_id = adv.id

    campaign = Campaign(advertiser_id=adv.id, name="Test Campaign", status=5, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0"))
    test_db.add(campaign)
    await test_db.flush()
    
    creative = Creative(campaign_id=campaign.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.APPROVED.value)
    targeting = TargetingRule(campaign_id=campaign.id, rule_type="geo", rule_value={"countries": ["US"]})
    # Domain not added
    test_db.add_all([creative, targeting])
    await test_db.commit()

    headers = auth_headers(user)
    resp = await client.post(f"/api/v1/campaign/{campaign.id}/activate", headers=headers)
    assert resp.status_code == 400
    assert "domain" in resp.json()["detail"].lower()
    assert "not verified" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ad_serving_excludes_unapproved_creatives(client: AsyncClient, test_db: AsyncSession):
    user = User(id=403, email="verified403@test.com", role="advertiser", name="test", password_hash="test", email_verified=True)
    adv = Advertiser(name="Test Adv", owner_user_id=user.id, balance=Decimal("100.0"))
    test_db.add_all([user, adv])
    await test_db.commit()
    user.advertiser_id = adv.id

    # Active campaign
    campaign = Campaign(advertiser_id=adv.id, name="Test Campaign", status=1, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0"))
    test_db.add(campaign)
    await test_db.flush()
    
    # Active but PENDING creative
    creative = Creative(campaign_id=campaign.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.PENDING_REVIEW.value)
    targeting = TargetingRule(campaign_id=campaign.id, rule_type="geo", rule_value={"countries": ["US"]})
    domain = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test")
    test_db.add_all([creative, targeting, domain])
    await test_db.commit()

    # Hit ad serving
    req_body = {
        "slot_id": "banner_home_top",
        "device": {"os": "iOS", "os_version": "14", "device_type": "mobile", "model": "iPhone", "brand": "Apple", "screen_width": 390, "screen_height": 844},
        "geo": {"country": "US"}
    }
    resp = await client.post("/api/v1/ad/request", json=req_body)
    # Ad server returns 200 with empty list or whatever
    assert resp.status_code == 200
    assert len(resp.json().get("ads", [])) == 0
