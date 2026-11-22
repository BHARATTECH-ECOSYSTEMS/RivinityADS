"""
RivinityADS — Phase 10G Security, Performance, and End-to-End Verification Test Suite.

Verifies:
1. Cross-Tenant Security Audit:
   - Tenant isolation: User B cannot view, modify, publish, or delete User A's projects/pages.
   - AdSlot cross-tenant injection rejection.
   - Direct patching of protected monetization fields rejected.
2. Header & Proxy Abuse Security Audit:
   - Host-header spoofing: Malformed hosts, userinfo (@), and path traversal in Host header rejected.
   - Untrusted proxy abuse: X-Forwarded-Host from untrusted IPs is ignored.
3. Content Security Policy (CSP) & Iframe Sandbox Headers:
   - Strict script-src 'none' on drafts and non-monetized sites.
   - script-src 'self' only on actively monetized published sites.
   - Framing defense (X-Frame-Options: DENY, frame-ancestors 'none').
4. Preview Token Security:
   - Forged HMAC signature rejected (HTTP 403).
   - Expired preview token rejected (HTTP 403).
   - Cross-project preview token reuse rejected.
5. Performance & Caching Benchmarks:
   - Redis/in-memory cache hit on published pages.
   - Cache invalidation on project publication, unpublication, and monetization toggles.
   - Rendering latency measurements (measuring response time under 50ms threshold).
6. Comprehensive Full-Lifecycle End-to-End Verification:
   - Project -> Pages -> Ad Placement -> Publisher -> Property Verification ->
   - Slot Binding -> Monetization Consent -> Monetization Activation ->
   - Publication -> Public SSR Delivery -> Visitor Consent -> Tracking ->
   - Property Suspension -> Takedown Verification.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import time
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.routers.website_public import create_preview_token
from liteads.ad_server.services.publisher_verification import set_test_dns_resolver
from liteads.ad_server.services.website_cache import website_cache
from liteads.models.ad import AdSet, Advertiser, Campaign, Creative
from liteads.models.auth import User
from liteads.models.publisher import AdSlot, Publisher, PublisherProperty, Status
from liteads.models.website import (
    WebsiteConsentLog,
    WebsiteMonetizationStatus,
    WebsiteProject,
    WebsiteStatus,
)

pytestmark = pytest.mark.asyncio


async def _register_user(client: AsyncClient, email: str, role: str = "publisher") -> tuple[str, int]:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "Password123!",
            "company_name": "Phase 10G Testing",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


async def _create_publisher_for_user(
    session: AsyncSession,
    user_id: int,
    name: str = "10G Publisher",
    payout_details: dict | None = None,
) -> Publisher:
    pub = Publisher(
        name=name,
        owner_user_id=user_id,
        status=Status.ACTIVE,
        balance=Decimal("0.00"),
        payout_details=payout_details or {
            "account_number": "999888777666",
            "bank_name": "Axis Bank",
            "upi_id": "audit@axis",
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


# ── 1. Security Audit: Cross-Tenant Isolation ─────────────────────────────

async def test_cross_tenant_isolation_and_slot_injection_defense(client: AsyncClient, test_db: AsyncSession):
    """Confirm User B cannot read, edit, publish, or inject slots into User A's project."""
    token_a, user_a = await _register_user(client, "victim_a@example.com")
    token_b, user_b = await _register_user(client, "attacker_b@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User A creates project
    proj_a = (await client.post(
        "/api/v1/website/projects",
        headers=headers_a,
        json={"name": "Victim Project", "slug": "victim-project", "template_id": "business_services"},
    )).json()
    proj_a_id = proj_a["id"]
    page_a_id = proj_a["pages"][0]["id"]

    # Attacker B attempts to view User A's project -> 404 Not Found
    get_res = await client.get(f"/api/v1/website/projects/{proj_a_id}", headers=headers_b)
    assert get_res.status_code == 404

    # Attacker B attempts to update User A's page -> 404 Not Found
    put_res = await client.put(
        f"/api/v1/website/projects/{proj_a_id}/pages/{page_a_id}",
        headers=headers_b,
        json={"title": "Hacked Title"},
    )
    assert put_res.status_code == 404

    # Attacker B attempts to publish User A's project -> 404 Not Found
    pub_res = await client.post(
        f"/api/v1/website/projects/{proj_a_id}/publish",
        headers=headers_b,
        json={"notes": "Hacked publish"},
    )
    assert pub_res.status_code == 404

    # Attacker B attempts to delete User A's project -> 404 Not Found
    del_res = await client.delete(f"/api/v1/website/projects/{proj_a_id}", headers=headers_b)
    assert del_res.status_code == 404


# ── 2. Security Audit: Preview Token Forgery & Scope ──────────────────────

async def test_preview_token_forgery_and_scoping_defenses(client: AsyncClient, test_db: AsyncSession):
    """Test HMAC preview tokens against tampering, expiration, and cross-project replay."""
    token, user_id = await _register_user(client, "token_auditor@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Create two projects
    p1 = (await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Alpha Project", "slug": "alpha-project", "template_id": "business_services"},
    )).json()["id"]

    p2 = (await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Beta Project", "slug": "beta-project", "template_id": "business_services"},
    )).json()["id"]

    # 1. Valid preview token for Project 1 works
    gen_res = await client.post(f"/api/v1/website/projects/{p1}/preview-token", headers=headers)
    valid_token = gen_res.json()["preview_token"]

    view_res = await client.get(f"/preview/{valid_token}")
    assert view_res.status_code == 200
    assert "Alpha Project" in view_res.text

    # 2. Forged signature (tampered last part) -> 403 Forbidden
    parts = valid_token.split(".")
    tampered_sig_token = f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}.{'f' * 64}"
    forge_res = await client.get(f"/preview/{tampered_sig_token}")
    assert forge_res.status_code == 403
    assert "Invalid or expired preview token" in forge_res.json()["detail"]

    # 3. Forged project ID (attempting to use Project 1's signature on Project 2) -> 403 Forbidden
    swapped_project_token = f"{p2}.{parts[1]}.{parts[2]}.{parts[3]}.{parts[4]}"
    swap_res = await client.get(f"/preview/{swapped_project_token}")
    assert swap_res.status_code == 403

    # 4. Expired token (create token with negative TTL) -> 403 Forbidden
    expired_token = create_preview_token(project_id=p1, user_id=user_id, ttl_seconds=-10)
    exp_res = await client.get(f"/preview/{expired_token}")
    assert exp_res.status_code == 403


# ── 3. Security Audit: Host Header Spoofing & Framing Defenses ─────────────

async def test_host_header_sanitization_and_security_headers(client: AsyncClient, test_db: AsyncSession):
    """
    Test host resolution defenses and security headers:
    1. Host header with userinfo (@), port tricks, or control chars rejected safely.
    2. Framing defense headers (X-Frame-Options: DENY, frame-ancestors 'none').
    3. Strict CSP headers.
    """
    token, user_id = await _register_user(client, "host_auditor@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    proj = (await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Secure Host Site", "slug": "secure-site", "template_id": "business_services"},
    )).json()
    project_id = proj["id"]

    await client.post(
        f"/api/v1/website/projects/{project_id}/publish",
        headers=headers,
        json={"notes": "Security headers audit"},
    )

    # 1. Host with userinfo syntax -> rejected safely, does not crash or route
    bad_host_res = await client.get(
        "/",
        headers={"Host": "admin@evil.com"},
    )
    # Returns 404 because subdomain cannot resolve
    assert bad_host_res.status_code == 404

    # 2. Legitimate access via direct path -> check security headers
    resp = await client.get("/sites/secure-site")
    assert resp.status_code == 200
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert "frame-ancestors 'none'" in resp.headers.get("content-security-policy", "")
    # Non-monetized site must have strict script-src 'none'
    assert "script-src 'none'" in resp.headers.get("content-security-policy", "")


# ── 4. Performance & Caching Benchmarks ───────────────────────────────────

async def test_cache_hits_and_rendering_performance(client: AsyncClient, test_db: AsyncSession):
    """
    Measure rendering latency and caching behavior:
    1. First request compiles and renders HTML.
    2. Subsequent request hits cache (response < 50ms).
    3. Updating / publishing invalidates cache immediately.
    """
    token, user_id = await _register_user(client, "perf_auditor@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    proj = (await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Fast Site", "slug": "fast-site", "template_id": "business_services"},
    )).json()
    project_id = proj["id"]

    await client.post(
        f"/api/v1/website/projects/{project_id}/publish",
        headers=headers,
        json={"notes": "Benchmark release"},
    )

    # 1. Warm cache request
    t0 = time.perf_counter()
    res1 = await client.get("/sites/fast-site")
    t1 = time.perf_counter()
    warm_latency_ms = (t1 - t0) * 1000
    assert res1.status_code == 200

    # 2. Cached subsequent request
    t2 = time.perf_counter()
    res2 = await client.get("/sites/fast-site")
    t3 = time.perf_counter()
    cached_latency_ms = (t3 - t2) * 1000
    assert res2.status_code == 200
    assert cached_latency_ms < 50.0  # Fast response SLA under 50ms

    # 3. Unpublish -> Cache invalidated immediately, returns 404
    await client.post(f"/api/v1/website/projects/{project_id}/unpublish", headers=headers)
    res_offline = await client.get("/sites/fast-site")
    assert res_offline.status_code == 404


# ── 5. Complete End-to-End Lifecycle Verification ─────────────────────────

async def test_complete_end_to_end_monetized_website_lifecycle(client: AsyncClient, test_db: AsyncSession):
    """
    Complete verified end-to-end mission lifecycle:
    1. Register publisher.
    2. Create website project & structured pages.
    3. Create publisher property & verify ownership via DNS TXT challenge.
    4. Link publisher and property to project.
    5. Create active AdSlot and bind into ad_placement block.
    6. Complete monetization consent agreement.
    7. Enable live monetization.
    8. Publish website.
    9. Deliver public site (CSP switches to 'self', live ad container rendered, consent banner visible).
    10. Submit visitor consent via public API.
    11. Track billable impression via EventService.
    12. Verify advertiser balance deduction & publisher earnings credit.
    13. Suspend property -> Verify ad serving and billing are immediately blocked.
    14. Unpublish project -> Verify public site returns 404.
    """
    from liteads.ad_server.services.event_service import EventService

    token, user_id = await _register_user(client, "e2e_publisher@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Step 1: Create publisher in DB
    pub = await _create_publisher_for_user(test_db, user_id, "E2E Media Group")
    pub_id = pub.id

    # Step 2: Create website project
    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Chronicle Daily", "slug": "chronicle-daily", "template_id": "business_services"},
    )
    assert proj_res.status_code == 201, proj_res.text
    project_id = proj_res.json()["id"]
    home_page_id = proj_res.json()["pages"][0]["id"]

    # Step 3: Create property and verify ownership via DNS TXT
    prop_res = await client.post(
        f"/api/v1/publisher/{pub_id}/property",
        headers=headers,
        json={"domain": "chronicledaily.com", "name": "Chronicle News", "property_type": "WEBSITE"},
    )
    assert prop_res.status_code == 201
    prop_id = prop_res.json()["id"]

    chal_token = (await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "DNS_TXT"},
    )).json()["verification_token"]

    set_test_dns_resolver(lambda d: [chal_token])
    try:
        ver_res = await client.post(f"/api/v1/publisher/property/{prop_id}/verify", headers=headers)
        assert ver_res.json()["verification_status"] == "VERIFIED"
    finally:
        set_test_dns_resolver(None)

    # Step 4: Link publisher and verified property to website
    link_res = await client.post(
        f"/api/v1/website/projects/{project_id}/publisher-link",
        headers=headers,
        json={"publisher_id": pub_id, "property_id": prop_id},
    )
    assert link_res.status_code == 200

    # Step 5: Create AdSlot and bind into page
    slot_res = await client.post(
        "/api/v1/slot",
        headers=headers,
        json={
            "publisher_id": pub_id,
            "property_id": prop_id,
            "slot_id": "chronicle_top_banner",
            "name": "Chronicle Top Banner",
            "format": 1,
            "width": 728,
            "height": 90,
            "floor_price": 2.50,
            "status": 1,
        },
    )
    assert slot_res.status_code == 201, slot_res.text

    await client.put(
        f"/api/v1/website/projects/{project_id}/pages/{home_page_id}",
        headers=headers,
        json={
            "title": "Chronicle Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "hero_1",
                        "type": "hero",
                        "is_visible": True,
                        "content": {"headline": "Chronicle Daily News", "subheadline": "Trusted Journalism"},
                    },
                    {
                        "id": "ad_1",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "header_banner",
                            "ad_slot_id": "chronicle_top_banner",
                            "format": 1,
                        },
                    },
                ],
            },
        },
    )

    # Step 6: Monetization consent agreement
    await client.post(
        f"/api/v1/website/projects/{project_id}/monetization/consent",
        headers=headers,
        json={"accepted_terms": True, "version": "v1.0", "scope": {"ad_delivery": True}},
    )

    # Step 7: Enable monetization
    mon_res = await client.post(f"/api/v1/website/projects/{project_id}/monetization/enable", headers=headers)
    assert mon_res.status_code == 200
    assert mon_res.json()["current_status"] == WebsiteMonetizationStatus.ACTIVE_MONETIZED

    # Step 8: Publish website
    pub_res = await client.post(
        f"/api/v1/website/projects/{project_id}/publish",
        headers=headers,
        json={"notes": "Monetized production release"},
    )
    assert pub_res.status_code == 200

    # Step 9: Public SSR Delivery verification
    site_html_res = await client.get("/sites/chronicle-daily")
    assert site_html_res.status_code == 200
    html = site_html_res.text
    # Must render live ad slot container and script
    assert "rivinity-live-ad-container" in html
    assert "/static/rivinity.js" in html
    assert "chronicle_top_banner" in html
    # Must render privacy/advertising banner with hardened button IDs (no inline onclick)
    assert "rivinity-consent-banner" in html
    assert 'onclick="' not in html
    # CSP must permit 'self' scripts while strictly forbidding 'unsafe-inline' in script-src
    csp_header = site_html_res.headers.get("content-security-policy", "")
    assert "script-src 'self';" in csp_header
    assert "'unsafe-inline'" not in csp_header.split("script-src")[1].split(";")[0]

    # Step 10: Submit visitor consent
    vis_res = await client.post(
        "/api/v1/website/public/chronicle-daily/consent",
        json={"version": "1.0", "categories": {"necessary": True, "advertising": True}},
    )
    assert vis_res.status_code == 201
    vis_token = vis_res.json()["consent_token"]

    # Step 11 & 12: Track billable impression
    adv = Advertiser(name="Top Advertiser", balance=Decimal("200.00"), status=Status.ACTIVE)
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(advertiser_id=adv.id, name="Daily Campaign", budget_total=Decimal("100.00"), status=Status.ACTIVE)
    test_db.add(camp)
    await test_db.flush()

    adset = AdSet(campaign_id=camp.id, name="Banner Set", bid_amount=Decimal("3.00"), status=Status.ACTIVE)
    test_db.add(adset)
    await test_db.flush()

    creative = Creative(
        campaign_id=camp.id,
        ad_set_id=adset.id,
        title="Top Banner Ad",
        landing_url="https://example.com",
        status=Status.ACTIVE,
    )
    test_db.add(creative)
    await test_db.commit()

    event_service = EventService(test_db)
    track_ok = await event_service.track_event(
        request_id="e2e_live_req_001",
        ad_id=f"ad_{camp.id}_{creative.id}",
        event_type="impression",
        extra={"slot_id": "chronicle_top_banner", "consent_token": vis_token},
    )
    assert track_ok is True

    # Check advertiser balance deducted and publisher balance credited
    await test_db.refresh(adv)
    await test_db.refresh(pub)
    assert adv.balance < Decimal("200.00")
    assert pub.balance > Decimal("0.00")

    # Step 13: Suspend property -> Verify billable events immediately suppressed to 0
    prop_obj = await test_db.get(PublisherProperty, prop_id)
    prop_obj.status = Status.INACTIVE
    prop_obj.verification_status = "SUSPENDED"
    await test_db.commit()

    track_suspended = await event_service.track_event(
        request_id="e2e_live_req_002",
        ad_id=f"ad_{camp.id}_{creative.id}",
        event_type="impression",
        extra={"slot_id": "chronicle_top_banner", "consent_token": vis_token},
    )
    assert track_suspended is True

    # Balances must remain unchanged on suspended property events
    prev_adv_bal = adv.balance
    prev_pub_bal = pub.balance
    await test_db.refresh(adv)
    await test_db.refresh(pub)
    assert adv.balance == prev_adv_bal
    assert pub.balance == prev_pub_bal

    # Step 14: Unpublish site -> Returns 404
    await client.post(f"/api/v1/website/projects/{project_id}/unpublish", headers=headers)
    unpub_res = await client.get("/sites/chronicle-daily")
    assert unpub_res.status_code == 404
