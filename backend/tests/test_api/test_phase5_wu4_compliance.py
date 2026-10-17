"""Tests for Phase 5 Work Unit 4 (WU4):
Ad Compliance, Sponsored Disclosure, Chat-Surface Restrictions & Privacy-Safe Frequency Capping.
"""

import hashlib
import hmac
import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from liteads.ad_server.main import app
from liteads.ad_server.services.ad_service import AdService
from liteads.common.cache import CacheKeys, redis_client
from liteads.common.security import hash_password
from liteads.management.api_key_service import ApiKeyService
from liteads.models.ad import Advertiser, Campaign, Creative
from liteads.models.auth import User
from liteads.models.base import CreativeType, ModerationStatus, Status
from liteads.models.domain import AdvertiserDomain
from liteads.models.publisher import AdSlot, Publisher
from liteads.rec_engine.filter.frequency import FrequencyFilter
from liteads.schemas.internal import AdCandidate, UserContext
from liteads.schemas.management import ApiKeyCreate
from liteads.schemas.request import AdRequest
from liteads.schemas.response import AdResponse


def make_hmac_headers(
    raw_key: str,
    body: bytes,
    timestamp: int | None = None,
    signature: str | None = None,
    method: str = "POST",
    path: str = "/api/v1/ad/request",
    version: str = "2",
) -> dict[str, str]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    if signature is None:
        body_sha = hashlib.sha256(body).hexdigest()
        if version == "1":
            msg = f"{raw_key}|{ts}|{body_sha}".encode("utf-8")
        else:
            msg = f"{method.upper()}|{path}|{ts}|{body_sha}".encode("utf-8")
        sig = hmac.new(raw_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    else:
        sig = signature

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": raw_key,
        "X-Timestamp": ts,
        "X-Signature": sig,
        "X-Signature-Version": version,
    }
    return headers


@pytest.fixture(autouse=True)
async def setup_redis_fixture():
    await redis_client.connect()
    if redis_client.connected:
        await redis_client.client.flushdb()
    yield
    if redis_client.connected:
        await redis_client.client.flushdb()
        await redis_client.close()


@pytest.fixture
async def setup_wu4_data(test_db):
    """Set up publishers, slots, advertiser, campaigns, creatives, and API keys for WU4 testing."""
    # Publishers
    pub1 = Publisher(id=1, name="Publisher One", status=1, is_sandbox=False)
    pub2 = Publisher(id=2, name="Publisher Two", status=1, is_sandbox=False)
    test_db.add_all([pub1, pub2])
    await test_db.flush()

    # Publisher user
    user_pub1 = User(
        id=10,
        email="pub1_wu4@example.com",
        name="Publisher 1 User",
        password_hash=hash_password("password123"),
        role="publisher",
        publisher_id=1,
        is_active=True,
    )
    test_db.add(user_pub1)

    # Slots:
    # 1. Chat surface slot (format=1 banner, is_chat_surface=True)
    slot_chat = AdSlot(
        id=1,
        publisher_id=pub1.id,
        slot_id="slot_chat_wu4",
        name="Chat Slot",
        format=1,
        status=1,
        is_chat_surface=True,
        width=300,
        height=250,
    )
    # 2. Standard non-chat slot (format=4 interstitial, is_chat_surface=False)
    slot_interstitial = AdSlot(
        id=2,
        publisher_id=pub1.id,
        slot_id="slot_interstitial_wu4",
        name="Interstitial Slot",
        format=4,
        status=1,
        is_chat_surface=False,
        width=320,
        height=480,
    )
    # 3. Standard non-chat banner slot
    slot_banner = AdSlot(
        id=3,
        publisher_id=pub1.id,
        slot_id="slot_banner_wu4",
        name="Banner Slot",
        format=1,
        status=1,
        is_chat_surface=False,
        width=300,
        height=250,
    )
    # 4. Empty slot with no matching creatives
    slot_empty = AdSlot(
        id=4,
        publisher_id=pub1.id,
        slot_id="slot_empty_wu4",
        name="Empty Slot",
        format=1,
        status=1,
        is_chat_surface=False,
        width=10,
        height=10,
    )
    test_db.add_all([slot_chat, slot_interstitial, slot_banner, slot_empty])

    # Advertiser
    adv = Advertiser(id=1, name="Advertiser WU4", balance=Decimal("1000.00"), daily_budget=Decimal("500.00"), status=1)
    test_db.add(adv)
    await test_db.flush()

    domain = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test_tok")
    test_db.add(domain)

    # Campaigns:
    # Camp 1: Banner campaign
    camp_banner = Campaign(
        id=1,
        advertiser_id=adv.id,
        name="Banner Campaign",
        status=Status.ACTIVE,
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        bid_amount=Decimal("1.50"),
        bid_type=1,
        freq_cap_daily=2,
        freq_cap_hourly=10,
    )
    # Camp 2: Interstitial campaign
    camp_interstitial = Campaign(
        id=2,
        advertiser_id=adv.id,
        name="Interstitial Campaign",
        status=Status.ACTIVE,
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        bid_amount=Decimal("2.00"),
        bid_type=1,
        freq_cap_daily=5,
        freq_cap_hourly=2,
    )
    test_db.add_all([camp_banner, camp_interstitial])
    await test_db.flush()

    # Creatives:
    # 1. Banner creative (approved)
    creat_banner = Creative(
        id=1,
        campaign_id=camp_banner.id,
        title="Banner Ad",
        description="Banner Description",
        landing_url="https://example.com/banner",
        creative_type=1,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    # 2. Interstitial creative (approved)
    creat_interstitial = Creative(
        id=2,
        campaign_id=camp_interstitial.id,
        title="Interstitial Ad",
        description="Interstitial Description",
        landing_url="https://example.com/interstitial",
        creative_type=4,  # INTERSTITIAL
        width=320,
        height=480,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    test_db.add_all([creat_banner, creat_interstitial])

    # API key for publisher 1
    db_key, raw_key = await ApiKeyService.create_api_key(
        test_db,
        publisher_id=pub1.id,
        schema=ApiKeyCreate(name="WU4 Key"),
    )
    await test_db.commit()

    return {
        "pub1": pub1,
        "slot_chat": slot_chat,
        "slot_interstitial": slot_interstitial,
        "slot_banner": slot_banner,
        "adv": adv,
        "camp_banner": camp_banner,
        "camp_interstitial": camp_interstitial,
        "creat_banner": creat_banner,
        "creat_interstitial": creat_interstitial,
        "raw_key": raw_key,
    }


# ==============================================================================
# STAGE C: SPONSORED DISCLOSURE TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_sponsored_disclosure_in_ad_response(client: AsyncClient, setup_wu4_data: dict):
    """Successful ad responses must contain sponsored: true and disclosure_label: 'Sponsored'."""
    raw_key = setup_wu4_data["raw_key"]
    body = b'{"slot_id": "slot_banner_wu4", "num_ads": 1}'
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["ads"]) >= 1
    ad = data["ads"][0]
    assert ad["sponsored"] is True
    assert ad["disclosure_label"] == "Sponsored"


@pytest.mark.asyncio
async def test_client_cannot_override_sponsored_disclosure(client: AsyncClient, setup_wu4_data: dict):
    """Client request cannot suppress or falsify server-controlled sponsored disclosure."""
    raw_key = setup_wu4_data["raw_key"]
    # Even if client tries to pass sponsored: false in request body
    body = b'{"slot_id": "slot_banner_wu4", "num_ads": 1, "sponsored": false, "disclosure_label": "Organic"}'
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["ads"]) >= 1
    ad = data["ads"][0]
    assert ad["sponsored"] is True
    assert ad["disclosure_label"] == "Sponsored"


@pytest.mark.asyncio
async def test_ad_response_serialization_mandatory_defaults():
    """AdResponse schema defaults must ensure sponsored=True and disclosure_label='Sponsored'."""
    from liteads.schemas.response import CreativeResponse, TrackingUrls
    ad_resp = AdResponse(
        ad_id="ad_test_123",
        campaign_id=1,
        creative_id=1,
        creative=CreativeResponse(
            title="Test",
            description="Desc",
            image_url="https://example.com/banner.png",
            landing_url="https://example.com/click",
            creative_type="banner",
        ),
        tracking=TrackingUrls(
            impression_url="https://example.com/imp",
            click_url="https://example.com/click",
        ),
    )
    assert ad_resp.sponsored is True
    assert ad_resp.disclosure_label == "Sponsored"
    dumped = ad_resp.model_dump()
    assert dumped["sponsored"] is True
    assert dumped["disclosure_label"] == "Sponsored"


@pytest.mark.asyncio
async def test_empty_response_does_not_label_ad(client: AsyncClient, setup_wu4_data: dict):
    """Empty responses must return ads: [] without false sponsored labels."""
    raw_key = setup_wu4_data["raw_key"]
    # Request slot with no matching size creatives -> empty ads
    body = b'{"slot_id": "slot_empty_wu4", "num_ads": 1}'
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["ads"] == []


# ==============================================================================
# STAGE D: CHAT-SURFACE AD-FORMAT RESTRICTIONS TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_chat_surface_rejects_interstitial_ads(client: AsyncClient, setup_wu4_data: dict):
    """Chat surfaces (is_chat_surface=True) must never serve interstitial ads (format 4)."""
    raw_key = setup_wu4_data["raw_key"]
    # Explicitly requesting format 4 (interstitial) on chat surface slot
    body = b'{"slot_id": "slot_chat_wu4", "num_ads": 1, "format": 4}'
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    # Interstitial is strictly forbidden on chat surface
    assert data["ads"] == []


@pytest.mark.asyncio
async def test_chat_surface_allows_banner_ads(client: AsyncClient, setup_wu4_data: dict):
    """Chat surfaces (is_chat_surface=True) allow permitted banner ads (format 1)."""
    raw_key = setup_wu4_data["raw_key"]
    body = b'{"slot_id": "slot_chat_wu4", "num_ads": 1, "format": 1}'
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["ads"]) >= 1
    assert data["ads"][0]["creative"]["title"] == "Banner Ad"
    assert data["ads"][0]["sponsored"] is True


@pytest.mark.asyncio
async def test_non_chat_surface_allows_interstitial_ads(client: AsyncClient, setup_wu4_data: dict):
    """Non-chat surfaces (is_chat_surface=False) permit interstitial ads when valid."""
    raw_key = setup_wu4_data["raw_key"]
    body = b'{"slot_id": "slot_interstitial_wu4", "num_ads": 1, "format": 4}'
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["ads"]) >= 1
    assert data["ads"][0]["creative"]["title"] == "Interstitial Ad"
    assert data["ads"][0]["creative"]["creative_type"] == "interstitial"


@pytest.mark.asyncio
async def test_chat_surface_anonymous_access_denied(client: AsyncClient, setup_wu4_data: dict):
    """Anonymous access to chat surface slots must be denied (401)."""
    # Calling /api/v1/ad/request without API key headers for a chat slot
    body = {"slot_id": "slot_chat_wu4", "num_ads": 1}
    res = await client.post("/api/v1/ad/request", json=body)
    assert res.status_code == 401
    assert "chat surface" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_interstitial_rejected_by_chat_surface_not_billed(client: AsyncClient, setup_wu4_data: dict, test_db):
    """When an interstitial is rejected by chat surface, balance is not deducted and no impressions are tracked."""
    adv_before = await test_db.get(Advertiser, 1)
    balance_before = adv_before.balance

    raw_key = setup_wu4_data["raw_key"]
    body = b'{"slot_id": "slot_chat_wu4", "num_ads": 1, "format": 4}'
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["ads"] == []

    await test_db.refresh(adv_before)
    assert adv_before.balance == balance_before


# ==============================================================================
# STAGE E: PRIVACY-SAFE FREQUENCY CAPPING TESTS
# ==============================================================================

VALID_HASH_UPPER = "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855"
VALID_HASH_LOWER = VALID_HASH_UPPER.lower()


@pytest.mark.asyncio
async def test_valid_64_hex_user_hash_normalized_and_accepted(client: AsyncClient, setup_wu4_data: dict):
    """Uppercase 64-hex SHA-256 hash is accepted and normalized to lowercase."""
    raw_key = setup_wu4_data["raw_key"]
    body = f'{{"slot_id": "slot_banner_wu4", "num_ads": 1, "user": {{"id": "{VALID_HASH_UPPER}"}}}}'.encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["ads"]) >= 1

    # Check safe key helper normalization
    safe_key = CacheKeys._safe_user_key(VALID_HASH_UPPER)
    assert safe_key == VALID_HASH_LOWER


@pytest.mark.asyncio
async def test_malformed_user_hash_rejected_422(client: AsyncClient, setup_wu4_data: dict):
    """Malformed user hashes must be rejected with 422 Unprocessable Entity."""
    raw_key = setup_wu4_data["raw_key"]
    malformed_hashes = [
        "not_a_hash",
        "12345",
        "zzzz" * 16,  # 64 chars but not hex
        "a" * 63,      # 63 chars (too short)
        "a" * 65,      # 65 chars (too long)
        "user@example.com",  # raw PII
    ]

    for bad_hash in malformed_hashes:
        body = f'{{"slot_id": "slot_banner_wu4", "num_ads": 1, "user": {{"id": "{bad_hash}"}}}}'.encode("utf-8")
        headers = make_hmac_headers(raw_key, body)
        res = await client.post("/api/v1/ad/request", content=body, headers=headers)
        assert res.status_code == 422, f"Expected 422 for bad hash '{bad_hash}', got {res.status_code}"


@pytest.mark.asyncio
async def test_frequency_cap_enforcement_blocks_at_threshold(client: AsyncClient, setup_wu4_data: dict):
    """When daily frequency cap (2) is reached, ad is blocked from serving to that user."""
    raw_key = setup_wu4_data["raw_key"]
    test_hash = "1" * 64
    body = f'{{"slot_id": "slot_banner_wu4", "num_ads": 1, "user": {{"id": "{test_hash}"}}}}'.encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    # First request: served
    res1 = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res1.status_code == 200
    assert len(res1.json()["ads"]) == 1

    # Simulate impression increment in frequency counter
    ff = FrequencyFilter()
    await ff.increment(user_id=test_hash, campaign_id=1, slot_id="slot_banner_wu4")

    # Second request: served
    res2 = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res2.status_code == 200
    assert len(res2.json()["ads"]) == 1

    # Second impression increment -> reach cap of 2
    await ff.increment(user_id=test_hash, campaign_id=1, slot_id="slot_banner_wu4")

    # Third request: candidate filtered out by frequency cap
    res3 = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res3.status_code == 200
    assert res3.json()["ads"] == []  # Blocked by frequency cap


@pytest.mark.asyncio
async def test_bounded_ttl_applied_to_frequency_keys(setup_wu4_data: dict):
    """Atomic increment must apply bounded TTL (<=86400s daily, <=3600s hourly)."""
    from liteads.common.utils import current_date, current_hour
    ff = FrequencyFilter()
    test_hash = "2" * 64
    await ff.increment(user_id=test_hash, campaign_id=1, slot_id="slot_1")

    daily_key = CacheKeys.freq_daily(test_hash, 1, current_date(), slot_id="slot_1")
    hourly_key = CacheKeys.freq_hourly(test_hash, 1, current_hour(), slot_id="slot_1")

    daily_ttl = await redis_client.ttl(daily_key)
    hourly_ttl = await redis_client.ttl(hourly_key)

    assert 0 < daily_ttl <= 86400, f"Daily TTL expected <=86400, got {daily_ttl}"
    assert 0 < hourly_ttl <= 3600, f"Hourly TTL expected <=3600, got {hourly_ttl}"


@pytest.mark.asyncio
async def test_tenant_and_slot_isolation_in_frequency_caps(setup_wu4_data: dict):
    """Frequency counts for slot 1 must not bleed into slot 2."""
    from liteads.common.utils import current_date
    ff = FrequencyFilter()
    test_hash = "3" * 64

    # Increment for slot 1
    await ff.increment(user_id=test_hash, campaign_id=1, slot_id="slot_1")

    daily_key_slot1 = CacheKeys.freq_daily(test_hash, 1, current_date(), slot_id="slot_1")
    daily_key_slot2 = CacheKeys.freq_daily(test_hash, 1, current_date(), slot_id="slot_2")

    val1 = await redis_client.get(daily_key_slot1)
    val2 = await redis_client.get(daily_key_slot2)

    assert val1 is not None and int(val1) == 1
    assert val2 is None


@pytest.mark.asyncio
async def test_redis_failure_fail_closed_503(setup_wu4_data: dict):
    """When Redis fails during frequency capping check, fail-closed pattern raises 503."""
    ff = FrequencyFilter()
    user_context = UserContext(user_id="4" * 64)
    candidates = [
        AdCandidate(
            campaign_id=1,
            creative_id=1,
            advertiser_id=1,
            bid=1.0,
            bid_type=1,
            metadata={"freq_cap_daily": 5, "freq_cap_hourly": 2},
        )
    ]

    with patch.object(redis_client, "connect", AsyncMock(side_effect=Exception("Redis down"))):
        with patch.object(redis_client, "_connected", False):
            with pytest.raises(Exception) as exc_info:
                await ff.filter(candidates=candidates, user_context=user_context, slot_id="slot_1")
            assert "503" in str(exc_info.value) or "unavailable" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_request_without_user_hash_proceeds_safely(client: AsyncClient, setup_wu4_data: dict):
    """Requests without a user_id proceed safely without crashing or touching Redis."""
    raw_key = setup_wu4_data["raw_key"]
    body = b'{"slot_id": "slot_banner_wu4", "num_ads": 1}'
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    assert len(res.json()["ads"]) >= 1


@pytest.mark.asyncio
async def test_safe_user_key_never_stores_raw_pii():
    """CacheKeys._safe_user_key hashes any raw string to a 64-character lowercase SHA-256."""
    raw_email = "victim@example.com"
    safe = CacheKeys._safe_user_key(raw_email)
    assert len(safe) == 64
    assert raw_email not in safe
    assert safe == hashlib.sha256(raw_email.encode("utf-8")).hexdigest()
