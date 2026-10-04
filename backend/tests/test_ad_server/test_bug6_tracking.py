import time
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models import User, Advertiser, Campaign, Creative, AdEvent, Transaction
from liteads.common.tracking_sig import sign_tracking
from liteads.common.security import hash_password

@pytest.fixture
async def active_campaign(test_db: AsyncSession):
    adv = Advertiser(name="BUG6 Adv", balance=100.0)
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="BUG6 Camp",
        budget_daily=10.0,
        budget_total=100.0,
        bid_amount=1.0,
        bid_type=1,
        status=1,
    )
    test_db.add(camp)
    await test_db.flush()

    creative = Creative(
        campaign_id=camp.id,
        title="BUG6 Ad",
        description="Test",
        image_url="http://img.com",
        landing_url="http://land.com",
        creative_type=1,
        status=1
    )
    test_db.add(creative)
    await test_db.commit()

    return {"adv": adv, "camp": camp, "creative": creative}

@pytest.mark.asyncio
async def test_bug6_non_numeric_timestamp(client: AsyncClient, active_campaign):
    # Test 1: ts=abc, ts=hello, ts=not-a-number
    creative = active_campaign["creative"]
    req_id = "req_123"
    slot_id = "slot_1"
    
    # Construct invalid signatures manually
    for invalid_ts in ["abc", "hello", "not-a-number"]:
        invalid_sig = f"{invalid_ts}.fakehash"
        
        # Test impression
        res = await client.post("/api/v1/event/track", json={
            "request_id": req_id,
            "ad_id": str(creative.id),
            "event_type": "impression",
            "slot_id": slot_id,
            "sig": invalid_sig
        })
        
        # Must fail safely with 400
        assert res.status_code == 400
        assert res.json()["detail"] == "Bad signature"

@pytest.mark.asyncio
async def test_bug6_empty_timestamp(client: AsyncClient, active_campaign):
    # Test 2: empty timestamp (ts=)
    creative = active_campaign["creative"]
    invalid_sig = ".fakehash"
    
    res = await client.post("/api/v1/event/track", json={
        "request_id": "req_123",
        "ad_id": str(creative.id),
        "event_type": "impression",
        "slot_id": "slot_1",
        "sig": invalid_sig
    })
    
    assert res.status_code == 400
    assert res.json()["detail"] == "Bad signature"

@pytest.mark.asyncio
async def test_bug6_missing_timestamp_delimiter(client: AsyncClient, active_campaign):
    # Test 3: missing timestamp (no dot, so split fails)
    creative = active_campaign["creative"]
    invalid_sig = "fakehashwithoutdot"
    
    res = await client.post("/api/v1/event/track", json={
        "request_id": "req_123",
        "ad_id": str(creative.id),
        "event_type": "impression",
        "slot_id": "slot_1",
        "sig": invalid_sig
    })
    
    assert res.status_code == 400
    assert res.json()["detail"] == "Bad signature"

@pytest.mark.asyncio
async def test_bug6_valid_timestamp(client: AsyncClient, active_campaign):
    # Test 4: valid timestamp works
    creative = active_campaign["creative"]
    req_id = "req_123"
    slot_id = "slot_1"
    
    valid_sig = sign_tracking(req_id, str(creative.id), slot_id)
    
    res = await client.post("/api/v1/event/track", json={
        "request_id": req_id,
        "ad_id": str(creative.id),
        "event_type": "impression",
        "slot_id": slot_id,
        "sig": valid_sig
    })
    
    # Should record properly
    assert res.status_code == 200

@pytest.mark.asyncio
async def test_bug6_malformed_timestamp_must_not_bill(client: AsyncClient, test_db: AsyncSession, active_campaign):
    # Test 5: verify no billing/events occurred from bad signatures
    creative = active_campaign["creative"]
    adv = active_campaign["adv"]
    
    # Refresh adv balance from DB
    await test_db.refresh(adv)
    initial_balance = adv.balance
    
    from sqlalchemy import select, func
    
    # Initial event count
    ev_count = await test_db.scalar(select(func.count()).select_from(AdEvent).where(AdEvent.creative_id == creative.id))
    
    # Send bad sig
    res = await client.post("/api/v1/event/track", json={
        "request_id": "req_999",
        "ad_id": str(creative.id),
        "event_type": "impression",
        "slot_id": "slot_x",
        "sig": "notanumber.hash"
    })
    assert res.status_code == 400
    
    # Verify no new events
    new_ev_count = await test_db.scalar(select(func.count()).select_from(AdEvent).where(AdEvent.creative_id == creative.id))
    assert new_ev_count == ev_count
    
    # Verify balance hasn't changed
    await test_db.refresh(adv)
    assert adv.balance == initial_balance

    # Verify ledger hasn't changed
    ledger_count = await test_db.scalar(select(func.count()).select_from(Transaction).where(Transaction.advertiser_id == adv.id))
    assert ledger_count == 0  # No transactions occurred
