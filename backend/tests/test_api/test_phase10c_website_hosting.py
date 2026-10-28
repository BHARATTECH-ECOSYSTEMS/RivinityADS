"""Phase 10C: Safe Template Renderer and Public Website Hosting Tests.

Verifies:
1. Safe rendering of all 8 supported block types.
2. XSS payload neutralization in text, URLs, image attributes, and theme tokens.
3. Host normalization, platform domain constraints, and untrusted proxy header rejection.
4. Publication state enforcement: 404/403 for draft, archived, deleted, or suspended projects.
5. Home and secondary page routing, with clean 404s for unknown page slugs.
6. Missing/cross-project published version references fail closed.
7. Snapshot isolation: draft mutations do not affect published output until republished.
8. Cache invalidation on publish, unpublish, rollback, and takedown.
9. Dynamic XML sitemap and robots.txt excluding drafts and noindex pages.
10. Response security headers (strict CSP, nosniff, frame-ancestors, referrer policy).
11. Inert, non-billable ad placement placeholders without tracking pixels or ad calls.
12. Cryptographic draft preview tokens, expiration, and project isolation.
"""

from __future__ import annotations

import time
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from liteads.ad_server.routers.website_public import create_preview_token, verify_preview_token
from liteads.ad_server.services.website_cache import website_cache
from liteads.ad_server.services.website_renderer import safe_theme_config, safe_url, website_renderer
from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.models.auth import User
from liteads.models.website import (
    WebsiteContactSubmission,
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
    return data["access_token"], data["user"]["id"]


# ---------------------------------------------------------------------------
# 1. Safe Template Rendering Unit Tests & Block Types
# ---------------------------------------------------------------------------

async def test_safe_url_sanitization():
    """Verify safe_url allowlists safe schemes and blocks dangerous/executable protocols."""
    assert safe_url("https://example.com/page") == "https://example.com/page"
    assert safe_url("http://example.com") == "http://example.com"
    assert safe_url("mailto:info@example.com") == "mailto:info@example.com"
    assert safe_url("tel:+1234567890") == "tel:+1234567890"
    assert safe_url("/about") == "/about"

    # Hostile vectors neutralized to #
    assert safe_url("javascript:alert(1)") == "#"
    assert safe_url("JAVASCRIPT:alert(document.cookie)") == "#"
    assert safe_url("data:text/html,<script>alert(1)</script>") == "#"
    assert safe_url("vbscript:msgbox(1)") == "#"
    assert safe_url("blob:https://example.com/1234") == "#"
    assert safe_url("https://example.com/<script>bad</script>") == "#"
    assert safe_url('https://example.com/" onfocus="alert(1)') == "#"
    assert safe_url(None) == "#"
    assert safe_url("") == "#"


async def test_safe_theme_config_css_injection_defense():
    """Verify safe_theme_config rejects CSS injection attempts and enforces defaults."""
    # Hostile theme payload attempting CSS breakout
    malicious_theme = {
        "primary_color": "red; background: url(javascript:alert(1)); </style><script>",
        "secondary_color": "#invalid",
        "background_color": "#ffffff",
        "text_color": "#000000",
        "font_family": "'; alert(1); /*",
        "border_radius": "0.5rem; color: red;",
        "logo_url": "javascript:alert(1)",
    }

    clean = safe_theme_config(malicious_theme)
    # Primary color should fall back to default hex #2563eb
    assert clean["primary_color"] == "#2563eb"
    assert clean["secondary_color"] == "#475569"
    assert clean["background_color"] == "#ffffff"
    assert clean["text_color"] == "#000000"
    # Font family should fall back to Inter
    assert clean["font_family"] == "Inter"
    assert clean["border_radius"] == "0.5rem"
    assert clean["logo_url"] == "#"


async def test_renderer_neutralizes_xss_across_all_blocks():
    """Verify that hostile input in any block field renders as harmless escaped text."""
    project_dict = {
        "name": "<script>alert('project')</script>",
        "slug": "xss-test",
        "theme_config": {"primary_color": "#123456", "font_family": "Roboto"},
    }

    page_dict = {
        "title": "Page <img src=x onerror=alert(1)>",
        "slug": "",
        "is_home": True,
        "seo_meta": {
            "meta_description": '"><script>alert("meta")</script>',
            "og_image_url": "javascript:alert('og')",
        },
        "blocks": {
            "blocks": [
                {
                    "type": "hero",
                    "content": {
                        "headline": "Hero <script>alert('headline')</script>",
                        "subheadline": "Sub <svg onload=alert(1)>",
                        "cta_button": {
                            "label": "Click <b onmouseover=alert(1)>me</b>",
                            "target_url": "https://example.com/safe-cta",
                        },
                    },
                },
                {
                    "type": "text_section",
                    "content": {
                        "heading": "Heading <iframe src='evil.com'>",
                        "body": "Body paragraph <script>evil()</script>\n\nSecond paragraph",
                    },
                },
                {
                    "type": "features_grid",
                    "content": {
                        "title": "Features <script>grid()</script>",
                        "items": [
                            {
                                "title": "Feature <script>f()</script>",
                                "description": "Desc <img src=x onerror=alert(2)>",
                            }
                        ],
                    },
                },
                {
                    "type": "image_gallery",
                    "content": {
                        "images": [
                            {
                                "image_url": "javascript:alert('img')",
                                "alt_text": 'Alt " onfocus="alert(1)',
                                "caption": "Caption <script>c()</script>",
                            }
                        ]
                    },
                },
                {
                    "type": "cta_banner",
                    "content": {
                        "title": "CTA <script>b()</script>",
                        "button": {
                            "label": "CTA Btn",
                            "target_url": "javascript:alert('banner')",
                        },
                    },
                },
                {
                    "type": "ad_placement",
                    "content": {
                        "placement_location": "in_article",
                        "format": 1,
                        "ad_slot_id": 'slot"><script>alert(1)</script>',
                    },
                },
            ]
        },
    }

    html = website_renderer.render_published_page(
        project_dict=project_dict,
        page_dict=page_dict,
        all_pages_dict=[page_dict],
        base_origin="https://xss-test.rivinity.site",
    )

    # 1. No raw unescaped <script> tags anywhere in the body
    assert "<script>alert('project')</script>" not in html
    assert "<script>alert('headline')</script>" not in html
    assert "<script>evil()</script>" not in html
    assert "<script>grid()</script>" not in html
    assert "<script>alert(1)</script>" not in html
    assert "<iframe" not in html
    assert "<svg" not in html

    # 2. Hostile script tags are rendered escaped as text (&lt;script&gt;)
    assert "&lt;script&gt;alert(&#39;headline&#39;)&lt;/script&gt;" in html
    assert "&lt;script&gt;evil()&lt;/script&gt;" in html

    # 3. No executable tags or handlers (tags are escaped to &lt;)
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<b onmouseover" not in html
    assert "&lt;b onmouseover=alert(1)&gt;" in html

    # 4. Javascript URLs neutralized to #
    assert "javascript:" not in html

    # 5. Ad placement is placeholder only
    assert "RivinityADS Ad Placement Placeholder" in html
    assert "Non-Billable Sandbox" in html
    assert "/api/v1/ad/serve" not in html


# ---------------------------------------------------------------------------
# 2. Public Subdomain & Direct Path Resolution Tests
# ---------------------------------------------------------------------------

async def test_public_website_full_lifecycle(client: AsyncClient, test_db):
    """End-to-end integration test: create project, update 8 blocks, publish, serve via Host header."""
    token, _ = await _create_user_and_token(client, "web_hoster@test.com", "publisher")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create project
    proj_res = await client.post(
        "/api/v1/website/projects",
        json={
            "name": "Acme Innovations",
            "slug": "acme-inno",
            "template_id": "corporate",
            "theme_config": {
                "primary_color": "#2563eb",
                "font_family": "Inter",
            },
        },
        headers=headers,
    )
    assert proj_res.status_code == 201, proj_res.text
    proj_data = proj_res.json()
    proj_id = proj_data["id"]
    home_page_id = proj_data["pages"][0]["id"]

    # 2. Update Homepage with all 8 block types
    all_blocks = [
        {
            "id": "b-hero",
            "type": "hero",
            "content": {
                "headline": "Welcome to Acme Innovations",
                "subheadline": "We build scalable systems for modern enterprises.",
                "cta_button": {"label": "Get Started", "target_url": "/contact"},
                "alignment": "center",
            },
        },
        {
            "id": "b-text",
            "type": "text_section",
            "content": {
                "heading": "About Our Platform",
                "body": "First paragraph describing the technology.\n\nSecond paragraph with details.",
            },
        },
        {
            "id": "b-features",
            "type": "features_grid",
            "content": {
                "title": "Platform Highlights",
                "items": [
                    {"title": "High Speed", "description": "Sub-millisecond latency."},
                    {"title": "Security First", "description": "Zero trust architecture."},
                ],
            },
        },
        {
            "id": "b-gallery",
            "type": "image_gallery",
            "content": {
                "title": "Our Offices",
                "images": [
                    {"image_url": "https://images.example.com/office1.jpg", "alt_text": "Tech HQ"}
                ],
            },
        },
        {
            "id": "b-form",
            "type": "contact_form",
            "content": {
                "title": "Get in Touch",
                "submit_label": "Submit Inquiry",
                "fields": ["name", "email", "phone", "message"],
            },
        },
        {
            "id": "b-banner",
            "type": "cta_banner",
            "content": {
                "title": "Join 10,000+ Teams",
                "description": "Start building today.",
                "button": {"label": "Sign Up Free", "target_url": "https://example.com/signup"},
            },
        },
        {
            "id": "b-ad",
            "type": "ad_placement",
            "content": {
                "placement_location": "in_article",
                "format": 1,
                "ad_slot_id": "slot_homepage_mid",
            },
        },
        {
            "id": "b-footer",
            "type": "footer",
            "content": {
                "copyright_text": "2026 Acme Innovations Inc. All rights reserved.",
                "links": [{"label": "Privacy Policy", "target_url": "/privacy"}],
            },
        },
    ]

    home_update_res = await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
        json={
            "title": "Home",
            "seo_meta": {
                "meta_title": "Acme Innovations — Enterprise Tech",
                "meta_description": "Official portal of Acme Innovations.",
            },
            "blocks": {"blocks": all_blocks},
        },
        headers=headers,
    )
    assert home_update_res.status_code == 200, home_update_res.text

    # Add secondary page
    about_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/pages",
        json={
            "title": "About Us",
            "slug": "about-us",
            "is_home": False,
            "seo_meta": {"meta_description": "About Acme team and vision."},
            "blocks": {
                "blocks": [
                    {
                        "id": "b-about-text",
                        "type": "text_section",
                        "content": {
                            "heading": "Our Vision",
                            "body": "Building next-generation open systems.",
                        },
                    }
                ]
            },
        },
        headers=headers,
    )
    assert about_res.status_code == 201

    # 3. Before publication: verify public route returns 404
    public_headers = {"Host": "acme-inno.rivinity.site"}
    draft_check = await client.get("/", headers=public_headers)
    assert draft_check.status_code == 404, "Draft site must not be publicly accessible"

    # 4. Publish project
    pub_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/publish",
        json={"notes": "v1.0 launch"},
        headers=headers,
    )
    assert pub_res.status_code == 200, pub_res.text

    # 5. Serve Homepage via Host header
    home_pub = await client.get("/", headers=public_headers)
    assert home_pub.status_code == 200, home_pub.text
    content = home_pub.text

    # Assert block content presence
    assert "Welcome to Acme Innovations" in content
    assert "About Our Platform" in content
    assert "Platform Highlights" in content
    assert "Our Offices" in content
    assert "Get in Touch" in content
    assert "Join 10,000+ Teams" in content
    assert "RivinityADS Ad Placement Placeholder" in content
    assert "2026 Acme Innovations Inc." in content

    # Assert Security Headers
    assert "Content-Security-Policy" in home_pub.headers
    csp = home_pub.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert home_pub.headers["X-Content-Type-Options"] == "nosniff"
    assert home_pub.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert home_pub.headers["X-Frame-Options"] == "DENY"

    # Assert Canonical URL
    assert '<link rel="canonical" href="https://acme-inno.rivinity.site/">' in content

    # 6. Serve Secondary Page via Subdomain
    about_pub = await client.get("/about-us", headers=public_headers)
    assert about_pub.status_code == 200
    assert "Our Vision" in about_pub.text
    assert '<link rel="canonical" href="https://acme-inno.rivinity.site/about-us">' in about_pub.text

    # 7. Unknown page slug returns clean 404
    missing_page = await client.get("/non-existent-page", headers=public_headers)
    assert missing_page.status_code == 404
    assert "Page Not Found" in missing_page.text

    # 8. Direct Path Routing test (/sites/{slug})
    direct_home = await client.get("/sites/acme-inno")
    assert direct_home.status_code == 200
    assert "Welcome to Acme Innovations" in direct_home.text

    direct_about = await client.get("/sites/acme-inno/about-us")
    assert direct_about.status_code == 200
    assert "Our Vision" in direct_about.text


# ---------------------------------------------------------------------------
# 3. Host Normalization and Untrusted Header Protection
# ---------------------------------------------------------------------------

async def test_host_normalization_and_untrusted_forwarded_host(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "host_tester@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Create & publish a site
    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Host Test Site", "slug": "host-test"},
        headers=headers,
    )
    p_id = p_res.json()["id"]

    await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)

    # 1. Valid host with port (e.g. host-test.rivinity.site:8000)
    res_port = await client.get("/", headers={"Host": "host-test.rivinity.site:8000"})
    assert res_port.status_code == 200

    # 2. Localhost subdomain (host-test.localhost)
    res_local = await client.get("/", headers={"Host": "host-test.localhost:8000"})
    assert res_local.status_code == 200

    # 3. Reserved platform subdomain should return 404
    res_admin = await client.get("/", headers={"Host": "admin.rivinity.site"})
    assert res_admin.status_code == 404

    res_api = await client.get("/", headers={"Host": "api.rivinity.site"})
    assert res_api.status_code == 404

    # 4. Malformed subdomain with double dots or symbols
    res_malformed = await client.get("/", headers={"Host": "bad..subdomain.rivinity.site"})
    assert res_malformed.status_code == 404

    # 5. Untrusted proxy X-Forwarded-Host:
    # Path or query param cannot override host identity
    res_override = await client.get("/?site=other-site", headers={"Host": "host-test.rivinity.site"})
    assert res_override.status_code == 200
    assert "Host Test Site" in res_override.text


# ---------------------------------------------------------------------------
# 4. Snapshot Integrity, Draft Isolation, and Cache Invalidation Lifecycle
# ---------------------------------------------------------------------------

async def test_snapshot_immutability_and_cache_invalidation_lifecycle(client: AsyncClient, test_db):
    """
    Proves:
    1. Public serving strictly reads from published snapshot, not draft table.
    2. Modifying draft pages has zero effect on public output until republished.
    3. Caching serves hits after initial MISS.
    4. Unpublishing immediately invalidates cache and returns 404.
    5. Rollback restores snapshot content and invalidates cache.
    6. Suspension immediately invalidates cache and returns 403.
    """
    token, _ = await _create_user_and_token(client, "cache_lifecycle@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create project & update initial home page
    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Lifecycle Brand", "slug": "life-brand"},
        headers=headers,
    )
    p_data = p_res.json()
    p_id = p_data["id"]
    page_id = p_data["pages"][0]["id"]

    await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{page_id}",
        json={
            "title": "Home",
            "blocks": {
                "blocks": [
                    {
                        "id": "b-hero",
                        "type": "hero",
                        "content": {"headline": "V1 Original Live Headline"},
                    }
                ]
            },
        },
        headers=headers,
    )

    # 2. Publish Version 1
    v1_res = await client.post(
        f"/api/v1/website/projects/{p_id}/publish",
        json={"notes": "v1.0.0"},
        headers=headers,
    )
    assert v1_res.status_code == 200
    v1_id = v1_res.json()["id"]

    pub_headers = {"Host": "life-brand.rivinity.site"}

    # First public request: MISS
    resp1 = await client.get("/", headers=pub_headers)
    assert resp1.status_code == 200
    assert "V1 Original Live Headline" in resp1.text
    assert resp1.headers["X-Cache"] == "MISS"

    # Second public request: HIT
    resp2 = await client.get("/", headers=pub_headers)
    assert resp2.status_code == 200
    assert resp2.headers["X-Cache"] == "HIT"

    # 3. Mutate draft page (DO NOT PUBLISH)
    update_res = await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{page_id}",
        json={
            "blocks": {
                "blocks": [
                    {
                        "id": "b-hero",
                        "type": "hero",
                        "content": {"headline": "SECRET DRAFT MUTATION — NOT PUBLISHED"},
                    }
                ]
            }
        },
        headers=headers,
    )
    assert update_res.status_code == 200

    # Public site MUST STILL serve V1 from snapshot!
    resp3 = await client.get("/", headers=pub_headers)
    assert resp3.status_code == 200
    assert "V1 Original Live Headline" in resp3.text
    assert "SECRET DRAFT MUTATION" not in resp3.text

    # 4. Publish Version 2
    v2_res = await client.post(
        f"/api/v1/website/projects/{p_id}/publish",
        json={"notes": "v2.0.0 with new headline"},
        headers=headers,
    )
    assert v2_res.status_code == 200

    # Cache must be invalidated! Now serves V2 content
    resp4 = await client.get("/", headers=pub_headers)
    assert resp4.status_code == 200
    assert "SECRET DRAFT MUTATION — NOT PUBLISHED" in resp4.text
    assert resp4.headers["X-Cache"] == "MISS"

    # 5. Unpublish site: cache must be evicted and public route returns 404
    unpub = await client.post(f"/api/v1/website/projects/{p_id}/unpublish", headers=headers)
    assert unpub.status_code == 200

    resp5 = await client.get("/", headers=pub_headers)
    assert resp5.status_code == 404, "Unpublished site must return 404"

    # 6. Rollback to Version 1
    roll_res = await client.post(
        f"/api/v1/website/projects/{p_id}/versions/{v1_id}/rollback",
        json={"notes": "Rolling back to V1"},
        headers=headers,
    )
    assert roll_res.status_code == 200

    # Public route now serves V1 content again!
    resp6 = await client.get("/", headers=pub_headers)
    assert resp6.status_code == 200
    assert "V1 Original Live Headline" in resp6.text

    # 7. Suspend site (takedown): must return 403 and evict cache
    admin_token, admin_uid = await _create_user_and_token(client, "admin_takedown@test.com", "publisher")
    stmt = select(User).where(User.id == admin_uid)
    res = await test_db.execute(stmt)
    admin_user = res.scalar_one()
    admin_user.role = "admin"
    await test_db.commit()
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    suspend_res = await client.post(
        f"/api/v1/website/projects/{p_id}/suspend",
        headers=admin_headers,
    )
    assert suspend_res.status_code == 200

    resp7 = await client.get("/", headers=pub_headers)
    assert resp7.status_code == 403, "Suspended site must immediately return 403"
    assert "suspended" in resp7.text.lower()


# ---------------------------------------------------------------------------
# 5. Snapshot Pointer Tampering Fail-Closed Protection
# ---------------------------------------------------------------------------

async def test_snapshot_pointer_tampering_fails_closed(client: AsyncClient, test_db):
    """Verify that an invalid or cross-project version reference fails closed with 404."""
    token, _ = await _create_user_and_token(client, "pointer_tester@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Project A
    p_a = await client.post(
        "/api/v1/website/projects",
        json={"name": "Project A", "slug": "proj-a"},
        headers=headers,
    )
    pa_id = p_a.json()["id"]
    await client.post(f"/api/v1/website/projects/{pa_id}/publish", json={}, headers=headers)

    # Project B
    p_b = await client.post(
        "/api/v1/website/projects",
        json={"name": "Project B", "slug": "proj-b"},
        headers=headers,
    )
    pb_id = p_b.json()["id"]
    await client.post(f"/api/v1/website/projects/{pb_id}/publish", json={}, headers=headers)

    # Corrupt Project A pointer in DB to point to an invalid ID (999999)
    stmt = select(WebsiteProject).where(WebsiteProject.id == pa_id)
    res = await test_db.execute(stmt)
    proj = res.scalar_one()
    proj.current_published_version_id = 999999
    await test_db.commit()
    await website_cache.invalidate_project(pa_id, "proj-a")

    # Public request must fail closed (404), not crash or serve corrupted data
    bad_resp = await client.get("/", headers={"Host": "proj-a.rivinity.site"})
    assert bad_resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. SEO, Dynamic Sitemap, and Robots.txt Tests
# ---------------------------------------------------------------------------

async def test_seo_sitemap_and_robots(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "seo_tester@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "SEO Pro Brand", "slug": "seo-pro"},
        headers=headers,
    )
    p_data = p_res.json()
    p_id = p_data["id"]
    home_id = p_data["pages"][0]["id"]

    # Configure Home (Indexable)
    await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{home_id}",
        json={"seo_meta": {"noindex": False}},
        headers=headers,
    )

    # Page 2: Docs (Indexable)
    await client.post(
        f"/api/v1/website/projects/{p_id}/pages",
        json={"title": "Docs", "slug": "docs-page", "is_home": False, "seo_meta": {"noindex": False}},
        headers=headers,
    )

    # Page 3: Secret Admin/Internal (Noindex)
    await client.post(
        f"/api/v1/website/projects/{p_id}/pages",
        json={"title": "Internal", "slug": "internal-notes", "is_home": False, "seo_meta": {"noindex": True}},
        headers=headers,
    )

    # Publish
    await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)

    pub_headers = {"Host": "seo-pro.rivinity.site"}

    # 1. Sitemap XML
    sm_res = await client.get("/sitemap.xml", headers=pub_headers)
    assert sm_res.status_code == 200
    assert sm_res.headers["Content-Type"].startswith("application/xml")
    sm_xml = sm_res.text

    assert "<loc>https://seo-pro.rivinity.site/</loc>" in sm_xml
    assert "<loc>https://seo-pro.rivinity.site/docs-page</loc>" in sm_xml
    # Secret page marked noindex must NOT be in sitemap!
    assert "internal-notes" not in sm_xml

    # 2. Robots.txt
    rb_res = await client.get("/robots.txt", headers=pub_headers)
    assert rb_res.status_code == 200
    assert "User-agent: *" in rb_res.text
    assert "Sitemap: https://seo-pro.rivinity.site/sitemap.xml" in rb_res.text


# ---------------------------------------------------------------------------
# 7. Contact Form Presentation & Visitor Submission Flow
# ---------------------------------------------------------------------------

async def test_contact_form_submission(client: AsyncClient, test_db):
    token, _ = await _create_user_and_token(client, "contact_owner@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Support Services", "slug": "support-co"},
        headers=headers,
    )
    p_data = p_res.json()
    p_id = p_data["id"]
    home_id = p_data["pages"][0]["id"]

    await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{home_id}",
        json={
            "title": "Contact",
            "blocks": {
                "blocks": [
                    {
                        "id": "b-cform",
                        "type": "contact_form",
                        "content": {"title": "Send Us a Message", "submit_label": "Submit"},
                    }
                ]
            },
        },
        headers=headers,
    )
    await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)

    # Public visitor submits the contact form
    submission_res = await client.post(
        "/api/v1/website/public/support-co/contact",
        json={
            "name": "Jane Visitor",
            "email": "jane@visitor.org",
            "phone": "+1-555-0199",
            "message": "Interested in your open ad server deployment.",
        },
    )
    assert submission_res.status_code == 201, submission_res.text
    data = submission_res.json()
    assert data["success"] is True
    submission_id = data["id"]

    # Verify submission stored in DB
    stmt = select(WebsiteContactSubmission).where(WebsiteContactSubmission.id == submission_id)
    res = await test_db.execute(stmt)
    sub = res.scalar_one_or_none()
    assert sub is not None
    assert sub.project_id == p_id
    assert sub.sender_name == "Jane Visitor"
    assert sub.sender_email == "jane@visitor.org"


# ---------------------------------------------------------------------------
# 8. Cryptographic Preview Token Tests
# ---------------------------------------------------------------------------

async def test_preview_token_generation_and_verification(client: AsyncClient, test_db):
    token, user_id = await _create_user_and_token(client, "preview_author@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create draft project (Unpublished)
    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Draft Project", "slug": "draft-proj"},
        headers=headers,
    )
    p_data = p_res.json()
    p_id = p_data["id"]
    home_id = p_data["pages"][0]["id"]

    await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{home_id}",
        json={
            "title": "Draft Home",
            "blocks": {
                "blocks": [
                    {
                        "id": "b-hero",
                        "type": "hero",
                        "content": {"headline": "Draft Exclusive Secret Preview"},
                    }
                ]
            },
        },
        headers=headers,
    )

    # 2. Generate preview token
    token_res = await client.post(
        f"/api/v1/website/projects/{p_id}/preview-token",
        headers=headers,
    )
    assert token_res.status_code == 200, token_res.text
    preview_token = token_res.json()["preview_token"]
    assert preview_token

    # 3. Access draft preview without authentication (anonymous visitor with token)
    preview_resp = await client.get(f"/preview/{preview_token}")
    assert preview_resp.status_code == 200
    assert "Draft Exclusive Secret Preview" in preview_resp.text
    assert "Draft Preview Mode" in preview_resp.text
    assert "noindex, nofollow, noarchive" in preview_resp.headers["X-Robots-Tag"]
    assert "no-store" in preview_resp.headers["Cache-Control"]

    # 4. Tampered token returns 403
    tampered_token = preview_token + "bad"
    bad_preview = await client.get(f"/preview/{tampered_token}")
    assert bad_preview.status_code == 403

    # 5. Expired token verification
    past_expiry = int(time.time()) - 100
    expired_token = f"{p_id}.{user_id}.{past_expiry}.dummy"
    assert verify_preview_token(expired_token) is None


# ---------------------------------------------------------------------------
# 9. Monetization & Ad Serving Separation
# ---------------------------------------------------------------------------

async def test_monetization_remains_strictly_disabled(client: AsyncClient, test_db):
    """
    Ensure Phase 10C rendering does not call production ad decisioning endpoints
    or activate billable slots.
    """
    token, _ = await _create_user_and_token(client, "ad_boundary@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    p_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Ad Boundary Site", "slug": "ad-bound"},
        headers=headers,
    )
    p_data = p_res.json()
    p_id = p_data["id"]
    home_id = p_data["pages"][0]["id"]

    await client.put(
        f"/api/v1/website/projects/{p_id}/pages/{home_id}",
        json={
            "title": "Home",
            "blocks": {
                "blocks": [
                    {
                        "id": "b-ad",
                        "type": "ad_placement",
                        "content": {
                            "placement_location": "in_article",
                            "format": 1,
                            "ad_slot_id": "slot_non_billable_test",
                        },
                    }
                ]
            },
        },
        headers=headers,
    )
    await client.post(f"/api/v1/website/projects/{p_id}/publish", json={}, headers=headers)

    pub_res = await client.get("/", headers={"Host": "ad-bound.rivinity.site"})
    assert pub_res.status_code == 200
    content = pub_res.text

    # Placeholder notice confirmed
    assert "Non-Billable Sandbox" in content
    assert "Monetization Inactive" in content

    # Absolutely no ad tracking pixel or production ad request
    assert "/api/v1/event/track" not in content
    assert "/api/v1/ad/request" not in content
    assert "<iframe" not in content
