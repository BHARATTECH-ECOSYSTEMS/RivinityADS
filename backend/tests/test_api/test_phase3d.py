import pytest
import asyncio
from decimal import Decimal
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, MagicMock

from liteads.models.ad import Advertiser, Campaign, Creative, TargetingRule
from liteads.models.domain import AdvertiserDomain
from liteads.models.finance import Transaction
from liteads.models import User
from liteads.models.base import ModerationStatus
from liteads.common.security import create_access_token


@pytest.fixture
def auth_headers():
    def _get_headers(user: User):
        token = create_access_token(user)
        return {"Authorization": f"Bearer {token}"}
    return _get_headers


@pytest.mark.asyncio
async def test_successful_activation(client: AsyncClient, test_db: AsyncSession, auth_headers):
    # Setup
    user = User(id=200, email="adv200@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Test Adv", owner_user_id=user.id, balance=Decimal("100.0"))
    test_db.add_all([user, adv])
    await test_db.commit()
    
    user.advertiser_id = adv.id
    await test_db.commit()

    campaign = Campaign(advertiser_id=adv.id, name="Test Campaign", status=5, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0")) # DRAFT
    test_db.add(campaign)
    await test_db.flush()
    creative = Creative(campaign_id=campaign.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.APPROVED.value)
    targeting = TargetingRule(campaign_id=campaign.id, rule_type="geo", rule_value={"countries": ["US"]})
    domain = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test_token")
    test_db.add_all([creative, targeting, domain])
    await test_db.commit()
    await test_db.refresh(campaign)

    headers = auth_headers(user)
    
    # Pre-checks
    assert campaign.status == 5
    
    resp = await client.post(f"/api/v1/campaign/{campaign.id}/activate", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == 1
    
    await test_db.refresh(campaign)
    assert campaign.status == 1 # ACTIVE
    
    await test_db.refresh(adv)
    # NO MONEY DEDUCTED
    assert adv.balance == Decimal("100.0")
    
    # NO TRANSACTION CREATED
    result = await test_db.execute(select(Transaction).where(Transaction.advertiser_id == adv.id))
    transactions = result.scalars().all()
    assert len(transactions) == 0


@pytest.mark.asyncio
async def test_unauthenticated(client: AsyncClient, test_db: AsyncSession):
    user = User(id=201, email="adv201@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Test Adv 2", owner_user_id=user.id, balance=Decimal("100.0"))
    test_db.add_all([user, adv])
    await test_db.commit()

    campaign = Campaign(advertiser_id=adv.id, name="Test Campaign 2", status=5, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0")) # DRAFT
    test_db.add(campaign)
    await test_db.flush()
    creative = Creative(campaign_id=campaign.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.APPROVED.value)
    targeting = TargetingRule(campaign_id=campaign.id, rule_type="geo", rule_value={"countries": ["US"]})
    domain = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test_token")
    test_db.add_all([creative, targeting, domain])
    await test_db.commit()
    await test_db.refresh(campaign)

    # No headers -> 401
    resp = await client.post(f"/api/v1/campaign/{campaign.id}/activate")
    assert resp.status_code == 401
    
    await test_db.refresh(campaign)
    assert campaign.status == 5 # Remains DRAFT


@pytest.mark.asyncio
async def test_wrong_advertiser(client: AsyncClient, test_db: AsyncSession, auth_headers):
    user1 = User(id=202, email="adv202@test.com", role="advertiser", name="test", password_hash="test")
    adv1 = Advertiser(name="Test Adv 1", owner_user_id=user1.id, balance=Decimal("100.0"))
    
    user2 = User(id=203, email="adv203@test.com", role="advertiser", name="test", password_hash="test")
    adv2 = Advertiser(name="Test Adv 2", owner_user_id=user2.id, balance=Decimal("100.0"))
    
    test_db.add_all([user1, adv1, user2, adv2])
    await test_db.commit()
    user1.advertiser_id = adv1.id
    user2.advertiser_id = adv2.id
    await test_db.commit()

    campaign1 = Campaign(advertiser_id=adv1.id, name="Test Campaign", status=5, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0"))
    test_db.add(campaign1)
    await test_db.flush()
    creative = Creative(campaign_id=campaign1.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.APPROVED.value)
    targeting = TargetingRule(campaign_id=campaign1.id, rule_type="geo", rule_value={"countries": ["US"]})
    domain = AdvertiserDomain(advertiser_id=adv1.id, domain="example.com", status="VERIFIED", verification_token="test_token")
    test_db.add_all([creative, targeting, domain])
    await test_db.commit()
    await test_db.refresh(campaign1)

    # user2 tries to activate campaign1
    headers = auth_headers(user2)
    resp = await client.post(f"/api/v1/campaign/{campaign1.id}/activate", headers=headers)
    assert resp.status_code == 403
    
    await test_db.refresh(campaign1)
    assert campaign1.status == 5 # Remains DRAFT


@pytest.mark.asyncio
async def test_non_draft_campaign(client: AsyncClient, test_db: AsyncSession, auth_headers):
    user = User(id=204, email="adv204@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Test Adv", owner_user_id=user.id, balance=Decimal("100.0"))
    test_db.add_all([user, adv])
    await test_db.commit()
    user.advertiser_id = adv.id
    await test_db.commit()

    # Create ACTIVE campaign
    campaign = Campaign(advertiser_id=adv.id, name="Test Campaign", status=1, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0")) # ACTIVE
    test_db.add(campaign)
    await test_db.flush()
    creative = Creative(campaign_id=campaign.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.APPROVED.value)
    targeting = TargetingRule(campaign_id=campaign.id, rule_type="geo", rule_value={"countries": ["US"]})
    domain = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test_token")
    test_db.add_all([creative, targeting, domain])
    await test_db.commit()
    await test_db.refresh(campaign)

    headers = auth_headers(user)
    resp = await client.post(f"/api/v1/campaign/{campaign.id}/activate", headers=headers)
    assert resp.status_code == 400
    assert "Only DRAFT campaigns" in resp.json()["detail"]
    
    await test_db.refresh(campaign)
    assert campaign.status == 1 # Remains ACTIVE


@pytest.mark.asyncio
async def test_insufficient_balance(client: AsyncClient, test_db: AsyncSession, auth_headers):
    user = User(id=205, email="adv205@test.com", role="advertiser", name="test", password_hash="test")
    # Balance = 0
    adv = Advertiser(name="Test Adv", owner_user_id=user.id, balance=Decimal("0.0"))
    test_db.add_all([user, adv])
    await test_db.commit()
    user.advertiser_id = adv.id
    await test_db.commit()

    campaign = Campaign(advertiser_id=adv.id, name="Test Campaign", status=5, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0")) # DRAFT
    test_db.add(campaign)
    await test_db.flush()
    creative = Creative(campaign_id=campaign.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.APPROVED.value)
    targeting = TargetingRule(campaign_id=campaign.id, rule_type="geo", rule_value={"countries": ["US"]})
    domain = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test_token")
    test_db.add_all([creative, targeting, domain])
    await test_db.commit()
    await test_db.refresh(campaign)

    headers = auth_headers(user)
    resp = await client.post(f"/api/v1/campaign/{campaign.id}/activate", headers=headers)
    assert resp.status_code == 400
    assert "Insufficient wallet balance" in resp.json()["detail"]
    
    await test_db.refresh(campaign)
    assert campaign.status == 5 # Remains DRAFT
    
    await test_db.refresh(adv)
    assert adv.balance == Decimal("0.0")


@pytest.mark.asyncio
async def test_double_activation(client: AsyncClient, test_db: AsyncSession, auth_headers):
    user = User(id=206, email="adv206@test.com", role="advertiser", name="test", password_hash="test")
    adv = Advertiser(name="Test Adv", owner_user_id=user.id, balance=Decimal("100.0"))
    test_db.add_all([user, adv])
    await test_db.commit()
    user.advertiser_id = adv.id
    await test_db.commit()

    campaign = Campaign(advertiser_id=adv.id, name="Test Campaign", status=5, budget_daily=Decimal("10.0"), budget_total=Decimal("100.0")) # DRAFT
    test_db.add(campaign)
    await test_db.flush()
    creative = Creative(campaign_id=campaign.id, title="Test", landing_url="https://example.com", status=1, moderation_status=ModerationStatus.APPROVED.value)
    targeting = TargetingRule(campaign_id=campaign.id, rule_type="geo", rule_value={"countries": ["US"]})
    domain = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test_token")
    test_db.add_all([creative, targeting, domain])
    await test_db.commit()
    await test_db.refresh(campaign)

    headers = auth_headers(user)
    
    # First activation
    resp1 = await client.post(f"/api/v1/campaign/{campaign.id}/activate", headers=headers)
    assert resp1.status_code == 200
    
    # Second activation (immediately after)
    resp2 = await client.post(f"/api/v1/campaign/{campaign.id}/activate", headers=headers)
    assert resp2.status_code == 400
    assert "Only DRAFT campaigns" in resp2.json()["detail"]
    
    await test_db.refresh(campaign)
    assert campaign.status == 1 # ACTIVE
