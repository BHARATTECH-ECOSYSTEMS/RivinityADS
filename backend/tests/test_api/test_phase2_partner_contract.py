"""
Phase 2 Partner Contract Integration Test Suite.

Verifies:
1. Wrong tenant isolation: Publisher A key cannot access or serve ads for Publisher B slot (HTTP 403).
2. Revoked API key: Inbound requests with revoked key receive HTTP 401.
3. Invalid HMAC signature: Malformed or mismatched signatures receive HTTP 401.
4. Expired token / timestamp drift: Timestamp outside 300s window receives HTTP 401.
5. Wrong domain / origin mismatch: Requests with mismatched Origin/Referer receive HTTP 403.
6. Unsupported creative format: Format mismatch returns no ad (ads=[]) when allow_format_fallback=False.
7. Allowed format fallback: Format mismatch returns available ads when allow_format_fallback=True.
8. Rate limit enforcement: Exceeding rate_limit_per_minute returns HTTP 429 with Retry-After and X-RateLimit headers.
9. Daily quota enforcement: Exceeding daily_quota returns HTTP 429 with X-Quota headers.
10. No eligible inventory: Returns HTTP 200 with empty ads list when targeting does not match.
11. Paused campaign: Inactive / paused campaigns are never served as candidates.
12. Budget exhausted: Campaigns that have reached daily budget are filtered out.
13. Chat/AI surface opt-in (Task J): Ordinary web slots reject chat surface requests (HTTP 403).
14. Chat/AI surface opted-in: Conversational slots serve ads with sponsored=True, disclosure_label="Sponsored", and interstitials filtered out.
15. Publisher sandbox mode (Task G): Requests with pk_test_ keys generate zero billable activity (cost=0, is_test=True, no wallet deduction).
16. API key rotation (Task D): Rotating an API key revokes the old key and generates a new active key with identical limits and audit trail.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from liteads.ad_server.main import app
from liteads.common.cache import redis_client
from liteads.management.api_key_service import ApiKeyService
from liteads.models.ad import AdEvent, AdSet, Advertiser, Campaign, Creative
from liteads.models.auth import AuditLog, User
from liteads.models.base import BidType, CreativeType, ModerationStatus, Status
from liteads.models.finance import Transaction
from liteads.models.publisher import AdSlot, Publisher, PublisherApiKey, PublisherProperty
from liteads.schemas.management import ApiKeyCreate

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# In-Memory Redis Mock with Lua eval, sliding window, and pipeline
# ─────────────────────────────────────────────────────────────────────────────

class MockRedisPipeline:
    def __init__(self, store: dict, ttls: dict):
        self.store = store
        self.ttls = ttls
        self.commands: list[tuple[Any, ...]] = []

    def __getattr__(self, name: str):
        def _method(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            return self
        return _method

    async def execute(self):
        results = []
        for item in self.commands:
            cmd = item[0]
            args = item[1]
            if cmd == "get":
                key = args[0]
                val = self.store.get(key)
                results.append(str(val) if val is not None else None)
            elif cmd == "hgetall":
                key = args[0]
                val = self.store.get(key, {})
                results.append(val if isinstance(val, dict) else {})
            elif cmd == "incr":
                key = args[0]
                val = int(self.store.get(key, 0)) + 1
                self.store[key] = val
                results.append(val)
            elif cmd == "expire":
                key, ttl = args[0], args[1]
                if key in self.store:
                    self.ttls[key] = ttl
                    results.append(True)
                else:
                    results.append(False)
            elif cmd == "hincrby":
                key, field = args[0], args[1]
                amount = args[2] if len(args) > 2 else 1
                h = self.store.setdefault(key, {})
                if isinstance(h, dict):
                    h[field] = h.get(field, 0) + amount
                    results.append(h[field])
                else:
                    results.append(1)
            else:
                results.append(None)
        self.commands.clear()
        return results


@pytest.fixture(autouse=True)
def mock_redis_partner(monkeypatch):
    """In-memory Redis mock supporting sliding-window rate limit, quota, and pipeline."""
    store: dict[str, Any] = {}
    ttls: dict[str, int] = {}

    async def mock_connect():
        redis_client._connected = True

    async def mock_close():
        redis_client._connected = False

    async def mock_ping():
        return True

    async def mock_get(key: str):
        val = store.get(key)
        return str(val) if val is not None else None

    async def mock_set(key: str, value: Any, ttl: int | None = None, ex: int | None = None, nx: bool = False, xx: bool = False, **kwargs):
        if nx and key in store:
            return False
        if xx and key not in store:
            return False
        store[key] = value
        effective_ttl = ttl or ex
        if effective_ttl:
            ttls[key] = effective_ttl
        return True

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

    async def mock_delete(*keys: str):
        count = 0
        for k in keys:
            if k in store:
                del store[k]
                ttls.pop(k, None)
                count += 1
        return count

    async def mock_eval(script: str, *args):
        numkeys = 1
        keys = []
        argv = []
        if args and isinstance(args[0], int):
            numkeys = args[0]
            keys = list(args[1 : 1 + numkeys])
            argv = list(args[1 + numkeys :])
        elif args:
            keys = [args[0]]
            argv = list(args[1:])

        # Sliding window rate limit Lua simulation
        if "ZREMRANGEBYSCORE" in script or "max_limit" in script:
            max_limit = int(argv[2]) if len(argv) > 2 else 60
            key = keys[0] if keys else "rl"
            cur = int(store.get(key, 0))
            if cur < max_limit:
                store[key] = cur + 1
                return [1, max_limit - cur - 1, 60]
            else:
                return [0, 0, 60]

        # Daily quota check Lua simulation
        if "max_quota" in script or "daily_quota" in script:
            max_quota = int(argv[0]) if len(argv) > 0 else 10000
            key = keys[0] if keys else "quota"
            cur = int(store.get(key, 0))
            if cur < max_quota:
                store[key] = cur + 1
                return [1, max_quota - cur - 1, 86400]
            else:
                return [0, 0, 86400]

        return 1

    def mock_pipeline():
        return MockRedisPipeline(store, ttls)

    monkeypatch.setattr(redis_client, "connect", mock_connect)
    monkeypatch.setattr(redis_client, "close", mock_close)
    monkeypatch.setattr(redis_client, "get", mock_get)
    monkeypatch.setattr(redis_client, "set", mock_set)
    monkeypatch.setattr(redis_client, "incr", mock_incr)
    monkeypatch.setattr(redis_client, "expire", mock_expire)
    monkeypatch.setattr(redis_client, "ttl", mock_ttl)
    monkeypatch.setattr(redis_client, "delete", mock_delete)
    monkeypatch.setattr(redis_client, "pipeline", mock_pipeline)

    mock_inner_client = MagicMock()
    mock_inner_client.ping = mock_ping
    mock_inner_client.eval = mock_eval
    mock_inner_client.pipeline = mock_pipeline
    mock_inner_client.flushdb = AsyncMock(return_value=True)
    monkeypatch.setattr(redis_client, "_client", mock_inner_client)
    redis_client._connected = True

    yield store, ttls

    redis_client._connected = False


def make_hmac_headers(
    raw_key: str,
    body: bytes,
    timestamp: int | None = None,
    signature: str | None = None,
    method: str = "POST",
    path: str = "/api/v1/ad/request",
    version: str = "2",
) -> dict[str, str]:
    """Generate canonical HMAC headers for S2S ad requests (v2 standard)."""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    if signature is None:
        body_sha = hashlib.sha256(body).hexdigest()
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


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures for Partner Data Setup
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
async def partner_context(test_db):
    """Seed test publishers, verified properties, slots, live & sandbox keys, advertiser, and campaigns."""
    # 1. Publisher 1 (Primary live publisher)
    pub1 = Publisher(
        name="Partner Publisher One",
        status=Status.ACTIVE,
        is_sandbox=False,
        balance=Decimal("100.00"),
    )
    # 2. Publisher 2 (Secondary tenant for wrong-tenant testing)
    pub2 = Publisher(
        name="Partner Publisher Two",
        status=Status.ACTIVE,
        is_sandbox=False,
        balance=Decimal("50.00"),
    )
    test_db.add_all([pub1, pub2])
    await test_db.flush()

    # 3. Verified property for Publisher 1
    prop1 = PublisherProperty(
        publisher_id=pub1.id,
        name="Tech News Portal",
        domain="technews.in",
        status=Status.ACTIVE,
        verification_status="VERIFIED",
    )
    test_db.add(prop1)
    await test_db.flush()

    # 4. Slots for Publisher 1
    slot_web = AdSlot(
        publisher_id=pub1.id,
        property_id=prop1.id,
        slot_id="slot_partner_web",
        name="Web Banner Slot",
        format=1,  # Banner
        width=300,
        height=250,
        status=Status.ACTIVE,
        is_chat_surface=False,  # Ordinary web slot
        floor_price=Decimal("0.05"),
    )
    slot_chat = AdSlot(
        publisher_id=pub1.id,
        property_id=prop1.id,
        slot_id="slot_partner_chat",
        name="AI Chat Inline Slot",
        format=2,  # Native
        width=0,
        height=0,
        status=Status.ACTIVE,
        is_chat_surface=True,  # Opted-in chat surface
        floor_price=Decimal("0.10"),
    )
    # Slot for Publisher 2
    slot_pub2 = AdSlot(
        publisher_id=pub2.id,
        slot_id="slot_pub2_only",
        name="Pub2 Slot",
        format=1,
        status=Status.ACTIVE,
        is_chat_surface=False,
    )
    test_db.add_all([slot_web, slot_chat, slot_pub2])
    await test_db.flush()

    # 5. API Keys for Publisher 1
    db_live_key, raw_live_key = await ApiKeyService.create_api_key(
        test_db,
        pub1.id,
        ApiKeyCreate(
            name="Live Production Key",
            is_sandbox=False,
            rate_limit_per_minute=60,
            daily_quota=1000,
        ),
    )
    db_sandbox_key, raw_sandbox_key = await ApiKeyService.create_api_key(
        test_db,
        pub1.id,
        ApiKeyCreate(
            name="Sandbox Testing Key",
            is_sandbox=True,
            rate_limit_per_minute=60,
            daily_quota=1000,
        ),
    )
    db_revoked_key, raw_revoked_key = await ApiKeyService.create_api_key(
        test_db,
        pub1.id,
        ApiKeyCreate(
            name="Revoked Key",
            is_sandbox=False,
            rate_limit_per_minute=60,
            daily_quota=1000,
        ),
    )
    await ApiKeyService.revoke_api_key(test_db, pub1.id, db_revoked_key.id)

    # API Key for Publisher 2
    db_pub2_key, raw_pub2_key = await ApiKeyService.create_api_key(
        test_db,
        pub2.id,
        ApiKeyCreate(
            name="Pub2 Key",
            is_sandbox=False,
            rate_limit_per_minute=60,
            daily_quota=1000,
        ),
    )

    # 6. Advertiser and Active Campaign with Banner & Native Creatives
    adv = Advertiser(
        name="Bharat Tech Solutions",
        company="Bharat Tech Corp",
        balance=Decimal("5000.00"),
    )
    test_db.add(adv)
    await test_db.flush()

    campaign = Campaign(
        advertiser_id=adv.id,
        name="Cloud Hosting Promotion",
        budget_total=Decimal("50000.00"),
        budget_daily=Decimal("1000.00"),
        bid_type=BidType.CPM,
        bid_amount=Decimal("1.50"),
        status=Status.ACTIVE,
        spent_today=Decimal("0.00"),
    )
    test_db.add(campaign)
    await test_db.flush()

    ad_set = AdSet(
        campaign_id=campaign.id,
        name="Standard Web & Native Set",
        bid_type=BidType.CPM,
        bid_amount=Decimal("1.50"),
        status=Status.ACTIVE,
    )
    test_db.add(ad_set)
    await test_db.flush()

    banner_creative = Creative(
        campaign_id=campaign.id,
        ad_set_id=ad_set.id,
        title="High Speed Indian Cloud",
        description="Launch VMs in Mumbai and Bengaluru starting ₹499/mo.",
        image_url="https://cdn.liteads.com/img/cloud_300x250.jpg",
        landing_url="https://bharatcloud.in/landing",
        creative_type=1,  # Banner
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    native_creative = Creative(
        campaign_id=campaign.id,
        ad_set_id=ad_set.id,
        title="AI API Compute Platform",
        description="Zero-latency inference endpoints hosted within India.",
        image_url="https://cdn.liteads.com/img/native_card.jpg",
        landing_url="https://bharatcloud.in/ai",
        creative_type=2,  # Native
        width=0,
        height=0,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    interstitial_creative = Creative(
        campaign_id=campaign.id,
        ad_set_id=ad_set.id,
        title="Full Screen Promo",
        description="Exclusive cloud credit voucher for developers.",
        image_url="https://cdn.liteads.com/img/interstitial.jpg",
        landing_url="https://bharatcloud.in/promo",
        creative_type=4,  # Interstitial
        width=1080,
        height=1920,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    test_db.add_all([banner_creative, native_creative, interstitial_creative])
    await test_db.commit()

    return {
        "pub1": pub1,
        "pub2": pub2,
        "prop1": prop1,
        "slot_web": slot_web,
        "slot_chat": slot_chat,
        "slot_pub2": slot_pub2,
        "raw_live_key": raw_live_key,
        "raw_sandbox_key": raw_sandbox_key,
        "raw_revoked_key": raw_revoked_key,
        "raw_pub2_key": raw_pub2_key,
        "db_live_key": db_live_key,
        "adv": adv,
        "campaign": campaign,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Tenant Boundary Tests
# ─────────────────────────────────────────────────────────────────────────────

async def test_wrong_tenant_cannot_request_ads_for_other_publisher_slot(client: AsyncClient, partner_context):
    """Publisher 2 API key calling Publisher 1's slot receives HTTP 403 Forbidden."""
    raw_key = partner_context["raw_pub2_key"]
    body = json.dumps({"slot_id": partner_context["slot_web"].slot_id, "num_ads": 1}).encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 403
    assert res.json()["detail"] == "Not authorized for this slot"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Authentication & Integrity Tests
# ─────────────────────────────────────────────────────────────────────────────

async def test_revoked_api_key_is_rejected(client: AsyncClient, partner_context):
    """Inbound request using a revoked key receives HTTP 401 Unauthorized."""
    raw_key = partner_context["raw_revoked_key"]
    body = json.dumps({"slot_id": partner_context["slot_web"].slot_id, "num_ads": 1}).encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 401
    assert "revoked" in res.json()["detail"].lower()


async def test_invalid_hmac_signature_is_rejected(client: AsyncClient, partner_context):
    """Inbound request with invalid signature receives HTTP 401 Unauthorized."""
    raw_key = partner_context["raw_live_key"]
    body = json.dumps({"slot_id": partner_context["slot_web"].slot_id, "num_ads": 1}).encode("utf-8")
    headers = make_hmac_headers(raw_key, body, signature="deadbeef" * 8)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 401
    assert "Invalid request signature" in res.json()["detail"]


async def test_expired_timestamp_drift_is_rejected(client: AsyncClient, partner_context):
    """Timestamp older than 300 seconds is rejected with HTTP 401."""
    raw_key = partner_context["raw_live_key"]
    body = json.dumps({"slot_id": partner_context["slot_web"].slot_id, "num_ads": 1}).encode("utf-8")
    stale_ts = int(time.time()) - 305  # 305s in the past
    headers = make_hmac_headers(raw_key, body, timestamp=stale_ts)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 401
    assert "outside the permitted 300-second window" in res.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Domain & Origin Security Boundary Tests
# ─────────────────────────────────────────────────────────────────────────────

async def test_unauthorized_origin_domain_is_rejected(client: AsyncClient, partner_context):
    """Browser request with mismatched Origin header receives HTTP 403 Forbidden."""
    # Anonymous request from browser must have valid Origin matching verified property (technews.in)
    body = {
        "slot_id": partner_context["slot_web"].slot_id,
        "num_ads": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://attacker.com",
    }
    res = await client.post("/api/v1/ad/request", json=body, headers=headers)
    assert res.status_code == 403
    assert "not authorized for this ad slot" in res.json()["detail"]


async def test_unauthorized_referer_prefix_spoof_is_rejected(client: AsyncClient, partner_context):
    """Attacker attempting prefix spoof (technews.in.attacker.com) is rejected with HTTP 403."""
    body = {
        "slot_id": partner_context["slot_web"].slot_id,
        "num_ads": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://technews.in.attacker.com/page",
    }
    res = await client.post("/api/v1/ad/request", json=body, headers=headers)
    assert res.status_code == 403
    assert "not authorized for this ad slot" in res.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. Format Mismatch & Fallback Tests (Task B)
# ─────────────────────────────────────────────────────────────────────────────

async def test_format_mismatch_returns_no_ad_when_fallback_disabled(client: AsyncClient, partner_context):
    """When format=2 (native) is requested on a banner slot with no native match, returns no ad."""
    raw_key = partner_context["raw_live_key"]
    # Request format=4 (interstitial) on slot_web; slot_web is 300x250 banner
    payload = {
        "slot_id": partner_context["slot_web"].slot_id,
        "num_ads": 1,
        "format": 4,  # Interstitial
        "allow_format_fallback": False,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 0
    assert data["ads"] == []


async def test_format_mismatch_returns_ad_when_fallback_allowed(client: AsyncClient, partner_context):
    """When allow_format_fallback=True, candidate of alternative format is permitted."""
    raw_key = partner_context["raw_live_key"]
    payload = {
        "slot_id": partner_context["slot_web"].slot_id,
        "num_ads": 1,
        "format": 3,  # Video (none exist in DB)
        "allow_format_fallback": True,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    # Should fall back to the available banner ad
    assert data["count"] >= 1
    assert data["ads"][0]["creative"]["creative_type"] == "banner"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Rate Limit & Daily Quota Tests
# ─────────────────────────────────────────────────────────────────────────────

async def test_rate_limit_exceeded_returns_429(client: AsyncClient, test_db, partner_context):
    """Exceeding rate_limit_per_minute returns HTTP 429 with standard headers."""
    pub_id = partner_context["pub1"].id
    _, raw_key = await ApiKeyService.create_api_key(
        test_db,
        pub_id,
        ApiKeyCreate(
            name="Strict Rate Limit Key",
            is_sandbox=False,
            rate_limit_per_minute=2,  # Limit = 2
            daily_quota=1000,
        ),
    )
    payload = {"slot_id": partner_context["slot_web"].slot_id, "num_ads": 1}
    body = json.dumps(payload).encode("utf-8")

    # Request 1: allowed
    res1 = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key, body))
    assert res1.status_code == 200

    # Request 2: allowed
    res2 = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key, body))
    assert res2.status_code == 200

    # Request 3: rate limit exceeded -> 429
    res3 = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key, body))
    assert res3.status_code == 429
    assert "Retry-After" in res3.headers
    assert res3.headers["X-RateLimit-Limit"] == "2"
    assert res3.headers["X-RateLimit-Remaining"] == "0"


async def test_daily_quota_exceeded_returns_429(client: AsyncClient, test_db, partner_context):
    """Exceeding daily_quota returns HTTP 429 with X-Quota headers."""
    pub_id = partner_context["pub1"].id
    _, raw_key = await ApiKeyService.create_api_key(
        test_db,
        pub_id,
        ApiKeyCreate(
            name="Strict Quota Key",
            is_sandbox=False,
            rate_limit_per_minute=100,
            daily_quota=2,  # Quota = 2
        ),
    )
    payload = {"slot_id": partner_context["slot_web"].slot_id, "num_ads": 1}
    body = json.dumps(payload).encode("utf-8")

    # Request 1: allowed
    res1 = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key, body))
    assert res1.status_code == 200

    # Request 2: allowed
    res2 = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key, body))
    assert res2.status_code == 200

    # Request 3: daily quota exceeded -> 429
    res3 = await client.post("/api/v1/ad/request", content=body, headers=make_hmac_headers(raw_key, body))
    assert res3.status_code == 429
    assert res3.headers["X-Quota-Limit"] == "2"
    assert res3.headers["X-Quota-Remaining"] == "0"
    assert "Daily quota exceeded" in res3.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. Chat & AI Surface Opt-In Tests (Task J)
# ─────────────────────────────────────────────────────────────────────────────

async def test_chat_surface_rejected_on_ordinary_web_slot(client: AsyncClient, partner_context):
    """Ordinary web slot (is_chat_surface=False) rejects conversational assistant requests with HTTP 403."""
    raw_key = partner_context["raw_live_key"]
    payload = {
        "slot_id": partner_context["slot_web"].slot_id,  # is_chat_surface=False
        "num_ads": 1,
        "surface": "chat",  # Requesting chat surface
    }
    body = json.dumps(payload).encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 403
    assert "Ad slot is not configured for conversational or chat surfaces" in res.json()["detail"]


async def test_chat_surface_allowed_and_interstitial_prohibited(client: AsyncClient, partner_context):
    """Conversational slot serves native ads with disclosure_label='Sponsored', excluding interstitials."""
    raw_key = partner_context["raw_live_key"]
    payload = {
        "slot_id": partner_context["slot_chat"].slot_id,  # is_chat_surface=True
        "num_ads": 2,
        "surface": "chat",
        "is_chat_surface": True,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 1

    ad = data["ads"][0]
    # Task F compliance: Sponsored disclosure and review status
    assert ad["sponsored"] is True
    assert ad["disclosure_label"] == "Sponsored"
    assert ad["review_status"] == "APPROVED"
    assert ad["advertiser_id"] is not None
    # Interstitials must NEVER be served on chat surfaces
    for item in data["ads"]:
        assert item["creative"]["creative_type"] != "interstitial"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Publisher Sandbox & Isolation Tests (Task G)
# ─────────────────────────────────────────────────────────────────────────────

async def test_sandbox_key_serves_test_inventory_with_zero_billable_spend(client: AsyncClient, test_db, partner_context):
    """Sandbox requests with pk_test_ key generate zero billable spend and never modify ledgers."""
    raw_key = partner_context["raw_sandbox_key"]
    payload = {
        "slot_id": partner_context["slot_web"].slot_id,
        "num_ads": 1,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    # 1. Request ad in sandbox mode
    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["request_id"].startswith("test_")
    assert data["count"] >= 1
    ad = data["ads"][0]
    imp_url = ad["tracking"]["impression_url"]
    assert "sandbox=1" in imp_url or "req=test_" in imp_url

    # Check advertiser initial balance
    adv_before = await test_db.get(Advertiser, partner_context["adv"].id)
    balance_before = adv_before.balance

    # 2. Fire impression pixel using tracking URL
    imp_path = imp_url.split("/api/v1")[-1]
    imp_res = await client.get(f"/api/v1{imp_path}")
    assert imp_res.status_code == 200
    assert imp_res.json()["success"] is True

    # 3. Verify ZERO billable deduction and ZERO Transaction records created
    await test_db.refresh(adv_before)
    assert adv_before.balance == balance_before  # No deduction!

    tx_res = await test_db.execute(
        select(Transaction).where(Transaction.reference_id.like(f"%{data['request_id']}%"))
    )
    assert tx_res.scalars().all() == []  # No financial transaction logged!

    # 4. Verify event recorded with is_test = True
    ev_res = await test_db.execute(
        select(AdEvent).where(AdEvent.request_id == data["request_id"])
    )
    events = ev_res.scalars().all()
    assert len(events) >= 1
    assert events[0].is_test is True
    assert events[0].cost == Decimal("0.000000")


# ─────────────────────────────────────────────────────────────────────────────
# 8. API Key Rotation Tests (Task D)
# ─────────────────────────────────────────────────────────────────────────────

async def test_publisher_api_key_rotation(client: AsyncClient, test_db, partner_context):
    """Rotating an API key revokes the old key and generates a new key with identical quota."""
    pub_id = partner_context["pub1"].id
    old_key_db, old_raw_key = await ApiKeyService.create_api_key(
        test_db,
        pub_id,
        ApiKeyCreate(
            name="Rotatable Key",
            is_sandbox=False,
            rate_limit_per_minute=45,
            daily_quota=5000,
        ),
    )

    # Perform rotation via ApiKeyService
    new_key_db, new_raw_key = await ApiKeyService.rotate_api_key(test_db, pub_id, old_key_db.id)

    assert new_key_db.id != old_key_db.id
    assert new_raw_key != old_raw_key
    assert new_key_db.rate_limit_per_minute == 45
    assert new_key_db.daily_quota == 5000
    assert new_key_db.status == 1

    # Verify old key is revoked in DB
    refreshed_old = await test_db.get(PublisherApiKey, old_key_db.id)
    assert refreshed_old.revoked_at is not None
    assert refreshed_old.status == 0

    # Old key must now be rejected
    body = json.dumps({"slot_id": partner_context["slot_web"].slot_id, "num_ads": 1}).encode("utf-8")
    old_headers = make_hmac_headers(old_raw_key, body)
    res_old = await client.post("/api/v1/ad/request", content=body, headers=old_headers)
    assert res_old.status_code == 401

    # New key must succeed
    new_headers = make_hmac_headers(new_raw_key, body)
    res_new = await client.post("/api/v1/ad/request", content=body, headers=new_headers)
    assert res_new.status_code == 200

    # Verify AuditLog entry was recorded
    audit_res = await test_db.execute(
        select(AuditLog).where(
            AuditLog.action == "rotate_publisher_api_key",
        )
    )
    assert audit_res.scalar_one_or_none() is not None


# ─────────────────────────────────────────────────────────────────────────────
# 9. Campaign Eligibility & Budget Exhaustion Tests
# ─────────────────────────────────────────────────────────────────────────────

async def test_paused_campaign_is_not_served(client: AsyncClient, test_db, partner_context):
    """Paused campaigns are filtered out and return no ads."""
    campaign = partner_context["campaign"]
    campaign.status = Status.PAUSED
    await test_db.commit()

    raw_key = partner_context["raw_live_key"]
    payload = {"slot_id": partner_context["slot_web"].slot_id, "num_ads": 1}
    body = json.dumps(payload).encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    assert res.json()["count"] == 0
    assert res.json()["ads"] == []


async def test_budget_exhausted_campaign_is_not_served(client: AsyncClient, test_db, partner_context):
    """Campaigns that reached daily budget limit are filtered out and return no ads."""
    campaign = partner_context["campaign"]
    campaign.spent_today = campaign.budget_daily
    await test_db.commit()

    raw_key = partner_context["raw_live_key"]
    payload = {"slot_id": partner_context["slot_web"].slot_id, "num_ads": 1}
    body = json.dumps(payload).encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    assert res.json()["count"] == 0
    assert res.json()["ads"] == []


async def test_no_eligible_inventory_returns_empty_ad_list(client: AsyncClient, test_db, partner_context):
    """When no active campaigns match requested placement, ad server returns 200 with empty list."""
    campaign = partner_context["campaign"]
    campaign.status = Status.PAUSED
    await test_db.commit()

    raw_key = partner_context["raw_live_key"]
    payload = {
        "slot_id": partner_context["slot_web"].slot_id,
        "num_ads": 1,
        "format": 1,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = make_hmac_headers(raw_key, body)

    res = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 0
    assert data["ads"] == []
    assert "request_id" in data
