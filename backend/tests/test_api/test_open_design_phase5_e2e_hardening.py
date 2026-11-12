"""
RivinityADS — Open Design Integration Phase 5 (Stage 5.5)
End-to-End Hardening & Security Verification Test Suite.

Validates the complete 8-step journey:
Website Project -> Open Design Editor -> Ad-Slot Placement -> Publisher Linking ->
Domain Verification -> Monetization Consent -> Publication -> Secure Ad Delivery.

Organized into 6 core verification areas:
1. Publisher property linking & tenant isolation
2. Domain verification challenges & anti-spoofing
3. Ad-slot discovery & binding scoping
4. Monetization consent & prerequisite enforcement
5. Publication integrity & secure ad delivery
6. Anti-tampering, preview isolation & defense-in-depth

CRITICAL RESTRICTION:
Live monetization remains disabled. All ad rendering and preview assertions
verify zero-spend, non-billable sandbox isolation.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer
from liteads.common.config import get_settings
from liteads.models.ad import Advertiser, AdSet, Campaign, Creative, Status
from liteads.models.auth import User
from liteads.models.publisher import AdSlot, Publisher, PublisherProperty
from liteads.models.website import (
    WebsiteMonetizationStatus,
    WebsiteProject,
    WebsiteStatus,
)

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _register_user(client: AsyncClient, email: str, role: str = "publisher") -> tuple[str, int]:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "Password123!",
            "company_name": "Stage 5.5 Hardening",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


async def _create_publisher_for_user(
    session: AsyncSession,
    user_id: int,
    name: str = "Stage 5.5 Publisher",
) -> Publisher:
    pub = Publisher(
        name=name,
        owner_user_id=user_id,
        status=Status.ACTIVE,
        payout_details={
            "account_number": "112233445566",
            "bank_name": "HDFC Bank",
            "upi_id": "hardening@upi",
            "status": "active",
        },
    )
    session.add(pub)
    await session.flush()

    user = await session.get(User, user_id)
    if user:
        user.publisher_id = pub.id
        user.email_verified = True
        session.add(user)

    await session.commit()
    await session.refresh(pub)
    return pub


async def _create_property(
    session: AsyncSession,
    publisher_id: int,
    domain: str = "verified-e2e.com",
    is_verified: bool = True,
    status: Status = Status.ACTIVE,
) -> PublisherProperty:
    prop = PublisherProperty(
        publisher_id=publisher_id,
        domain=domain,
        name="Hardening Domain Property",
        verification_status="VERIFIED" if is_verified else "PENDING",
        verification_method="DNS_TXT",
        verification_token="tok_e2e_verified_123",
        verified_at=datetime.now(timezone.utc) if is_verified else None,
        status=status,
    )
    session.add(prop)
    await session.commit()
    await session.refresh(prop)
    return prop


async def _create_slot(
    session: AsyncSession,
    publisher_id: int,
    property_id: int | None,
    slot_id: str = "slot_hardening_top",
    format_type: int = 1,
    status: Status = Status.ACTIVE,
) -> AdSlot:
    slot = AdSlot(
        publisher_id=publisher_id,
        property_id=property_id,
        slot_id=slot_id,
        name="Leaderboard Ad Slot",
        format=format_type,
        width=728,
        height=90,
        floor_price=2.00,
        status=status,
    )
    session.add(slot)
    await session.commit()
    await session.refresh(slot)
    return slot


async def _create_campaign_and_creative(session: AsyncSession) -> tuple[Campaign, Creative]:
    adv = Advertiser(name="E2E Advertiser", balance=Decimal("1000.00"), status=Status.ACTIVE)
    session.add(adv)
    await session.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="E2E Campaign",
        budget_total=Decimal("500.00"),
        status=Status.ACTIVE,
    )
    session.add(camp)
    await session.flush()

    ad_set = AdSet(
        campaign_id=camp.id,
        name="E2E AdSet",
        bid_amount=Decimal("1.50"),
        status=Status.ACTIVE,
    )
    session.add(ad_set)
    await session.flush()

    creative = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set.id,
        title="Verified Safe Ad",
        description="Stage 5.5 Verified Creative",
        image_url="https://images.example.com/banner.png",
        landing_url="https://advertiser.example.com/e2e",
        status=Status.ACTIVE,
        width=728,
        height=90,
    )
    session.add(creative)
    await session.commit()
    await session.refresh(creative)
    return camp, creative


# ── Area 1: Publisher Property Linking & Tenant Isolation ──────────────────────

class TestPublisherPropertyLinking:
    """Verifies property linking security, cross-tenant isolation and platform subdomain rules."""

    async def test_link_property_success(self, client: AsyncClient, test_db: AsyncSession):
        token, user_id = await _register_user(client, "pub_link_success@test.com")
        headers = {"Authorization": f"Bearer {token}"}
        pub = await _create_publisher_for_user(test_db, user_id)
        prop = await _create_property(test_db, pub.id, "myblog.org", is_verified=True)

        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers,
            json={"name": "Blog Project", "slug": "blog-project-link"},
        )
        assert proj_res.status_code == 201
        proj_id = proj_res.json()["id"]

        link_res = await client.post(
            f"/api/v1/website/projects/{proj_id}/publisher-link",
            headers=headers,
            json={"publisher_id": pub.id, "property_id": prop.id},
        )
        assert link_res.status_code == 200
        data = link_res.json()
        assert data["publisher_id"] == pub.id
        assert data["property_id"] == prop.id

    async def test_link_foreign_publisher_rejected(self, client: AsyncClient, test_db: AsyncSession):
        """A user cannot link a website project to a publisher account owned by someone else."""
        token_a, user_a = await _register_user(client, "pub_user_a@test.com")
        token_b, user_b = await _register_user(client, "pub_user_b@test.com")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        pub_a = await _create_publisher_for_user(test_db, user_a, name="Pub A")
        prop_a = await _create_property(test_db, pub_a.id, "pub-a-site.com", is_verified=True)

        pub_b = await _create_publisher_for_user(test_db, user_b, name="Pub B")

        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers_b,
            json={"name": "User B Site", "slug": "user-b-site"},
        )
        assert proj_res.status_code == 201
        proj_id = proj_res.json()["id"]

        # User B attempts to link to Pub A's account
        link_res = await client.post(
            f"/api/v1/website/projects/{proj_id}/publisher-link",
            headers=headers_b,
            json={"publisher_id": pub_a.id, "property_id": prop_a.id},
        )
        assert link_res.status_code == 403
        assert "Publisher account does not belong to your tenant" in link_res.json()["detail"]

    async def test_link_foreign_property_rejected(self, client: AsyncClient, test_db: AsyncSession):
        """A user cannot link a property belonging to a different publisher."""
        token_a, user_a = await _register_user(client, "pub_prop_a@test.com")
        token_b, user_b = await _register_user(client, "pub_prop_b@test.com")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        pub_a = await _create_publisher_for_user(test_db, user_a, name="Pub A Corp")
        prop_a = await _create_property(test_db, pub_a.id, "pub-a-only.com", is_verified=True)

        pub_b = await _create_publisher_for_user(test_db, user_b, name="Pub B Corp")

        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers_b,
            json={"name": "User B Project", "slug": "user-b-proj"},
        )
        assert proj_res.status_code == 201
        proj_id = proj_res.json()["id"]

        # User B supplies their own publisher_id but property_id belonging to Pub A
        link_res = await client.post(
            f"/api/v1/website/projects/{proj_id}/publisher-link",
            headers=headers_b,
            json={"publisher_id": pub_b.id, "property_id": prop_a.id},
        )
        assert link_res.status_code == 400
        assert "Property does not exist or does not belong to the specified publisher" in link_res.json()["detail"]

    async def test_link_nonexistent_property_rejected(self, client: AsyncClient, test_db: AsyncSession):
        token, user_id = await _register_user(client, "pub_nonexistent_prop@test.com")
        headers = {"Authorization": f"Bearer {token}"}
        pub = await _create_publisher_for_user(test_db, user_id)

        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers,
            json={"name": "No Prop Site", "slug": "no-prop-site"},
        )
        proj_id = proj_res.json()["id"]

        link_res = await client.post(
            f"/api/v1/website/projects/{proj_id}/publisher-link",
            headers=headers,
            json={"publisher_id": pub.id, "property_id": 999999},
        )
        assert link_res.status_code == 400


# ── Area 2: Domain Verification Challenges & Anti-Spoofing ─────────────────────

class TestDomainVerificationSecurity:
    """Verifies domain challenge generation, property verification and exact hostname matching."""

    async def test_create_dns_and_meta_challenges(self, client: AsyncClient, test_db: AsyncSession):
        token, user_id = await _register_user(client, "pub_challenges@test.com")
        headers = {"Authorization": f"Bearer {token}"}
        pub = await _create_publisher_for_user(test_db, user_id)
        prop = await _create_property(test_db, pub.id, "newsportal.org", is_verified=False)

        # 1. DNS_TXT challenge
        dns_res = await client.post(
            f"/api/v1/publisher/property/{prop.id}/challenge",
            headers=headers,
            json={"method": "DNS_TXT"},
        )
        assert dns_res.status_code == 200
        dns_data = dns_res.json()
        assert dns_data["verification_method"] == "DNS_TXT"
        assert dns_data["dns_record_name"] == "_rivinity-challenge.newsportal.org"
        assert len(dns_data["verification_token"]) >= 16

        # 2. HTML_META challenge
        meta_res = await client.post(
            f"/api/v1/publisher/property/{prop.id}/challenge",
            headers=headers,
            json={"method": "HTML_META"},
        )
        assert meta_res.status_code == 200
        meta_data = meta_res.json()
        assert meta_data["verification_method"] == "HTML_META"
        assert '<meta name="rivinity-site-verification"' in meta_data["html_meta_tag"]

    async def test_challenge_tenant_isolation(self, client: AsyncClient, test_db: AsyncSession):
        """Publisher B cannot generate challenges or verify properties belonging to Publisher A."""
        token_a, user_a = await _register_user(client, "pub_iso_a@test.com")
        token_b, user_b = await _register_user(client, "pub_iso_b@test.com")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        pub_a = await _create_publisher_for_user(test_db, user_a)
        prop_a = await _create_property(test_db, pub_a.id, "topsecretsite.com", is_verified=False)

        res = await client.post(
            f"/api/v1/publisher/property/{prop_a.id}/challenge",
            headers=headers_b,
            json={"method": "DNS_TXT"},
        )
        assert res.status_code == 403


# ── Area 3: Ad-Slot Discovery & Binding Scoping ────────────────────────────────

class TestAdSlotDiscoveryAndBinding:
    """Verifies that project slot listing is scoped to project's active property and publisher."""

    async def test_list_project_slots_strict_property_scoping(self, client: AsyncClient, test_db: AsyncSession):
        token, user_id = await _register_user(client, "pub_slot_scope@test.com")
        headers = {"Authorization": f"Bearer {token}"}
        pub = await _create_publisher_for_user(test_db, user_id)

        prop1 = await _create_property(test_db, pub.id, "prop1.com", is_verified=True)
        prop2 = await _create_property(test_db, pub.id, "prop2.com", is_verified=True)

        slot1 = await _create_slot(test_db, pub.id, prop1.id, "slot_prop1_active", status=Status.ACTIVE)
        slot1_inactive = await _create_slot(test_db, pub.id, prop1.id, "slot_prop1_inactive", status=Status.INACTIVE)
        slot2 = await _create_slot(test_db, pub.id, prop2.id, "slot_prop2_active", status=Status.ACTIVE)

        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers,
            json={"name": "Scope Test Project", "slug": "scope-test-proj"},
        )
        proj_id = proj_res.json()["id"]

        # Before linking: slots list is empty
        unlinked_slots = await client.get(f"/api/v1/website/projects/{proj_id}/slots", headers=headers)
        assert unlinked_slots.status_code == 200
        assert unlinked_slots.json() == []

        # Link to prop1
        await client.post(
            f"/api/v1/website/projects/{proj_id}/publisher-link",
            headers=headers,
            json={"publisher_id": pub.id, "property_id": prop1.id},
        )

        linked_slots = await client.get(f"/api/v1/website/projects/{proj_id}/slots", headers=headers)
        assert linked_slots.status_code == 200
        slots_data = linked_slots.json()
        slot_ids = [s["slot_id"] for s in slots_data]

        assert "slot_prop1_active" in slot_ids
        assert "slot_prop1_inactive" not in slot_ids, "Inactive slot must be excluded"
        assert "slot_prop2_active" not in slot_ids, "Slot from other property must be excluded"


# ── Area 4: Monetization Consent & Prerequisite Enforcement ────────────────────

class TestMonetizationConsentAndPrerequisites:
    """Verifies prerequisite checks, terms versioning, 70/30 disclosure and activation guards."""

    async def test_prerequisite_evaluation_unmet(self, client: AsyncClient, test_db: AsyncSession):
        token, user_id = await _register_user(client, "pub_prereq@test.com")
        headers = {"Authorization": f"Bearer {token}"}
        pub = await _create_publisher_for_user(test_db, user_id)
        # Property is unverified
        prop = await _create_property(test_db, pub.id, "unverified-prereq.com", is_verified=False)

        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers,
            json={"name": "Prereq Site", "slug": "prereq-site"},
        )
        proj_id = proj_res.json()["id"]

        await client.post(
            f"/api/v1/website/projects/{proj_id}/publisher-link",
            headers=headers,
            json={"publisher_id": pub.id, "property_id": prop.id},
        )

        status_res = await client.get(f"/api/v1/website/projects/{proj_id}/monetization", headers=headers)
        assert status_res.status_code == 200
        data = status_res.json()
        assert data["is_eligible"] is False
        assert data["prerequisites"]["property_verified"] is False
        assert any("verified" in r.lower() for r in data["failure_reasons"])

    async def test_record_explicit_consent(self, client: AsyncClient, test_db: AsyncSession):
        token, user_id = await _register_user(client, "pub_consent_record@test.com")
        headers = {"Authorization": f"Bearer {token}"}
        pub = await _create_publisher_for_user(test_db, user_id)
        prop = await _create_property(test_db, pub.id, "consent-prop.com", is_verified=True)

        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers,
            json={"name": "Consent Site", "slug": "consent-site"},
        )
        proj_id = proj_res.json()["id"]

        await client.post(
            f"/api/v1/website/projects/{proj_id}/publisher-link",
            headers=headers,
            json={"publisher_id": pub.id, "property_id": prop.id},
        )

        consent_payload = {
            "accepted_terms": True,
            "version": "v1.0",
            "scope": {
                "display_ads": True,
                "banner": True,
                "revenue_share_acknowledged": True,
                "terms_agreed": True,
            },
        }

        consent_res = await client.post(
            f"/api/v1/website/projects/{proj_id}/monetization/consent",
            headers=headers,
            json=consent_payload,
        )
        assert consent_res.status_code == 200
        data = consent_res.json()
        assert data["consent"]["accepted_terms"] is True
        assert data["consent"]["version"] == "v1.0"
        assert data["consent"]["user_id"] == user_id
        assert data["prerequisites"]["consent_recorded"] is True

    async def test_enable_monetization_fails_closed_when_ineligible(self, client: AsyncClient, test_db: AsyncSession):
        """Activation of monetization must fail-closed if any prerequisite is unfulfilled."""
        token, user_id = await _register_user(client, "pub_enable_fail@test.com")
        headers = {"Authorization": f"Bearer {token}"}
        pub = await _create_publisher_for_user(test_db, user_id)
        prop = await _create_property(test_db, pub.id, "unverified-prop.com", is_verified=False)

        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers,
            json={"name": "Unverified Site", "slug": "unverified-site"},
        )
        proj_id = proj_res.json()["id"]

        await client.post(
            f"/api/v1/website/projects/{proj_id}/publisher-link",
            headers=headers,
            json={"publisher_id": pub.id, "property_id": prop.id},
        )

        enable_res = await client.post(
            f"/api/v1/website/projects/{proj_id}/monetization/enable",
            headers=headers,
        )
        assert enable_res.status_code == 400
        assert "Cannot enable monetization" in enable_res.json()["detail"]


# ── Area 5: Publication Integrity & Secure Ad Delivery ─────────────────────────

class TestPublicationIntegrityAndSecureDelivery:
    """Verifies full publication journey, public container rendering, and domain-matched ad serving."""

    async def test_full_publication_and_public_rendering(self, client: AsyncClient, test_db: AsyncSession):
        """Complete 8-step journey:
        Project -> Ad-Slot Placement -> Linking -> Verification -> Consent -> Publish -> Render -> Ad Serving.
        """
        token, user_id = await _register_user(client, "pub_full_journey@test.com")
        headers = {"Authorization": f"Bearer {token}"}
        pub = await _create_publisher_for_user(test_db, user_id)
        prop = await _create_property(test_db, pub.id, "journal-weekly.com", is_verified=True)
        slot = await _create_slot(test_db, pub.id, prop.id, "slot_journal_header", format_type=1)

        # Setup advertiser campaign for live serving verification
        await _create_campaign_and_creative(test_db)

        # 1. Create project
        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers,
            json={"name": "Journal Weekly", "slug": "journal-weekly"},
        )
        assert proj_res.status_code == 201
        proj_id = proj_res.json()["id"]
        home_page_id = proj_res.json()["pages"][0]["id"]

        # 2. Link publisher & property
        await client.post(
            f"/api/v1/website/projects/{proj_id}/publisher-link",
            headers=headers,
            json={"publisher_id": pub.id, "property_id": prop.id},
        )

        # 3. Add page with authorized ad placement
        page_update = await client.put(
            f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
            headers=headers,
            json={
                "title": "Home",
                "slug": "",
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "hero_1",
                            "type": "hero",
                            "is_visible": True,
                            "content": {"headline": "Welcome to Journal Weekly"},
                        },
                        {
                            "id": "ad_top_header",
                            "type": "ad_placement",
                            "is_visible": True,
                            "content": {
                                "placement_location": "in_article",
                                "ad_slot_id": "slot_journal_header",
                                "format": 1,
                                "width": 728,
                                "height": 90,
                            },
                        },
                    ],
                },
            },
        )
        assert page_update.status_code == 200

        # 4. Record consent
        await client.post(
            f"/api/v1/website/projects/{proj_id}/monetization/consent",
            headers=headers,
            json={
                "accepted_terms": True,
                "version": "v1.0",
                "scope": {"display_ads": True, "revenue_share_acknowledged": True},
            },
        )

        # 5. Publish project
        pub_res = await client.post(
            f"/api/v1/website/projects/{proj_id}/publish",
            headers=headers,
            json={"notes": "Stage 5.5 E2E Hardening Publication"},
        )
        assert pub_res.status_code == 200
        assert pub_res.json()["version_number"] >= 1

        # 6. Verify public page rendering produces expected ad container
        public_site = await client.get("/sites/journal-weekly")
        assert public_site.status_code == 200
        html = public_site.text
        # In non-monetized state, website_renderer strictly outputs non-billable sandbox placeholder
        assert 'class="rivinity-ad-placeholder"' in html
        assert "Non-Billable Sandbox" in html
        assert 'data-slot="slot_journal_header"' in html
        assert "/static/rivinity.js" not in html, "Non-monetized site must not load live ad script"

        # 7. Ad serving request security:
        # Serving ad request with matching Origin domain
        ad_res = await client.post(
            "/api/v1/ad/request",
            headers={"Origin": "https://journal-weekly.com"},
            json={
                "slot_id": "slot_journal_header",
                "url": "https://journal-weekly.com/articles/1",
            },
        )
        assert ad_res.status_code == 200
        ad_data = ad_res.json()
        assert "request_id" in ad_data
        assert isinstance(ad_data.get("ads"), list)

        # 8. Ad serving rejection for mismatched Origin domain
        bad_origin_res = await client.post(
            "/api/v1/ad/request",
            headers={"Origin": "https://malicious-hijack.com"},
            json={
                "slot_id": "slot_journal_header",
                "url": "https://malicious-hijack.com/exploit",
            },
        )
        assert bad_origin_res.status_code == 403
        assert "is not authorized for this ad slot" in bad_origin_res.json()["detail"]


# ── Area 6: Anti-Tampering, Preview Isolation & Defense-in-Depth ────────────────

class TestAntiTamperingAndDefenseInDepth:
    """Verifies CSP, X-Frame-Options, HMAC preview tokens, and bridge ad block exclusion."""

    async def test_preview_token_hmac_tampering_rejected(self, client: AsyncClient, test_db: AsyncSession):
        token, user_id = await _register_user(client, "pub_hmac@test.com")
        headers = {"Authorization": f"Bearer {token}"}
        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers,
            json={"name": "HMAC Site", "slug": "hmac-site"},
        )
        proj_id = proj_res.json()["id"]

        # Generate legitimate preview token
        token_res = await client.post(f"/api/v1/website/projects/{proj_id}/preview-token", headers=headers)
        assert token_res.status_code == 200
        valid_token = token_res.json()["preview_token"]

        # Legitimate token succeeds
        preview_res = await client.get(f"/preview/{valid_token}")
        assert preview_res.status_code == 200

        # Tampered token fails
        tampered_token = valid_token[:-4] + "ffff"
        bad_preview_res = await client.get(f"/preview/{tampered_token}")
        assert bad_preview_res.status_code == 403
        assert "Invalid or expired preview token" in bad_preview_res.json()["detail"]

    async def test_public_security_headers_and_csp(self, client: AsyncClient, test_db: AsyncSession):
        token, user_id = await _register_user(client, "pub_csp@test.com")
        headers = {"Authorization": f"Bearer {token}"}
        proj_res = await client.post(
            "/api/v1/website/projects",
            headers=headers,
            json={"name": "CSP Site", "slug": "csp-site"},
        )
        proj_id = proj_res.json()["id"]

        await client.post(
            f"/api/v1/website/projects/{proj_id}/publish",
            headers=headers,
            json={"notes": "Initial CSP publish"},
        )

        res = await client.get("/sites/csp-site")
        assert res.status_code == 200
        # Validate security headers
        h = res.headers
        assert "Content-Security-Policy" in h
        assert "X-Content-Type-Options" in h
        assert h["X-Content-Type-Options"] == "nosniff"
        assert "X-Frame-Options" in h

    async def test_website_renderer_bridge_excludes_ad_placement(self):
        """Website renderer's inline bridge JavaScript must strictly exclude ad blocks from mutation."""
        dummy_project = {"name": "Test", "slug": "test", "theme_config": {}}
        dummy_page = {
            "title": "Home",
            "slug": "",
            "is_home": True,
            "blocks": {
                "schema_version": 2,
                "blocks": [
                    {
                        "id": "blk_ad_test",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "format": 1,
                            "ad_slot_id": "slot_bridge_test",
                        },
                    }
                ],
            },
        }
        rendered = SafeWebsiteRenderer.render_published_page(
            project_dict=dummy_project,
            page_dict=dummy_page,
            all_pages_dict=[dummy_page],
            base_origin="http://test.localhost",
            is_preview=True,
            bridge_nonce="secure_test_nonce_bridge",
        )
        assert '<script nonce="secure_test_nonce_bridge" data-od-edit-bridge>' in rendered
        assert 'data-od-protected="true"' in rendered
        assert 'data-od-editable="false"' in rendered
        assert "meta.blockType === 'ad_placement'" in rendered
        assert ".block-ad-placement, .rivinity-ad-placeholder, .rivinity-live-ad-container, .rivinity-ad-slot" in rendered
