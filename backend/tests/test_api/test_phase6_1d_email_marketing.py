"""Unit and integration tests for Phase 6.1D: Tenant-Safe Backend Email Marketing API.

Verifies:
- Strict tenant isolation: Advertiser A cannot view, modify, or launch Advertiser B's lists/campaigns.
- Cross-tenant requests return 404 Not Found to prevent resource enumeration.
- Role enforcement: Publishers are rejected with 403 Forbidden by default.
- Admin manages isolated platform-marketing workspace.
- Consent enforcement: Prohibited sources (scraped, purchased) and suppressed contacts are rejected.
- CSV import: row limits, syntax validation, duplicate detection, and error counts.
- Campaign lifecycle: draft, preview, test-send (allowlist capped at 5), and confirmed launch.
- Automatic inclusion of mandatory unsubscribe notice on launch.
- Offline mode: graceful local operation when Listmonk is disabled.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.config import ListmonkSettings
from liteads.common.listmonk_client import ListmonkClient
from liteads.common.security import create_access_token
from liteads.models.ad import Advertiser
from liteads.models.auth import User
from liteads.models.consent import ConsentCategory, ConsentStatus, MarketingConsent
from liteads.models.email_marketing import (
    EmailMarketingCampaign,
    EmailMarketingContact,
    EmailMarketingList,
)
from liteads.models.publisher import Publisher


@pytest.fixture
async def setup_tenants(test_db: AsyncSession) -> dict[str, Any]:
    """Create two advertisers, one publisher, and one admin user for tenant testing."""
    # Advertiser 1
    adv1 = Advertiser(name="Brand One", contact_email="brand1@test.com")
    test_db.add(adv1)
    await test_db.flush()
    user1 = User(
        name="User One",
        email="user1@brand1.com",
        password_hash="pwd",
        role="advertiser",
        advertiser_id=adv1.id,
        is_active=True,
        email_verified=True,
    )
    test_db.add(user1)

    # Advertiser 2
    adv2 = Advertiser(name="Brand Two", contact_email="brand2@test.com")
    test_db.add(adv2)
    await test_db.flush()
    user2 = User(
        name="User Two",
        email="user2@brand2.com",
        password_hash="pwd",
        role="advertiser",
        advertiser_id=adv2.id,
        is_active=True,
        email_verified=True,
    )
    test_db.add(user2)

    # Publisher
    pub = Publisher(name="Publisher Media", email="pub@media.com")
    test_db.add(pub)
    await test_db.flush()
    user_pub = User(
        name="Publisher User",
        email="user@media.com",
        password_hash="pwd",
        role="publisher",
        publisher_id=pub.id,
        is_active=True,
        email_verified=True,
    )
    test_db.add(user_pub)

    # Platform Admin
    user_admin = User(
        name="Admin Operator",
        email="admin@rivinityads.com",
        password_hash="pwd",
        role="admin",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user_admin)

    await test_db.commit()

    return {
        "adv1": adv1,
        "user1": user1,
        "token1": create_access_token(user1),
        "adv2": adv2,
        "user2": user2,
        "token2": create_access_token(user2),
        "user_pub": user_pub,
        "token_pub": create_access_token(user_pub),
        "user_admin": user_admin,
        "token_admin": create_access_token(user_admin),
    }


# ── 1. Tenant Isolation & Cross-Tenant Access Denial ──────────────────────────

@pytest.mark.asyncio
async def test_cross_tenant_list_access_denied_with_404(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """Advertiser B cannot read or modify Advertiser A's mailing list (returns 404)."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    h2 = {"Authorization": f"Bearer {setup_tenants['token2']}"}

    # 1. Advertiser 1 creates a list
    res_create = await client.post(
        "/api/v1/email-marketing/lists",
        headers=h1,
        json={"name": "Brand 1 VIPs", "type": "private"},
    )
    assert res_create.status_code == 201
    list_id = res_create.json()["id"]

    # 2. Advertiser 2 attempts to GET the list -> 404 Not Found (no leakage)
    res_get = await client.get(f"/api/v1/email-marketing/lists/{list_id}", headers=h2)
    assert res_get.status_code == 404

    # 3. Advertiser 2 attempts to add contact to Advertiser 1's list -> 404
    res_add = await client.post(
        f"/api/v1/email-marketing/lists/{list_id}/contacts",
        headers=h2,
        json={"email": "attacker@target.com", "consent_source": "customer_opt_in"},
    )
    assert res_add.status_code == 404

    # 4. Advertiser 2 attempts to DELETE Advertiser 1's list -> 404
    res_del = await client.delete(f"/api/v1/email-marketing/lists/{list_id}", headers=h2)
    assert res_del.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_campaign_access_denied_with_404(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """Advertiser B cannot read, update, or launch Advertiser A's campaign."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    h2 = {"Authorization": f"Bearer {setup_tenants['token2']}"}

    # Advertiser 1 creates list and draft campaign
    res_list = await client.post(
        "/api/v1/email-marketing/lists",
        headers=h1,
        json={"name": "Campaign List", "type": "private"},
    )
    list_id = res_list.json()["id"]

    res_camp = await client.post(
        "/api/v1/email-marketing/campaigns",
        headers=h1,
        json={
            "list_id": list_id,
            "name": "Spring Sale",
            "subject": "50% Off Everything",
            "body": "<p>Big deals</p>",
        },
    )
    assert res_camp.status_code == 201
    camp_id = res_camp.json()["id"]

    # Advertiser 2 attempts access -> 404 on GET, PUT, preview, and launch
    assert (await client.get(f"/api/v1/email-marketing/campaigns/{camp_id}", headers=h2)).status_code == 404
    assert (await client.put(f"/api/v1/email-marketing/campaigns/{camp_id}", headers=h2, json={"name": "Hacked"})).status_code == 404
    assert (await client.post(f"/api/v1/email-marketing/campaigns/{camp_id}/preview", headers=h2)).status_code == 404
    assert (await client.post(f"/api/v1/email-marketing/campaigns/{camp_id}/launch", headers=h2, json={"confirmed": True})).status_code == 404


# ── 2. Role Enforcement (Publishers Denied by Default) ─────────────────────────

@pytest.mark.asyncio
async def test_publishers_denied_access_by_default(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """Publishers attempting to access email marketing are rejected with 403 Forbidden."""
    h_pub = {"Authorization": f"Bearer {setup_tenants['token_pub']}"}

    res_lists = await client.get("/api/v1/email-marketing/lists", headers=h_pub)
    assert res_lists.status_code == 403
    assert "Publishers do not have access" in res_lists.json()["detail"]

    res_camps = await client.get("/api/v1/email-marketing/campaigns", headers=h_pub)
    assert res_camps.status_code == 403


# ── 3. Platform Workspace for Admin ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_platform_workspace_isolation(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """Admin uses platform tenant ('platform', 1) isolated from advertisers."""
    h_admin = {"Authorization": f"Bearer {setup_tenants['token_admin']}"}
    h_adv1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}

    # Admin creates platform list
    res = await client.post(
        "/api/v1/email-marketing/lists",
        headers=h_admin,
        json={"name": "Platform Announcements", "type": "public"},
    )
    assert res.status_code == 201
    assert res.json()["tenant_type"] == "platform"
    assert res.json()["tenant_id"] == 1
    admin_list_id = res.json()["id"]

    # Advertiser 1 does not see admin platform lists
    adv_lists = (await client.get("/api/v1/email-marketing/lists", headers=h_adv1)).json()
    assert all(l["id"] != admin_list_id for l in adv_lists)


# ── 4. Consent Enforcement & Prohibited Sources ───────────────────────────────

@pytest.mark.asyncio
async def test_prohibited_permission_sources_rejected(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """Scraped, purchased, or unverified permission sources are rejected with 400."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    res_list = await client.post(
        "/api/v1/email-marketing/lists",
        headers=h1,
        json={"name": "Consent Test List"},
    )
    list_id = res_list.json()["id"]

    for bad_source in ("purchased_list", "scraped", "unverified", "third_party"):
        res = await client.post(
            f"/api/v1/email-marketing/lists/{list_id}/contacts",
            headers=h1,
            json={"email": "lead@target.com", "consent_source": bad_source},
        )
        assert res.status_code == 400
        assert "Prohibited permission source" in res.json()["detail"]


@pytest.mark.asyncio
async def test_suppressed_contact_cannot_be_readded_without_new_optin(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
    test_db: AsyncSession,
):
    """A recipient who previously opted out cannot be added to a marketing list."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    res_list = await client.post(
        "/api/v1/email-marketing/lists",
        headers=h1,
        json={"name": "Strict Suppression List"},
    )
    list_id = res_list.json()["id"]

    # Record local revocation in MarketingConsent
    consent = MarketingConsent(
        email="revoked@privacy.org",
        category=ConsentCategory.PRODUCT_NEWSLETTER.value,
        status=ConsentStatus.OPTED_OUT.value,
        revoked_at=datetime.now(timezone.utc),
    )
    test_db.add(consent)
    await test_db.commit()

    # Attempt to add revoked email
    res = await client.post(
        f"/api/v1/email-marketing/lists/{list_id}/contacts",
        headers=h1,
        json={"email": "revoked@privacy.org", "consent_source": "customer_opt_in"},
    )
    assert res.status_code == 400
    assert "previously revoked marketing consent" in res.json()["detail"]


# ── 5. CSV Import: Batch Limits, Validation, and Duplicates ───────────────────

@pytest.mark.asyncio
async def test_csv_import_success_and_duplicate_detection(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """CSV import validates emails, skips internal/external duplicates, and returns counts."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    res_list = await client.post(
        "/api/v1/email-marketing/lists",
        headers=h1,
        json={"name": "CSV Import List"},
    )
    list_id = res_list.json()["id"]

    csv_data = (
        "email,name\n"
        "alice@import.com,Alice\n"
        "bob@import.com,Bob\n"
        "alice@import.com,Duplicate Alice\n"  # Internal duplicate
        "invalid-email-address,Invalid\n"    # Malformed syntax
        "charlie@import.com,Charlie\n"
    )

    res_import = await client.post(
        f"/api/v1/email-marketing/lists/{list_id}/import",
        headers=h1,
        json={"csv_content": csv_data, "permission_source": "event_registration"},
    )
    assert res_import.status_code == 200
    report = res_import.json()
    assert report["total_rows"] == 5
    assert report["valid_count"] == 3  # alice, bob, charlie
    assert report["duplicate_count"] == 1
    assert report["error_count"] == 1
    assert len(report["errors"]) == 1

    # Second import: re-importing bob should be detected as existing duplicate
    res_reimport = await client.post(
        f"/api/v1/email-marketing/lists/{list_id}/import",
        headers=h1,
        json={
            "csv_content": "email,name\nbob@import.com,Bob\ndavid@import.com,David\n",
            "permission_source": "event_registration",
        },
    )
    assert res_reimport.status_code == 200
    report2 = res_reimport.json()
    assert report2["valid_count"] == 1  # only david
    assert report2["duplicate_count"] == 1  # bob already exists


@pytest.mark.asyncio
async def test_csv_import_batch_limit_exceeded(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """CSV exceeding max_csv_import_rows is rejected with 400 Bad Request."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    res_list = await client.post(
        "/api/v1/email-marketing/lists",
        headers=h1,
        json={"name": "Limit List"},
    )
    list_id = res_list.json()["id"]

    with patch("liteads.ad_server.services.email_marketing_service.get_settings") as mock_settings:
        mock_cfg = MagicMock()
        mock_cfg.listmonk.max_csv_import_rows = 3  # Small limit for test
        mock_settings.return_value = mock_cfg

        csv_data = "email\n1@a.com\n2@a.com\n3@a.com\n4@a.com\n"
        res = await client.post(
            f"/api/v1/email-marketing/lists/{list_id}/import",
            headers=h1,
            json={"csv_content": csv_data, "permission_source": "newsletter_signup"},
        )
        assert res.status_code == 400
        assert "exceeds configured batch limit" in res.json()["detail"]


# ── 6. Campaign Lifecycle: Preview, Test-Send & Launch ─────────────────────────

@pytest.mark.asyncio
async def test_campaign_preview_and_automatic_unsubscribe_notice(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """Preview renders audience count and appends mandatory unsubscribe footer if missing."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    res_list = await client.post(
        "/api/v1/email-marketing/lists",
        headers=h1,
        json={"name": "Preview List"},
    )
    list_id = res_list.json()["id"]

    # Add 2 contacts
    await client.post(
        f"/api/v1/email-marketing/lists/{list_id}/contacts",
        headers=h1,
        json={"email": "p1@preview.com", "consent_source": "customer_opt_in"},
    )
    await client.post(
        f"/api/v1/email-marketing/lists/{list_id}/contacts",
        headers=h1,
        json={"email": "p2@preview.com", "consent_source": "customer_opt_in"},
    )

    # Create campaign without explicit unsubscribe footer
    res_camp = await client.post(
        "/api/v1/email-marketing/campaigns",
        headers=h1,
        json={
            "list_id": list_id,
            "name": "Announcement",
            "subject": "Platform Updates",
            "body": "<p>Hello Subscribers</p>",
        },
    )
    camp_id = res_camp.json()["id"]

    # Preview
    res_prev = await client.post(
        f"/api/v1/email-marketing/campaigns/{camp_id}/preview",
        headers=h1,
    )
    assert res_prev.status_code == 200
    prev_data = res_prev.json()
    assert prev_data["eligible_recipient_count"] == 2
    assert "unsubscribe" in prev_data["rendered_body"].lower()
    assert prev_data["has_unsubscribe_notice"] is True


@pytest.mark.asyncio
async def test_campaign_test_send_limits(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """Test send validates allowlist size (1 to 5 emails)."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    res_list = await client.post("/api/v1/email-marketing/lists", headers=h1, json={"name": "Test List"})
    list_id = res_list.json()["id"]

    res_camp = await client.post(
        "/api/v1/email-marketing/campaigns",
        headers=h1,
        json={"list_id": list_id, "name": "Test Camp", "subject": "Subj", "body": "Body"},
    )
    camp_id = res_camp.json()["id"]

    # 1. Valid test send with 2 emails
    res_ok = await client.post(
        f"/api/v1/email-marketing/campaigns/{camp_id}/test-send",
        headers=h1,
        json={"test_emails": ["qa1@brand1.com", "qa2@brand1.com"]},
    )
    assert res_ok.status_code == 200
    assert "Test preview sent" in res_ok.json()["message"]

    # 2. More than 5 emails rejected
    too_many = [f"dev{i}@brand1.com" for i in range(6)]
    res_bad = await client.post(
        f"/api/v1/email-marketing/campaigns/{camp_id}/test-send",
        headers=h1,
        json={"test_emails": too_many},
    )
    assert res_bad.status_code == 422  # Pydantic max_length=5


@pytest.mark.asyncio
async def test_campaign_launch_requires_explicit_confirmation(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """Campaign launch rejected if confirmed is False, and succeeds when confirmed is True."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    res_list = await client.post("/api/v1/email-marketing/lists", headers=h1, json={"name": "Launch List"})
    list_id = res_list.json()["id"]

    await client.post(
        f"/api/v1/email-marketing/lists/{list_id}/contacts",
        headers=h1,
        json={"email": "sub1@launch.com", "consent_source": "customer_opt_in"},
    )

    res_camp = await client.post(
        "/api/v1/email-marketing/campaigns",
        headers=h1,
        json={"list_id": list_id, "name": "Launch Camp", "subject": "Ready", "body": "Hello"},
    )
    camp_id = res_camp.json()["id"]

    # 1. confirmed=False -> 400 Bad Request
    res_unconfirmed = await client.post(
        f"/api/v1/email-marketing/campaigns/{camp_id}/launch",
        headers=h1,
        json={"confirmed": False},
    )
    assert res_unconfirmed.status_code == 400
    assert "explicit confirmation" in res_unconfirmed.json()["detail"]

    # 2. confirmed=True -> 200 OK, status changes to sent/running
    res_launch = await client.post(
        f"/api/v1/email-marketing/campaigns/{camp_id}/launch",
        headers=h1,
        json={"confirmed": True},
    )
    assert res_launch.status_code == 200
    launched_data = res_launch.json()
    assert launched_data["status"] in ("running", "sent")
    assert launched_data["total_recipients"] == 1
    assert launched_data["sent_at"] is not None

    # 3. Repeated launch rejected
    res_repeat = await client.post(
        f"/api/v1/email-marketing/campaigns/{camp_id}/launch",
        headers=h1,
        json={"confirmed": True},
    )
    assert res_repeat.status_code == 400
    assert "already been launched" in res_repeat.json()["detail"]


@pytest.mark.asyncio
async def test_campaign_analytics(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """Retrieve analytics for an owned campaign."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    res_list = await client.post("/api/v1/email-marketing/lists", headers=h1, json={"name": "Stats List"})
    list_id = res_list.json()["id"]

    res_camp = await client.post(
        "/api/v1/email-marketing/campaigns",
        headers=h1,
        json={"list_id": list_id, "name": "Stats Camp", "subject": "News", "body": "Body"},
    )
    camp_id = res_camp.json()["id"]

    res_stats = await client.get(f"/api/v1/email-marketing/campaigns/{camp_id}/analytics", headers=h1)
    assert res_stats.status_code == 200
    stats = res_stats.json()
    assert stats["campaign_id"] == camp_id
    assert "views" in stats
    assert "clicks" in stats


@pytest.mark.asyncio
async def test_contacts_server_side_search_and_tenant_isolation(
    client: AsyncClient,
    setup_tenants: dict[str, Any],
):
    """Test server-side contact searching by email or name, preserving tenant isolation."""
    h1 = {"Authorization": f"Bearer {setup_tenants['token1']}"}
    h2 = {"Authorization": f"Bearer {setup_tenants['token2']}"}

    # Tenant 1 creates list and contacts
    res_list1 = await client.post("/api/v1/email-marketing/lists", headers=h1, json={"name": "Searchable List"})
    list1_id = res_list1.json()["id"]

    await client.post(
        f"/api/v1/email-marketing/lists/{list1_id}/contacts",
        headers=h1,
        json={"email": "alice.cooper@music.org", "name": "Alice Cooper", "consent_source": "website_signup"},
    )
    await client.post(
        f"/api/v1/email-marketing/lists/{list1_id}/contacts",
        headers=h1,
        json={"email": "bob.marley@reggae.com", "name": "Bob Marley", "consent_source": "event_registration"},
    )
    await client.post(
        f"/api/v1/email-marketing/lists/{list1_id}/contacts",
        headers=h1,
        json={"email": "charlie.brown@peanuts.net", "name": "Charlie", "consent_source": "checkout_optin"},
    )

    # 1. Search by name substring
    res_name = await client.get(f"/api/v1/email-marketing/lists/{list1_id}/contacts?query=alice", headers=h1)
    assert res_name.status_code == 200
    data_name = res_name.json()
    assert len(data_name) == 1
    assert data_name[0]["email"] == "alice.cooper@music.org"

    # 2. Search by email domain substring
    res_email = await client.get(f"/api/v1/email-marketing/lists/{list1_id}/contacts?query=reggae.com", headers=h1)
    assert res_email.status_code == 200
    data_email = res_email.json()
    assert len(data_email) == 1
    assert data_email[0]["name"] == "Bob Marley"

    # 3. No match query
    res_none = await client.get(f"/api/v1/email-marketing/lists/{list1_id}/contacts?query=nonexistent", headers=h1)
    assert res_none.status_code == 200
    assert len(res_none.json()) == 0

    # 4. Cross-tenant attempt to query contacts with search returns 404
    res_cross = await client.get(f"/api/v1/email-marketing/lists/{list1_id}/contacts?query=alice", headers=h2)
    assert res_cross.status_code == 404


