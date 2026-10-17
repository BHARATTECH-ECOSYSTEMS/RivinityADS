"""Focused tests for Phase 5 Work Unit 3:
Rate Limiting, Daily Quotas, Usage Accounting & Frontend Management.
"""

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from liteads.ad_server.main import app
from liteads.ad_server.services.rate_limit_service import RateLimitService
from liteads.common.cache import redis_client
from liteads.common.security import create_access_token, hash_password
from liteads.management.api_key_service import ApiKeyService
from liteads.models.ad import Advertiser, Campaign, Creative
from liteads.models.auth import User
from liteads.models.domain import AdvertiserDomain
from liteads.models.publisher import AdSlot, Publisher, PublisherApiKey, ApiKeyDailyStat
from liteads.schemas.management import ApiKeyCreate


def make_hmac_headers(
    raw_key: str,
    body: bytes,
    timestamp: int | None = None,
    signature: str | None = None,
    method: str = "POST",
    path: str = "/api/v1/ad/request",
    version: str | None = None,
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
    }
    if version:
        headers["X-Signature-Version"] = version
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
async def setup_wu3_data(test_db):
    """Set up publishers, slots, advertiser, campaign, and keys for WU3 testing."""
    pub1 = Publisher(id=1, name="Publisher One", status=1, is_sandbox=False)
    pub2 = Publisher(id=2, name="Publisher Two", status=1, is_sandbox=False)
    test_db.add_all([pub1, pub2])
    await test_db.flush()

    # Create publisher users for management endpoint authorization tests
    user_pub1 = User(
        id=10,
        email="pub1@example.com",
        name="Publisher 1 User",
        password_hash=hash_password("password123"),
        role="publisher",
        publisher_id=1,
        is_active=True,
    )
    user_pub2 = User(
        id=20,
        email="pub2@example.com",
        name="Publisher 2 User",
        password_hash=hash_password("password123"),
        role="publisher",
        publisher_id=2,
        is_active=True,
    )
    user_admin = User(
        id=99,
        email="admin@example.com",
        name="Admin User",
        password_hash=hash_password("password123"),
        role="admin",
        is_active=True,
    )
    test_db.add_all([user_pub1, user_pub2, user_admin])

    slot1 = AdSlot(id=1, publisher_id=pub1.id, slot_id="slot_pub1", name="Slot 1", format=2, status=1)
    slot_pub = AdSlot(id=2, publisher_id=pub1.id, slot_id="slot_public", name="Public Slot", format=1, status=1, is_chat_surface=False)
    slot_chat = AdSlot(id=3, publisher_id=pub1.id, slot_id="slot_chat", name="Chat Slot", format=2, status=1, is_chat_surface=True)
    test_db.add_all([slot1, slot_pub, slot_chat])

    adv = Advertiser(id=1, name="Advertiser One", balance=Decimal("1000.00"), daily_budget=Decimal("500.00"), status=1)
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        id=1,
        advertiser_id=adv.id,
        name="Campaign One",
        status=1,
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        bid_amount=Decimal("1.00"),
        bid_type=1,
    )
    test_db.add(camp)
    await test_db.flush()

    domain = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test")
    creat = Creative(
        id=1,
        campaign_id=camp.id,
        title="Test Creative",
        description="Great ad description",
        landing_url="https://example.com",
        creative_type=2,
        width=300,
        height=250,
        status=1,
        moderation_status=2,
    )
    test_db.add_all([creat, domain])
    await test_db.commit()

    # Key A for Pub 1: rate limit = 3 req/min, daily quota = 5 req/day
    db_key_a, raw_key_a = await ApiKeyService.create_api_key(
        test_db,
        publisher_id=pub1.id,
        schema=ApiKeyCreate(name="Key A Limited", is_sandbox=False, rate_limit_per_minute=3, daily_quota=5),
    )

    # Key B for Pub 1: rate limit = 10 req/min, daily quota = 20 req/day
    db_key_b, raw_key_b = await ApiKeyService.create_api_key(
        test_db,
        publisher_id=pub1.id,
        schema=ApiKeyCreate(name="Key B Independent", is_sandbox=False, rate_limit_per_minute=10, daily_quota=20),
    )

    # Sandbox Key for Pub 1
    db_key_sandbox, raw_key_sandbox = await ApiKeyService.create_api_key(
        test_db,
        publisher_id=pub1.id,
        schema=ApiKeyCreate(name="Key Sandbox", is_sandbox=True, rate_limit_per_minute=5, daily_quota=10),
    )

    # Revoked Key for Pub 1
    db_key_rev, raw_key_rev = await ApiKeyService.create_api_key(
        test_db,
        publisher_id=pub1.id,
        schema=ApiKeyCreate(name="Key Revoked", is_sandbox=False, rate_limit_per_minute=5, daily_quota=10),
    )
    await ApiKeyService.revoke_api_key(test_db, publisher_id=pub1.id, key_id=db_key_rev.id)
    await test_db.commit()

    # JWT tokens
    token_pub1 = create_access_token(user_pub1)
    token_pub2 = create_access_token(user_pub2)
    token_admin = create_access_token(user_admin)

    return {
        "pub1": pub1,
        "pub2": pub2,
        "db_key_a": db_key_a,
        "raw_key_a": raw_key_a,
        "db_key_b": db_key_b,
        "raw_key_b": raw_key_b,
        "db_key_sandbox": db_key_sandbox,
        "raw_key_sandbox": raw_key_sandbox,
        "db_key_rev": db_key_rev,
        "raw_key_rev": raw_key_rev,
        "token_pub1": token_pub1,
        "token_pub2": token_pub2,
        "token_admin": token_admin,
    }


# ── Test 1 & 2: Rate limit below limit succeeds and at limit receives HTTP 429 ──

@pytest.mark.asyncio
async def test_1_and_2_rate_limit_per_minute_enforcement(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_a = setup_wu3_data["raw_key_a"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Key A limit is 3 req/min. Requests 1, 2, 3 should succeed
    for i in range(3):
        headers = make_hmac_headers(raw_key_a, body)
        res = await client.post("/api/v1/ad/request", content=body, headers=headers)
        assert res.status_code == 200, f"Request {i+1} failed: {res.text}"
        assert "X-RateLimit-Limit" in res.headers
        assert res.headers["X-RateLimit-Limit"] == "3"
        assert res.headers["X-RateLimit-Remaining"] == str(3 - i - 1)
        assert "X-RateLimit-Reset" in res.headers

    # 4th request must be rejected with HTTP 429 Too Many Requests
    headers_4 = make_hmac_headers(raw_key_a, body)
    res_4 = await client.post("/api/v1/ad/request", content=body, headers=headers_4)
    assert res_4.status_code == 429
    assert "Rate limit exceeded" in res_4.text
    assert "Retry-After" in res_4.headers
    assert res_4.headers["X-RateLimit-Remaining"] == "0"


# ── Test 3: Different key has an independent rate limit bucket ──────────────────

@pytest.mark.asyncio
async def test_3_independent_key_buckets(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_a = setup_wu3_data["raw_key_a"]
    raw_key_b = setup_wu3_data["raw_key_b"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Exhaust Key A (3 req/min)
    for _ in range(3):
        h = make_hmac_headers(raw_key_a, body)
        await client.post("/api/v1/ad/request", content=body, headers=h)

    # Key A is rate limited
    res_a = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_a, body))
    assert res_a.status_code == 429

    # Key B has its own independent bucket and must succeed
    res_b = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_b, body))
    assert res_b.status_code == 200
    assert res_b.headers["X-RateLimit-Limit"] == "10"
    assert res_b.headers["X-RateLimit-Remaining"] == "9"


# ── Test 4: Concurrent requests cannot bypass rate limit ───────────────────────

@pytest.mark.asyncio
async def test_4_concurrent_requests_rate_limit(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_a = setup_wu3_data["raw_key_a"]  # limit = 3 req/min
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Send 8 concurrent requests simultaneously
    tasks = [
        client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_a, body))
        for _ in range(8)
    ]
    responses = await asyncio.gather(*tasks)

    status_codes = [r.status_code for r in responses]
    success_count = status_codes.count(200)
    rate_limited_count = status_codes.count(429)

    # Exactly 3 must succeed, 5 must be rate limited
    assert success_count == 3, f"Expected 3 successes, got {success_count} ({status_codes})"
    assert rate_limited_count == 5, f"Expected 5 rate limited, got {rate_limited_count}"


# ── Test 5: Rate limit window resets correctly ─────────────────────────────────

@pytest.mark.asyncio
async def test_5_rate_limit_window_reset(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_a = setup_wu3_data["raw_key_a"]
    db_key_a = setup_wu3_data["db_key_a"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Exhaust limit
    for _ in range(3):
        await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_a, body))
    res_blocked = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_a, body))
    assert res_blocked.status_code == 429

    # Clear sliding window in Redis simulating passage of window
    if redis_client.connected:
        await redis_client.client.delete(f"ratelimit:apikey:{db_key_a.id}:minute")

    # Request succeeds again
    res_after = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_a, body))
    assert res_after.status_code == 200


# ── Test 6: Redis failure follows fail-closed policy ───────────────────────────

@pytest.mark.asyncio
async def test_6_redis_failure_fails_closed(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_a = setup_wu3_data["raw_key_a"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key_a, body)

    # Simulate Redis connection failure during rate limit check
    with patch.object(RateLimitService, "check_rate_limit", side_effect=Exception("Redis connection refused")):
        res = await client.post("/api/v1/ad/request", content=body, headers=headers)
        # S2S partner authenticated request must fail closed with 500 or 503 rather than silently bypassing limits
        assert res.status_code in (500, 503)


# ── Test 7 & 8: Daily quota below limit succeeds and at limit is rejected ─────

@pytest.mark.asyncio
async def test_7_and_8_daily_quota_enforcement(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_a = setup_wu3_data["raw_key_a"]
    db_key_a = setup_wu3_data["db_key_a"]  # daily_quota = 5, rate_limit = 3
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Clear rate limit between calls to isolate daily quota testing
    for i in range(5):
        if redis_client.connected:
            await redis_client.client.delete(f"ratelimit:apikey:{db_key_a.id}:minute")
        res = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_a, body))
        assert res.status_code == 200, f"Request {i+1} failed: {res.text}"
        assert res.headers["X-Quota-Limit"] == "5"
        assert res.headers["X-Quota-Remaining"] == str(5 - i - 1)

    # 6th request exhausts daily quota -> HTTP 429
    if redis_client.connected:
        await redis_client.client.delete(f"ratelimit:apikey:{db_key_a.id}:minute")
    res_6 = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_a, body))
    assert res_6.status_code == 429
    assert "Daily quota exceeded" in res_6.text
    assert res_6.headers["X-Quota-Remaining"] == "0"
    assert "Retry-After" in res_6.headers


# ── Test 9: Concurrent requests cannot exceed daily quota ──────────────────────

@pytest.mark.asyncio
async def test_9_concurrent_requests_daily_quota(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_b = setup_wu3_data["raw_key_b"]  # daily_quota = 20, rate_limit = 10
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Set daily quota low for testing (e.g. 4)
    setup_wu3_data["db_key_b"].daily_quota = 4

    tasks = [
        client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_b, body))
        for _ in range(10)
    ]
    responses = await asyncio.gather(*tasks)
    status_codes = [r.status_code for r in responses]

    # Exactly 4 must succeed, 6 must be 429
    assert status_codes.count(200) == 4
    assert status_codes.count(429) == 6


# ── Test 10: Invalid API-key auth does not consume quota ───────────────────────

@pytest.mark.asyncio
async def test_10_invalid_auth_does_not_consume_quota(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_a = setup_wu3_data["raw_key_a"]
    db_key_a = setup_wu3_data["db_key_a"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Bad signature
    bad_headers = make_hmac_headers(raw_key_a, body, signature="bad" * 16)
    res_bad = await client.post("/api/v1/ad/request", content=body, headers=bad_headers)
    assert res_bad.status_code == 401

    # Check that quota counter in Redis was not touched
    utc_date = RateLimitService.get_utc_date_str()
    val = await redis_client.get(f"quota:apikey:{db_key_a.id}:{utc_date}")
    assert val is None or int(val) == 0


# ── Test 11: Rate-limited requests do not count as served ads ──────────────────

@pytest.mark.asyncio
async def test_11_rate_limited_requests_do_not_count_as_served_ads(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_a = setup_wu3_data["raw_key_a"]
    db_key_a = setup_wu3_data["db_key_a"]  # limit = 3
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # 3 successful requests
    for _ in range(3):
        await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_a, body))

    # 4th and 5th rejected
    for _ in range(2):
        await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_a, body))

    # Inspect usage counters in Redis
    utc_date = RateLimitService.get_utc_date_str()
    usage = await redis_client.client.hgetall(f"usage:apikey:{db_key_a.id}:{utc_date}")
    
    admitted = int(usage.get(b"admitted_requests", usage.get("admitted_requests", 0)))
    rate_limited = int(usage.get(b"rate_limited_requests", usage.get("rate_limited_requests", 0)))
    served = int(usage.get(b"served_ads", usage.get("served_ads", 0)))

    assert admitted == 3
    assert rate_limited == 2
    assert served <= 3  # Served ads does not count rate-limited requests


# ── Test 12: Sandbox usage is isolated from production ─────────────────────────

@pytest.mark.asyncio
async def test_12_sandbox_usage_isolated(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_sandbox = setup_wu3_data["raw_key_sandbox"]
    db_key_sandbox = setup_wu3_data["db_key_sandbox"]
    db_key_a = setup_wu3_data["db_key_a"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Request with sandbox key
    res = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_sandbox, body))
    assert res.status_code == 200

    # Ensure sandbox key has its own distinct Redis keys
    utc_date = RateLimitService.get_utc_date_str()
    sandbox_quota = await redis_client.get(f"quota:apikey:{db_key_sandbox.id}:{utc_date}")
    prod_quota = await redis_client.get(f"quota:apikey:{db_key_a.id}:{utc_date}")

    assert sandbox_quota is not None and int(sandbox_quota) == 1
    assert prod_quota is None or int(prod_quota) == 0


# ── Test 13: Revoked key cannot consume quota ─────────────────────────────────

@pytest.mark.asyncio
async def test_13_revoked_key_cannot_consume_quota(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_rev = setup_wu3_data["raw_key_rev"]
    db_key_rev = setup_wu3_data["db_key_rev"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    res = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_rev, body))
    assert res.status_code == 401
    assert "API key is revoked" in res.text

    utc_date = RateLimitService.get_utc_date_str()
    val = await redis_client.get(f"quota:apikey:{db_key_rev.id}:{utc_date}")
    assert val is None or int(val) == 0


# ── Test 14: Publisher cannot read another publisher's usage ───────────────────

@pytest.mark.asyncio
async def test_14_cross_publisher_usage_authorization(client: AsyncClient, setup_wu3_data) -> None:
    token_pub2 = setup_wu3_data["token_pub2"]  # Publisher 2
    db_key_a = setup_wu3_data["db_key_a"]      # Owned by Publisher 1

    # Publisher 2 attempts to read Publisher 1's key usage
    headers_pub2 = {"Authorization": f"Bearer {token_pub2}"}
    res = await client.get(f"/api/v1/publisher/1/keys/{db_key_a.id}/usage", headers=headers_pub2)
    assert res.status_code == 403
    assert "outside your account" in res.text or "Insufficient permissions" in res.text

    # Publisher 2 attempts to list all keys for Publisher 1
    res_list = await client.get("/api/v1/publisher/1/keys/usage", headers=headers_pub2)
    assert res_list.status_code == 403


# ── Test 15: API responses never reveal stored key hashes or secrets ───────────

@pytest.mark.asyncio
async def test_15_api_responses_never_reveal_secrets(client: AsyncClient, setup_wu3_data) -> None:
    token_pub1 = setup_wu3_data["token_pub1"]
    db_key_a = setup_wu3_data["db_key_a"]
    headers = {"Authorization": f"Bearer {token_pub1}"}

    res = await client.get(f"/api/v1/publisher/1/keys/{db_key_a.id}/usage", headers=headers)
    assert res.status_code == 200
    data = res.json()

    assert "key_hash" not in data
    assert "secret_hash" not in data
    assert "raw_key" not in data
    assert "key_prefix" in data  # safe prefix is allowed


# ── Test 16: Usage API returns accurate limits, remaining, and reset ──────────

@pytest.mark.asyncio
async def test_16_usage_api_accuracy(client: AsyncClient, setup_wu3_data) -> None:
    token_pub1 = setup_wu3_data["token_pub1"]
    raw_key_a = setup_wu3_data["raw_key_a"]
    db_key_a = setup_wu3_data["db_key_a"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Perform 2 ad requests
    for _ in range(2):
        await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_a, body))

    # Query usage API
    headers = {"Authorization": f"Bearer {token_pub1}"}
    res = await client.get(f"/api/v1/publisher/1/keys/{db_key_a.id}/usage", headers=headers)
    assert res.status_code == 200
    usage = res.json()

    assert usage["key_id"] == db_key_a.id
    assert usage["rate_limit_per_minute"] == 3
    assert usage["daily_quota"] == 5
    assert usage["today_usage"] == 2
    assert usage["daily_quota_remaining"] == 3
    assert usage["breakdown"]["total_requests"] == 2
    assert usage["breakdown"]["admitted_requests"] == 2
    assert "daily_reset_time_utc" in usage


# ── Test 17: Existing public anonymous placements continue to work ─────────────

@pytest.mark.asyncio
async def test_17_anonymous_public_placements_unaffected(client: AsyncClient, setup_wu3_data) -> None:
    # 1. Anonymous request to public website slot succeeds without API key
    body_pub = json.dumps({"slot_id": "slot_public", "num_ads": 1}).encode()
    res_pub = await client.post("/api/v1/ad/request", content=body_pub, headers={"Content-Type": "application/json"})
    assert res_pub.status_code == 200
    assert "ads" in res_pub.json()

    # 2. Anonymous request to private chat slot is rejected with 401
    body_chat = json.dumps({"slot_id": "slot_chat", "num_ads": 1}).encode()
    res_chat = await client.post("/api/v1/ad/request", content=body_chat, headers={"Content-Type": "application/json"})
    assert res_chat.status_code == 401


# ── Test 18: Ad serving, tracking, and billing remain intact ───────────────────

@pytest.mark.asyncio
async def test_18_ad_serving_tracking_intact(client: AsyncClient, setup_wu3_data) -> None:
    raw_key_b = setup_wu3_data["raw_key_b"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    res = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key_b, body))
    assert res.status_code == 200
    data = res.json()
    assert len(data["ads"]) >= 1

    ad = data["ads"][0]
    assert "creative" in ad
    assert "tracking" in ad
    assert "sig=" in ad["tracking"]["impression_url"]

    # Tracking event call
    track_url = ad["tracking"]["impression_url"]
    path_and_query = track_url.split("http://localhost:8000")[-1]
    res_track = await client.get(path_and_query)
    assert res_track.status_code in (200, 204)
