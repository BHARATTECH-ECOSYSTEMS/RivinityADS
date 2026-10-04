import asyncio
import time
from typing import Any
import pytest
from httpx import AsyncClient

from liteads.common.cache import redis_client
from liteads.common.tracking_sig import sign_tracking
from liteads.models import Advertiser, Campaign, Publisher, PlatformSettings
from decimal import Decimal

@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    store = {}
    ttls = {}
    
    async def mock_incr(key: str, amount: int = 1):
        store[key] = store.get(key, 0) + amount
        return store[key]
        
    async def mock_expire(key: str, ttl: int):
        if key in store:
            ttls[key] = ttl
            return True
        return False
        
    async def mock_ttl(key: str):
        if key not in store:
            return -2
        return ttls.get(key, -1)
        
    monkeypatch.setattr(redis_client, "incr", mock_incr)
    monkeypatch.setattr(redis_client, "expire", mock_expire)
    monkeypatch.setattr(redis_client, "ttl", mock_ttl)
    yield


@pytest.fixture
async def billing_test_data(test_db):
    adv = Advertiser(id=99, name="Adv 99", balance=Decimal("100.0000"))
    # bid_type=1 means CPM (impression)
    camp = Campaign(id=99, advertiser_id=99, name="Camp 99", bid_type=1, bid_amount=Decimal("1.5000"), spent_today=Decimal("0"), spent_total=Decimal("0"))
    pub = Publisher(id=99, name="Pub 99", balance=Decimal("10.0000"))
    settings = PlatformSettings(id=2, publisher_rev_share=Decimal("0.700"))
    
    test_db.add_all([adv, camp, pub, settings])
    await test_db.commit()


@pytest.mark.asyncio
async def test_replay_impression(client: AsyncClient, sample_event_request: dict[str, Any]):
    req_id = "test_replay_imp_1"
    ad_id = "ad_100_200"
    sig = sign_tracking(req_id, ad_id, "")
    sample_event_request.update({"request_id": req_id, "ad_id": ad_id, "event_type": "impression", "sig": sig})
    
    # 1. First impression -> accepted
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 200
    
    # 2. Second impression -> 429
    res2 = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res2.status_code == 429
    assert res2.json()["detail"] == "Duplicate event rejected"
    
    # 3. Third impression -> 429
    res3 = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res3.status_code == 429


@pytest.mark.asyncio
async def test_replay_click(client: AsyncClient, sample_event_request: dict[str, Any]):
    req_id = "test_replay_clk_1"
    ad_id = "ad_100_200"
    sig = sign_tracking(req_id, ad_id, "")
    sample_event_request.update({"request_id": req_id, "ad_id": ad_id, "event_type": "click", "sig": sig})
    
    # Click 1 -> 200
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 200
    
    # Click 2 -> 200
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 200
    
    # Click 3 -> 200
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 200
    
    # Click 4 -> 429
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 429


@pytest.mark.asyncio
async def test_replay_conversion(client: AsyncClient, sample_event_request: dict[str, Any]):
    req_id = "test_replay_conv_1"
    ad_id = "ad_100_200"
    sig = sign_tracking(req_id, ad_id, "")
    sample_event_request.update({"request_id": req_id, "ad_id": ad_id, "event_type": "conversion", "sig": sig})
    
    # 1. First conversion -> 200
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 200
    
    # 2. Second conversion -> 429
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 429


@pytest.mark.asyncio
async def test_signature_ttl(client: AsyncClient, sample_event_request: dict[str, Any]):
    import hashlib
    import hmac
    from liteads.common.config import get_settings
    
    req_id = "test_ttl_1"
    ad_id = "ad_100_200"
    
    def manual_sign(ts: int):
        msg = f"{req_id}|{ad_id}||{ts}".encode()
        sig = hmac.new(get_settings().tracking.secret.encode(), msg, hashlib.sha256).hexdigest()[:32]
        return f"{ts}.{sig}"
    
    # Expired signature (older than 600s)
    expired_timestamp = int(time.time()) - 601
    expired_sig = manual_sign(expired_timestamp)
    
    sample_event_request.update({"request_id": req_id, "ad_id": ad_id, "event_type": "impression", "sig": expired_sig})
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 400
    assert "expired" in res.json()["detail"].lower()
    
    # Valid signature
    valid_timestamp = int(time.time()) - 300
    valid_sig = manual_sign(valid_timestamp)
    sample_event_request["sig"] = valid_sig
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_concurrency_impressions(client: AsyncClient, sample_event_request: dict[str, Any]):
    req_id = "test_concurrent_imp"
    ad_id = "ad_100_200"
    sig = sign_tracking(req_id, ad_id, "")
    sample_event_request.update({"request_id": req_id, "ad_id": ad_id, "event_type": "impression", "sig": sig})
    
    # Fire 5 concurrent identical requests
    tasks = [client.post("/api/v1/event/track", json=sample_event_request) for _ in range(5)]
    responses = await asyncio.gather(*tasks)
    
    success_count = sum(1 for r in responses if r.status_code == 200)
    fail_count = sum(1 for r in responses if r.status_code == 429)
    
    # Exactly one should succeed, 4 should fail
    assert success_count == 1
    assert fail_count == 4


@pytest.mark.asyncio
async def test_redis_behavior(client: AsyncClient, sample_event_request: dict[str, Any]):
    req_id = "test_redis_behavior"
    ad_id = "ad_100_200"
    sig = sign_tracking(req_id, ad_id, "")
    sample_event_request.update({"request_id": req_id, "ad_id": ad_id, "event_type": "impression", "sig": sig})
    
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 200
    
    key = f"trk:nonce:impression:{req_id}:{ad_id}"
    
    # Verify key created and has TTL
    ttl = await redis_client.ttl(key)
    assert 0 < ttl <= 900
    
    # Do another request to ensure TTL doesn't get overwritten to 900
    # Let's mock a scenario where time passes (not strictly possible here, but we can verify it doesn't reset)
    await redis_client.expire(key, 500)
    
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 429
    
    # TTL should still be around 500
    new_ttl = await redis_client.ttl(key)
    assert new_ttl <= 500


@pytest.mark.asyncio
async def test_billing_safety(client: AsyncClient, sample_event_request: dict[str, Any], test_db, billing_test_data):
    req_id = "test_billing_safety"
    ad_id = "ad_99_1"  # matches campaign 99 created in fixture
    sig = sign_tracking(req_id, ad_id, "")
    
    sample_event_request.update({
        "request_id": req_id, 
        "ad_id": ad_id, 
        "event_type": "impression", 
        "sig": sig,
        "extra": {}  # no slot_id to simplify, or maybe we don't need full billing end-to-end to verify that it doesn't run twice
    })
    
    # Actually, the billing uses campaign.spent_today and advertiser balance. Let's provide a slot to trigger billing.
    from liteads.models import AdSlot
    slot = AdSlot(slot_id="slot_99", publisher_id=99, name="Slot 99")
    test_db.add(slot)
    await test_db.commit()
    
    # Need to generate signature with slot
    sig = sign_tracking(req_id, ad_id, "slot_99")
    sample_event_request["sig"] = sig
    sample_event_request["slot_id"] = "slot_99"
    sample_event_request["extra"] = {"slot_id": "slot_99"}
    
    # 1. First impression
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 200
    
    await test_db.refresh(await test_db.get(Advertiser, 99))
    adv = await test_db.get(Advertiser, 99)
    # Balance should decrease by 1.5000 / 1000 = 0.0015
    assert adv.balance == Decimal("99.9985")
    
    # 2. Second impression (replay)
    res = await client.post("/api/v1/event/track", json=sample_event_request)
    assert res.status_code == 429
    
    await test_db.refresh(adv)
    # Balance should be unchanged
    assert adv.balance == Decimal("99.9985")
