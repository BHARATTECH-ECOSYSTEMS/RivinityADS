"""
RivinityADS — Phase 1 Visitor Consent Gate Test Suite (P0).

Covers all 11 mandatory Phase 1 scenarios:
1. Default to denied: missing/null/empty categories evaluate to False; banner renders; 0 ads.
2. Accept advertising: is_granted=True, signed token returned, ad request succeeds, billable event deducts balance.
3. Reject advertising: is_granted=False, ad request returns 0 ads, tracking cost = 0.
4. Analytics only: analytics=True, advertising=False -> is_granted=False, delivery blocked, cost = 0.
5. Cross-site tampering: Token from Site A used on Site B -> HTTP 403; event tracking cost = 0; tampered payload -> HTTP 403.
6. Expired token & version mismatch: Expired timestamp -> HTTP 400; version mismatch -> HTTP 400.
7. Withdrawn consent: Accept -> ads work; withdraw -> immediate 0 ads, cost = 0.
8. Direct API bypass attempt: Direct ad request without token -> 0 ads; direct event tracking without token or with fabricated booleans -> cost = 0, zero spend in DB.
9. Preview and sandbox non-billable: Preview traffic produces cost = 0 regardless of consent choice.
10. Rapid duplicate submissions (idempotency): Identical submissions with same IP/UA hash reuse existing WebsiteConsentLog ID.
11. Independence from publisher monetization consent: Publisher terms accepted does NOT substitute for visitor consent.
"""

from __future__ import annotations

from decimal import Decimal
import asyncio
import hashlib
from pathlib import Path
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

    # Bind slot into page
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

    # Publisher monetization terms consent and enable
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


# ── Scenario 1: Default to Denied ──────────────────────────────────────────

async def test_scenario_1_default_to_denied(client: AsyncClient, test_db: AsyncSession):
    """Scenario 1: Default to denied.
    - Public monetized site renders banner, modal, and reopen button.
    - Direct ad request without consent token returns 0 ads.
    - Direct event tracking without consent token yields cost = 0.
    - POST /consent with empty payload, missing categories, or non-dict defaults to denied.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="p1-default-site", slot_id="p1_def_slot")

    # 1. Check rendered HTML
    site_res = await client.get(f"/sites/{ctx['site_slug']}")
    assert site_res.status_code == 200
    html = site_res.text
    assert "rivinity-consent-banner" in html
    assert f'data-site="{ctx["site_slug"]}"' in html
    assert "rivinity-consent-modal" in html
    assert "rivinity-consent-reopen" in html

    # 2. Ad request without consent token yields 0 ads
    ad_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1},
    )
    assert ad_res.status_code == 200
    data = ad_res.json()
    assert len(data["ads"]) == 0
    assert data["count"] == 0

    # 3. Direct event tracking without consent yields cost = 0
    event_service = EventService(test_db)
    track_ok = await event_service.track_event(
        request_id="req_def_unconsented_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"]},
    )
    assert track_ok is True
    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00"), "Unconsented event must not debit advertiser"

    # 4. POST /consent with empty payload or missing categories defaults to denied
    r_empty = await client.post(f"/api/v1/website/public/{ctx['site_slug']}/consent", json={})
    assert r_empty.status_code == 201
    assert r_empty.json()["is_granted"] is False
    assert r_empty.json()["categories"] == {"necessary": True, "advertising": False, "analytics": False}

    r_none = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"categories": None},
    )
    assert r_none.status_code == 201
    assert r_none.json()["is_granted"] is False
    assert r_none.json()["categories"]["advertising"] is False


# ── Scenario 2: Accept Advertising ────────────────────────────────────────

async def test_scenario_2_accept_advertising(client: AsyncClient, test_db: AsyncSession):
    """Scenario 2: Accept advertising consent.
    - POST /consent with advertising=True returns is_granted=True with valid HMAC token.
    - Subsequent ad request with consent_token returns active ads with consent_token in tracking URLs.
    - Impression tracking event with consent_token charges advertiser and credits publisher.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="p1-accept-site", slot_id="p1_acc_slot")

    # 1. Accept consent
    vis_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        headers={"User-Agent": "TestBrowser/1.0", "X-Forwarded-For": "198.51.100.10"},
        json={"version": "v1.0", "categories": {"necessary": True, "advertising": True, "analytics": True}},
    )
    assert vis_res.status_code == 201
    body = vis_res.json()
    assert body["is_granted"] is True
    token = body["consent_token"]
    assert token is not None

    # Verify HMAC token
    verified = verify_consent_token(token, expected_site_slug=ctx["site_slug"], expected_project_id=ctx["project_id"])
    assert verified["is_granted"] is True
    assert verified["version"] == "v1.0"

    # Verify DB record hashes
    log = (await test_db.execute(select(WebsiteConsentLog).where(WebsiteConsentLog.id == body["consent_id"]))).scalar_one()
    assert log.is_granted is True
    assert log.client_ip_hash == hashlib.sha256(b"198.51.100.10").hexdigest()
    assert "198.51.100.10" not in (log.client_ip_hash or "")

    # 2. Ad request with consent token succeeds
    ad_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": token},
    )
    assert ad_res.status_code == 200
    ad_data = ad_res.json()
    assert len(ad_data["ads"]) >= 1
    ad = ad_data["ads"][0]
    # Check that tracking URLs carry consent_token
    assert "consent_token=" in ad["tracking"]["impression_url"]
    assert "consent_token=" in ad["tracking"]["click_url"]

    # 3. Billable impression charges advertiser and credits publisher
    event_service = EventService(test_db)
    track_ok = await event_service.track_event(
        request_id="req_acc_billable_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": token},
    )
    assert track_ok is True
    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    pub = await test_db.get(Publisher, ctx["publisher_id"])
    assert adv.balance < Decimal("500.00"), "Consented event must charge advertiser"
    assert pub.balance > Decimal("0.00"), "Consented event must credit publisher"


# ── Scenario 3: Reject Advertising ────────────────────────────────────────

async def test_scenario_3_reject_advertising(client: AsyncClient, test_db: AsyncSession):
    """Scenario 3: Reject advertising consent.
    - POST /consent with advertising=False returns is_granted=False.
    - Subsequent ad request with rejected token returns 0 ads.
    - Event tracking with rejected token yields cost = 0.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="p1-reject-site", slot_id="p1_rej_slot")

    rej_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        headers={"User-Agent": "TestBrowser/1.0", "X-Forwarded-For": "198.51.100.20"},
        json={"version": "v1.0", "categories": {"advertising": False, "analytics": False}},
    )
    assert rej_res.status_code == 201
    rej_data = rej_res.json()
    assert rej_data["is_granted"] is False
    rej_token = rej_data["consent_token"]

    # Ad request returns 0 ads
    ad_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": rej_token},
    )
    assert ad_res.status_code == 200
    assert len(ad_res.json()["ads"]) == 0

    # Event tracking yields cost = 0
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_rej_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": rej_token},
    )
    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00")


# ── Scenario 4: Analytics Only ────────────────────────────────────────────

async def test_scenario_4_analytics_only(client: AsyncClient, test_db: AsyncSession):
    """Scenario 4: Analytics only consent (advertising declined).
    - POST /consent with analytics=True, advertising=False -> is_granted=False.
    - Ad delivery strictly blocked (0 ads), billing cost = 0.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="p1-analytics-site", slot_id="p1_ana_slot")

    ana_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"version": "v1.0", "categories": {"necessary": True, "advertising": False, "analytics": True}},
    )
    assert ana_res.status_code == 201
    ana_data = ana_res.json()
    assert ana_data["is_granted"] is False
    assert ana_data["categories"]["analytics"] is True
    assert ana_data["categories"]["advertising"] is False
    token = ana_data["consent_token"]

    ad_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": token},
    )
    assert ad_res.status_code == 200
    assert len(ad_res.json()["ads"]) == 0

    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_ana_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": token},
    )
    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00")


# ── Scenario 5: Cross-Site Tampering ──────────────────────────────────────

async def test_scenario_5_cross_site_tampering(client: AsyncClient, test_db: AsyncSession):
    """Scenario 5: Cross-site tampering defenses.
    - Token from Site A used for Site B ad request is rejected with HTTP 403 Forbidden.
    - Token from Site A used for Site B event tracking suppresses cost = 0.
    - Tampered payload signature fails HMAC validation (HTTP 403).
    """
    site_a = await _setup_monetized_site(client, test_db, site_slug="p1-site-a", slot_id="p1_slot_a")
    site_b = await _setup_monetized_site(client, test_db, site_slug="p1-site-b", slot_id="p1_slot_b")

    acc_res = await client.post(
        f"/api/v1/website/public/{site_a['site_slug']}/consent",
        json={"version": "v1.0", "categories": {"advertising": True}},
    )
    token_a = acc_res.json()["consent_token"]

    # 1. Ad request on Site B using Site A's token -> HTTP 403
    cross_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{site_b['prop_domain']}"},
        json={"slot_id": site_b["slot_id"], "num_ads": 1, "consent_token": token_a},
    )
    assert cross_res.status_code == 403
    assert "Consent token site mismatch" in cross_res.json()["detail"]

    # 2. Event tracking on Site B using Site A's token -> cost = 0
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_cross_event_01",
        ad_id=f"ad_{site_b['campaign_id']}_{site_b['creative_id']}",
        event_type="impression",
        extra={"slot_id": site_b["slot_id"], "consent_token": token_a},
    )
    adv_b = await test_db.get(Advertiser, site_b["advertiser_id"])
    assert adv_b.balance == Decimal("500.00"), "Cross-site token must not bill advertiser"

    # 3. Payload tampering: flip granted flag
    parts = token_a.split(".")
    tampered_parts = list(parts)
    tampered_parts[2] = "0" if parts[2] == "1" else "1"
    tampered_token = ".".join(tampered_parts)

    tamp_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{site_a['prop_domain']}"},
        json={"slot_id": site_a["slot_id"], "num_ads": 1, "consent_token": tampered_token},
    )
    assert tamp_res.status_code == 403
    assert "Invalid consent token signature" in tamp_res.json()["detail"]


# ── Scenario 6: Expired Token & Policy Version Mismatch ────────────────────

async def test_scenario_6_expired_token_and_version(client: AsyncClient, test_db: AsyncSession):
    """Scenario 6: Expired token & version mismatch.
    - Token with past expiration timestamp -> HTTP 400 Bad Request.
    - verify_consent_token with expected_version mismatch -> HTTP 400 Bad Request.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="p1-expired-site", slot_id="p1_exp_slot")

    expired_token = sign_consent_token(ctx["site_slug"], ctx["project_id"], is_granted=True, ttl_seconds=-10)
    exp_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": expired_token},
    )
    assert exp_res.status_code == 400
    assert "Consent token expired" in exp_res.json()["detail"]

    # Version mismatch check in verify_consent_token
    valid_token = sign_consent_token(ctx["site_slug"], ctx["project_id"], is_granted=True, version="v1.0")
    with pytest.raises(Exception) as exc_info:
        verify_consent_token(valid_token, expected_version="v2.0")
    assert "version mismatch" in str(exc_info.value.detail).lower()


# ── Scenario 7: Withdrawn Consent ─────────────────────────────────────────

async def test_scenario_7_withdrawn_consent(client: AsyncClient, test_db: AsyncSession):
    """Scenario 7: Withdrawn consent.
    - Accept consent -> ad request succeeds.
    - Withdraw consent -> immediate next ad request returns 0 ads; events cost = 0.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="p1-withdraw-site", slot_id="p1_with_slot")

    # Accept
    acc_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"version": "v1.0", "categories": {"advertising": True}},
    )
    acc_token = acc_res.json()["consent_token"]
    ad_1 = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": acc_token},
    )
    assert len(ad_1.json()["ads"]) >= 1

    # Withdraw
    with_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"version": "v1.0", "categories": {"advertising": False, "analytics": False}},
    )
    with_token = with_res.json()["consent_token"]
    assert with_res.json()["is_granted"] is False

    # Immediate next ad request returns 0 ads
    ad_2 = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": with_token},
    )
    assert len(ad_2.json()["ads"]) == 0

    # Tracking cost = 0 with withdraw token
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_withdrawn_ev_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": with_token},
    )
    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00")

    # ── Post-Withdrawal Token Replay Attack Prevention ──
    # Replay initial grant token (acc_token) AFTER withdrawal:
    # Server-side revocation gate MUST reject the stale grant token!
    ad_replay = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": acc_token},
    )
    assert ad_replay.status_code == 200
    assert len(ad_replay.json()["ads"]) == 0, "Replayed pre-withdrawal grant token must return 0 ads"

    # Event tracking with replayed token MUST NOT charge advertiser
    await event_service.track_event(
        request_id="req_replay_attack_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": acc_token},
    )
    await test_db.refresh(adv)
    assert adv.balance == Decimal("500.00"), "Replayed pre-withdrawal grant token must not bill advertiser"

    # Re-accept consent -> new token valid, old token remains rejected
    await asyncio.sleep(1.05)
    reacc_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"version": "v1.0", "categories": {"advertising": True}},
    )
    new_acc_token = reacc_res.json()["consent_token"]
    assert new_acc_token != acc_token

    ad_new = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": new_acc_token},
    )
    assert len(ad_new.json()["ads"]) >= 1, "New grant token after re-accept must serve ads"

    # Old pre-withdrawal token remains revoked
    ad_old_replay = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": acc_token},
    )
    assert len(ad_old_replay.json()["ads"]) == 0, "Historical pre-withdrawal token remains revoked after re-accept"


# ── Scenario 8: Direct API Bypass Attempt ─────────────────────────────────

async def test_scenario_8_direct_api_bypass(client: AsyncClient, test_db: AsyncSession):
    """Scenario 8: Direct API bypass attempt.
    - Direct ad request without consent_token to a monetized website slot -> returns 0 ads.
    - Direct event tracking without consent_token -> cost = Decimal('0'), zero spend.
    - Direct event tracking with fabricated extra flags (e.g. extra.consent=True) without valid signed token -> cost = 0.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="p1-bypass-site", slot_id="p1_byp_slot")

    # 1. Direct ad request without consent token
    ad_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1},
    )
    assert ad_res.status_code == 200
    assert len(ad_res.json()["ads"]) == 0

    # 2. Direct event tracking without consent token
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_byp_no_token",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"]},
    )

    # 3. Direct event tracking with fabricated extra flags
    await event_service.track_event(
        request_id="req_byp_fake_flags",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent": True, "visitor_consented": True, "is_consented": True},
    )

    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    pub = await test_db.get(Publisher, ctx["publisher_id"])
    assert adv.balance == Decimal("500.00"), "Bypass attempts must NOT charge advertiser"
    assert pub.balance == Decimal("0.00"), "Bypass attempts must NOT credit publisher"


# ── Scenario 9: Preview and Sandbox Non-Billable ──────────────────────────

async def test_scenario_9_preview_and_sandbox_non_billable(client: AsyncClient, test_db: AsyncSession):
    """Scenario 9: Preview / sandbox traffic produces cost = 0 regardless of consent choice."""
    ctx = await _setup_monetized_site(client, test_db, site_slug="p1-preview-site", slot_id="p1_prev_slot")

    acc_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"version": "v1.0", "categories": {"advertising": True}},
    )
    token = acc_res.json()["consent_token"]

    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_preview_999",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": token, "is_preview": True},
    )

    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00"), "Preview traffic must remain strictly non-billable"


# ── Scenario 10: Rapid Duplicate Submissions (Idempotency) ────────────────

async def test_scenario_10_rapid_duplicate_idempotency(client: AsyncClient, test_db: AsyncSession):
    """Scenario 10: Rapid duplicate submissions reuse existing WebsiteConsentLog records."""
    ctx = await _setup_monetized_site(client, test_db, site_slug="p1-idemp-site", slot_id="p1_idemp_slot")

    headers = {"User-Agent": "IdempBrowser/1.0", "X-Forwarded-For": "198.51.100.99"}
    payload = {"version": "v1.0", "categories": {"advertising": True}}

    r1 = await client.post(f"/api/v1/website/public/{ctx['site_slug']}/consent", headers=headers, json=payload)
    id1 = r1.json()["consent_id"]

    r2 = await client.post(f"/api/v1/website/public/{ctx['site_slug']}/consent", headers=headers, json=payload)
    id2 = r2.json()["consent_id"]

    assert id1 == id2, "Duplicate consent submission must reuse existing record ID without DB duplication"


# ── Scenario 11: Independence From Publisher Monetization Consent ─────────

async def test_scenario_11_publisher_monetization_independence(client: AsyncClient, test_db: AsyncSession):
    """Scenario 11: Publisher monetization consent does NOT substitute for visitor consent.
    Even with publisher monetization fully active (monetization_eligibility prerequisites satisfied),
    missing visitor consent strictly blocks delivery and billing.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="p1-indep-site", slot_id="p1_indep_slot")

    # Confirm publisher monetization is active
    status_res = await client.get(f"/api/v1/website/projects/{ctx['project_id']}/monetization", headers={"Authorization": f"Bearer {ctx['token']}"})
    assert status_res.status_code == 200
    assert status_res.json()["current_status"] == 2  # ACTIVE

    # Without visitor consent token: ad request returns 0 ads
    ad_res = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1},
    )
    assert ad_res.status_code == 200
    assert len(ad_res.json()["ads"]) == 0

    # Event tracking without visitor consent token suppresses cost to 0
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_indep_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"]},
    )
    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00"), "Active publisher monetization must NOT bypass visitor consent gate"


# ── Scenario 12: Static SDK Asset Identity & Distinct Placeholder States ─

async def test_scenario_12_static_sdk_identity_and_drift():
    """Scenario 12: Verify rivinity.js in root static and ad_server static are byte-for-byte identical,
    and assert presence of distinct placeholder states.
    """
    root_sdk = Path("backend/liteads/static/rivinity.js").resolve()
    ad_server_sdk = Path("backend/liteads/ad_server/static/rivinity.js").resolve()

    assert root_sdk.exists(), f"Missing root static SDK at {root_sdk}"
    assert ad_server_sdk.exists(), f"Missing ad_server static SDK at {ad_server_sdk}"

    root_bytes = root_sdk.read_bytes()
    ad_bytes = ad_server_sdk.read_bytes()

    assert root_bytes == ad_bytes, (
        "SDK drift detected! backend/liteads/static/rivinity.js and "
        "backend/liteads/ad_server/static/rivinity.js must be byte-for-byte identical."
    )

    content = root_sdk.read_text(encoding="utf-8")
    assert "Consent Required" in content, "SDK must support 'Consent Required' placeholder state"
    assert "Consent Declined" in content, "SDK must support 'Consent Declined' placeholder state"
    assert "Monetisation Inactive" in content, "SDK must support 'Monetisation Inactive' placeholder state"
    assert "Preview / Sandbox Mode" in content, "SDK must support 'Preview / Sandbox Mode' placeholder state"
    assert "No Fill" in content, "SDK must support 'No Fill' placeholder state"


# ── Scenario 13: Revocation Survives 30-Visitor Flood (Flaw 1 Regression) ──

async def test_revocation_survives_30_visitor_flood(client: AsyncClient, test_db: AsyncSession):
    """Verify that a visitor's withdrawal survives an arbitrary flood of 30+ subsequent
    visitor consent records without being bypassed by a query limit ceiling (e.g. .limit(25)).
    Real DB balances and records are asserted.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="flood-site", slot_id="flood_slot")
    vid_a = "flood_target_visitor"

    # Step 1: Visitor A grants consent
    accept_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"visitor_id": vid_a, "version": "v1.0", "categories": {"necessary": True, "advertising": True}},
    )
    assert accept_res.status_code in (200, 201)
    token_a = accept_res.json()["consent_token"]
    assert token_a is not None

    # Verify initial token works for ad delivery
    ad_res_initial = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": token_a},
    )
    assert ad_res_initial.status_code == 200
    assert len(ad_res_initial.json()["ads"]) == 1

    # Step 2: Visitor A withdraws consent
    withdraw_res = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"visitor_id": vid_a, "version": "v1.0", "categories": {"necessary": True, "advertising": False}},
    )
    assert withdraw_res.status_code in (200, 201)
    assert withdraw_res.json()["is_granted"] is False

    # Step 3: Flood with 30 distinct visitors recording consent (pushes Visitor A's withdrawal past 25 records)
    for i in range(1, 31):
        vid_flood = f"flood_noise_visitor_{i:03d}"
        res_i = await client.post(
            f"/api/v1/website/public/{ctx['site_slug']}/consent",
            headers={"X-Forwarded-For": f"10.0.{i // 250}.{i % 250 + 1}"},
            json={"visitor_id": vid_flood, "version": "v1.0", "categories": {"necessary": True, "advertising": True}},
        )
        assert res_i.status_code in (200, 201)

    # Step 4: Replay Visitor A's initial grant token (token_a).
    # Must fail closed despite 30 intervening visitor consent records!
    ad_res_replay = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": token_a},
    )
    assert ad_res_replay.status_code == 200
    assert len(ad_res_replay.json()["ads"]) == 0, "Replaying token after 30-visitor flood must return 0 ads"

    # Step 5: Event tracking replay must suppress billing to 0
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_flood_replay_01",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": token_a},
    )

    # Assert real DB balance: no balance deducted
    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00"), "Revoked token must NOT debit advertiser balance even after 30-visitor flood"


# ── Scenario 14: NAT Shared IP Withdrawal Isolation (Flaw 2 Regression) ──

async def test_nat_shared_ip_withdrawal_isolation(client: AsyncClient, test_db: AsyncSession):
    """Verify that when Visitor A and Visitor B share the same office/NAT public IP,
    Visitor A withdrawing consent DOES NOT revoke Visitor B's valid token.
    Tests strict visitor_id isolation over IP-hash fallback.
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="nat-site", slot_id="nat_slot")
    shared_nat_ip = "203.0.113.88"
    nat_headers = {"X-Forwarded-For": shared_nat_ip, "Origin": f"https://{ctx['prop_domain']}"}

    vid_a = "nat_office_visitor_alpha"
    vid_b = "nat_office_visitor_beta"

    # Step 1: Both visitors behind same IP grant affirmative consent
    res_a = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        headers=nat_headers,
        json={"visitor_id": vid_a, "version": "v1.0", "categories": {"necessary": True, "advertising": True}},
    )
    assert res_a.status_code in (200, 201)
    token_a = res_a.json()["consent_token"]

    res_b = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        headers=nat_headers,
        json={"visitor_id": vid_b, "version": "v1.0", "categories": {"necessary": True, "advertising": True}},
    )
    assert res_b.status_code in (200, 201)
    token_b = res_b.json()["consent_token"]

    # Step 2: Visitor A withdraws consent from the same NAT IP
    withdraw_a = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        headers=nat_headers,
        json={"visitor_id": vid_a, "version": "v1.0", "categories": {"necessary": True, "advertising": False}},
    )
    assert withdraw_a.status_code in (200, 201)

    # Step 3: Assert Visitor A is revoked
    ad_a = await client.post(
        "/api/v1/ad/request",
        headers=nat_headers,
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": token_a},
    )
    assert ad_a.status_code == 200
    assert len(ad_a.json()["ads"]) == 0, "Visitor A must be revoked after withdrawing"

    # Step 4: Assert Visitor B is STILL VALID (NAT isolation: Visitor A's withdrawal does not affect B)
    ad_b = await client.post(
        "/api/v1/ad/request",
        headers=nat_headers,
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": token_b},
    )
    assert ad_b.status_code == 200
    assert len(ad_b.json()["ads"]) == 1, "Visitor B must remain valid despite shared NAT IP with withdrawn visitor A"

    # Step 5: Assert Visitor B can produce billable event and debit DB balance
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_nat_valid_b",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": token_b, "ip_address": shared_nat_ip},
    )

    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance < Decimal("500.00"), "Visitor B event must successfully debit advertiser balance"


# ── Scenario 15: Monotonic Same-Second Flapper Resolution (Flaw 3 Regression) ──

async def test_same_second_accept_withdraw_reaccept_revocation(client: AsyncClient, test_db: AsyncSession):
    """Verify that when a visitor accepts (T1), withdraws, and re-accepts (T3) rapidly within
    the same integer second, monotonic consent_log_id resolution guarantees:
    - Pre-withdrawal token T1 remains strictly revoked (0 ads, 0 billable cost).
    - Post-reacceptance token T3 is fully valid (1 ad, billable cost debited).
    """
    ctx = await _setup_monetized_site(client, test_db, site_slug="flapper-site", slot_id="flapper_slot")
    vid = "rapid_flapper_visitor"

    # Rapid back-to-back: Accept (T1) -> Withdraw -> Re-Accept (T3)
    res_t1 = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"visitor_id": vid, "version": "v1.0", "categories": {"necessary": True, "advertising": True}},
    )
    assert res_t1.status_code in (200, 201)
    t1 = res_t1.json()["consent_token"]

    res_withdraw = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"visitor_id": vid, "version": "v1.0", "categories": {"necessary": True, "advertising": False}},
    )
    assert res_withdraw.status_code in (200, 201)
    assert res_withdraw.json()["is_granted"] is False

    res_t3 = await client.post(
        f"/api/v1/website/public/{ctx['site_slug']}/consent",
        json={"visitor_id": vid, "version": "v1.0", "categories": {"necessary": True, "advertising": True}},
    )
    assert res_t3.status_code in (200, 201)
    t3 = res_t3.json()["consent_token"]

    assert t1 != t3, "Re-issued token must have different log ID / payload"

    # Assert T1 is revoked
    ad_t1 = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": t1},
    )
    assert ad_t1.status_code == 200
    assert len(ad_t1.json()["ads"]) == 0, "Pre-withdrawal token T1 must be rejected even if re-accepted in same second"

    # Assert T3 is valid
    ad_t3 = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": f"https://{ctx['prop_domain']}"},
        json={"slot_id": ctx["slot_id"], "num_ads": 1, "consent_token": t3},
    )
    assert ad_t3.status_code == 200
    assert len(ad_t3.json()["ads"]) == 1, "Post-reacceptance token T3 must be accepted"

    # Assert T1 event produces cost = 0
    event_service = EventService(test_db)
    await event_service.track_event(
        request_id="req_t1_stale",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": t1},
    )
    adv = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv.balance == Decimal("500.00"), "Stale T1 event must not charge advertiser balance"

    # Assert T3 event produces billable charge
    await event_service.track_event(
        request_id="req_t3_fresh",
        ad_id=f"ad_{ctx['campaign_id']}_{ctx['creative_id']}",
        event_type="impression",
        extra={"slot_id": ctx["slot_id"], "consent_token": t3},
    )
    adv_after = await test_db.get(Advertiser, ctx["advertiser_id"])
    assert adv_after.balance < Decimal("500.00"), "Fresh T3 event must charge advertiser balance"

