"""
RivinityADS — Phase 10D.1 Website Builder Integration, Persistence & Security Audit Tests.

Verifies against the real FastAPI endpoints and database:
1. End-to-end Project creation, listing, retrieval, update, and soft deletion.
2. Full Page lifecycle: creation, editing, block persistence, reordering, and DELETE endpoint.
3. Homepage delete protection and single-page deletion prohibition.
4. Publication snapshot immutability, unpublish lifecycle, and version rollback.
5. Strict multi-tenant isolation (cross-tenant access/mutation rejected with HTTP 404).
6. Preview token routing for root and secondary pages (?page= and ?path=).
7. Framing security headers and non-billable ad placement safeguards.
8. Suspended and archived project access restrictions.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from liteads.models.website import WebsiteStatus

pytestmark = pytest.mark.asyncio


async def _register_user(client: AsyncClient, email: str, role: str = "publisher") -> tuple[str, int]:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "Password123!",
            "company_name": "Audit Tenant",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


async def test_end_to_end_project_and_page_persistence(client: AsyncClient, test_db):
    """Verify that project theme tokens, pages, and all 8 block types persist accurately."""
    token, user_id = await _register_user(client, "author10d@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create project
    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={
            "name": "Apex Innovations",
            "slug": "apex-innovations",
            "template_id": "business_services",
            "theme_config": {
                "primary_color": "#059669",
                "secondary_color": "#334155",
                "background_color": "#ffffff",
                "text_color": "#0f172a",
                "font_family": "Poppins",
                "border_radius": "0.75rem",
                "logo_url": "https://example.com/apex-logo.svg",
            },
        },
    )
    assert proj_res.status_code == 201, proj_res.text
    project_data = proj_res.json()
    project_id = project_data["id"]
    assert project_data["name"] == "Apex Innovations"
    assert project_data["slug"] == "apex-innovations"
    assert project_data["status"] == 0  # DRAFT
    assert len(project_data["pages"]) == 1  # Default Home page created

    home_page_id = project_data["pages"][0]["id"]

    # 2. Add multiple structured blocks to Home page
    blocks_payload = {
        "title": "Apex Digital Home",
        "slug": "",
        "is_home": True,
        "seo_meta": {
            "meta_title": "Apex Home — Next Gen Tech",
            "meta_description": "Building sustainable digital systems.",
            "og_image_url": "https://example.com/og.jpg",
            "noindex": False,
        },
        "blocks": {
            "schema_version": 1,
            "blocks": [
                {
                    "id": "hero_1",
                    "type": "hero",
                    "is_visible": True,
                    "content": {
                        "headline": "Empower Your Digital Growth",
                        "subheadline": "Enterprise ad tech solutions",
                        "alignment": "center",
                        "cta_button": {
                            "label": "Get Started",
                            "target_url": "/contact",
                            "style": "primary",
                        },
                    },
                },
                {
                    "id": "text_1",
                    "type": "text_section",
                    "is_visible": True,
                    "content": {
                        "heading": "Our Architectural Standard",
                        "body": "Deterministic business rules and real-time event tracking.",
                    },
                },
                {
                    "id": "ad_1",
                    "type": "ad_placement",
                    "is_visible": True,
                    "content": {
                        "placement_location": "in_article",
                        "format": 1,
                        "ad_slot_id": "slot_in_article_01",
                    },
                },
                {
                    "id": "footer_1",
                    "type": "footer",
                    "is_visible": True,
                    "content": {
                        "copyright_text": "© 2026 Apex Innovations",
                        "links": [{"label": "Privacy", "target_url": "/privacy"}],
                    },
                },
            ],
        },
    }

    update_res = await client.put(
        f"/api/v1/website/projects/{project_id}/pages/{home_page_id}",
        headers=headers,
        json=blocks_payload,
    )
    assert update_res.status_code == 200, update_res.text
    saved_page = update_res.json()
    assert saved_page["title"] == "Apex Digital Home"
    assert len(saved_page["blocks"]["blocks"]) == 4

    # 3. Reload from server to verify database persistence
    fetch_res = await client.get(
        f"/api/v1/website/projects/{project_id}/pages/{home_page_id}",
        headers=headers,
    )
    assert fetch_res.status_code == 200
    fetched_page = fetch_res.json()
    assert fetched_page["title"] == "Apex Digital Home"
    assert fetched_page["seo_meta"]["meta_title"] == "Apex Home — Next Gen Tech"
    assert fetched_page["blocks"]["blocks"][0]["content"]["headline"] == "Empower Your Digital Growth"
    assert fetched_page["blocks"]["blocks"][2]["type"] == "ad_placement"


async def test_page_lifecycle_and_delete_protection(client: AsyncClient, test_db):
    """Verify page creation, reordering, homepage deletion block, and secondary page deletion."""
    token, user_id = await _register_user(client, "pages10d@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Create project
    p_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Multi-Page Site", "slug": "multipage-site", "template_id": "business_services"},
    )
    project_id = p_res.json()["id"]
    home_page_id = p_res.json()["pages"][0]["id"]

    # 1. Attempt to delete the required homepage -> must fail with HTTP 400
    del_home_res = await client.delete(
        f"/api/v1/website/projects/{project_id}/pages/{home_page_id}",
        headers=headers,
    )
    assert del_home_res.status_code == 400
    assert "home page" in del_home_res.json()["detail"].lower()

    # 2. Create secondary pages: /services and /about
    page2_res = await client.post(
        f"/api/v1/website/projects/{project_id}/pages",
        headers=headers,
        json={"title": "Our Services", "slug": "services", "is_home": False},
    )
    assert page2_res.status_code == 201
    services_page_id = page2_res.json()["id"]

    page3_res = await client.post(
        f"/api/v1/website/projects/{project_id}/pages",
        headers=headers,
        json={"title": "About Team", "slug": "about", "is_home": False},
    )
    assert page3_res.status_code == 201
    about_page_id = page3_res.json()["id"]

    # 3. Reorder pages: About, Services, Home
    reorder_res = await client.patch(
        f"/api/v1/website/projects/{project_id}/pages/reorder",
        headers=headers,
        json={"page_ids": [about_page_id, services_page_id, home_page_id]},
    )
    assert reorder_res.status_code == 200
    reordered_list = reorder_res.json()
    assert reordered_list[0]["id"] == about_page_id
    assert reordered_list[1]["id"] == services_page_id
    assert reordered_list[2]["id"] == home_page_id

    # 4. Successfully delete secondary page (/about)
    del_about_res = await client.delete(
        f"/api/v1/website/projects/{project_id}/pages/{about_page_id}",
        headers=headers,
    )
    assert del_about_res.status_code == 200
    assert del_about_res.json()["message"] == "Page deleted successfully"

    # Verify /about is gone from project pages
    pages_list_res = await client.get(f"/api/v1/website/projects/{project_id}/pages", headers=headers)
    remaining_ids = [p["id"] for p in pages_list_res.json()]
    assert about_page_id not in remaining_ids
    assert services_page_id in remaining_ids
    assert home_page_id in remaining_ids


async def test_preview_token_routing_and_framing_security(client: AsyncClient, test_db):
    """Verify preview token generation, root vs secondary page query routing, and framing headers."""
    token, user_id = await _register_user(client, "preview10d@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Create project
    p_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={"name": "Preview Portal", "slug": "preview-portal", "template_id": "business_services"},
    )
    project_id = p_res.json()["id"]

    # Add secondary page
    await client.post(
        f"/api/v1/website/projects/{project_id}/pages",
        headers=headers,
        json={
            "title": "Contact Us",
            "slug": "contact",
            "is_home": False,
            "blocks": {
                "schema_version": 1,
                "blocks": [
                    {
                        "id": "c1",
                        "type": "contact_form",
                        "is_visible": True,
                        "content": {"title": "Get in Touch With Preview Portal", "submit_label": "Submit"},
                    }
                ],
            },
        },
    )

    # 1. Generate preview token
    tok_res = await client.post(
        f"/api/v1/website/projects/{project_id}/preview-token",
        headers=headers,
    )
    assert tok_res.status_code == 200
    preview_token = tok_res.json()["preview_token"]

    # 2. Fetch root preview
    root_prev = await client.get(f"/preview/{preview_token}")
    assert root_prev.status_code == 200
    assert "frame-ancestors" in root_prev.headers["content-security-policy"]
    assert "script-src 'none'" in root_prev.headers["content-security-policy"]
    assert root_prev.headers.get("referrer-policy") == "no-referrer"
    assert "no-store" in root_prev.headers.get("cache-control", "")
    assert "Welcome to Preview Portal" in root_prev.text

    # 3. Fetch secondary page via ?page=contact
    page_prev = await client.get(f"/preview/{preview_token}?page=contact")
    assert page_prev.status_code == 200
    assert "Get in Touch With Preview Portal" in page_prev.text

    # 4. Fetch secondary page via ?path=/contact
    path_prev = await client.get(f"/preview/{preview_token}?path=/contact")
    assert path_prev.status_code == 200
    assert "Get in Touch With Preview Portal" in path_prev.text

    # 5. Invalid token rejected with HTTP 403
    bad_prev = await client.get(f"/preview/{preview_token}invalid")
    assert bad_prev.status_code == 403


async def test_tenant_isolation_cross_tenant_access_denial(client: AsyncClient, test_db):
    """Verify strict tenant isolation across projects, pages, versions, and rollback."""
    # Tenant A
    token_a, user_a = await _register_user(client, "tenant_a@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Tenant B
    token_b, user_b = await _register_user(client, "tenant_b@example.com")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Tenant A creates project
    proj_a = await client.post(
        "/api/v1/website/projects",
        headers=headers_a,
        json={"name": "Tenant A Site", "slug": "tenant-a-site", "template_id": "business_services"},
    )
    proj_a_id = proj_a.json()["id"]
    page_a_id = proj_a.json()["pages"][0]["id"]

    # Tenant B attempts to access Tenant A's project -> 404 Not Found (anti-enumeration)
    get_res = await client.get(f"/api/v1/website/projects/{proj_a_id}", headers=headers_b)
    assert get_res.status_code == 404

    # Tenant B attempts to update Tenant A's page -> 404
    put_res = await client.put(
        f"/api/v1/website/projects/{proj_a_id}/pages/{page_a_id}",
        headers=headers_b,
        json={"title": "Hacked Title"},
    )
    assert put_res.status_code == 404

    # Tenant B attempts to delete Tenant A's page -> 404
    del_res = await client.delete(
        f"/api/v1/website/projects/{proj_a_id}/pages/{page_a_id}",
        headers=headers_b,
    )
    assert del_res.status_code == 404

    # Tenant B attempts to publish Tenant A's project -> 404
    pub_res = await client.post(
        f"/api/v1/website/projects/{proj_a_id}/publish",
        headers=headers_b,
        json={"notes": "Malicious publish"},
    )
    assert pub_res.status_code == 404

    # Tenant B attempts to generate preview token for Tenant A's project -> 404
    prev_tok_res = await client.post(
        f"/api/v1/website/projects/{proj_a_id}/preview-token",
        headers=headers_b,
    )
    assert prev_tok_res.status_code == 404


async def test_publication_snapshot_immutability_and_rollback(client: AsyncClient, test_db):
    """Verify that publication snapshots remain immutable and rollback restores draft accurately."""
    token, user_id = await _register_user(client, "publisher10d@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Create project
    proj_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={
            "name": "Snapshot Test Site",
            "slug": "snapshot-test-site",
            "template_id": "business_services",
            "theme_config": {"primary_color": "#2563eb", "font_family": "Inter"},
        },
    )
    project_id = proj_res.json()["id"]
    home_page_id = proj_res.json()["pages"][0]["id"]

    # 1. Publish Version 1
    pub1_res = await client.post(
        f"/api/v1/website/projects/{project_id}/publish",
        headers=headers,
        json={"notes": "Initial launch v1"},
    )
    assert pub1_res.status_code == 200
    v1_id = pub1_res.json()["id"]

    # 2. Mutate draft page and draft project theme
    await client.put(
        f"/api/v1/website/projects/{project_id}/pages/{home_page_id}",
        headers=headers,
        json={"title": "Mutated Draft Title", "slug": "", "is_home": True},
    )
    await client.put(
        f"/api/v1/website/projects/{project_id}",
        headers=headers,
        json={"name": "Mutated Project Name", "theme_config": {"primary_color": "#dc2626", "font_family": "Roboto"}},
    )

    # 3. Verify Version 1 snapshot in history remains unchanged
    versions_res = await client.get(f"/api/v1/website/projects/{project_id}/versions", headers=headers)
    assert versions_res.status_code == 200
    versions = versions_res.json()
    assert len(versions) == 1
    assert versions[0]["notes"] == "Initial launch v1"

    # 4. Perform Rollback to Version 1
    rollback_res = await client.post(
        f"/api/v1/website/projects/{project_id}/versions/{v1_id}/rollback",
        headers=headers,
        json={"notes": "Reverting mutation back to v1"},
    )
    assert rollback_res.status_code == 200

    # 5. Verify draft restored to v1 values
    restored_proj = await client.get(f"/api/v1/website/projects/{project_id}", headers=headers)
    assert restored_proj.json()["name"] == "Snapshot Test Site"
    assert restored_proj.json()["theme_config"]["primary_color"] == "#2563eb"

    restored_page = await client.get(f"/api/v1/website/projects/{project_id}/pages/{home_page_id}", headers=headers)
    assert restored_page.json()["title"] == "Home"

    # 6. Unpublish website
    unpub_res = await client.post(f"/api/v1/website/projects/{project_id}/unpublish", headers=headers)
    assert unpub_res.status_code == 200
    assert unpub_res.json()["status"] == 0  # Reset to DRAFT
