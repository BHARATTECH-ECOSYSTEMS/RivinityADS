"""
RivinityADS — Open Design Integration Phase 5 (Stage 5.1)
Backend Validation & Ad Serving Hardening Test Suite.

Verifies:
1. Publication rejects unknown slot IDs (fail-closed, not fail-open).
2. Publication rejects slots owned by another publisher (HTTP 403).
3. Publication rejects slots owned by another property (HTTP 403).
4. Publication rejects slots with NULL property_id (HTTP 400).
5. Publication rejects slots when publisher property is unverified or inactive (HTTP 400).
6. Publication rejects inactive slots (HTTP 400).
7. Publication rejects format mismatches between placement and slot (HTTP 400).
8. Anonymous ad requests reject unknown slot IDs with HTTP 404.
9. Ad requests reject slots with missing property_id (HTTP 400).
10. Ad requests reject slots with unverified property (HTTP 403).
11. Ad requests reject mismatched Origin domains (HTTP 403).
12. Ad requests reject mismatched Referer domains (HTTP 403).
13. Ad requests accept matching Origin and Referer domains.
14. Headerless request behavior follows compatibility policy (logged, allowed for S2S/SDK).
15. Invalid requests never reach candidate retrieval.
16. Invalid requests never generate usable tracking URLs or billable events.
17. Multiple placements referencing the same slot produce unique DOM container IDs.
18. Preview placeholders strictly remain non-billable sandbox placeholders.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.website_renderer import website_renderer
from liteads.management.api_key_service import ApiKeyService
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
            "company_name": "Phase 5 Testing",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


def _make_hmac_headers(
    raw_key: str,
    body: bytes,
    method: str = "POST",
    path: str = "/api/v1/ad/request",
) -> dict[str, str]:
    ts = str(int(time.time()))
    body_sha = hashlib.sha256(body).hexdigest()
    msg = f"{method.upper()}|{path}|{ts}|{body_sha}".encode("utf-8")
    sig = hmac.new(raw_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-API-Key": raw_key,
        "X-Timestamp": ts,
        "X-Signature": sig,
    }


async def _create_publisher_for_user(
    session: AsyncSession,
    user_id: int,
    name: str = "Phase 5 Publisher",
) -> Publisher:
    pub = Publisher(
        name=name,
        owner_user_id=user_id,
        status=Status.ACTIVE,
        payout_details={
            "account_number": "987654321098",
            "bank_name": "State Bank of India",
            "upi_id": "phase5@upi",
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
    domain: str = "chronicle.com",
    is_verified: bool = True,
    status: Status = Status.ACTIVE,
) -> PublisherProperty:
    prop = PublisherProperty(
        publisher_id=publisher_id,
        domain=domain,
        name="Chronicle Domain",
        verification_status="VERIFIED" if is_verified else "PENDING",
        verification_method="DNS_TXT",
        verification_token="tok_phase5_verified",
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
    slot_id: str = "slot_banner_top",
    format_type: int = 1,
    status: Status = Status.ACTIVE,
) -> AdSlot:
    slot = AdSlot(
        publisher_id=publisher_id,
        property_id=property_id,
        slot_id=slot_id,
        name="Header Slot",
        format=format_type,
        width=728,
        height=90,
        floor_price=1.50,
        status=status,
    )
    session.add(slot)
    await session.commit()
    await session.refresh(slot)
    return slot


async def _create_campaign_and_creative(session: AsyncSession) -> tuple[Campaign, Creative]:
    adv = Advertiser(name="Test Advertiser", balance=Decimal("1000.00"), status=Status.ACTIVE)
    session.add(adv)
    await session.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="Verified Campaign",
        budget_total=Decimal("500.00"),
        status=Status.ACTIVE,
    )
    session.add(camp)
    await session.flush()

    adset = AdSet(
        campaign_id=camp.id,
        name="Standard AdSet",
        bid_amount=Decimal("2.00"),
        status=Status.ACTIVE,
    )
    session.add(adset)
    await session.flush()

    creative = Creative(
        campaign_id=camp.id,
        ad_set_id=adset.id,
        title="Live Sponsored Creative",
        description="High conversion verified ad",
        image_url="https://images.example.com/banner.png",
        landing_url="https://landing.example.com/promo",
        status=Status.ACTIVE,
        width=728,
        height=90,
    )
    session.add(creative)
    await session.commit()
    await session.refresh(creative)
    return camp, creative


# ── 1. Fail-Closed Publication Validation Tests (Stage 5.1A) ───────────────────

async def test_publish_rejects_unknown_slot_id(client: AsyncClient, test_db: AsyncSession):
    """Publication must reject non-existent or forged ad_slot_id (fail-closed, not fail-open)."""
    token, user_id = await _register_user(client, "pub_unknown@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "unknown-test.com", is_verified=True)

    # Create website project
    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Unknown Slot Site", "slug": "unknown-slot-site"},
    )
    assert proj_res.status_code == 201
    proj_id = proj_res.json()["id"]
    home_page_id = proj_res.json()["pages"][0]["id"]

    # Link publisher and property
    await client.post(
        f"/api/v1/website/projects/{proj_id}/publisher-link",
        headers=headers,
        json={"publisher_id": pub.id, "property_id": prop.id},
    )

    # Insert page with completely fake/forged ad_slot_id
    await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
        headers=headers,
        json={
            "title": "Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_fake",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": "totally_forged_slot_999",
                            "format": 1,
                        },
                    }
                ],
            },
        },
    )

    # Publication attempt must be rejected with HTTP 400
    pub_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/publish",
        headers=headers,
        json={"notes": "Attempt forged publish"},
    )
    assert pub_res.status_code == 400
    assert "does not exist" in pub_res.json()["detail"]


async def test_publish_rejects_slot_owned_by_another_publisher(client: AsyncClient, test_db: AsyncSession):
    """Publication must reject slot belonging to a different publisher tenant."""
    # Publisher A (victim)
    token_a, user_a = await _register_user(client, "pub_a@test.com")
    pub_a = await _create_publisher_for_user(test_db, user_a)
    prop_a = await _create_property(test_db, pub_a.id, "victim.com", is_verified=True)
    slot_a = await _create_slot(test_db, pub_a.id, prop_a.id, "slot_victim_1", format_type=1)

    # Publisher B (attacker)
    token_b, user_b = await _register_user(client, "pub_b@test.com")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    pub_b = await _create_publisher_for_user(test_db, user_b)
    prop_b = await _create_property(test_db, pub_b.id, "attacker.com", is_verified=True)

    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers_b,
        json={"name": "Attacker Site", "slug": "attacker-slot-site"},
    )
    proj_id = proj_res.json()["id"]
    home_page_id = proj_res.json()["pages"][0]["id"]

    await client.post(
        f"/api/v1/website/projects/{proj_id}/publisher-link",
        headers=headers_b,
        json={"publisher_id": pub_b.id, "property_id": prop_b.id},
    )

    # Attacker tries to inject Publisher A's slot
    await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
        headers=headers_b,
        json={
            "title": "Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_victim",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": slot_a.slot_id,
                            "format": 1,
                        },
                    }
                ],
            },
        },
    )

    pub_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/publish",
        headers=headers_b,
        json={"notes": "Steal victim ads"},
    )
    assert pub_res.status_code == 403
    assert "does not belong" in pub_res.json()["detail"]


async def test_publish_rejects_slot_owned_by_another_property(client: AsyncClient, test_db: AsyncSession):
    """Publication must reject slot belonging to publisher's Property 1 placed on Property 2."""
    token, user_id = await _register_user(client, "pub_props@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id)

    prop1 = await _create_property(test_db, pub.id, "news.com", is_verified=True)
    prop2 = await _create_property(test_db, pub.id, "sports.com", is_verified=True)
    slot_prop1 = await _create_slot(test_db, pub.id, prop1.id, "slot_news_only", format_type=1)

    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Sports Site", "slug": "sports-site"},
    )
    proj_id = proj_res.json()["id"]
    home_page_id = proj_res.json()["pages"][0]["id"]

    # Link project to Property 2 (sports.com)
    await client.post(
        f"/api/v1/website/projects/{proj_id}/publisher-link",
        headers=headers,
        json={"publisher_id": pub.id, "property_id": prop2.id},
    )

    # Attempt to use slot belonging to Property 1 (news.com)
    await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
        headers=headers,
        json={
            "title": "Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_mismatch_prop",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": slot_prop1.slot_id,
                            "format": 1,
                        },
                    }
                ],
            },
        },
    )

    pub_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/publish",
        headers=headers,
        json={"notes": "Cross-property publish"},
    )
    assert pub_res.status_code == 403
    assert "does not belong to this website's publisher property" in pub_res.json()["detail"]


async def test_publish_rejects_slot_with_null_property_id(client: AsyncClient, test_db: AsyncSession):
    """Publication must reject ad slot that has no property_id associated."""
    token, user_id = await _register_user(client, "pub_null_prop@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "myblog.com", is_verified=True)

    # Slot created without property_id
    slot_null = await _create_slot(test_db, pub.id, None, "slot_no_prop", format_type=1)

    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Blog Site", "slug": "blog-null-prop-site"},
    )
    proj_id = proj_res.json()["id"]
    home_page_id = proj_res.json()["pages"][0]["id"]

    await client.post(
        f"/api/v1/website/projects/{proj_id}/publisher-link",
        headers=headers,
        json={"publisher_id": pub.id, "property_id": prop.id},
    )

    await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
        headers=headers,
        json={
            "title": "Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_null_prop",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": slot_null.slot_id,
                            "format": 1,
                        },
                    }
                ],
            },
        },
    )

    pub_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/publish",
        headers=headers,
        json={"notes": "Publish with null property slot"},
    )
    assert pub_res.status_code == 400
    assert "not associated with any publisher property" in pub_res.json()["detail"]


async def test_publish_rejects_unverified_property(client: AsyncClient, test_db: AsyncSession):
    """Publication must reject slots when the linked property is not in VERIFIED status."""
    token, user_id = await _register_user(client, "pub_unverified@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id)

    # Unverified property
    prop_unver = await _create_property(test_db, pub.id, "unverified.com", is_verified=False)
    slot = await _create_slot(test_db, pub.id, prop_unver.id, "slot_unverified", format_type=1)

    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Unverified Site", "slug": "unverified-site"},
    )
    assert proj_res.status_code == 201, proj_res.text
    proj_id = proj_res.json()["id"]
    home_page_id = proj_res.json()["pages"][0]["id"]

    await client.post(
        f"/api/v1/website/projects/{proj_id}/publisher-link",
        headers=headers,
        json={"publisher_id": pub.id, "property_id": prop_unver.id},
    )

    await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
        headers=headers,
        json={
            "title": "Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_unver",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": slot.slot_id,
                            "format": 1,
                        },
                    }
                ],
            },
        },
    )

    pub_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/publish",
        headers=headers,
        json={"notes": "Publish unverified property"},
    )
    assert pub_res.status_code == 400
    assert "not active or verified" in pub_res.json()["detail"]


async def test_publish_rejects_inactive_slot(client: AsyncClient, test_db: AsyncSession):
    """Publication must reject inactive or paused ad slots."""
    token, user_id = await _register_user(client, "pub_inactive_slot@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "activeprop.com", is_verified=True)

    # Inactive slot (Status.PAUSED = 2 or INACTIVE = 0)
    slot_inactive = await _create_slot(test_db, pub.id, prop.id, "slot_inactive", format_type=1, status=Status.PAUSED)

    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Inactive Slot Site", "slug": "inactive-slot-site"},
    )
    proj_id = proj_res.json()["id"]
    home_page_id = proj_res.json()["pages"][0]["id"]

    await client.post(
        f"/api/v1/website/projects/{proj_id}/publisher-link",
        headers=headers,
        json={"publisher_id": pub.id, "property_id": prop.id},
    )

    await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
        headers=headers,
        json={
            "title": "Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_inactive",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": slot_inactive.slot_id,
                            "format": 1,
                        },
                    }
                ],
            },
        },
    )

    pub_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/publish",
        headers=headers,
        json={"notes": "Publish with inactive slot"},
    )
    assert pub_res.status_code == 400
    assert "is not active" in pub_res.json()["detail"]


async def test_publish_rejects_format_mismatch(client: AsyncClient, test_db: AsyncSession):
    """Publication must reject when ad_placement format does not match authoritative AdSlot format."""
    token, user_id = await _register_user(client, "pub_fmt_mismatch@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "format-test.com", is_verified=True)

    # Slot is format 2 (Native)
    slot_native = await _create_slot(test_db, pub.id, prop.id, "slot_native_fmt2", format_type=2)

    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Format Mismatch Site", "slug": "fmt-mismatch-site"},
    )
    proj_id = proj_res.json()["id"]
    home_page_id = proj_res.json()["pages"][0]["id"]

    await client.post(
        f"/api/v1/website/projects/{proj_id}/publisher-link",
        headers=headers,
        json={"publisher_id": pub.id, "property_id": prop.id},
    )

    # Placement specifies format 1 (Banner), but slot is format 2 (Native)
    await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
        headers=headers,
        json={
            "title": "Home",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "ad_fmt_err",
                        "type": "ad_placement",
                        "is_visible": True,
                        "content": {
                            "placement_location": "in_article",
                            "ad_slot_id": slot_native.slot_id,
                            "format": 1,  # Mismatch!
                        },
                    }
                ],
            },
        },
    )

    pub_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/publish",
        headers=headers,
        json={"notes": "Publish format mismatch"},
    )
    assert pub_res.status_code == 400
    assert "Format mismatch" in pub_res.json()["detail"]
    assert "placement requires format 1, but slot is configured as format 2" in pub_res.json()["detail"]


# ── 2. Ad Request Hardening & Domain Validation Tests (Stage 5.1B) ─────────────

async def test_anonymous_ad_request_rejects_unknown_slot(client: AsyncClient, test_db: AsyncSession):
    """Anonymous ad requests for non-existent slot IDs must receive HTTP 404, never candidate ads."""
    response = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": "non_existent_slot_404", "num_ads": 1},
    )
    assert response.status_code == 404
    assert "Ad slot not found" in response.json()["detail"]


async def test_ad_request_rejects_missing_property(client: AsyncClient, test_db: AsyncSession):
    """Ad requests for a slot without an associated publisher property must be rejected with HTTP 400."""
    token, user_id = await _register_user(client, "pub_req_null_prop@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    slot_null = await _create_slot(test_db, pub.id, None, "slot_req_no_prop", format_type=1)

    response = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": "https://pub-domain.com"},
        json={"slot_id": slot_null.slot_id, "num_ads": 1},
    )
    assert response.status_code == 400
    assert "not associated with a publisher property" in response.json()["detail"]


async def test_ad_request_rejects_unverified_property(client: AsyncClient, test_db: AsyncSession):
    """Ad requests for a slot whose property is not VERIFIED must be rejected with HTTP 403."""
    token, user_id = await _register_user(client, "pub_req_unver@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    prop_unver = await _create_property(test_db, pub.id, "unver-serving.com", is_verified=False)
    slot = await _create_slot(test_db, pub.id, prop_unver.id, "slot_serving_unver", format_type=1)

    response = await client.post(
        "/api/v1/ad/request",
        headers={"Origin": "https://unver-serving.com"},
        json={"slot_id": slot.slot_id, "num_ads": 1},
    )
    assert response.status_code == 403
    assert "Publisher property is not verified" in response.json()["detail"]


async def test_ad_request_rejects_mismatched_origin_domain(client: AsyncClient, test_db: AsyncSession):
    """Ad requests originating from an unauthorized domain must be rejected with HTTP 403."""
    token, user_id = await _register_user(client, "pub_origin_test@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "legit-domain.com", is_verified=True)
    slot = await _create_slot(test_db, pub.id, prop.id, "slot_origin_protected", format_type=1)
    await _create_campaign_and_creative(test_db)

    # Request with mismatched Origin header
    response = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot.slot_id, "num_ads": 1},
        headers={"Origin": "https://evil-attacker.com"},
    )
    assert response.status_code == 403
    assert "Origin 'evil-attacker.com' is not authorized" in response.json()["detail"]


async def test_ad_request_rejects_mismatched_referer_domain(client: AsyncClient, test_db: AsyncSession):
    """Ad requests with a Referer header from an unauthorized domain must be rejected with HTTP 403."""
    token, user_id = await _register_user(client, "pub_referer_test@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "authorized-news.org", is_verified=True)
    slot = await _create_slot(test_db, pub.id, prop.id, "slot_referer_protected", format_type=1)
    await _create_campaign_and_creative(test_db)

    # Request with mismatched Referer header
    response = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot.slot_id, "num_ads": 1},
        headers={"Referer": "https://fake-authorized-news.org/article/10"},
    )
    assert response.status_code == 403
    assert "Referer 'fake-authorized-news.org' is not authorized" in response.json()["detail"]


async def test_ad_request_accepts_matching_origin_and_referer(client: AsyncClient, test_db: AsyncSession):
    """Ad requests with exact matching Origin and Referer headers succeed, but apex property rejects www."""
    token, user_id = await _register_user(client, "pub_match_test@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "goodmedia.com", is_verified=True)
    slot = await _create_slot(test_db, pub.id, prop.id, "slot_good_media", format_type=1)
    await _create_campaign_and_creative(test_db)

    # 1. Exact apex match succeeds
    response = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot.slot_id, "num_ads": 1},
        headers={
            "Origin": "https://goodmedia.com",
            "Referer": "https://goodmedia.com/posts/tech",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "request_id" in data
    assert isinstance(data.get("ads"), list)

    # 2. Apex-only verification does NOT authorize www (no implicit alias)
    response_www = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot.slot_id, "num_ads": 1},
        headers={
            "Origin": "https://www.goodmedia.com",
            "Referer": "https://www.goodmedia.com/posts/tech",
        },
    )
    assert response_www.status_code == 403
    assert "is not authorized for this ad slot" in response_www.json()["detail"]


async def test_exact_www_property_matches_www_and_rejects_apex(client: AsyncClient, test_db: AsyncSession):
    """Property verified as www.domain.com authorizes ONLY www and rejects apex domain."""
    token, user_id = await _register_user(client, "pub_www_test@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    prop_www = await _create_property(test_db, pub.id, "www.independentmedia.com", is_verified=True)
    slot_www = await _create_slot(test_db, pub.id, prop_www.id, "slot_www_media", format_type=1)
    await _create_campaign_and_creative(test_db)

    # 1. Exact www match succeeds
    res_ok = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot_www.slot_id, "num_ads": 1},
        headers={
            "Origin": "https://www.independentmedia.com",
            "Referer": "https://www.independentmedia.com/news",
        },
    )
    assert res_ok.status_code == 200
    assert "request_id" in res_ok.json()

    # 2. www-only verification does NOT authorize apex domain
    res_apex = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot_www.slot_id, "num_ads": 1},
        headers={
            "Origin": "https://independentmedia.com",
            "Referer": "https://independentmedia.com/news",
        },
    )
    assert res_apex.status_code == 403
    assert "is not authorized for this ad slot" in res_apex.json()["detail"]


async def test_headerless_anonymous_request_rejected(client: AsyncClient, test_db: AsyncSession):
    """VULN-5.1-01: Anonymous requests missing Origin and Referer must be rejected with HTTP 403."""
    token, user_id = await _register_user(client, "pub_anon_hl@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "compat-portal.com", is_verified=True)
    slot = await _create_slot(test_db, pub.id, prop.id, "slot_compat_portal", format_type=1)
    await _create_campaign_and_creative(test_db)

    # Headerless request: no Origin or Referer passed by anonymous caller
    response = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot.slot_id, "num_ads": 1},
    )
    assert response.status_code == 403
    assert "missing Origin or Referer" in response.json()["detail"]


async def test_authenticated_jwt_headerless_request_rejected(client: AsyncClient, test_db: AsyncSession):
    """VULN-5.1-01: Authenticated non-S2S callers (JWT) cannot make headerless requests (HTTP 403)."""
    token, user_id = await _register_user(client, "pub_jwt_hl@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "jwt-portal.com", is_verified=True)
    slot = await _create_slot(test_db, pub.id, prop.id, "slot_jwt_portal", format_type=1)
    await _create_campaign_and_creative(test_db)

    # JWT authenticated caller with no Origin or Referer
    response = await client.post(
        "/api/v1/ad/request",
        headers={"Authorization": f"Bearer {token}"},
        json={"slot_id": slot.slot_id, "num_ads": 1},
    )
    assert response.status_code == 403
    assert "missing Origin or Referer" in response.json()["detail"]


async def test_authenticated_s2s_headerless_request_succeeds(client: AsyncClient, test_db: AsyncSession):
    """VULN-5.1-01: Authenticated S2S callers (HMAC API key) succeed headerlessly for valid publisher slots."""
    token, user_id = await _register_user(client, "pub_s2s_hl@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "s2s-portal.com", is_verified=True)
    slot = await _create_slot(test_db, pub.id, prop.id, "slot_s2s_portal", format_type=1)
    await _create_campaign_and_creative(test_db)

    # Create active API key for publisher
    db_key, raw_key = await ApiKeyService.create_api_key(
        test_db,
        publisher_id=pub.id,
        schema=type("Schema", (), {
            "name": "S2S Headerless Key",
            "is_sandbox": False,
            "rate_limit_per_minute": 60,
            "daily_quota": 10000,
        })(),
    )

    body = json.dumps({"slot_id": slot.slot_id, "num_ads": 1}).encode("utf-8")
    headers = _make_hmac_headers(raw_key, body)
    # Ensure neither Origin nor Referer is included
    assert "Origin" not in headers
    assert "Referer" not in headers

    with patch("liteads.ad_server.services.rate_limit_service.RateLimitService.check_rate_limit", return_value=(True, 59, 60)), \
         patch("liteads.ad_server.services.rate_limit_service.RateLimitService.check_daily_quota", return_value=(True, 9999, 86400)), \
         patch("liteads.ad_server.services.rate_limit_service.RateLimitService.record_usage_metric", new_callable=AsyncMock):
        response = await client.post(
            "/api/v1/ad/request",
            content=body,
            headers=headers,
        )
    assert response.status_code == 200
    data = response.json()
    assert "request_id" in data
    assert isinstance(data.get("ads"), list)


async def test_arbitrary_unauthorized_subdomains_rejected(client: AsyncClient, test_db: AsyncSession):
    """VULN-5.1-02: Ambient wildcard subdomain trust eliminated; unauthorized subdomains rejected with HTTP 403."""
    token, user_id = await _register_user(client, "pub_sub_guard@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    prop = await _create_property(test_db, pub.id, "maindomain.com", is_verified=True)
    slot = await _create_slot(test_db, pub.id, prop.id, "slot_sub_guard", format_type=1)
    await _create_campaign_and_creative(test_db)

    # 1. Attacker-controlled subdomain in Origin
    res1 = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot.slot_id, "num_ads": 1},
        headers={"Origin": "https://attacker.maindomain.com"},
    )
    assert res1.status_code == 403
    assert "is not authorized for this ad slot" in res1.json()["detail"]

    # 2. Arbitrary unverified subdomain in Referer
    res2 = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot.slot_id, "num_ads": 1},
        headers={"Referer": "https://blog.maindomain.com/article/1"},
    )
    assert res2.status_code == 403
    assert "is not authorized for this ad slot" in res2.json()["detail"]


async def test_explicit_subdomain_property_authorized_specifically(client: AsyncClient, test_db: AsyncSession):
    """VULN-5.1-02: Explicitly verified subdomain properties succeed, while root domain cannot claim their slots."""
    token, user_id = await _register_user(client, "pub_explicit_sub@test.com")
    pub = await _create_publisher_for_user(test_db, user_id)
    # Verified specifically for blog.specificdomain.com
    prop_sub = await _create_property(test_db, pub.id, "blog.specificdomain.com", is_verified=True)
    slot_sub = await _create_slot(test_db, pub.id, prop_sub.id, "slot_specific_sub", format_type=1)
    await _create_campaign_and_creative(test_db)

    # Exact match for the authorized subdomain succeeds
    res_ok = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot_sub.slot_id, "num_ads": 1},
        headers={"Origin": "https://blog.specificdomain.com"},
    )
    assert res_ok.status_code == 200

    # Apex root domain is unauthorized for a slot explicitly bound to blog.specificdomain.com
    res_apex = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot_sub.slot_id, "num_ads": 1},
        headers={"Origin": "https://specificdomain.com"},
    )
    assert res_apex.status_code == 403
    assert "is not authorized for this ad slot" in res_apex.json()["detail"]

    # www prefix on the subdomain is also unauthorized (exact match only)
    res_www = await client.post(
        "/api/v1/ad/request",
        json={"slot_id": slot_sub.slot_id, "num_ads": 1},
        headers={"Origin": "https://www.blog.specificdomain.com"},
    )
    assert res_www.status_code == 403
    assert "is not authorized for this ad slot" in res_www.json()["detail"]


async def test_invalid_requests_never_reach_candidate_retrieval(client: AsyncClient, test_db: AsyncSession):
    """Invalid requests fail early before invoking ad_service.serve_ads or touching campaigns."""
    with patch("liteads.ad_server.services.ad_service.AdService.serve_ads", new_callable=AsyncMock) as mock_serve:
        # 1. Non-existent slot ID
        res1 = await client.post("/api/v1/ad/request", json={"slot_id": "ghost_slot_000", "num_ads": 1})
        assert res1.status_code == 404
        mock_serve.assert_not_called()

        # 2. Origin mismatch
        token, user_id = await _register_user(client, "pub_mock_guard@test.com")
        pub = await _create_publisher_for_user(test_db, user_id)
        prop = await _create_property(test_db, pub.id, "safe-domain.io", is_verified=True)
        slot = await _create_slot(test_db, pub.id, prop.id, "slot_safe_domain", format_type=1)

        res2 = await client.post(
            "/api/v1/ad/request",
            json={"slot_id": slot.slot_id, "num_ads": 1},
            headers={"Origin": "https://hacker.com"},
        )
        assert res2.status_code == 403
        mock_serve.assert_not_called()

        # 3. Unauthorized subdomain (VULN-5.1-02)
        res3 = await client.post(
            "/api/v1/ad/request",
            json={"slot_id": slot.slot_id, "num_ads": 1},
            headers={"Origin": "https://attacker.safe-domain.io"},
        )
        assert res3.status_code == 403
        mock_serve.assert_not_called()

        # 4. Anonymous headerless request (VULN-5.1-01)
        res4 = await client.post(
            "/api/v1/ad/request",
            json={"slot_id": slot.slot_id, "num_ads": 1},
        )
        assert res4.status_code == 403
        mock_serve.assert_not_called()

        # 5. Authenticated JWT headerless request (VULN-5.1-01)
        res5 = await client.post(
            "/api/v1/ad/request",
            headers={"Authorization": f"Bearer {token}"},
            json={"slot_id": slot.slot_id, "num_ads": 1},
        )
        assert res5.status_code == 403
        mock_serve.assert_not_called()


async def test_invalid_requests_never_create_billable_events(client: AsyncClient, test_db: AsyncSession):
    """Rejected ad requests never generate signed tracking URLs, preventing unauthorized billable events."""
    # Attempting to track an event without a valid signature generated by the server fails closed
    fake_track_res = await client.post(
        "/api/v1/event/track",
        json={
            "request_id": "req_unauthorized_999",
            "ad_id": "ad_1_1",
            "event_type": "impression",
            "sig": "forged_signature_token",
            "slot_id": "forged_slot",
        },
    )
    assert fake_track_res.status_code in (400, 403)


# ── 3. Unique Live Ad Container IDs & Preview Safeguards (Stage 5.1C) ──────────

async def test_multiple_placements_same_slot_produce_unique_dom_ids():
    """Website renderer disambiguates multiple ad placements referencing the same slot ID using block.id."""
    project_dict = {
        "name": "Multi Ad Site",
        "slug": "multi-ad-site",
        "theme_config": {},
    }
    page_dict = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "blk_ad_top",
                    "type": "ad_placement",
                    "is_visible": True,
                    "content": {
                        "placement_location": "header_banner",
                        "ad_slot_id": "slot_shared_leaderboard",
                        "format": 1,
                    },
                },
                {
                    "id": "blk_ad_bottom",
                    "type": "ad_placement",
                    "is_visible": True,
                    "content": {
                        "placement_location": "footer",
                        "ad_slot_id": "slot_shared_leaderboard",
                        "format": 1,
                    },
                },
            ],
        },
    }

    html = website_renderer.render_published_page(
        project_dict=project_dict,
        page_dict=page_dict,
        all_pages_dict=[page_dict],
        base_origin="https://multi-ad-site.rivinity.com",
        is_preview=False,
        is_monetized=True,
    )

    # Both blocks reference the same slot, but their DOM container element IDs must be distinct
    assert 'id="rivinity-ad-slot_shared_leaderboard-blk_ad_top"' in html
    assert 'id="rivinity-ad-slot_shared_leaderboard-blk_ad_bottom"' in html
    # But data-slot-id and script data-slot remain the authoritative slot ID
    assert 'data-slot-id="slot_shared_leaderboard"' in html
    assert 'data-slot="slot_shared_leaderboard"' in html


async def test_existing_preview_placeholders_remain_non_billable():
    """In draft preview mode (is_preview=True), ad placements always render non-billable sandbox placeholders."""
    project_dict = {"name": "Preview Site", "slug": "preview-site", "theme_config": {}}
    page_dict = {
        "title": "Home",
        "slug": "",
        "blocks": {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "blk_ad_prev",
                    "type": "ad_placement",
                    "is_visible": True,
                    "content": {
                        "placement_location": "in_article",
                        "ad_slot_id": "live_slot_123",
                        "format": 1,
                    },
                }
            ],
        },
    }

    html = website_renderer.render_published_page(
        project_dict=project_dict,
        page_dict=page_dict,
        all_pages_dict=[page_dict],
        base_origin="https://preview-site.rivinity.com",
        is_preview=True,
        is_monetized=True,
    )

    # Must NOT render live container or live SDK script tag
    assert "rivinity-live-ad-container" not in html
    assert "/static/rivinity.js" not in html
    # Must render sandbox placeholder with non-billable label
    assert "rivinity-ad-placeholder" in html
    assert "Non-Billable Sandbox" in html
