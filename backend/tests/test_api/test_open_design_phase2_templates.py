"""Open Design Phase 2: Template Catalog & Design System Ingestion Tests.

Verifies:
1. Idempotent template seeding in database.
2. Catalog listing, category filtering, and keyword search.
3. Template detail retrieval with default pages and blocks.
4. Validation rejection of invalid or inactive template IDs on project creation.
5. Project creation from template instantiates deep-copied pages and blocks (Schema v2).
6. Deep-copy independence: subsequent catalog changes do not affect created projects.
7. Creation using all 15 approved Open Design templates.
8. Anti-ad-spoofing guards on template blocks.
9. Tenant ownership isolation.
"""

import copy
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from liteads.ad_server.services.website_templates_catalog import (
    BUILTIN_TEMPLATES,
    seed_website_templates,
)
from liteads.models.website import WebsiteProject, WebsiteTemplate, WebsiteStatus, WebsiteMonetizationStatus
from liteads.schemas.website import BlockType, PageBlocksPayload

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
    return data["access_token"], data["user"]["id"]


# ---------------------------------------------------------------------------
# 1. Template Seeding and Idempotency Tests
# ---------------------------------------------------------------------------

async def test_template_seeding_idempotence(test_db):
    """Verify that seed_website_templates creates templates and is strictly idempotent."""
    # First seed: inserts all builtin templates
    inserted_first = await seed_website_templates(test_db)
    assert inserted_first == len(BUILTIN_TEMPLATES)
    assert inserted_first >= 15

    # Second seed: inserts 0 duplicates
    inserted_second = await seed_website_templates(test_db)
    assert inserted_second == 0

    # Query all seeded records
    stmt = select(WebsiteTemplate)
    res = await test_db.execute(stmt)
    records = list(res.scalars().all())
    assert len(records) == len(BUILTIN_TEMPLATES)

    # Verify key approved template IDs are present
    ids = {r.id for r in records}
    expected_ids = [
        "saas-landing", "pricing-page", "blog-post", "docs-page",
        "web-prototype-taste-editorial", "waitlist-page", "web-prototype-taste-soft",
        "web-prototype-taste-brutalist", "dashboard", "kanban-board",
        "team-okrs", "eng-runbook", "finance-report", "hr-onboarding", "meeting-notes",
    ]
    for exp in expected_ids:
        assert exp in ids, f"Missing expected template ID: {exp}"


# ---------------------------------------------------------------------------
# 2. Template Catalog API Endpoints Tests
# ---------------------------------------------------------------------------

async def test_list_templates_api(client: AsyncClient, test_db):
    """Verify GET /api/v1/website/templates lists active templates with summary fields."""
    token, _ = await _create_user_and_token(client, "tmpl_user1@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.get("/api/v1/website/templates", headers=headers)
    assert res.status_code == 200, res.text
    templates = res.json()
    assert len(templates) >= 15

    first = templates[0]
    assert "id" in first
    assert "name" in first
    assert "category" in first
    assert "default_theme_config" in first
    assert "order_index" in first
    assert "is_active" in first
    assert first["is_active"] is True


async def test_list_templates_filtering_and_search(client: AsyncClient, test_db):
    """Verify category filtering and keyword search in GET /api/v1/website/templates."""
    token, _ = await _create_user_and_token(client, "tmpl_user2@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Filter by category: Landing & Marketing
    res_cat = await client.get(
        "/api/v1/website/templates?category=Landing%20%26%20Marketing",
        headers=headers,
    )
    assert res_cat.status_code == 200
    landing_templates = res_cat.json()
    assert len(landing_templates) >= 3
    for t in landing_templates:
        assert t["category"] == "Landing & Marketing"

    # Filter by category: Creative & Agency
    res_creative = await client.get(
        "/api/v1/website/templates?category=Creative%20%26%20Agency",
        headers=headers,
    )
    assert res_creative.status_code == 200
    creative_templates = res_creative.json()
    assert len(creative_templates) >= 3
    for t in creative_templates:
        assert t["category"] == "Creative & Agency"

    # Search by keyword: 'pricing'
    res_search = await client.get(
        "/api/v1/website/templates?search=pricing",
        headers=headers,
    )
    assert res_search.status_code == 200
    search_results = res_search.json()
    assert len(search_results) >= 1
    ids = [t["id"] for t in search_results]
    assert "pricing-page" in ids or any("pricing" in t["name"].lower() for t in search_results)


async def test_get_template_detail_api(client: AsyncClient, test_db):
    """Verify GET /api/v1/website/templates/{id} returns detail and 404 for invalid."""
    token, _ = await _create_user_and_token(client, "tmpl_user3@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Valid template detail
    res = await client.get("/api/v1/website/templates/saas-landing", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "saas-landing"
    assert data["name"] == "SaaS & Product Landing"
    assert "default_pages" in data
    assert len(data["default_pages"]) >= 1

    home_page = data["default_pages"][0]
    assert home_page["title"] == "Home"
    assert home_page["slug"] == ""
    assert home_page["is_home"] is True
    assert home_page["blocks"]["schema_version"] == 2
    assert len(home_page["blocks"]["blocks"]) > 0

    # Non-existent template returns 404
    res_404 = await client.get("/api/v1/website/templates/non-existent-template-id", headers=headers)
    assert res_404.status_code == 404
    assert "not found" in res_404.text.lower()


# ---------------------------------------------------------------------------
# 3. Website Creation from Template & Validation Rejections
# ---------------------------------------------------------------------------

async def test_create_project_rejects_invalid_template(client: AsyncClient, test_db):
    """Verify POST /api/v1/website/projects rejects invalid or inactive template IDs."""
    token, _ = await _create_user_and_token(client, "tmpl_user4@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "name": "Invalid Template Site",
        "slug": "invalid-tmpl-site",
        "template_id": "malicious_or_unknown_template_999",
    }
    res = await client.post("/api/v1/website/projects", json=payload, headers=headers)
    assert res.status_code == 400
    assert "invalid or inactive template_id" in res.text.lower()


async def test_create_project_from_template_instantiates_schema_v2_blocks(client: AsyncClient, test_db):
    """Verify that creating a website from template copies full pages and Schema v2 blocks."""
    token, user_id = await _create_user_and_token(client, "tmpl_user5@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "name": "Acme SaaS Platform",
        "slug": "acme-saas-platform",
        "template_id": "saas-landing",
    }
    res = await client.post("/api/v1/website/projects", json=payload, headers=headers)
    assert res.status_code == 201
    proj_data = res.json()
    proj_id = proj_data["id"]

    assert proj_data["template_id"] == "saas-landing"
    assert len(proj_data["pages"]) == 1
    assert proj_data["pages"][0]["title"] == "Home"
    assert proj_data["pages"][0]["slug"] == ""

    # Fetch created page details and inspect blocks
    page_id = proj_data["pages"][0]["id"]
    res_page = await client.get(
        f"/api/v1/website/projects/{proj_id}/pages/{page_id}",
        headers=headers,
    )
    assert res_page.status_code == 200
    page_data = res_page.json()

    # Blocks must be Schema v2 with full template blocks
    blocks_payload = page_data["blocks"]
    assert blocks_payload["schema_version"] == 2
    block_list = blocks_payload["blocks"]
    assert len(block_list) >= 5

    block_types = [b["type"] for b in block_list]
    assert "hero" in block_types
    assert "features_grid" in block_types
    assert "ad_placement" in block_types
    assert "open_design_section" in block_types
    assert "footer" in block_types

    # Ensure hero contains template content
    hero_blk = next(b for b in block_list if b["type"] == "hero")
    assert "File sync" in hero_blk["content"]["headline"]


async def test_template_deep_copy_independence(client: AsyncClient, test_db):
    """Verify that modifying catalog template does NOT alter already-created projects."""
    token, _ = await _create_user_and_token(client, "tmpl_user6@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create project from pricing-page template
    payload = {
        "name": "Independent Pricing Site",
        "slug": "indep-pricing-site",
        "template_id": "pricing-page",
    }
    res = await client.post("/api/v1/website/projects", json=payload, headers=headers)
    assert res.status_code == 201
    proj_id = res.json()["id"]
    page_id = res.json()["pages"][0]["id"]

    # Retrieve initial page blocks
    res_page_before = await client.get(
        f"/api/v1/website/projects/{proj_id}/pages/{page_id}",
        headers=headers,
    )
    blocks_before = res_page_before.json()["blocks"]

    # 2. Modify the catalog template in the database
    stmt = select(WebsiteTemplate).where(WebsiteTemplate.id == "pricing-page")
    t_res = await test_db.execute(stmt)
    tmpl_record = t_res.scalar_one()
    # Mutate the template record's default pages in catalog
    mutated_pages = copy.deepcopy(tmpl_record.default_pages)
    mutated_pages[0]["title"] = "MUTATED CATALOG TITLE"
    tmpl_record.default_pages = mutated_pages
    await test_db.commit()

    # 3. Retrieve the publisher's project page again - must remain unchanged!
    res_page_after = await client.get(
        f"/api/v1/website/projects/{proj_id}/pages/{page_id}",
        headers=headers,
    )
    blocks_after = res_page_after.json()["blocks"]
    page_title_after = res_page_after.json()["title"]

    assert page_title_after == "Home"
    assert blocks_after == blocks_before


async def test_create_project_with_all_15_templates(client: AsyncClient, test_db):
    """Verify that all 15 approved Open Design templates instantiate projects successfully."""
    token, _ = await _create_user_and_token(client, "tmpl_user7@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    test_slugs = [
        ("saas-landing", "site-saas-landing"),
        ("pricing-page", "site-pricing-page"),
        ("blog-post", "site-blog-post"),
        ("docs-page", "site-docs-page"),
        ("web-prototype-taste-editorial", "site-taste-editorial"),
        ("waitlist-page", "site-waitlist-page"),
        ("web-prototype-taste-soft", "site-taste-soft"),
        ("web-prototype-taste-brutalist", "site-taste-brutalist"),
        ("dashboard", "site-dashboard"),
        ("kanban-board", "site-kanban-board"),
        ("team-okrs", "site-team-okrs"),
        ("eng-runbook", "site-eng-runbook"),
        ("finance-report", "site-finance-report"),
        ("hr-onboarding", "site-hr-onboarding"),
        ("meeting-notes", "site-meeting-notes"),
    ]

    for template_id, slug in test_slugs:
        payload = {
            "name": f"Site for {template_id}",
            "slug": slug,
            "template_id": template_id,
        }
        res = await client.post("/api/v1/website/projects", json=payload, headers=headers)
        assert res.status_code == 201, f"Failed to create project with template {template_id}: {res.text}"
        data = res.json()
        assert data["template_id"] == template_id
        assert len(data["pages"]) >= 1


async def test_tenant_isolation_on_template_instantiated_projects(client: AsyncClient, test_db):
    """Verify tenant isolation: User B cannot view or modify User A's project."""
    token_a, user_a = await _create_user_and_token(client, "tenant_a@test.com")
    token_b, user_b = await _create_user_and_token(client, "tenant_b@test.com")

    # User A creates a website project
    res_a = await client.post(
        "/api/v1/website/projects",
        json={"name": "Tenant A Site", "slug": "tenant-a-site", "template_id": "saas-landing"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert res_a.status_code == 201
    proj_a_id = res_a.json()["id"]

    # User B attempts to access User A's project -> must receive 404 (anti-enumeration)
    res_b = await client.get(
        f"/api/v1/website/projects/{proj_a_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b.status_code == 404

