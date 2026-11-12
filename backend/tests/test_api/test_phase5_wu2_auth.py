"""Tests for Phase 5 Work Unit 2: API-Key Authentication Hardening & Ad Request Contract Alignment."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from liteads.ad_server.main import app
from liteads.common.cache import redis_client
from liteads.management.api_key_service import ApiKeyService
from liteads.models.ad import Advertiser, Campaign, Creative
from liteads.models.publisher import AdSlot, Publisher, PublisherApiKey
from liteads.schemas.management import ApiKeyCreate


class MockRedisPipeline:
    def __init__(self, store, ttls):
        self.store = store
        self.ttls = ttls
        self.commands = []

    def __getattr__(self, name: str):
        def _method(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            return self
        return _method

    async def execute(self):
        results = []
        for item in self.commands:
            if len(item) == 2:
                cmd, args = item
                kwargs = {}
            else:
                cmd, args, kwargs = item
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
def mock_redis_auth(monkeypatch):
    """In-memory Redis mock supporting eval, pipeline, incr, get, ttl, expire."""
    store = {}
    ttls = {}

    async def mock_connect():
        redis_client._connected = True

    async def mock_close():
        redis_client._connected = False

    async def mock_ping():
        return True

    async def mock_get(key: str):
        val = store.get(key)
        return str(val) if val is not None else None

    async def mock_set(key: str, value, ttl: int | None = None, ex: int | None = None, nx: bool = False, xx: bool = False, **kwargs):
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
        if args and isinstance(args[0], (list, tuple)):
            keys = list(args[0])
            argv = list(args[1]) if len(args) > 1 else []
        elif args and isinstance(args[0], int):
            numkeys = args[0]
            keys = list(args[1:1 + numkeys])
            argv = list(args[1 + numkeys:])

        # Rate limiting sliding window Lua
        if "ZREMRANGEBYSCORE" in script or "max_limit" in script:
            max_limit = int(argv[2]) if len(argv) > 2 else 60
            key = keys[0] if keys else "rl"
            cur = store.get(key, 0)
            if cur < max_limit:
                store[key] = cur + 1
                return [1, max_limit - cur - 1, 60]
            else:
                return [0, 0, 60]

        # Daily quota check Lua
        if "max_quota" in script:
            max_quota = int(argv[0]) if len(argv) > 0 else 10000
            key = keys[0] if keys else "quota"
            cur = store.get(key, 0)
            if cur < max_quota:
                store[key] = cur + 1
                return [1, max_quota - cur - 1, 86400]
            else:
                return [0, 0, 86400]

        # Atomic frequency cap incr Lua
        if "EXPIRE" in script and "INCR" in script:
            key = keys[0] if keys else "freq"
            ttl = int(argv[0]) if len(argv) > 0 else 86400
            store[key] = int(store.get(key, 0)) + 1
            ttls[key] = ttl
            return store[key]

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
    version: str | None = None,
) -> dict[str, str]:
    """Generate canonical HMAC headers for S2S ad requests.

    Defaults to authoritative v2 format: METHOD|PATH|TIMESTAMP|BODY_SHA256.
    If version=='1', generates legacy format: RAW_KEY|TIMESTAMP|BODY_SHA256.
    """
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


@pytest.fixture
async def setup_ad_data(test_db):
    """Set up test publishers, slots, advertiser, campaign, and creative."""
    # Publisher 1 (active)
    pub1 = Publisher(id=1, name="Publisher One", status=1, is_sandbox=False)
    # Publisher 2 (active)
    pub2 = Publisher(id=2, name="Publisher Two", status=1, is_sandbox=False)
    test_db.add_all([pub1, pub2])
    await test_db.flush()

    # Slots
    slot1 = AdSlot(id=1, publisher_id=pub1.id, slot_id="slot_pub1", name="Slot 1", format=2, status=1)
    slot2 = AdSlot(id=2, publisher_id=pub2.id, slot_id="slot_pub2", name="Slot 2", format=2, status=1)
    test_db.add_all([slot1, slot2])

    # Advertiser, Campaign, Creative
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

    from liteads.models.domain import AdvertiserDomain

    domain = AdvertiserDomain(advertiser_id=adv.id, domain="example.com", status="VERIFIED", verification_token="test")
    creat = Creative(
        id=1,
        campaign_id=camp.id,
        title="Test Creative",
        description="Great ad",
        landing_url="https://example.com",
        creative_type=2,
        width=300,
        height=250,
        status=1,
        moderation_status=2,
    )
    test_db.add_all([creat, domain])
    await test_db.commit()

    # Create active API key for Publisher 1
    db_key1, raw_key1 = await ApiKeyService.create_api_key(
        test_db, publisher_id=pub1.id, schema=type("Schema", (), {"name": "Key Pub 1", "is_sandbox": False, "rate_limit_per_minute": 60, "daily_quota": 10000})()
    )

    # Create active API key for Publisher 2
    db_key2, raw_key2 = await ApiKeyService.create_api_key(
        test_db, publisher_id=pub2.id, schema=type("Schema", (), {"name": "Key Pub 2", "is_sandbox": False, "rate_limit_per_minute": 60, "daily_quota": 10000})()
    )

    # Create revoked API key for Publisher 1
    db_key_rev, raw_key_rev = await ApiKeyService.create_api_key(
        test_db, publisher_id=pub1.id, schema=type("Schema", (), {"name": "Revoked Key", "is_sandbox": False, "rate_limit_per_minute": 60, "daily_quota": 10000})()
    )
    db_key_rev.revoked_at = datetime.now(timezone.utc)
    test_db.add(db_key_rev)
    await test_db.commit()

    return {
        "pub1": pub1,
        "pub2": pub2,
        "raw_key1": raw_key1,
        "db_key1": db_key1,
        "raw_key2": raw_key2,
        "db_key2": db_key2,
        "raw_key_rev": raw_key_rev,
        "db_key_rev": db_key_rev,
        "camp": camp,
        "adv": adv,
    }


# ==============================================================================
# Security Regression Tests (Requirements 1 - 11)
# ==============================================================================

@pytest.mark.asyncio
async def test_sec_1_unknown_key_with_self_generated_hmac_rejected(client: AsyncClient, setup_ad_data) -> None:
    """1. An unknown API key with a self-generated HMAC cannot receive ads."""
    fake_key = "pk_test_fake_unregistered_key_12345678"
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = make_hmac_headers(fake_key, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 401
    assert "Invalid API key" in response.text or "unregistered" in response.text.lower()


@pytest.mark.asyncio
async def test_sec_2_unknown_key_without_signature_rejected(client: AsyncClient, setup_ad_data) -> None:
    """2. An unknown key without a signature cannot bypass authentication."""
    fake_key = "pk_test_unknown_nosig"
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = {"Content-Type": "application/json", "X-API-Key": fake_key}

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_sec_3_registered_key_missing_signature_rejected(client: AsyncClient, setup_ad_data) -> None:
    """3. A registered key with a missing signature is rejected wherever signatures are required."""
    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = {"Content-Type": "application/json", "X-API-Key": raw_key1}

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 401
    assert "X-Signature header required" in response.text


@pytest.mark.asyncio
async def test_sec_4_registered_key_incorrect_signature_rejected(client: AsyncClient, setup_ad_data) -> None:
    """4. A registered key with an incorrect signature is rejected."""
    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key1, body, signature="bad_signature_deadbeef" * 4)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 401
    assert "Invalid request signature" in response.text


@pytest.mark.asyncio
async def test_sec_5_valid_registered_key_with_valid_signature_succeeds(client: AsyncClient, setup_ad_data) -> None:
    """5. A valid registered key with a valid signature succeeds."""
    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "request_id" in data
    assert "ads" in data
    assert len(data["ads"]) >= 1


@pytest.mark.asyncio
async def test_sec_6_revoked_key_rejected(client: AsyncClient, setup_ad_data) -> None:
    """6. A revoked key is rejected (never falls back to anonymous)."""
    raw_key_rev = setup_ad_data["raw_key_rev"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key_rev, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 401
    assert "revoked" in response.text.lower()


@pytest.mark.asyncio
async def test_sec_7_cross_publisher_slot_impersonation_prevented(client: AsyncClient, setup_ad_data) -> None:
    """7. A key belonging to Publisher 1 cannot access Publisher 2's slot."""
    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_pub2", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 403
    assert "Not authorized for this slot" in response.text


@pytest.mark.asyncio
async def test_sec_8_payload_tampering_invalidates_signature(client: AsyncClient, setup_ad_data) -> None:
    """8. Altering a signed request after signature generation invalidates the signature."""
    raw_key1 = setup_ad_data["raw_key1"]
    original_body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key1, original_body)

    # Tampered body sent with original signature
    tampered_body = json.dumps({"slot_id": "slot_pub1", "num_ads": 2}).encode()
    response = await client.post("/api/v1/ad/request", content=tampered_body, headers=headers)
    assert response.status_code == 401
    assert "Invalid request signature" in response.text


@pytest.mark.asyncio
async def test_sec_9_auth_failures_do_not_mutate_state_or_fallback(client: AsyncClient, test_db, setup_ad_data) -> None:
    """9. Authentication failures do not trigger anonymous fallback or mutate advertiser budgets."""
    raw_key_rev = setup_ad_data["raw_key_rev"]
    adv_id = setup_ad_data["adv"].id

    adv_before = await test_db.get(Advertiser, adv_id)
    balance_before = adv_before.balance

    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key_rev, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 401

    adv_after = await test_db.get(Advertiser, adv_id)
    assert adv_after.balance == balance_before


@pytest.mark.asyncio
async def test_sec_10_authenticated_request_updates_last_used_at(client: AsyncClient, test_db, setup_ad_data) -> None:
    """10. A successful authenticated request updates last_used_at metadata correctly."""
    raw_key1 = setup_ad_data["raw_key1"]
    db_key1 = setup_ad_data["db_key1"]
    assert db_key1.last_used_at is None

    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 200

    # Refresh key from database and verify last_used_at was updated
    refreshed_key = await test_db.get(PublisherApiKey, db_key1.id)
    assert refreshed_key.last_used_at is not None


@pytest.mark.asyncio
async def test_sec_11_db_failure_fails_closed(client: AsyncClient, setup_ad_data) -> None:
    """11. Database failures fail closed on protected requests (never fallback to anonymous)."""
    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key1, body)

    with patch("liteads.common.security.resolve_publisher_api_key", side_effect=Exception("Database error")):
        response = await client.post("/api/v1/ad/request", content=body, headers=headers)
        assert response.status_code in (401, 500)
        assert "Authentication verification failed" in response.text


# ==============================================================================
# Public Ad-Request Contract Alignment & Privacy User ID Tests (Requirements 3 & 4)
# ==============================================================================

@pytest.mark.asyncio
async def test_contract_nested_user_payload_succeeds(client: AsyncClient, setup_ad_data) -> None:
    """Documented Phase 5 nested user payload succeeds and maps user.id, sector_context, geo."""
    raw_key1 = setup_ad_data["raw_key1"]
    valid_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    payload = {
        "slot_id": "slot_pub1",
        "num_ads": 1,
        "user": {
            "id": valid_sha256,
            "sector_context": ["developer"],
            "geo": {
                "country": "IN",
                "state": "Maharashtra",
                "city": "Mumbai",
                "pincode": "400001",
            },
        },
    }
    body = json.dumps(payload).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "request_id" in data
    assert len(data["ads"]) >= 1


@pytest.mark.asyncio
async def test_contract_uppercase_user_id_normalized(client: AsyncClient, setup_ad_data) -> None:
    """Uppercase 64-character SHA-256 hex user.id is normalized to lowercase."""
    raw_key1 = setup_ad_data["raw_key1"]
    upper_sha256 = "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855"
    payload = {
        "slot_id": "slot_pub1",
        "num_ads": 1,
        "user": {
            "id": upper_sha256,
            "sector_context": ["developer"],
        },
    }
    body = json.dumps(payload).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_privacy_short_user_id_rejected(client: AsyncClient, setup_ad_data) -> None:
    """user.id shorter than 64 characters is rejected with 422."""
    raw_key1 = setup_ad_data["raw_key1"]
    payload = {
        "slot_id": "slot_pub1",
        "num_ads": 1,
        "user": {
            "id": "short_user_id",
        },
    }
    body = json.dumps(payload).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 422
    assert "64-character hexadecimal" in response.text


@pytest.mark.asyncio
async def test_privacy_non_hex_user_id_rejected(client: AsyncClient, setup_ad_data) -> None:
    """user.id containing non-hex characters (even if 64 chars long) is rejected with 422."""
    raw_key1 = setup_ad_data["raw_key1"]
    non_hex_64 = "z" * 64
    payload = {
        "slot_id": "slot_pub1",
        "num_ads": 1,
        "user": {
            "id": non_hex_64,
        },
    }
    body = json.dumps(payload).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 422
    assert "64-character hexadecimal" in response.text


@pytest.mark.asyncio
async def test_privacy_empty_user_id_rejected(client: AsyncClient, setup_ad_data) -> None:
    """Empty string user.id is rejected with 422."""
    raw_key1 = setup_ad_data["raw_key1"]
    payload = {
        "slot_id": "slot_pub1",
        "num_ads": 1,
        "user": {
            "id": "",
        },
    }
    body = json.dumps(payload).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_privacy_pii_email_user_id_rejected(client: AsyncClient, setup_ad_data) -> None:
    """Raw email address passed as user.id is rejected with 422."""
    raw_key1 = setup_ad_data["raw_key1"]
    payload = {
        "slot_id": "slot_pub1",
        "num_ads": 1,
        "user": {
            "id": "alice.smith@corporate-domain.com",
        },
    }
    body = json.dumps(payload).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_anonymous_request_without_auth_headers_succeeds(client: AsyncClient, setup_ad_data) -> None:
    """Anonymous headerless requests are rejected with HTTP 403 and never reach candidate retrieval or billing."""
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()
    headers = {"Content-Type": "application/json"}

    with patch("liteads.ad_server.services.ad_service.AdService.serve_ads", new_callable=AsyncMock) as mock_serve:
        response = await client.post("/api/v1/ad/request", content=body, headers=headers)
        assert response.status_code == 403
        assert "missing Origin or Referer" in response.json()["detail"]
        mock_serve.assert_not_called()

    # Verify no tracking URLs are issued
    data = response.json()
    assert "ads" not in data or not data.get("ads")

    # Verify no billable event can be created
    track_res = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": "req_unauthorized_headerless",
            "ad_id": "ad_1_1",
            "event_type": "impression",
            "sig": "invalid_forged_sig",
            "slot_id": "slot_pub1",
        },
    )
    assert track_res.status_code in (400, 403)


@pytest.mark.asyncio
async def test_method_or_path_tampering_invalidates_bound_signature(client: AsyncClient, setup_ad_data) -> None:
    """Altering the path bound into the HMAC signature invalidates it."""
    raw_key1 = setup_ad_data["raw_key1"]
    payload = {"slot_id": "slot_pub1", "num_ads": 1}
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    body_sha = hashlib.sha256(body).hexdigest()
    # Sign over GET or a different path
    tampered_msg = f"POST|/api/v1/event/track|{ts}|{body_sha}".encode()
    bad_sig = hmac.new(raw_key1.encode(), tampered_msg, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": raw_key1,
        "X-Timestamp": ts,
        "X-Signature": bad_sig,
    }

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 401
    assert "Invalid request signature" in response.text


@pytest.mark.asyncio
async def test_anonymous_to_chat_surface_rejected_with_401(client: AsyncClient, test_db, setup_ad_data) -> None:
    """Anonymous requests cannot access private / chat surface slots."""
    chat_slot = AdSlot(
        publisher_id=setup_ad_data["pub1"].id,
        slot_id="slot_chat_private",
        name="Private Chat Surface",
        format=2,
        status=1,
        is_chat_surface=True,
    )
    test_db.add(chat_slot)
    await test_db.commit()

    body = json.dumps({"slot_id": "slot_chat_private", "num_ads": 1}).encode()
    headers = {"Content-Type": "application/json"}

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 401
    assert "Authentication required for chat surface slots" in response.text


@pytest.mark.asyncio
async def test_authenticated_to_chat_surface_succeeds(client: AsyncClient, test_db, setup_ad_data) -> None:
    """Authenticated requests can access chat surface slots owned by the publisher."""
    chat_slot = AdSlot(
        publisher_id=setup_ad_data["pub1"].id,
        slot_id="slot_chat_private_auth",
        name="Private Chat Surface",
        format=2,
        status=1,
        is_chat_surface=True,
    )
    test_db.add(chat_slot)
    await test_db.commit()

    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_chat_private_auth", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 200
    assert "ads" in response.json()


@pytest.mark.asyncio
async def test_inactive_slot_rejected_with_400(client: AsyncClient, test_db, setup_ad_data) -> None:
    """Requests targeting inactive slots are rejected with 400."""
    inactive_slot = AdSlot(
        publisher_id=setup_ad_data["pub1"].id,
        slot_id="slot_inactive_test",
        name="Inactive Slot",
        format=1,
        status=0,  # INACTIVE
    )
    test_db.add(inactive_slot)
    await test_db.commit()

    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_inactive_test", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 400
    assert "Ad slot is not active" in response.text


@pytest.mark.asyncio
async def test_nonexistent_slot_rejected_with_404(client: AsyncClient, setup_ad_data) -> None:
    """Authenticated requests targeting non-existent slots are rejected with 404."""
    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_does_not_exist_xyz", "num_ads": 1}).encode()
    headers = make_hmac_headers(raw_key1, body)

    response = await client.post("/api/v1/ad/request", content=body, headers=headers)
    assert response.status_code == 404
    assert "Ad slot not found" in response.text


def test_redis_cache_key_defense_in_depth_hashes_raw_identifiers() -> None:
    """Redis frequency keys sanitize raw identifiers and never store raw emails/PII."""
    from liteads.common.cache import CacheKeys
    raw_email = "victim.user@personal-domain.com"
    key = CacheKeys.freq_daily(raw_email, 10, "20261013")
    assert raw_email not in key
    assert "@" not in key
    # Key must contain a 64-char sha256 hash
    parts = key.split(":")
    assert len(parts[2]) == 64
    assert parts[2] == hashlib.sha256(raw_email.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_legacy_format_requires_explicit_version_header(client: AsyncClient, setup_ad_data) -> None:
    """Legacy v1 format without version header is rejected; with X-Signature-Version: 1 it succeeds."""
    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # 1. Legacy format WITHOUT version header -> rejected (must use v2 by default)
    headers_unversioned = make_hmac_headers(raw_key1, body, version="1")
    # Deliberately remove the version header
    headers_unversioned.pop("X-Signature-Version", None)
    res1 = await client.post("/api/v1/ad/request", content=body, headers=headers_unversioned)
    assert res1.status_code == 401
    assert "Invalid request signature" in res1.text

    # 2. Legacy format WITH X-Signature-Version: 1 -> accepted during controlled migration
    headers_v1 = make_hmac_headers(raw_key1, body, version="1")
    res2 = await client.post("/api/v1/ad/request", content=body, headers=headers_v1)
    assert res2.status_code == 200
    assert "ads" in res2.json()


@pytest.mark.asyncio
async def test_timestamp_boundary_valid_inside_edges(client: AsyncClient, setup_ad_data) -> None:
    """Timestamps at 295s in past and 295s in future are within the 300s window and succeed."""
    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Inside lower boundary (past: -295s)
    ts_past = int(time.time()) - 295
    headers_past = make_hmac_headers(raw_key1, body, timestamp=ts_past)
    res_past = await client.post("/api/v1/ad/request", content=body, headers=headers_past)
    assert res_past.status_code == 200

    # Inside upper boundary (future: +295s)
    ts_future = int(time.time()) + 295
    headers_future = make_hmac_headers(raw_key1, body, timestamp=ts_future)
    res_future = await client.post("/api/v1/ad/request", content=body, headers=headers_future)
    assert res_future.status_code == 200


@pytest.mark.asyncio
async def test_timestamp_boundary_rejected_outside_edges(client: AsyncClient, setup_ad_data) -> None:
    """Timestamps at 305s in past and 305s in future are outside the 300s window and rejected with 401."""
    raw_key1 = setup_ad_data["raw_key1"]
    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # Outside lower boundary (-305s)
    ts_past = int(time.time()) - 305
    headers_past = make_hmac_headers(raw_key1, body, timestamp=ts_past)
    res_past = await client.post("/api/v1/ad/request", content=body, headers=headers_past)
    assert res_past.status_code == 401
    assert "outside the permitted 300-second window" in res_past.text

    # Outside upper boundary (+305s)
    ts_future = int(time.time()) + 305
    headers_future = make_hmac_headers(raw_key1, body, timestamp=ts_future)
    res_future = await client.post("/api/v1/ad/request", content=body, headers=headers_future)
    assert res_future.status_code == 401
    assert "outside the permitted 300-second window" in res_future.text


@pytest.mark.asyncio
async def test_key_rotation_and_old_credential_revocation(client: AsyncClient, test_db, setup_ad_data) -> None:
    """Creating a rotated replacement key and revoking the old key immediately prevents old key use."""
    pub1 = setup_ad_data["pub1"]
    old_key = setup_ad_data["db_key1"]
    raw_old_key = setup_ad_data["raw_key1"]

    body = json.dumps({"slot_id": "slot_pub1", "num_ads": 1}).encode()

    # 1. Old key works before rotation
    headers_old = make_hmac_headers(raw_old_key, body)
    r1 = await client.post("/api/v1/ad/request", content=body, headers=headers_old)
    assert r1.status_code == 200

    # 2. Rotate: create new key
    new_key_record, raw_new_key = await ApiKeyService.create_api_key(
        db=test_db,
        publisher_id=pub1.id,
        schema=ApiKeyCreate(name="Rotated Key v2", is_sandbox=False),
    )

    # 3. Revoke old key
    revoked = await ApiKeyService.revoke_api_key(test_db, publisher_id=pub1.id, key_id=old_key.id)
    assert revoked is True

    # 4. Old key is immediately rejected
    r_old_after = await client.post("/api/v1/ad/request", content=body, headers=headers_old)
    assert r_old_after.status_code == 401
    assert "API key is revoked" in r_old_after.text

    # 5. New key succeeds
    headers_new = make_hmac_headers(raw_new_key, body)
    r_new = await client.post("/api/v1/ad/request", content=body, headers=headers_new)
    assert r_new.status_code == 200


