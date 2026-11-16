"""Phase 3 — Publisher property verification and onboarding test suite.

Verifies:
1. Challenge generation outside website builder (standalone API).
2. DNS TXT record verification with test adapter (mismatch & success).
3. HTML meta tag verification with test adapter (mismatch & success).
4. Challenge expiry enforcement and token renewal.
5. Rate limiting on verification checks (max 5 checks in 15 min).
6. Cross-tenant isolation (Publisher A cannot view, challenge, or verify Publisher B's properties).
7. Cross-publisher domain conflict defense (Publisher B cannot verify a domain actively verified by Publisher A).
8. Ad serving gating (ad delivery returns 403 Forbidden when property is unverified; passes gate once verified).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.publisher_verification import (
    set_test_dns_resolver,
    set_test_html_fetcher,
    _IN_MEMORY_VERIFY_RATE_LIMITS,
)
from liteads.models.auth import User
from liteads.models.publisher import AdSlot, Publisher, PublisherProperty, Status

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def reset_verification_test_state():
    """Reset in-memory rate limiting state and mock resolvers before and after every test."""
    _IN_MEMORY_VERIFY_RATE_LIMITS.clear()
    set_test_dns_resolver(None)
    set_test_html_fetcher(None)
    yield
    _IN_MEMORY_VERIFY_RATE_LIMITS.clear()
    set_test_dns_resolver(None)
    set_test_html_fetcher(None)


async def _register_user(client: AsyncClient, email: str, role: str = "publisher") -> tuple[str, int]:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "Password123!",
            "company_name": "Phase 3 Testing",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


async def _create_publisher_for_user(
    session: AsyncSession,
    user_id: int,
    name: str = "Phase 3 Publisher",
) -> Publisher:
    pub = Publisher(
        name=name,
        owner_user_id=user_id,
        status=Status.ACTIVE,
        payout_details={
            "account_number": "123456789012",
            "bank_name": "State Bank of India",
            "upi_id": "phase3@upi",
            "status": "active",
        },
    )
    session.add(pub)
    await session.flush()

    user = await session.get(User, user_id)
    if user:
        user.publisher_id = pub.id
        session.add(user)

    await session.commit()
    await session.refresh(pub)
    return pub


async def test_standalone_challenge_generation(client: AsyncClient, test_db: AsyncSession):
    """Test generating standalone DNS_TXT and HTML_META challenges via management API."""
    token, user_id = await _register_user(client, "pub_challenge_test@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id, "Challenge Test Publisher")

    # 1. Create property via API
    prop_res = await client.post(
        f"/api/v1/publisher/{pub.id}/property",
        headers=headers,
        json={"name": "Independent Blog", "domain": "independentblog.org"},
    )
    assert prop_res.status_code == 201, prop_res.text
    prop_data = prop_res.json()
    prop_id = prop_data["id"]
    assert prop_data["verification_status"] == "PENDING"

    # 2. Issue DNS TXT challenge
    dns_res = await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "DNS_TXT"},
    )
    assert dns_res.status_code == 200, dns_res.text
    dns_data = dns_res.json()
    assert dns_data["verification_method"] == "DNS_TXT"
    assert "rivinity-site-verification=" in dns_data["verification_token"]
    assert dns_data["dns_record_name"] == "_rivinity-challenge.independentblog.org"
    assert dns_data["dns_record_value"] == dns_data["verification_token"]
    assert "challenge_expires_at" in dns_data

    # 3. Issue HTML META challenge
    html_res = await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "HTML_META"},
    )
    assert html_res.status_code == 200, html_res.text
    html_data = html_res.json()
    assert html_data["verification_method"] == "HTML_META"
    assert "rivinity-site-verification" in html_data["html_meta_tag"]


async def test_dns_txt_verification_lifecycle(client: AsyncClient, test_db: AsyncSession):
    """Test DNS TXT verification success and mismatch reporting."""
    token, user_id = await _register_user(client, "pub_dns_cycle@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id, "DNS Cycle Publisher")

    prop_res = await client.post(
        f"/api/v1/publisher/{pub.id}/property",
        headers=headers,
        json={"name": "DNS Cycle News", "domain": "dnscyclenews.com"},
    )
    assert prop_res.status_code == 201
    prop_id = prop_res.json()["id"]

    chal_res = await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "DNS_TXT"},
    )
    token_str = chal_res.json()["verification_token"]

    # Mismatch check
    set_test_dns_resolver(lambda domain: ["unrelated-token"])
    try:
        verify_fail = await client.post(
            f"/api/v1/publisher/property/{prop_id}/verify",
            headers=headers,
        )
        assert verify_fail.status_code == 200
        fail_data = verify_fail.json()
        assert fail_data["verification_status"] == "FAILED"
        assert "not found in DNS" in fail_data["last_error"]

        # Success check
        set_test_dns_resolver(lambda domain: [token_str])
        verify_ok = await client.post(
            f"/api/v1/publisher/property/{prop_id}/verify",
            headers=headers,
        )
        assert verify_ok.status_code == 200
        ok_data = verify_ok.json()
        assert ok_data["verification_status"] == "VERIFIED"
        assert ok_data["verified_at"] is not None
        assert ok_data["last_error"] is None
        assert ok_data["status"] == 1
    finally:
        set_test_dns_resolver(None)


async def test_html_meta_verification_lifecycle(client: AsyncClient, test_db: AsyncSession):
    """Test HTML Meta tag verification success and mismatch reporting."""
    token, user_id = await _register_user(client, "pub_html_cycle@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id, "HTML Cycle Publisher")

    prop_res = await client.post(
        f"/api/v1/publisher/{pub.id}/property",
        headers=headers,
        json={"name": "HTML Site", "domain": "htmlsiteportal.net"},
    )
    prop_id = prop_res.json()["id"]

    chal_res = await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "HTML_META"},
    )
    token_str = chal_res.json()["verification_token"]

    # Mismatch check
    set_test_html_fetcher(lambda domain: "<html><head><title>No Meta Tag</title></head></html>")
    try:
        verify_fail = await client.post(
            f"/api/v1/publisher/property/{prop_id}/verify",
            headers=headers,
        )
        assert verify_fail.status_code == 200
        fail_data = verify_fail.json()
        assert fail_data["verification_status"] == "FAILED"
        assert "Meta tag" in fail_data["last_error"]

        # Success check
        valid_html = f'<html><head><meta name="rivinity-site-verification" content="{token_str}"></head></html>'
        set_test_html_fetcher(lambda domain: valid_html)
        verify_ok = await client.post(
            f"/api/v1/publisher/property/{prop_id}/verify",
            headers=headers,
        )
        assert verify_ok.status_code == 200
        ok_data = verify_ok.json()
        assert ok_data["verification_status"] == "VERIFIED"
        assert ok_data["verified_at"] is not None
        assert ok_data["last_error"] is None
    finally:
        set_test_html_fetcher(None)


async def test_challenge_expiry_and_regeneration(client: AsyncClient, test_db: AsyncSession):
    """Test that expired challenges fail verification and can be regenerated."""
    token, user_id = await _register_user(client, "pub_expiry@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id, "Expiry Publisher")

    prop_res = await client.post(
        f"/api/v1/publisher/{pub.id}/property",
        headers=headers,
        json={"name": "Expiring Property", "domain": "expiringprop.org"},
    )
    prop_id = prop_res.json()["id"]

    # Issue challenge
    await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "DNS_TXT"},
    )

    # Force challenge expiration in database
    prop_obj = await test_db.get(PublisherProperty, prop_id)
    assert prop_obj is not None
    old_token = prop_obj.verification_token
    prop_obj.challenge_expires_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await test_db.commit()

    # Verify attempt on expired token must fail
    set_test_dns_resolver(lambda domain: [old_token])
    try:
        verify_exp = await client.post(
            f"/api/v1/publisher/property/{prop_id}/verify",
            headers=headers,
        )
        assert verify_exp.status_code == 200
        exp_data = verify_exp.json()
        assert exp_data["verification_status"] == "FAILED"
        assert "expired" in exp_data["last_error"].lower()

        # Regenerate challenge
        new_chal = await client.post(
            f"/api/v1/publisher/property/{prop_id}/challenge",
            headers=headers,
            json={"method": "DNS_TXT"},
        )
        assert new_chal.status_code == 200
        new_token = new_chal.json()["verification_token"]
        assert new_token != old_token

        # Verification succeeds with renewed token
        set_test_dns_resolver(lambda domain: [new_token])
        verify_ok = await client.post(
            f"/api/v1/publisher/property/{prop_id}/verify",
            headers=headers,
        )
        assert verify_ok.status_code == 200
        assert verify_ok.json()["verification_status"] == "VERIFIED"
    finally:
        set_test_dns_resolver(None)


async def test_verification_rate_limiting(client: AsyncClient, test_db: AsyncSession):
    """Test that excessive verification attempts within 15 minutes are throttled."""
    token, user_id = await _register_user(client, "pub_rate@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id, "Rate Limited Publisher")

    prop_res = await client.post(
        f"/api/v1/publisher/{pub.id}/property",
        headers=headers,
        json={"name": "Rate Site", "domain": "ratelimitedsite.io"},
    )
    prop_id = prop_res.json()["id"]

    await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "DNS_TXT"},
    )

    _IN_MEMORY_VERIFY_RATE_LIMITS.pop(prop_id, None)
    set_test_dns_resolver(lambda domain: ["unrelated"])
    try:
        # First 5 attempts return normal FAILED status
        for _ in range(5):
            r = await client.post(f"/api/v1/publisher/property/{prop_id}/verify", headers=headers)
            assert r.status_code == 200
            assert "not found in DNS" in r.json()["last_error"]

        # 6th attempt hits rate limit
        rate_hit = await client.post(f"/api/v1/publisher/property/{prop_id}/verify", headers=headers)
        assert rate_hit.status_code == 200
        assert "Too many verification attempts" in rate_hit.json()["last_error"]
    finally:
        set_test_dns_resolver(None)
        _IN_MEMORY_VERIFY_RATE_LIMITS.pop(prop_id, None)


async def test_cross_tenant_property_isolation(client: AsyncClient, test_db: AsyncSession):
    """Verify Publisher B cannot view, challenge, or verify Publisher A's property."""
    token_a, user_a = await _register_user(client, "pub_iso_a@example.com")
    pub_a = await _create_publisher_for_user(test_db, user_a, "Publisher A Corp")

    token_b, user_b = await _register_user(client, "pub_iso_b@example.com")
    pub_b = await _create_publisher_for_user(test_db, user_b, "Publisher B Corp")

    # Pub A creates a property
    prop_res = await client.post(
        f"/api/v1/publisher/{pub_a.id}/property",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Pub A Property", "domain": "pub-a-property.com"},
    )
    prop_a_id = prop_res.json()["id"]

    # Pub B attempts to issue challenge on Pub A's property -> 403
    cross_chal = await client.post(
        f"/api/v1/publisher/property/{prop_a_id}/challenge",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"method": "DNS_TXT"},
    )
    assert cross_chal.status_code == 403

    # Pub B attempts to verify Pub A's property -> 403
    cross_verify = await client.post(
        f"/api/v1/publisher/property/{prop_a_id}/verify",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert cross_verify.status_code == 403


async def test_cross_publisher_domain_collision_rejection(client: AsyncClient, test_db: AsyncSession):
    """Verify Publisher B cannot verify a domain that is actively verified by Publisher A."""
    token_a, user_a = await _register_user(client, "domain_owner_a@example.com")
    pub_a = await _create_publisher_for_user(test_db, user_a, "Owner A")

    token_b, user_b = await _register_user(client, "domain_claimant_b@example.com")
    pub_b = await _create_publisher_for_user(test_db, user_b, "Claimant B")

    shared_domain = "exclusive-brand.com"

    # Pub A creates and verifies property
    res_a = await client.post(
        f"/api/v1/publisher/{pub_a.id}/property",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Exclusive Brand Official", "domain": shared_domain},
    )
    prop_a_id = res_a.json()["id"]

    chal_a = await client.post(
        f"/api/v1/publisher/property/{prop_a_id}/challenge",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"method": "DNS_TXT"},
    )
    token_a_val = chal_a.json()["verification_token"]

    set_test_dns_resolver(lambda domain: [token_a_val])
    try:
        v_a = await client.post(
            f"/api/v1/publisher/property/{prop_a_id}/verify",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert v_a.json()["verification_status"] == "VERIFIED"

        # 1. Pub B attempts to register the verified domain -> 409 Conflict
        res_b = await client.post(
            f"/api/v1/publisher/{pub_b.id}/property",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"name": "Exclusive Brand Copycat", "domain": shared_domain},
        )
        assert res_b.status_code == 409
        assert "currently verified by another publisher" in res_b.json()["detail"]

        # 2. Test verify_property collision defense if Pub B registered before Pub A verified
        competing_domain = "race-domain.com"
        res_b_race = await client.post(
            f"/api/v1/publisher/{pub_b.id}/property",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"name": "Race Domain B", "domain": competing_domain},
        )
        prop_b_race_id = res_b_race.json()["id"]

        res_a_race = await client.post(
            f"/api/v1/publisher/{pub_a.id}/property",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"name": "Race Domain A", "domain": competing_domain},
        )
        prop_a_race_id = res_a_race.json()["id"]

        chal_a_race = await client.post(
            f"/api/v1/publisher/property/{prop_a_race_id}/challenge",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"method": "DNS_TXT"},
        )
        token_a_race = chal_a_race.json()["verification_token"]
        set_test_dns_resolver(lambda domain: [token_a_race])
        v_a_race = await client.post(
            f"/api/v1/publisher/property/{prop_a_race_id}/verify",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert v_a_race.json()["verification_status"] == "VERIFIED"

        chal_b_race = await client.post(
            f"/api/v1/publisher/property/{prop_b_race_id}/challenge",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"method": "DNS_TXT"},
        )
        token_b_race = chal_b_race.json()["verification_token"]
        set_test_dns_resolver(lambda domain: [token_b_race])
        v_b_race = await client.post(
            f"/api/v1/publisher/property/{prop_b_race_id}/verify",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert v_b_race.status_code == 200
        b_race_data = v_b_race.json()
        assert b_race_data["verification_status"] == "FAILED"
        assert "verified by another publisher" in b_race_data["last_error"]
    finally:
        set_test_dns_resolver(None)


async def test_ad_serving_gating_unverified_property(client: AsyncClient, test_db: AsyncSession):
    """Ad delivery must return HTTP 403 for slots attached to unverified properties."""
    token, user_id = await _register_user(client, "pub_gating@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id, "Gating Publisher")

    # 1. Create unverified property
    prop_res = await client.post(
        f"/api/v1/publisher/{pub.id}/property",
        headers=headers,
        json={"name": "Gating Media", "domain": "gatingmedia.com"},
    )
    prop_id = prop_res.json()["id"]

    # 2. Create ad slot bound to the unverified property
    slot_res = await client.post(
        "/api/v1/slot",
        headers=headers,
        json={
            "slot_id": "slot-gating-test",
            "name": "Gating Test Slot",
            "format": 1,
            "width": 300,
            "height": 250,
            "floor_price": 0.5,
            "property_id": prop_id,
        },
    )
    assert slot_res.status_code == 201, slot_res.text

    # 3. Ad request for slot on unverified property -> 403 Forbidden
    ad_req = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": "https://gatingmedia.com"},
        json={"slot_id": "slot-gating-test", "num_ads": 1},
    )
    assert ad_req.status_code == 403
    assert "Publisher property is not verified" in ad_req.json()["detail"]

    # 4. Verify property
    chal_res = await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "DNS_TXT"},
    )
    token_val = chal_res.json()["verification_token"]

    set_test_dns_resolver(lambda domain: [token_val])
    try:
        verify_res = await client.post(
            f"/api/v1/publisher/property/{prop_id}/verify",
            headers=headers,
        )
        assert verify_res.json()["verification_status"] == "VERIFIED"

        # 5. Ad request now passes the property verification gate (does not return 403)
        ad_req_verified = await client.post(
            "/api/v1/ad/request",
            headers={"Origin": "https://gatingmedia.com"},
            json={"slot_id": "slot-gating-test", "num_ads": 1},
        )
        # Should NOT be 403 (returns 200 with or without ad winners depending on active campaigns)
        assert ad_req_verified.status_code != 403
    finally:
        set_test_dns_resolver(None)

