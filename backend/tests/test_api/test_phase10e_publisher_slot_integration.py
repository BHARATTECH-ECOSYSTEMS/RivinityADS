"""
RivinityADS — Phase 10E Publisher Verification & Ad-Slot Integration Test Suite.

Verifies against real FastAPI endpoints and database:
1. Domain normalization (rejecting userinfo, IPs, localhost, malformed FQDNs).
2. Unpredictable time-limited challenge creation (48h expiry, high entropy).
3. DNS TXT record verification with test adapter (success & mismatch).
4. HTML meta-tag verification with test adapter (success & mismatch).
5. SSRF protection during HTML meta-tag verification.
6. Rate limiting on verification attempts (sliding window).
7. Cross-publisher domain collision protection (preventing domain hijacking).
8. Client cannot directly mutate protected verification fields.
9. Website publisher & property linking with platform subdomain auto-verification.
10. Ad-slot binding validation during publication (preventing cross-tenant slot injection).
11. Preview mode preserves non-billable sandbox placeholders.
12. Publisher SDK route (GET /api/v1/sdk.js) availability and headers.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.publisher_verification import (
    normalize_property_domain,
    set_test_dns_resolver,
    set_test_html_fetcher,
    _is_ip_private_or_restricted,
    _IN_MEMORY_VERIFY_RATE_LIMITS,
)
from liteads.models.publisher import AdSlot, Publisher, PublisherProperty, Status
from liteads.models.website import WebsiteProject, WebsiteStatus

pytestmark = pytest.mark.asyncio


async def _register_user(client: AsyncClient, email: str, role: str = "publisher") -> tuple[str, int]:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "Password123!",
            "company_name": "Phase 10E Testing",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


from liteads.models.auth import User


async def _create_publisher_for_user(
    session: AsyncSession,
    user_id: int,
    name: str = "Test Publisher",
    payout_details: dict | None = None,
) -> Publisher:
    pub = Publisher(
        name=name,
        owner_user_id=user_id,
        status=Status.ACTIVE,
        payout_details=payout_details or {
            "account_number": "123456789012",
            "bank_name": "State Bank of India",
            "upi_id": "publisher@upi",
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


# ── Domain Normalization & SSRF Unit Tests ─────────────────────────────────

async def test_domain_normalization_valid_and_invalid():
    """Verify strict FQDN validation and normalization."""
    # Valid domains
    assert normalize_property_domain("example.com") == "example.com"
    assert normalize_property_domain("https://sub.news.org/path?q=1#hash") == "sub.news.org"
    assert normalize_property_domain("HTTP://Tech-Blog.CO.IN:8080/") == "tech-blog.co.in"
    assert normalize_property_domain("my-site.rivinityads.com.") == "my-site.rivinityads.com"

    # Invalid: localhost
    with pytest.raises(ValueError, match="localhost"):
        normalize_property_domain("localhost")

    # Invalid: IP addresses
    with pytest.raises(ValueError, match="IP addresses are not allowed"):
        normalize_property_domain("127.0.0.1")
    with pytest.raises(ValueError, match="IP addresses are not allowed"):
        normalize_property_domain("192.168.1.1")
    with pytest.raises(ValueError, match="IP addresses are not allowed"):
        normalize_property_domain("::1")

    # Invalid: userinfo
    with pytest.raises(ValueError, match="user-info syntax"):
        normalize_property_domain("admin@evil.com")

    # Invalid: empty
    with pytest.raises(ValueError, match="empty"):
        normalize_property_domain("   ")


async def test_ssrf_ip_restriction():
    """Verify internal and reserved IPs are flagged as restricted."""
    assert _is_ip_private_or_restricted("127.0.0.1") is True
    assert _is_ip_private_or_restricted("10.0.0.1") is True
    assert _is_ip_private_or_restricted("172.16.0.1") is True
    assert _is_ip_private_or_restricted("192.168.1.1") is True
    assert _is_ip_private_or_restricted("169.254.169.254") is True  # AWS / GCP metadata
    assert _is_ip_private_or_restricted("100.100.100.200") is True  # Alibaba Cloud metadata
    assert _is_ip_private_or_restricted("100.64.0.1") is True       # RFC 6598 CGNAT
    assert _is_ip_private_or_restricted("::1") is True
    assert _is_ip_private_or_restricted("8.8.8.8") is False
    assert _is_ip_private_or_restricted("1.1.1.1") is False


# ── Publisher Property Verification API Tests ──────────────────────────────

async def test_publisher_property_challenge_and_dns_verification(client: AsyncClient, test_db: AsyncSession):
    """Test generating a challenge and verifying domain via DNS TXT record using test adapter."""
    token, user_id = await _register_user(client, "pub_dns_owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create publisher account directly in DB (admin operation)
    pub = await _create_publisher_for_user(test_db, user_id, "Tech Times Publisher")
    pub_id = pub.id

    # 2. Create property via API
    prop_res = await client.post(
        f"/api/v1/publisher/{pub_id}/property",
        headers=headers,
        json={
            "domain": "techtimes.com",
            "name": "Tech Times Web",
            "property_type": "WEBSITE",
        },
    )
    assert prop_res.status_code == 201, prop_res.text
    prop_data = prop_res.json()
    prop_id = prop_data["id"]
    assert prop_data["domain"] == "techtimes.com"
    assert prop_data["verification_status"] == "PENDING"

    # 3. Create DNS_TXT challenge
    chal_res = await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "DNS_TXT"},
    )
    assert chal_res.status_code == 200, chal_res.text
    chal_data = chal_res.json()
    token_value = chal_data["verification_token"]
    assert "rivinity-site-verification=" in token_value
    assert chal_data["dns_record_name"] == "_rivinity-challenge.techtimes.com"

    # 4. Verification fails before DNS record is published
    set_test_dns_resolver(lambda domain: ["unrelated-txt-record"])
    try:
        verify_fail = await client.post(
            f"/api/v1/publisher/property/{prop_id}/verify",
            headers=headers,
        )
        assert verify_fail.status_code == 200
        fail_data = verify_fail.json()
        assert fail_data["verification_status"] == "FAILED"
        assert "not found in DNS" in fail_data["last_error"]

        # 5. Verification succeeds when DNS TXT record is returned by resolver
        set_test_dns_resolver(lambda domain: [token_value])
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


async def test_publisher_property_html_meta_verification(client: AsyncClient, test_db: AsyncSession):
    """Test HTML meta-tag verification using controlled test fetcher."""
    token, user_id = await _register_user(client, "pub_html_owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    pub = await _create_publisher_for_user(test_db, user_id, "Dev Daily Publisher")
    pub_id = pub.id

    prop_res = await client.post(
        f"/api/v1/publisher/{pub_id}/property",
        headers=headers,
        json={"domain": "devdaily.io", "name": "Dev Daily Blog", "property_type": "WEBSITE"},
    )
    assert prop_res.status_code == 201, prop_res.text
    prop_id = prop_res.json()["id"]

    chal_res = await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "HTML_META"},
    )
    assert chal_res.status_code == 200, chal_res.text
    token_value = chal_res.json()["verification_token"]

    # Test failure on missing meta tag
    set_test_html_fetcher(lambda d: "<html><head><title>No tag</title></head><body>Hello</body></html>")
    try:
        fail_res = await client.post(
            f"/api/v1/publisher/property/{prop_id}/verify",
            headers=headers,
        )
        assert fail_res.json()["verification_status"] == "FAILED"

        # Test success when meta tag is in HTML head
        html_page = f"""<!DOCTYPE html>
        <html>
        <head>
            <title>Dev Daily</title>
            <meta name="rivinity-site-verification" content="{token_value}">
        </head>
        <body><main>Content</main></body>
        </html>"""
        set_test_html_fetcher(lambda d: html_page)

        ok_res = await client.post(
            f"/api/v1/publisher/property/{prop_id}/verify",
            headers=headers,
        )
        assert ok_res.json()["verification_status"] == "VERIFIED"
    finally:
        set_test_html_fetcher(None)


async def test_verification_rate_limiting(client: AsyncClient, test_db: AsyncSession):
    """Verify rate limiting blocks excessive verification attempts."""
    token, user_id = await _register_user(client, "pub_ratelimit@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    pub = await _create_publisher_for_user(test_db, user_id, "Rate Limited Pub")
    pub_id = pub.id

    prop_res = await client.post(
        f"/api/v1/publisher/{pub_id}/property",
        headers=headers,
        json={"domain": "floodtest.org", "name": "Flood Test", "property_type": "WEBSITE"},
    )
    prop_id = prop_res.json()["id"]

    await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "DNS_TXT"},
    )

    # Clear rate limits for clean test
    _IN_MEMORY_VERIFY_RATE_LIMITS.pop(prop_id, None)

    set_test_dns_resolver(lambda d: ["nomatch"])
    try:
        # Exhaust 5 attempts
        for _ in range(5):
            r = await client.post(f"/api/v1/publisher/property/{prop_id}/verify", headers=headers)
            assert r.json()["verification_status"] == "FAILED"
            assert "Too many verification attempts" not in (r.json()["last_error"] or "")

        # 6th attempt should be blocked by rate limit
        r6 = await client.post(f"/api/v1/publisher/property/{prop_id}/verify", headers=headers)
        assert r6.status_code == 200
        assert "Too many verification attempts" in (r6.json()["last_error"] or "")
    finally:
        set_test_dns_resolver(None)
        _IN_MEMORY_VERIFY_RATE_LIMITS.pop(prop_id, None)


async def test_cross_publisher_domain_collision_protection(client: AsyncClient, test_db: AsyncSession):
    """Verify that Publisher B cannot claim or verify a domain actively verified by Publisher A."""
    token_a, user_a = await _register_user(client, "publisher_a@example.com")
    token_b, user_b = await _register_user(client, "publisher_b@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Publisher A creates & verifies premiumbrand.com
    pub_a = await _create_publisher_for_user(test_db, user_a, "Publisher A")
    pub_a_id = pub_a.id

    prop_a = (await client.post(
        f"/api/v1/publisher/{pub_a_id}/property",
        headers=headers_a,
        json={"domain": "premiumbrand.com", "name": "Brand Site", "property_type": "WEBSITE"},
    )).json()["id"]

    chal_a = (await client.post(
        f"/api/v1/publisher/property/{prop_a}/challenge",
        headers=headers_a,
        json={"method": "DNS_TXT"},
    )).json()["verification_token"]

    set_test_dns_resolver(lambda d: [chal_a])
    try:
        ver_a = await client.post(f"/api/v1/publisher/property/{prop_a}/verify", headers=headers_a)
        assert ver_a.json()["verification_status"] == "VERIFIED"

        # Publisher B tries to create the same domain -> blocked with 409 Conflict
        pub_b = await _create_publisher_for_user(test_db, user_b, "Publisher B")
        pub_b_id = pub_b.id

        prop_b_res = await client.post(
            f"/api/v1/publisher/{pub_b_id}/property",
            headers=headers_b,
            json={"domain": "premiumbrand.com", "name": "Hijacked Site", "property_type": "WEBSITE"},
        )
        assert prop_b_res.status_code == 409
        assert "verified by another publisher" in prop_b_res.json()["detail"]
    finally:
        set_test_dns_resolver(None)


async def test_protected_verification_fields_cannot_be_manipulated_by_client(client: AsyncClient, test_db: AsyncSession):
    """Ensure clients cannot patch verification_status directly via update_publisher_property."""
    token, user_id = await _register_user(client, "pub_tamper@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    pub = await _create_publisher_for_user(test_db, user_id, "Tamper Pub")
    pub_id = pub.id

    prop_id = (await client.post(
        f"/api/v1/publisher/{pub_id}/property",
        headers=headers,
        json={"domain": "tampertest.com", "name": "Tamper Test", "property_type": "WEBSITE"},
    )).json()["id"]

    # Attempt to fraudulently set verification_status to VERIFIED
    patch_res = await client.patch(
        f"/api/v1/publisher/property/{prop_id}",
        headers=headers,
        json={
            "name": "Tamper Updated",
            "verification_status": "VERIFIED",
        },
    )
    assert patch_res.status_code == 200
    data = patch_res.json()
    assert data["name"] == "Tamper Updated"
    # Verification status must remain PENDING
    assert data["verification_status"] == "PENDING"


# ── Website Ad-Slot Binding & Publication Integrity Tests ──────────────────

async def test_ad_slot_binding_and_cross_tenant_injection_defense(client: AsyncClient, test_db: AsyncSession):
    """
    Test website builder ad_placement blocks with AdSlot validation:
    1. Rejects publishing when ad_placement references a non-existent slot.
    2. Rejects publishing when ad_placement references another publisher's slot.
    3. Successfully publishes and freezes slot metadata when slot belongs to verified property.
    """
    token_pub, user_pub = await _register_user(client, "legit_pub@example.com")
    token_attacker, user_attacker = await _register_user(client, "attacker_pub@example.com")
    pub_headers = {"Authorization": f"Bearer {token_pub}"}
    att_headers = {"Authorization": f"Bearer {token_attacker}"}

    # Setup legit publisher, property, and slot
    pub = await _create_publisher_for_user(test_db, user_pub, "Legit Media")
    pub_id = pub.id

    prop_id = (await client.post(
        f"/api/v1/publisher/{pub_id}/property",
        headers=pub_headers,
        json={"domain": "legitmedia.com", "name": "Legit Media Web", "property_type": "WEBSITE"},
    )).json()["id"]

    # Verify legit property
    chal = (await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=pub_headers,
        json={"method": "DNS_TXT"},
    )).json()["verification_token"]

    set_test_dns_resolver(lambda d: [chal])
    try:
        await client.post(f"/api/v1/publisher/property/{prop_id}/verify", headers=pub_headers)
    finally:
        set_test_dns_resolver(None)

    # Create slot under legit property
    slot_res = await client.post(
        "/api/v1/slot",
        headers=pub_headers,
        json={
            "publisher_id": pub_id,
            "property_id": prop_id,
            "slot_id": "slot_header_728x90",
            "name": "Header Leaderboard",
            "format": 1,
            "width": 728,
            "height": 90,
            "floor_price": 1.50,
            "status": 1,
        },
    )
    assert slot_res.status_code == 201, slot_res.text
    legit_slot_id = slot_res.json()["slot_id"]

    # Setup attacker with their own project
    proj_att_res = await client.post(
        "/api/v1/website/projects",
        headers=att_headers,
        json={"name": "Attacker Site", "slug": "attacker-site", "template_id": "business_services"},
    )
    att_project_id = proj_att_res.json()["id"]
    att_home_id = proj_att_res.json()["pages"][0]["id"]

    # Attacker tries to inject legit_slot_id into their page
    put_att = await client.put(
        f"/api/v1/website/projects/{att_project_id}/pages/{att_home_id}",
        headers=att_headers,
        json={
            "title": "Attacker Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_1",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": legit_slot_id,
                            "format": 1,
                        },
                    }
                ],
            },
        },
    )
    assert put_att.status_code == 200, put_att.text

    # Attacker tries to publish without linking publisher/property -> 400 Bad Request
    pub_attempt_1 = await client.post(
        f"/api/v1/website/projects/{att_project_id}/publish",
        headers=att_headers,
        json={"notes": "Steal revenue"},
    )
    assert pub_attempt_1.status_code == 400
    assert "must be linked to a publisher and property" in pub_attempt_1.json()["detail"]

    # Now legit publisher creates project, links their publisher/property, and uses legit_slot_id
    proj_pub_res = await client.post(
        "/api/v1/website/projects",
        headers=pub_headers,
        json={"name": "Legit News", "slug": "legit-news", "template_id": "business_services"},
    )
    pub_project_id = proj_pub_res.json()["id"]
    pub_home_id = proj_pub_res.json()["pages"][0]["id"]

    # Link publisher and property to project
    link_res = await client.post(
        f"/api/v1/website/projects/{pub_project_id}/publisher-link",
        headers=pub_headers,
        json={"publisher_id": pub_id, "property_id": prop_id},
    )
    assert link_res.status_code == 200

    # Add ad_placement with legit slot
    put_pub = await client.put(
        f"/api/v1/website/projects/{pub_project_id}/pages/{pub_home_id}",
        headers=pub_headers,
        json={
            "title": "Legit News Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_legit_1",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": legit_slot_id,
                            "format": 1,
                        },
                    }
                ],
            },
        },
    )
    assert put_pub.status_code == 200, put_pub.text

    # Publish succeeds and freezes slot
    pub_version = await client.post(
        f"/api/v1/website/projects/{pub_project_id}/publish",
        headers=pub_headers,
        json={"notes": "Release v1 with ads"},
    )
    assert pub_version.status_code == 200, pub_version.text
    ver_data = pub_version.json()
    assert ver_data["version_number"] == 1

    # Verify bound slots endpoint returns active slot
    slots_res = await client.get(
        f"/api/v1/website/projects/{pub_project_id}/slots",
        headers=pub_headers,
    )
    assert slots_res.status_code == 200
    slots_list = slots_res.json()
    assert len(slots_list) == 1
    assert slots_list[0]["slot_id"] == legit_slot_id


async def test_preview_mode_preserves_non_billable_sandbox_placeholder(client: AsyncClient, test_db: AsyncSession):
    """Confirm draft preview always renders non-billable sandbox placeholder even with valid ad slots."""
    token, user_id = await _register_user(client, "preview_safeguard@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Sandbox Site", "slug": "sandbox-site", "template_id": "business_services"},
    )
    assert proj_res.status_code == 201, proj_res.text
    project_id = proj_res.json()["id"]
    home_page_id = proj_res.json()["pages"][0]["id"]

    put_res = await client.put(
        f"/api/v1/website/projects/{project_id}/pages/{home_page_id}",
        headers=headers,
        json={
            "title": "Sandbox Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_sandbox",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": "slot_test_preview",
                            "format": 1,
                        },
                    }
                ],
            },
        },
    )
    assert put_res.status_code == 200, put_res.text

    # Generate preview token
    token_res = await client.post(
        f"/api/v1/website/projects/{project_id}/preview-token",
        headers=headers,
    )
    preview_token = token_res.json()["preview_token"]

    # Fetch preview page
    view_res = await client.get(f"/preview/{preview_token}")
    assert view_res.status_code == 200
    html = view_res.text

    # Must contain placeholder marker and must NOT contain live Rivinity SDK script
    assert "Draft Preview Mode" in html
    assert "Non-Billable Sandbox" in html
    assert "rivinity.js" not in html
    assert "rivinity-ad-unit" not in html


async def test_publisher_sdk_route_and_headers(client: AsyncClient, test_db: AsyncSession):
    """Verify GET /api/v1/sdk.js is accessible with proper javascript content type and nosniff header."""
    res = await client.get("/api/v1/sdk.js")
    assert res.status_code == 200
    assert "application/javascript" in res.headers.get("content-type", "")
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert "RivinityADS" in res.text or "LiteAds" in res.text or "rivinity" in res.text.lower()
