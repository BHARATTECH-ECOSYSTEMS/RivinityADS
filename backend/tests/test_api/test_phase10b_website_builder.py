"""Phase 10B: Website Builder Data Models, Migrations, and APIs Tests.

Verifies:
1. Advisory slug check and reserved slug validation.
2. Project creation, listing, retrieval, updating, and soft deletion.
3. Tenant isolation and cross-tenant protection (404 for forbidden tenant access).
4. Cross-project page tampering prevention.
5. Page CRUD and per-project page slug uniqueness.
6. Navigation reordering.
7. Strict block schema validation, unknown block rejection, and Anti-XSS sanitization.
8. Immutable publication snapshots, versioning, and rollback provenance.
9. Unpublishing while preserving version history.
10. Monetization boundaries (disabled by default, no billing bypass).
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from liteads.ad_server.main import app
from liteads.common.database import get_session
from liteads.models.website import (
    WebsiteMonetizationStatus,
    WebsitePage,
    WebsiteProject,
    WebsiteStatus,
    WebsiteVersion,
)

pytestmark = pytest.mark.asyncio


async def _create_user_and_token(client: AsyncClient, email: str, role: str = "advertiser"):
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "SecurePassword123!",
            "role": role,
        },
    )
    assert res.status_code in [200, 201], f"Register failed: {res.text}"
    data = res.json()
    token = data["access_token"]
    user_id = data["user"]["id"]
    return token, user_id


# ---------------------------------------------------------------------------
# 1. Advisory Slug Availability and Reservation Tests
# ---------------------------------------------------------------------------

async def test_slug_check_and_reservations(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "slug_tester@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Available slug
    res = await client.get("/api/v1/website/slug/check?slug=fresh-brand-name", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["available"] is True
    assert data["slug"] == "fresh-brand-name"

    # Alias /api/v1/website/check-slug backwards compatibility
    res_alias = await client.get("/api/v1/website/check-slug?slug=fresh-brand-name", headers=headers)
    assert res_alias.status_code == 200, res_alias.text
    assert res_alias.json()["available"] is True

    # Reserved slug (admin, api, etc.)
    res = await client.get("/api/v1/website/slug/check?slug=admin", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is False
    assert "reserved" in data["reason"].lower()

    # Invalid slug formatting (uppercase, symbols, too short)
    res = await client.get("/api/v1/website/slug/check?slug=Bad_Slug!", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is False

    res = await client.get("/api/v1/website/slug/check?slug=ab", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is False


# ---------------------------------------------------------------------------
# 2. Project CRUD and Soft Deletion Tests
# ---------------------------------------------------------------------------

async def test_project_crud_and_soft_deletion(client: AsyncClient, test_db):
    token, user_id = await _create_user_and_token(client, "project_owner@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Create project
    create_payload = {
        "name": "My Awesome Studio",
        "slug": "awesome-studio",
        "template_id": "business_services",
        "theme_config": {
            "primary_color": "#4f46e5",
            "font_family": "Inter",
        },
    }
    res = await client.post("/api/v1/website/projects", json=create_payload, headers=headers)
    assert res.status_code == 201, res.text
    p_data = res.json()
    project_id = p_data["id"]
    assert p_data["name"] == "My Awesome Studio"
    assert p_data["slug"] == "awesome-studio"
    assert p_data["status"] == WebsiteStatus.DRAFT
    assert p_data["monetization_status"] == WebsiteMonetizationStatus.DISABLED
    assert len(p_data["pages"]) == 1  # Automatic default home page created
    assert p_data["owner_user_id"] == user_id

    # Duplicate slug rejected with 409 Conflict
    res_dup = await client.post("/api/v1/website/projects", json=create_payload, headers=headers)
    assert res_dup.status_code == 409
    assert "already taken" in res_dup.text

    # Reserved slug creation rejected with 422 Unprocessable Entity
    res_resv = await client.post(
        "/api/v1/website/projects",
        json={"name": "Admin Dashboard", "slug": "dashboard"},
        headers=headers,
    )
    assert res_resv.status_code == 422
    assert "reserved" in res_resv.text.lower()

    # List projects
    res_list = await client.get("/api/v1/website/projects", headers=headers)
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert len(list_data) == 1
    assert list_data[0]["id"] == project_id
    assert list_data[0]["page_count"] == 1

    # Retrieve single project
    res_get = await client.get(f"/api/v1/website/projects/{project_id}", headers=headers)
    assert res_get.status_code == 200
    assert res_get.json()["id"] == project_id

    # Update project
    patch_payload = {
        "name": "My Super Awesome Studio",
        "theme_config": {"primary_color": "#10b981", "font_family": "Roboto"},
    }
    res_patch = await client.patch(
        f"/api/v1/website/projects/{project_id}",
        json=patch_payload,
        headers=headers,
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["name"] == "My Super Awesome Studio"
    assert res_patch.json()["theme_config"]["primary_color"] == "#10b981"

    # Update project via PUT alias
    res_put = await client.put(
        f"/api/v1/website/projects/{project_id}",
        json={"name": "My Studio Renamed via PUT"},
        headers=headers,
    )
    assert res_put.status_code == 200
    assert res_put.json()["name"] == "My Studio Renamed via PUT"

    # Soft delete project
    res_del = await client.delete(f"/api/v1/website/projects/{project_id}", headers=headers)
    assert res_del.status_code == 200

    # Project is excluded from active list
    res_list_after = await client.get("/api/v1/website/projects", headers=headers)
    assert res_list_after.status_code == 200
    assert len(res_list_after.json()) == 0

    # Project GET returns 404 once soft deleted
    res_get_del = await client.get(f"/api/v1/website/projects/{project_id}", headers=headers)
    assert res_get_del.status_code == 404


# ---------------------------------------------------------------------------
# 3. Tenant Isolation and Cross-Tenant Protection Tests
# ---------------------------------------------------------------------------

async def test_tenant_isolation_forbidden_access(client: AsyncClient, test_db):
    token_alice, alice_id = await _create_user_and_token(client, "alice@test.com")
    token_bob, bob_id = await _create_user_and_token(client, "bob@test.com")

    headers_alice = {"Authorization": f"Bearer {token_alice}"}
    headers_bob = {"Authorization": f"Bearer {token_bob}"}

    # Alice creates project
    res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Alice Site", "slug": "alice-site"},
        headers=headers_alice,
    )
    assert res.status_code == 201
    p_alice_id = res.json()["id"]

    # Bob attempts unauthorized access (must return 404 to avoid enumeration)
    assert (await client.get(f"/api/v1/website/projects/{p_alice_id}", headers=headers_bob)).status_code == 404
    assert (
        await client.patch(
            f"/api/v1/website/projects/{p_alice_id}",
            json={"name": "Hacked"},
            headers=headers_bob,
        )
    ).status_code == 404
    assert (await client.delete(f"/api/v1/website/projects/{p_alice_id}", headers=headers_bob)).status_code == 404
    assert (await client.get(f"/api/v1/website/projects/{p_alice_id}/pages", headers=headers_bob)).status_code == 404
    assert (
        await client.post(
            f"/api/v1/website/projects/{p_alice_id}/pages",
            json={"title": "Bob Attack Page", "slug": "attack"},
            headers=headers_bob,
        )
    ).status_code == 404
    assert (await client.post(f"/api/v1/website/projects/{p_alice_id}/publish", json={}, headers=headers_bob)).status_code == 404
    assert (await client.post(f"/api/v1/website/projects/{p_alice_id}/unpublish", headers=headers_bob)).status_code == 404
    assert (await client.get(f"/api/v1/website/projects/{p_alice_id}/versions", headers=headers_bob)).status_code == 404


# ---------------------------------------------------------------------------
# 4. Cross-Project Page Tampering Within Same User
# ---------------------------------------------------------------------------

async def test_cross_project_page_tampering_within_same_user(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "multi_proj_user@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Create Project 1 and Project 2
    res_p1 = await client.post(
        "/api/v1/website/projects",
        json={"name": "Project One", "slug": "project-one"},
        headers=headers,
    )
    p1_id = res_p1.json()["id"]

    res_p2 = await client.post(
        "/api/v1/website/projects",
        json={"name": "Project Two", "slug": "project-two"},
        headers=headers,
    )
    p2_id = res_p2.json()["id"]

    # Create page in Project 1
    res_page = await client.post(
        f"/api/v1/website/projects/{p1_id}/pages",
        json={"title": "P1 Service Page", "slug": "services"},
        headers=headers,
    )
    assert res_page.status_code == 201
    page1_id = res_page.json()["id"]

    # Attempt to access page1_id using Project 2's URL -> must return 404
    res_cross = await client.get(f"/api/v1/website/projects/{p2_id}/pages/{page1_id}", headers=headers)
    assert res_cross.status_code == 404

    res_cross_put = await client.put(
        f"/api/v1/website/projects/{p2_id}/pages/{page1_id}",
        json={"title": "Tampered Title", "slug": "services"},
        headers=headers,
    )
    assert res_cross_put.status_code == 404


# ---------------------------------------------------------------------------
# 5. Page CRUD and Page Slug Uniqueness
# ---------------------------------------------------------------------------

async def test_page_crud_and_slug_uniqueness(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "page_creator@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_p = await client.post(
        "/api/v1/website/projects",
        json={"name": "Company Site", "slug": "company-site"},
        headers=headers,
    )
    p_id = res_p.json()["id"]

    # Default home page was created
    pages_res = await client.get(f"/api/v1/website/projects/{p_id}/pages", headers=headers)
    assert len(pages_res.json()) == 1
    assert pages_res.json()[0]["slug"] == ""
    assert pages_res.json()[0]["is_home"] is True

    # Create 'about' page
    res_about = await client.post(
        f"/api/v1/website/projects/{p_id}/pages",
        json={
            "title": "About Us",
            "slug": "about",
            "seo_meta": {"meta_description": "Learn about our company"},
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "blk-hero-about",
                        "type": "hero",
                        "is_visible": True,
                        "content": {
                            "headline": "Welcome to our team",
                            "subheadline": "Building the future of digital software.",
                        },
                    }
                ],
            },
        },
        headers=headers,
    )
    assert res_about.status_code == 201, res_about.text
    about_id = res_about.json()["id"]
    assert res_about.json()["slug"] == "about"

    # Attempt duplicate slug within the same project -> 409
    res_dup = await client.post(
        f"/api/v1/website/projects/{p_id}/pages",
        json={"title": "Another About", "slug": "about"},
        headers=headers,
    )
    assert res_dup.status_code == 409
    assert "already exists in this project" in res_dup.text

    # Updating page content
    res_put = await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{about_id}",
        json={
            "title": "About Our Team & Mission",
            "slug": "about",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "blk-text-mission",
                        "type": "text_section",
                        "is_visible": True,
                        "content": {
                            "heading": "Our Mission",
                            "body": "We are dedicated to building privacy-respecting platforms.",
                        },
                    }
                ],
            },
        },
        headers=headers,
    )
    assert res_put.status_code == 200
    assert res_put.json()["title"] == "About Our Team & Mission"
    assert len(res_put.json()["blocks"]["blocks"]) == 1


# ---------------------------------------------------------------------------
# 6. Page Navigation Reordering
# ---------------------------------------------------------------------------

async def test_page_navigation_reordering(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "reorder_user@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_p = await client.post(
        "/api/v1/website/projects",
        json={"name": "Reorder Site", "slug": "reorder-site"},
        headers=headers,
    )
    p_id = res_p.json()["id"]
    home_page_id = (await client.get(f"/api/v1/website/projects/{p_id}/pages", headers=headers)).json()[0]["id"]

    # Add page 2 and page 3
    p2 = (
        await client.post(
            f"/api/v1/website/projects/{p_id}/pages",
            json={"title": "Features", "slug": "features"},
            headers=headers,
        )
    ).json()["id"]

    p3 = (
        await client.post(
            f"/api/v1/website/projects/{p_id}/pages",
            json={"title": "Contact", "slug": "contact"},
            headers=headers,
        )
    ).json()["id"]

    # Reorder pages to: contact, home, features
    reorder_res = await client.patch(
        f"/api/v1/website/projects/{p_id}/pages/reorder",
        json={"page_ids": [p3, home_page_id, p2]},
        headers=headers,
    )
    assert reorder_res.status_code == 200
    ordered_pages = reorder_res.json()
    assert [p["id"] for p in ordered_pages] == [p3, home_page_id, p2]
    assert [p["order_index"] for p in ordered_pages] == [0, 1, 2]

    # Reorder pages via POST alias
    reorder_post_res = await client.post(
        f"/api/v1/website/projects/{p_id}/pages/reorder",
        json={"page_ids": [home_page_id, p2, p3]},
        headers=headers,
    )
    assert reorder_post_res.status_code == 200
    assert [p["id"] for p in reorder_post_res.json()] == [home_page_id, p2, p3]


# ---------------------------------------------------------------------------
# 7. Strict Block Validation and Anti-XSS Sanitization Tests
# ---------------------------------------------------------------------------

async def test_block_validation_and_xss_rejection(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "security_tester@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_p = await client.post(
        "/api/v1/website/projects",
        json={"name": "Sec Site", "slug": "sec-site"},
        headers=headers,
    )
    p_id = res_p.json()["id"]

    # 1. Reject <script> tag in hero heading
    bad_script = {
        "title": "XSS Attempt 1",
        "slug": "xss-1",
        "blocks": {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "bad-blk-1",
                    "type": "hero",
                    "is_visible": True,
                    "content": {
                        "headline": "Welcome <script>alert('pwned')</script>",
                    },
                }
            ],
        },
    }
    res1 = await client.post(f"/api/v1/website/projects/{p_id}/pages", json=bad_script, headers=headers)
    assert res1.status_code == 422
    assert "unsafe HTML" in res1.text or "malicious tags" in res1.text

    # 2. Reject javascript: pseudo-protocol in CTA link
    bad_js_url = {
        "title": "XSS Attempt 2",
        "slug": "xss-2",
        "blocks": {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "bad-blk-2",
                    "type": "cta_banner",
                    "is_visible": True,
                    "content": {
                        "title": "Click me",
                        "button": {
                            "label": "Click",
                            "target_url": "javascript:fetch('http://attacker.com')",
                        },
                    },
                }
            ],
        },
    }
    res2 = await client.post(f"/api/v1/website/projects/{p_id}/pages", json=bad_js_url, headers=headers)
    assert res2.status_code == 422

    # 3. Reject inline event handlers (onload, onerror)
    bad_inline_event = {
        "title": "XSS Attempt 3",
        "slug": "xss-3",
        "blocks": {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "bad-blk-3",
                    "type": "text_section",
                    "is_visible": True,
                    "content": {
                        "body": 'Hello <img src="x" onerror="alert(1)">',
                    },
                }
            ],
        },
    }
    res3 = await client.post(f"/api/v1/website/projects/{p_id}/pages", json=bad_inline_event, headers=headers)
    assert res3.status_code == 422

    # 4. Reject unknown block types
    unknown_block = {
        "title": "Unknown Block",
        "slug": "unknown-blk",
        "blocks": {
            "schema_version": 1,
            "blocks": [{"id": "bad-blk-4", "type": "crypto_miner", "content": {"hashrate": 9999}}],
        },
    }
    res4 = await client.post(f"/api/v1/website/projects/{p_id}/pages", json=unknown_block, headers=headers)
    assert res4.status_code == 422

    # 5. Reject excessive blocks count (> 50 blocks)
    too_many_blocks = {
        "title": "Too Many Blocks",
        "slug": "too-many",
        "blocks": {
            "schema_version": 1,
            "blocks": [
                {"id": f"b{i}", "type": "text_section", "content": {"body": f"Block content {i}"}}
                for i in range(55)
            ],
        },
    }
    res5 = await client.post(f"/api/v1/website/projects/{p_id}/pages", json=too_many_blocks, headers=headers)
    assert res5.status_code == 422
    assert "at most 50 items" in res5.text or "too_long" in res5.text

    # 6. Valid blocks schema accepted (hero, text, gallery, cta, footer, contact, features, ad_placement)
    valid_blocks = {
        "title": "Valid Blocks Page",
        "slug": "valid-blocks",
        "blocks": {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "blk-hero-ok",
                    "type": "hero",
                    "is_visible": True,
                    "content": {
                        "headline": "Safe Hero",
                        "subheadline": "Completely verified heading",
                    },
                },
                {
                    "id": "blk-feat-ok",
                    "type": "features_grid",
                    "is_visible": True,
                    "content": {
                        "title": "Features Grid",
                        "items": [
                            {"title": "Fast", "description": "Ultra fast serving"},
                            {"title": "Secure", "description": "Rigorous security"},
                        ],
                    },
                },
                {
                    "id": "blk-gal-ok",
                    "type": "image_gallery",
                    "is_visible": True,
                    "content": {
                        "title": "Nature Gallery",
                        "images": [
                            {"image_url": "https://example.com/nature.jpg", "alt_text": "A photo of nature", "caption": "Nature"},
                        ],
                    },
                },
                {
                    "id": "blk-contact-ok",
                    "type": "contact_form",
                    "is_visible": True,
                    "content": {
                        "title": "Get in touch",
                        "submit_label": "Send Note",
                        "fields": ["name", "email", "message"],
                    },
                },
                {
                    "id": "blk-ad-ok",
                    "type": "ad_placement",
                    "is_visible": True,
                    "content": {
                        "placement_location": "in_article",
                        "format": 1,
                        "ad_slot_id": "slot_homepage_top",
                    },
                },
                {
                    "id": "blk-footer-ok",
                    "type": "footer",
                    "is_visible": True,
                    "content": {
                        "copyright_text": "2026 RivinityADS Inc.",
                        "links": [{"label": "Home", "target_url": "/"}],
                    },
                },
            ],
        },
    }
    res_valid = await client.post(f"/api/v1/website/projects/{p_id}/pages", json=valid_blocks, headers=headers)
    assert res_valid.status_code == 201, res_valid.text
    assert len(res_valid.json()["blocks"]["blocks"]) == 6


# ---------------------------------------------------------------------------
# 8. Publication Snapshots, Immutability, and Rollback
# ---------------------------------------------------------------------------

async def test_publication_versioning_and_rollback(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "publisher_v@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_p = await client.post(
        "/api/v1/website/projects",
        json={"name": "Snapshot Site", "slug": "snapshot-site"},
        headers=headers,
    )
    p_id = res_p.json()["id"]

    # Edit default home page
    home_page = (await client.get(f"/api/v1/website/projects/{p_id}/pages", headers=headers)).json()[0]
    await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{home_page['id']}",
        json={
            "title": "Home V1",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "blk-v1",
                        "type": "hero",
                        "is_visible": True,
                        "content": {"headline": "Initial Version 1"},
                    }
                ],
            },
        },
        headers=headers,
    )

    # 1. Publish project (creates version 1)
    pub_res = await client.post(
        f"/api/v1/website/projects/{p_id}/publish",
        json={"notes": "Initial Release v1"},
        headers=headers,
    )
    assert pub_res.status_code == 200, pub_res.text
    v1_data = pub_res.json()
    assert v1_data["version_number"] == 1
    assert v1_data["notes"] == "Initial Release v1"
    v1_id = v1_data["id"]

    # Verify project status is published
    proj_after_pub = (await client.get(f"/api/v1/website/projects/{p_id}", headers=headers)).json()
    assert proj_after_pub["status"] == WebsiteStatus.PUBLISHED
    assert proj_after_pub["current_published_version_id"] == v1_id

    # 2. Modify draft page to "Home V2 Draft"
    await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{home_page['id']}",
        json={
            "title": "Home V2 Changed",
            "slug": "",
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "blk-v2",
                        "type": "hero",
                        "is_visible": True,
                        "content": {"headline": "Mutated Heading in V2"},
                    }
                ],
            },
        },
        headers=headers,
    )

    # 3. Publish second version (creates version 2)
    pub2_res = await client.post(
        f"/api/v1/website/projects/{p_id}/publish",
        json={"notes": "Update to v2"},
        headers=headers,
    )
    assert pub2_res.status_code == 200
    v2_data = pub2_res.json()
    assert v2_data["version_number"] == 2
    v2_id = v2_data["id"]

    # List versions
    versions_res = await client.get(f"/api/v1/website/projects/{p_id}/versions", headers=headers)
    assert versions_res.status_code == 200
    versions_list = versions_res.json()
    assert len(versions_list) == 2
    assert versions_list[0]["version_number"] == 2
    assert versions_list[1]["version_number"] == 1

    # 4. Rollback to version 1
    rollback_res = await client.post(
        f"/api/v1/website/projects/{p_id}/versions/{v1_id}/rollback",
        json={"notes": "Reverting broken v2 changes"},
        headers=headers,
    )
    assert rollback_res.status_code == 200, rollback_res.text
    v3_data = rollback_res.json()
    assert v3_data["version_number"] == 3
    assert v3_data["rollback_from_version_id"] == v1_id

    # Check that draft page was restored to v1 content
    home_page_restored = (await client.get(f"/api/v1/website/projects/{p_id}/pages/{home_page['id']}", headers=headers)).json()
    assert home_page_restored["title"] == "Home V1"
    assert home_page_restored["blocks"]["blocks"][0]["content"]["headline"] == "Initial Version 1"

    # Total versions now 3
    versions_res_after = await client.get(f"/api/v1/website/projects/{p_id}/versions", headers=headers)
    assert len(versions_res_after.json()) == 3


# ---------------------------------------------------------------------------
# 9. Unpublishing Lifecycle Tests
# ---------------------------------------------------------------------------

async def test_unpublish_lifecycle(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "unpublish_tester@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_p = await client.post(
        "/api/v1/website/projects",
        json={"name": "Live Site", "slug": "live-site"},
        headers=headers,
    )
    p_id = res_p.json()["id"]

    # Publish
    await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)

    # Unpublish
    unpub_res = await client.post(f"/api/v1/website/projects/{p_id}/unpublish", headers=headers)
    assert unpub_res.status_code == 200
    assert unpub_res.json()["status"] == WebsiteStatus.DRAFT
    assert unpub_res.json()["current_published_version_id"] is None

    # Version history remains completely intact
    versions = (await client.get(f"/api/v1/website/projects/{p_id}/versions", headers=headers)).json()
    assert len(versions) == 1


# ---------------------------------------------------------------------------
# 10. Monetization and Property Boundaries (Security Enforced)
# ---------------------------------------------------------------------------

async def test_monetization_remains_safely_disabled(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "monetization_tester@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to smuggle monetization_status in project create payload
    res = await client.post(
        "/api/v1/website/projects",
        json={
            "name": "Ad Monetized Site",
            "slug": "ad-site",
            "monetization_status": "ACTIVE",  # Extra field or invalid type
        },
        headers=headers,
    )
    # Pydantic will either ignore or reject unknown fields; if created, monetization_status is strictly DISABLED
    if res.status_code == 201:
        p_data = res.json()
        assert p_data["monetization_status"] == WebsiteMonetizationStatus.DISABLED
    else:
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# 11. Cross-Project Version Isolation (Audit Test)
# ---------------------------------------------------------------------------

async def test_cross_project_version_isolation(client: AsyncClient, test_db):
    """Ensure a project cannot rollback to a version ID belonging to another project."""
    token, _ = await _create_user_and_token(client, "version_isolator@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Project A
    res_a = await client.post(
        "/api/v1/website/projects",
        json={"name": "Project Alpha", "slug": "project-alpha"},
        headers=headers,
    )
    p_a_id = res_a.json()["id"]

    # Project B
    res_b = await client.post(
        "/api/v1/website/projects",
        json={"name": "Project Beta", "slug": "project-beta"},
        headers=headers,
    )
    p_b_id = res_b.json()["id"]

    # Publish Project A -> creates version on Project A
    pub_a = await client.post(f"/api/v1/website/projects/{p_a_id}/publish", json={"notes": "Release Alpha"}, headers=headers)
    v_a_id = pub_a.json()["id"]

    # Attempt to rollback Project B using Project A's version ID -> MUST return 404
    cross_rollback = await client.post(
        f"/api/v1/website/projects/{p_b_id}/versions/{v_a_id}/rollback",
        json={"notes": "Smuggle Version from A"},
        headers=headers,
    )
    assert cross_rollback.status_code == 404
    assert "Target version not found" in cross_rollback.text


# ---------------------------------------------------------------------------
# 12. Snapshot Immutability Under Draft Mutations (Audit Test)
# ---------------------------------------------------------------------------

async def test_snapshot_immutability_under_draft_mutations(client: AsyncClient, test_db):
    """Ensure existing publication snapshots cannot be mutated through project/page draft edits."""
    token, _ = await _create_user_and_token(client, "immutable_tester@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_p = await client.post(
        "/api/v1/website/projects",
        json={"name": "Immutable Site", "slug": "immutable-site"},
        headers=headers,
    )
    p_id = res_p.json()["id"]
    home_page_id = (await client.get(f"/api/v1/website/projects/{p_id}/pages", headers=headers)).json()[0]["id"]

    # Publish version 1
    pub1 = await client.post(f"/api/v1/website/projects/{p_id}/publish", json={"notes": "Freeze v1"}, headers=headers)
    v1_id = pub1.json()["id"]

    # Fetch snapshot from DB directly
    stmt = select(WebsiteVersion).where(WebsiteVersion.id == v1_id)
    v1_record = (await test_db.execute(stmt)).scalar_one()
    snapshot_v1_before = dict(v1_record.snapshot_data)

    # Mutate draft home page
    await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{home_page_id}",
        json={"title": "Completely Mutated Draft Title", "slug": ""},
        headers=headers,
    )

    # Add second page to draft
    await client.post(
        f"/api/v1/website/projects/{p_id}/pages",
        json={"title": "Draft Extra Page", "slug": "extra"},
        headers=headers,
    )

    # Update project theme
    await client.patch(
        f"/api/v1/website/projects/{p_id}",
        json={"name": "Mutated Site Name"},
        headers=headers,
    )

    # Re-fetch snapshot 1 from DB
    await test_db.refresh(v1_record)
    assert v1_record.snapshot_data == snapshot_v1_before, "Historical snapshot was corrupted by draft mutations!"


# ---------------------------------------------------------------------------
# 13. Suspended Project Moderation Guards (Audit Test)
# ---------------------------------------------------------------------------

async def test_suspended_project_lifecycle_guards(client: AsyncClient, test_db):
    """Ensure suspended projects cannot be published, unpublished, or rolled back by tenants."""
    token, _ = await _create_user_and_token(client, "suspended_owner@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_p = await client.post(
        "/api/v1/website/projects",
        json={"name": "Takedown Target", "slug": "takedown-target"},
        headers=headers,
    )
    p_id = res_p.json()["id"]

    # Publish v1 before suspension
    pub_res = await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)
    v1_id = pub_res.json()["id"]

    # Admin marks project SUSPENDED directly in DB
    proj_record = (await test_db.execute(select(WebsiteProject).where(WebsiteProject.id == p_id))).scalar_one()
    proj_record.status = WebsiteStatus.SUSPENDED
    await test_db.commit()

    # Tenant attempts to publish -> 403 Forbidden
    res_pub = await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)
    assert res_pub.status_code == 403
    assert "suspended" in res_pub.text.lower()

    # Tenant attempts to unpublish -> 403 Forbidden
    res_unpub = await client.post(f"/api/v1/website/projects/{p_id}/unpublish", headers=headers)
    assert res_unpub.status_code == 403
    assert "suspended" in res_unpub.text.lower()

    # Tenant attempts to rollback -> 403 Forbidden
    res_rb = await client.post(f"/api/v1/website/projects/{p_id}/versions/{v1_id}/rollback", json={}, headers=headers)
    assert res_rb.status_code == 403
    assert "suspended" in res_rb.text.lower()


# ---------------------------------------------------------------------------
# 14. Homepage Presence Required Before Publishing (Audit Test)
# ---------------------------------------------------------------------------

async def test_publish_requires_home_page(client: AsyncClient, test_db):
    """Ensure project cannot be published if no page is designated as homepage."""
    token, _ = await _create_user_and_token(client, "nohome_tester@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_p = await client.post(
        "/api/v1/website/projects",
        json={"name": "No Home Site", "slug": "nohome-site"},
        headers=headers,
    )
    p_id = res_p.json()["id"]
    home_page_id = (await client.get(f"/api/v1/website/projects/{p_id}/pages", headers=headers)).json()[0]["id"]

    # Demote homepage to non-home page
    await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{home_page_id}",
        json={"title": "Not Home", "slug": "not-home", "is_home": False},
        headers=headers,
    )

    # Attempt publish -> 400 Bad Request
    pub_res = await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)
    assert pub_res.status_code == 400
    assert "home page" in pub_res.text.lower()


# ---------------------------------------------------------------------------
# 15. Model Property Resolution for Current Published Version
# ---------------------------------------------------------------------------

async def test_current_published_version_model_property(client: AsyncClient, test_db):
    """Ensure project.current_published_version property strictly resolves correctly."""
    token, _ = await _create_user_and_token(client, "model_prop_tester@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_p = await client.post(
        "/api/v1/website/projects",
        json={"name": "Prop Site", "slug": "prop-site"},
        headers=headers,
    )
    p_id = res_p.json()["id"]

    # Before publishing, property is None
    proj_stmt = select(WebsiteProject).where(WebsiteProject.id == p_id)
    proj = (await test_db.execute(proj_stmt)).scalar_one()
    assert proj.current_published_version is None

    # Publish
    pub = await client.post(f"/api/v1/website/projects/{p_id}/publish", json={"notes": "v1"}, headers=headers)
    v_id = pub.json()["id"]

    await test_db.refresh(proj)
    assert proj.current_published_version is not None
    assert proj.current_published_version.id == v_id
    assert proj.current_published_version.version_number == 1


# ---------------------------------------------------------------------------
# 16. Alembic Migration Upgrade and Downgrade Lifecycle Test
# ---------------------------------------------------------------------------

async def test_migration_upgrade_and_downgrade_lifecycle():
    """Verify that Alembic migration l4d5e6f7a8b9 can upgrade, downgrade, and re-upgrade cleanly."""
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic import op
    import sqlalchemy as sa

    from pathlib import Path
    mig_path = Path("alembic/versions/l4d5e6f7a8b9_phase10b_website_builder.py")
    if not mig_path.exists():
        mig_path = Path("backend/alembic/versions/l4d5e6f7a8b9_phase10b_website_builder.py")
    spec = importlib.util.spec_from_file_location("migration", str(mig_path.resolve()))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite:///:memory:")

    with engine.connect() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY);"))
        conn.execute(sa.text("CREATE TABLE publishers (id INTEGER PRIMARY KEY);"))
        conn.execute(sa.text("CREATE TABLE publisher_properties (id INTEGER PRIMARY KEY);"))
        conn.commit()

        ctx = MigrationContext.configure(conn)
        op._proxy = Operations(ctx)

        # 1. Upgrade
        migration.upgrade()
        conn.commit()
        insp = sa.inspect(conn)
        tables = insp.get_table_names()
        assert "website_projects" in tables
        assert "website_pages" in tables
        assert "website_versions" in tables
        assert "website_contact_submissions" in tables

        # 2. Downgrade
        migration.downgrade()
        conn.commit()
        insp_after = sa.inspect(conn)
        tables_after = insp_after.get_table_names()
        assert not any(t.startswith("website_") for t in tables_after)

        # 3. Re-upgrade
        migration.upgrade()
        conn.commit()
        insp_re = sa.inspect(conn)
        assert "website_projects" in insp_re.get_table_names()
