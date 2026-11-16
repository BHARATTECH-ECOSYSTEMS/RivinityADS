"""
RivinityADS — Phase 1 Visitor Consent Gate Test Suite (P0).

Verifies all mandatory Phase 1 scenarios:
1. New visitor (no choice): No ad requests, impressions, clicks, or tracking occur.
2. Accept: Consent is recorded, signed proof is issued, and ad requests/tracking proceed.
3. Reject: Consent is recorded as denied, ad requests return 0 ads, persisting on subsequent requests.
4. Withdraw: Reopening consent settings and withdrawing immediately stops ad delivery & suppresses billing.
5. Tampering: Cross-site/project ID tampering or token payload tampering is strictly rejected (HTTP 403).
6. Defaults: Missing or malformed categories are denied by default; omitted fields never imply consent.
7. Preview: Sandbox/preview environments remain non-billable (cost = 0) regardless of visitor choice.
8. Bypasses: Arbitrary client booleans in direct API calls cannot bypass server-side consent/billing rules.
9. Idempotency: Duplicate identical consent submissions reuse existing records without database duplication.
"""

from __future__ import annotations

import base64
from decimal import Decimal
import hashlib
import json
import time
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.event_service import EventService
from liteads.ad_server.services.publisher_verification import (
    _IN_MEMORY_VERIFY_RATE_LIMITS,
    set_test_dns_resolver,
)
from liteads.common.cache import CacheKeys, redis_client
from liteads.common.tracking_sig import sign_consent_token, verify_consent_token
from liteads.models.ad import AdSet, Advertiser, Campaign, Creative
from liteads.models.auth import User
from liteads.models.base import ModerationStatus
from liteads.models.domain import AdvertiserDomain
from liteads.models.publisher import AdSlot, Publisher, PublisherProperty, Status
from liteads.models.website import (
    WebsiteConsentLog,
    WebsiteMonetizationStatus,
    WebsiteProject,
)

pytestmark = pytest.mark.asyncio


async def _setup_monetized_site(
    client: AsyncClient,
    test_db: AsyncSession,
    site_slug: str = "consent-news",
    slot_id: str = "consent_top_slot",
) -> dict:
    """Helper to provision a fully verified, active monetized website with bound ad slot."""
    _IN_MEMORY_VERIFY_RATE_LIMITS.clear()
    # Register publisher user
    user_email = f"pub_{site_slug}@example.com"
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": "Consent Publisher",
            "email": user_email,
            "password": "Password123!",
            "company_name": "Consent Media",
            "role": "publisher",
        },
    )
    token = res.json()["access_token"]
    user_id = res.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create publisher in DB
    pub = Publisher(
        name=f"Pub {site_slug}",
        owner_user_id=user_id,
        status=Status.ACTIVE,
        balance=Decimal("0.00"),
        payout_details={
            "account_number": "112233445566",
            "bank_name": "State Bank",
            "upi_id": "consent@sbi",
            "status": "active",
        },
    )
    test_db.add(pub)
    await test_db.flush()

    user = await test_db.get(User, user_id)
    if user:
        user.publisher_id = pub.id
        test_db.add(user)
    await test_db.commit()

    # Create website project
    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": f"Portal {site_slug}", "slug": site_slug, "template_id": "business_services"},
    )
    project_id = proj_res.json()["id"]
    page_id = proj_res.json()["pages"][0]["id"]

    # Create and verify property
    prop_domain = f"{site_slug}.org"
    prop_res = await client.post(
        f"/api/v1/publisher/{pub.id}/property",
        headers=headers,
        json={"domain": prop_domain, "name": "Consent Property", "property_type": "WEBSITE"},
    )
    prop_id = prop_res.json()["id"]

    chal_token = (await client.post(
        f"/api/v1/publisher/property/{prop_id}/challenge",
        headers=headers,
        json={"method": "DNS_TXT"},
    )).json()["verification_token"]

    set_test_dns_resolver(lambda d: [chal_token])
    try:
        ver_res = await client.post(f"/api/v1/publisher/property/{prop_id}/verify", headers=headers)
        assert ver_res.status_code == 200, ver_res.text
        assert ver_res.json()["verification_status"] == "VERIFIED"
    finally:
        set_test_dns_resolver(None)

    # Link publisher and property to project
    link_res = await client.post(
        f"/api/v1/website/projects/{project_id}/publisher-link",
        headers=headers,
        json={"publisher_id": pub.id, "property_id": prop_id},
    )
    assert link_res.status_code == 200, link_res.text

    # Create ad slot
    slot_res = await client.post(
        "/api/v1/slot",
        headers=headers,
        json={
            "publisher_id": pub.id,
            "property_id": prop_id,
            "slot_id": slot_id,
            "name": "Consent Top Slot",
            "format": 1,
            "width": 728,
            "height": 90,
            "floor_price": 1.00,
            "status": 1,
        },
    )
    assert slot_res.status_code == 201

    # Bind into page
    put_res = await client.put(
        f"/api/v1/website/projects/{project_id}/pages/{page_id}",
        headers=headers,
        json={
            "title": "Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_block_1",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {"placement_location": "header_banner", "ad_slot_id": slot_id, "format": 1},
                    }
                ],
            },
        },
    )
    assert put_res.status_code == 200, put_res.text

    # Monetization consent and enable
    c_res = await client.post(
        f"/api/v1/website/projects/{project_id}/monetization/consent",
        headers=headers,
        json={"accepted_terms": True, "version": "v1.0", "scope": {"ad_delivery": True}},
    )
    assert c_res.status_code == 200, c_res.text
    en_res = await client.post(f"/api/v1/website/projects/{project_id}/monetization/enable", headers=headers)
    assert en_res.status_code == 200, en_res.text
    pub_res = await client.post(f"/api/v1/website/projects/{project_id}/publish", headers=headers, json={"notes": "Init"})
    assert pub_res.status_code == 200, pub_res.text

    # Setup active advertiser and campaign
    adv = Advertiser(name=f"Adv {site_slug}", balance=Decimal("500.00"), status=Status.ACTIVE)
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="Consent Campaign",
        bid_type=1,
        bid_amount=Decimal("4.00"),
        budget_total=Decimal("200.00"),
        budget_daily=Decimal("100.00"),
        status=Status.ACTIVE,
    )
    test_db.add(camp)
    await test_db.flush()

    adset = AdSet(campaign_id=camp.id, name="Banner Set", bid_amount=Decimal("4.00"), status=Status.ACTIVE)
    test_db.add(adset)
    await test_db.flush()

    creative = Creative(
        campaign_id=camp.id,
        ad_set_id=adset.id,
        title="Consent Verified Banner",
        landing_url="https://example.com",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    test_db.add(creative)

    adv_domain = AdvertiserDomain(
        advertiser_id=adv.id,
        domain="example.com",
        status="VERIFIED",
        verification_token="test_token_verified",
    )
    test_db.add(adv_domain)
    await test_db.commit()

    if redis_client.connected:
        try:
            await redis_client.delete(CacheKeys.active_ads())
            await redis_client.delete("cache:active_campaigns")
        except Exception:
            pass

    return {
        "user_id": user_id,
        "token": token,
        "publisher_id": pub.id,
        "project_id": project_id,
        "property_id": prop_id,
        "prop_domain": prop_domain,
        "slot_id": slot_id,
        "site_slug": site_slug,
        "advertiser_id": adv.id,
        "campaign_id": camp.id,
        "creative_id": creative.id,
    }


# ── Scenario 1: New Visitor (No Choice) ───────────────────────────────────

async def test_scenario_1_new_visitor_no_choice(client: AsyncClient, test_db: AsyncSession):
    """
    Scenario 1: A new visitor arrives with no stored consent or token.
    - Public site renders consent banner with default denied notice.
    - Direct ad request without consent proof yields 0 ads.
    - Direct tracking event yields cost = 0 (no advertiser charge).
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="new-visitor-site", slot_id="new_vis_slot")

    # 1. Inspect public rendered HTML
    site_res = await client.get(f"/sites/{ctx['site_slug']}")
    assert site_res.status_code == 200
    html = site_res.text
    assert "rivinity-consent-banner" in html
    assert "data-site=\"new-visitor-site\"" in html
    assert "rivinity-consent-modal" in html
    assert "rivinity-consent-reopen" in html

    # 2. Ad request without consent token from an unconsented visitor
    ad_req_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1},
    )
    assert ad_req_res.status_code == 200
    data = ad_req_res.json()
    assert len(data["ads"]) == 0
    assert data["count"] == 0

    # 3. Direct event tracking on website placement without consent produces cost = 0
    event_service = EventService(test_db)
    track_res = await event_service.track_event(
        request_id="req_new_visitor_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"]},
    )
    assert track_res is True

    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00"), "Unconsented visitor must not cause advertiser spend"


# ── Scenario 2: Accept ────────────────────────────────────────────────────

async def test_scenario_2_accept_consent_lifecycle(client: AsyncClient, test_db: AsyncSession):
    """
    Scenario 2: Visitor explicitly accepts advertising consent.
    - Consent log recorded with SHA-256 IP/UA hashes.
    - Backend returns signed consent token.
    - Ad request with consent token succeeds and serves ads.
    - Billable impression successfully charges advertiser and credits publisher.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="accept-site", slot_id="accept_slot")

    # 1. Visitor submits Accept
    vis_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        headers={"User-Agent": "TestBrowser/1.0", "X-Forwarded-For": "198.51.100.42"},
        json={"version": "v1.0", "categories": {"necessary": True, "advertising": True, "analytics": True}},
    )
    assert vis_res.status_code == 201
    resp_data = vis_res.json()
    assert resp_data["is_granted"] is True
    consent_token = resp_data["consent_token"]
    assert consent_token is not None

    # Verify token payload
    token_data = verify_consent_token(consent_token, expected_site_slug=ctx["site_slug"])
    assert token_data["is_granted"] is True
    assert token_data["project_id"] == ctx["project_id"]

    # Verify DB privacy hashing
    log_stmt = select(WebsiteConsentLog).where(WebsiteConsentLog.id == resp_data["consent_id"])
    log = (await test_db.execute(log_stmt)).scalar_one()
    assert log.is_granted is True
    assert log.client_ip_hash == hashlib.sha256("198.51.100.42".encode()).hexdigest()
    assert "198.51.100.42" not in (log.client_ip_hash or "")

    # 2. Ad request with valid consent token returns ads
    ad_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": consent_token},
    )
    assert ad_res.status_code == 200
    ads_data = ad_res.json()
    assert len(ads_data["ads"]) >= 1
    assert ads_data["ads"][0]["campaign_id"] == ctx["campaign_id"]
    assert ads_data["ads"][0]["creative_id"] == ctx["creative_id"]

    # 3. Billable impression with consent token deducts balance
    event_service = EventService(test_db)
    track_res = await event_service.track_event(
        request_id="req_consented_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": consent_token},
    )
    assert track_res is True

    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    pub = await test_db.get(Publisher, ctx["publisher_id"])
    assert adv.balance < Decimal("500.00"), "Consented event must deduct advertiser spend"
    assert pub.balance > Decimal("0.00"), "Consented event must credit publisher earnings"


# ── Scenario 3: Reject ────────────────────────────────────────────────────

async def test_scenario_3_reject_consent_lifecycle(client: AsyncClient, test_db: AsyncSession):
    """
    Scenario 3: Visitor explicitly declines consent.
    - Consent log recorded with is_granted = False.
    - Ad requests with declined token return 0 ads.
    - Subsequent requests without token from same IP return 0 ads.
    - Event tracking yields cost = 0.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="reject-site", slot_id="reject_slot")

    # 1. Visitor submits Decline
    dec_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        headers={"User-Agent": "TestBrowser/1.0", "X-Forwarded-For": "198.51.100.55"},
        json={"version": "v1.0", "categories": {"necessary": True, "advertising": False, "analytics": False}},
    )
    assert dec_res.status_code == 201
    dec_data = dec_res.json()
    assert dec_data["is_granted"] is False
    declined_token = dec_data["consent_token"]

    token_data = verify_consent_token(declined_token, expected_site_slug=ctx["site_slug"])
    assert token_data["is_granted"] is False

    # 2. Ad request with declined token returns 0 ads
    ad_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": declined_token},
    )
    assert ad_res.status_code == 200
    assert len(ad_res.json()["ads"]) == 0

    # 3. Subsequent request without token from same IP also returns 0 ads
    refresh_ad_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}", "X-Forwarded-For": "198.51.100.55"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1},
    )
    assert refresh_ad_res.status_code == 200
    assert len(refresh_ad_res.json()["ads"]) == 0

    # 4. Event tracking yields cost = 0
    event_service = EventService(test_db)
    track_res = await event_service.track_event(
        request_id="req_declined_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": declined_token},
    )
    assert track_res is True
    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00")


# ── Scenario 4: Withdraw ──────────────────────────────────────────────────

async def test_scenario_4_withdraw_consent_lifecycle(client: AsyncClient, test_db: AsyncSession):
    """
    Scenario 4: Visitor accepts first, then withdraws consent.
    - Initial ad request succeeds.
    - Visitor withdraws via Decline/Manage.
    - Immediate next ad request returns 0 ads.
    - Future tracking events yield cost = 0.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="withdraw-site", slot_id="withdraw_slot")

    # Step A: Accept
    acc_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        headers={"X-Forwarded-For": "198.51.100.60"},
        json={"version": "v1.0", "categories": {"advertising": True}},
    )
    acc_token = acc_res.json()["consent_token"]

    ad_1 = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": acc_token},
    )
    assert len(ad_1.json()["ads"]) >= 1

    # Step B: Withdraw
    with_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        headers={"X-Forwarded-For": "198.51.100.60"},
        json={"version": "v1.0", "categories": {"advertising": False, "analytics": False}},
    )
    with_token = with_res.json()["consent_token"]
    assert with_res.json()["is_granted"] is False

    # Step C: Immediate next request returns 0 ads
    ad_2 = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": with_token},
    )
    assert len(ad_2.json()["ads"]) == 0

    # Step D: Event tracking suppressed to cost = 0
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_withdrawn_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": with_token},
    )
    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00")


# ── Scenario 5: Tampering ─────────────────────────────────────────────────

async def test_scenario_5_tampering_defenses(client: AsyncClient, test_db: AsyncSession):
    """
    Scenario 5: Anti-tampering verification:
    - Cross-site tampering: Token from Site A used on Site B is rejected (HTTP 403).
    - Modified token payload: Flipping is_granted from 0 to 1 fails HMAC validation (HTTP 403).
    - Expired token is rejected (HTTP 400).
    """
    site_a = await _setup_monetized_site(client, test_db, site_slug="tamper-site-a", slot_id="slot_tamper_a")
    site_b = await _setup_monetized_site(client, test_db, site_slug="tamper-site-b", slot_id="slot_tamper_b")

    # Generate legitimate token for Site A
    acc_res = await client.post(
        f"/api/v1/website/public/{site_a['site_slug']}/consent",
        json={"version": "v1.0", "categories": {"advertising": True}},
    )
    token_a = acc_res.json()["consent_token"]

    # 1. Cross-site attack: Send Site A's token to Site B slot -> 403 Forbidden
    cross_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{site_b['prop_domain']}"},
        json={"slot_id": site_b["slot_id"], "num_ads": 1, "consent_token": token_a},
    )
    assert cross_res.status_code == 403
    assert "Consent token site mismatch" in cross_res.json()["detail"]

    # 2. Payload tampering: Generate declined token and flip granted flag from 0 to 1
    declined_token = sign_consent_token(site_a["site_slug"], site_a["project_id"], is_granted=False)
    parts = declined_token.split(".")
    tampered_parts = list(parts)
    tampered_parts[2] = "1"  # flip granted flag 0 -> 1
    tampered_token = ".".join(tampered_parts)

    tamp_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{site_a['prop_domain']}"},
        json={"slot_id": site_a["slot_id"], "num_ads": 1, "consent_token": tampered_token},
    )
    assert tamp_res.status_code == 403
    assert "Invalid consent token signature" in tamp_res.json()["detail"]

    # 3. Expired token -> 400 Bad Request
    expired_token = sign_consent_token(site_a["site_slug"], site_a["project_id"], is_granted=True, ttl_seconds=-10)
    exp_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{site_a['prop_domain']}"},
        json={"slot_id": site_a["slot_id"], "num_ads": 1, "consent_token": expired_token},
    )
    assert exp_res.status_code == 400
    assert "Consent token expired" in exp_res.json()["detail"]


# ── Scenario 6: Defaults ──────────────────────────────────────────────────

async def test_scenario_6_defaults_to_denied(client: AsyncClient, test_db: AsyncSession):
    """
    Scenario 6: Missing or malformed categories must default to denied.
    - Empty payload: defaults to False.
    - Missing advertising key: defaults to False.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="default-site", slot_id="default_slot")

    # 1. Empty body -> defaults to denied
    r1 = await client.post(f"/api/v1/website/public/{ctx['site_slug']}/consent", json={})
    assert r1.status_code == 201
    assert r1.json()["is_granted"] is False
    assert r1.json()["categories"]["advertising"] is False

    # 2. Payload omitting advertising
    r2 = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"version": "v1.0", "categories": {"analytics": True}},
    )
    assert r2.status_code == 201
    assert r2.json()["is_granted"] is False
    assert r2.json()["categories"]["advertising"] is False


# ── Scenario 7: Preview ───────────────────────────────────────────────────

async def test_scenario_7_preview_strictly_non_billable(client: AsyncClient, test_db: AsyncSession):
    """
    Scenario 7: Preview / sandbox traffic produces cost = 0 regardless of consent choice.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="preview-site", slot_id="preview_slot")

    # Granted consent token
    acc_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"version": "v1.0", "categories": {"advertising": True}},
    )
    token = acc_res.json()["consent_token"]

    event_service = EventService(test_db)
    track_res = await event_service.track_event(
        request_id="prev_req_999",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": token, "is_preview": True},
    )
    assert track_res is True

    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00"), "Preview traffic must remain strictly non-billable"


# ── Scenario 8: Bypasses ──────────────────────────────────────────────────

async def test_scenario_8_arbitrary_client_booleans_rejected(client: AsyncClient, test_db: AsyncSession):
    """
    Scenario 8: Modified browser sending arbitrary booleans (e.g. extra.consent = True)
    without actual stored consent or valid cryptographic token must be rejected.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="bypass-site", slot_id="bypass_slot")

    event_service = EventService(test_db)

    # Attacker calls track_event with arbitrary boolean
    await event_service.track_event(
        request_id="req_bypass_attempt_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent": True, "visitor_consented": True},
    )

    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    pub = await test_db.get(Publisher, ctx["publisher_id"])
    assert adv.balance == Decimal("500.00"), "Arbitrary client booleans must NOT bill advertiser"
    assert pub.balance == Decimal("0.00"), "Arbitrary client booleans must NOT credit publisher"


# ── Scenario 9: Idempotency ───────────────────────────────────────────────

async def test_scenario_9_consent_write_idempotency(client: AsyncClient, test_db: AsyncSession):
    """
    Scenario 9: Repeated identical consent submissions reuse existing records.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="idempotent-site", slot_id="idemp_slot")

    headers = {"User-Agent": "IdempBrowser/1.0", "X-Forwarded-For": "198.51.100.99"}
    payload = {"version": "v1.0", "categories": {"advertising": True}}

    r1 = await client.post(f"/api/v1/website/public/{ctx['site_slug']}/consent", headers=headers, json=payload)
    id1 = r1.json()["consent_id"]

    # Repeat exact same post
    r2 = await client.post(f"/api/v1/website/public/{ctx['site_slug']}/consent", headers=headers, json=payload)
    id2 = r2.json()["consent_id"]

    assert id1 == id2, "Identical consent write must be idempotent and reuse existing record ID"

