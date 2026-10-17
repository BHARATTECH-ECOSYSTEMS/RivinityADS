import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from liteads.models import Advertiser, Campaign, Creative, AdSlot, Publisher, AdEvent, AdvertiserDomain
from liteads.models.base import ModerationStatus
from decimal import Decimal

@pytest.fixture
async def setup_bug4_data(test_db: AsyncSession):
    pub = Publisher(id=20, name="Pub Bug4", balance=Decimal("0"))
    slot = AdSlot(slot_id="slot_bug4", publisher_id=20, name="Slot Bug4")
    
    # Adv 1: Zero balance
    adv1 = Advertiser(id=21, name="Adv Zero", balance=Decimal("0.0"))
    camp1 = Campaign(id=21, advertiser_id=21, name="Camp Zero", bid_type=1, bid_amount=1.0)
    creative1 = Creative(campaign_id=21, title="Creative Zero", landing_url="https://x.com", status=1, moderation_status=ModerationStatus.APPROVED.value)

    # Adv 2: Negative balance
    adv2 = Advertiser(id=22, name="Adv Neg", balance=Decimal("-10.0"))
    camp2 = Campaign(id=22, advertiser_id=22, name="Camp Neg", bid_type=1, bid_amount=1.0)
    creative2 = Creative(campaign_id=22, title="Creative Neg", landing_url="https://x.com", status=1, moderation_status=ModerationStatus.APPROVED.value)

    # Adv 3: Positive balance
    adv3 = Advertiser(id=23, name="Adv Pos", balance=Decimal("100.0"))
    camp3 = Campaign(id=23, advertiser_id=23, name="Camp Pos", bid_type=1, bid_amount=1.0)
    creative3 = Creative(campaign_id=23, title="Creative Pos", landing_url="https://x.com", status=1, moderation_status=ModerationStatus.APPROVED.value)

    domain = AdvertiserDomain(advertiser_id=23, domain="x.com", status="VERIFIED", verification_token="test")

    test_db.add_all([pub, slot, adv1, camp1, creative1, adv2, camp2, creative2, adv3, camp3, creative3, domain])
    await test_db.commit()
    
    from liteads.common.cache import CacheKeys, redis_client
    if redis_client.connected:
        await redis_client.delete(CacheKeys.active_ads())

    return {"pub": pub.id, "slot": slot.slot_id, "adv1": adv1.id, "adv2": adv2.id, "adv3": adv3.id}

@pytest.mark.asyncio
async def test_balance_filtering(client: AsyncClient, test_db: AsyncSession, setup_bug4_data: dict):
    # Test that /request filters out Adv Zero and Adv Neg but serves Adv Pos
    # First, let's request ads without targeting so all campaigns are candidates
    res = await client.post("/api/v1/ad/request", json={
        "slot_id": setup_bug4_data["slot"],
        "user_id": "test_user_bug4",
        "device": {"os": "web"},
        "num_ads": 10
    })
    
    assert res.status_code == 200
    data = res.json()
    print("DATA RETURNED:", data)
    
    # We should only get ads from campaign 23 (Adv Pos)
    ads = data["ads"]
    assert len(ads) > 0
    for ad in ads:
        assert str(ad["campaign_id"]) == "23"

@pytest.mark.asyncio
async def test_billing_failure_stops_event_recording(client: AsyncClient, test_db: AsyncSession, setup_bug4_data: dict):
    from unittest.mock import patch
    
    # We will simulate an impression for an ad that we craft manually (even if not served due to balance, we can directly hit /track)
    # Let's say we have an event for campaign 21 (Adv Zero)
    ad_id_zero = "ad_21_1"
    req_id = "req_bug4_test"
    
    # Generate valid signature
    from liteads.common.tracking_sig import sign_tracking
    sig = sign_tracking(req_id, ad_id_zero, setup_bug4_data["slot"])
    
    # Try tracking the impression
    res = await client.post("/api/v1/event/track", json={
        "request_id": req_id,
        "ad_id": ad_id_zero,
        "event_type": "impression",
        "slot_id": setup_bug4_data["slot"],
        "extra": {"slot_id": setup_bug4_data["slot"]},
        "sig": sig
    })
    
    assert res.status_code == 200
    assert res.json()["success"] == False
    
    # Verify no AdEvent was recorded
    events_res = await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    assert len(events_res.all()) == 0

@pytest.mark.asyncio
async def test_billing_success_records_event(client: AsyncClient, test_db: AsyncSession, setup_bug4_data: dict):
    # Campaign 23 (Adv Pos) has balance
    ad_id_pos = "ad_23_3" # creative3 ID is likely 3 since they are created 1, 2, 3
    # Wait, creative ID might be different, let's fetch it
    c_res = await test_db.execute(select(Creative.id).where(Creative.campaign_id == 23))
    c_id = c_res.scalar_one()
    ad_id_pos = f"ad_23_{c_id}"
    
    req_id = "req_bug4_success"
    
    from liteads.common.tracking_sig import sign_tracking
    sig = sign_tracking(req_id, ad_id_pos, setup_bug4_data["slot"])
    
    res = await client.post("/api/v1/event/track", json={
        "request_id": req_id,
        "ad_id": ad_id_pos,
        "event_type": "impression",
        "slot_id": setup_bug4_data["slot"],
        "extra": {"slot_id": setup_bug4_data["slot"]},
        "sig": sig
    })
    
    assert res.status_code == 200
    assert res.json()["success"] == True
    
    # Verify AdEvent was recorded
    events_res = await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    assert len(events_res.all()) == 1
