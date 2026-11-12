"""
test_open_design_phase3_2_metadata.py — RivinityADS Open Design Phase 3.2 Semantic DOM Metadata Tests

Verifies:
1. Block ID propagation: Valid block IDs are preserved; missing/invalid IDs result in un-editable blocks.
2. Semantic annotations for all 7 native blocks (hero, text_section, features_grid, image_gallery, contact_form, cta_banner, footer).
3. Correct array item indexes (data-od-item-index) on repeating list items.
4. Read-only and protected fields (items.icon, form fields, honeypot inputs).
5. Ad placement immunity: data-od-protected="true" and data-od-editable="false" enforced on ad placements.
6. Custom Open Design sections: Trusted metadata emitted on outer wrapper only; inner HTML left untouched.
7. HTML Sanitizer rejection: User-authored HTML with injected data-od-* or data-slot attributes is rejected.
8. Preview endpoint integration: /preview/{token} correctly renders semantic DOM metadata.
"""

from __future__ import annotations

import re
import pytest
from httpx import AsyncClient

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer
from liteads.schemas.website import validate_and_sanitize_open_design_html

pytestmark = pytest.mark.asyncio


async def _create_user_and_token(client: AsyncClient, email: str, role: str = "publisher"):
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


# ── 1. Unit Tests: SafeWebsiteRenderer Semantic Metadata ──────────────────────

async def test_renderer_block_id_propagation_and_validation():
    """Valid IDs are preserved; missing/invalid IDs do not become editable."""
    dummy_project = {"name": "Test Site", "slug": "test-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk_hero_valid",
                    "type": "hero",
                    "is_visible": True,
                    "content": {"headline": "Valid Hero", "alignment": "left"},
                },
                {
                    "id": "invalid id with spaces!@#",
                    "type": "hero",
                    "is_visible": True,
                    "content": {"headline": "Invalid ID Hero", "alignment": "left"},
                },
                {
                    # Missing ID entirely
                    "type": "hero",
                    "is_visible": True,
                    "content": {"headline": "Missing ID Hero", "alignment": "left"},
                },
            ],
        },
    }

    html = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://localhost:3000",
        is_preview=True,
    )

    # Valid block must carry block-id and editable attribute
    assert 'data-od-block-id="blk_hero_valid"' in html
    assert 'data-od-block-type="hero"' in html
    assert 'data-od-field="headline" data-od-editable="true">Valid Hero<' in html

    # Invalid ID block must NOT have data-od-block-id with invalid id
    assert "invalid id with spaces" not in html
    # Missing ID block must NOT be marked editable
    assert '>Missing ID Hero<' in html
    # The invalid and missing ID headlines must NOT have data-od-editable="true"
    assert 'data-od-editable="true">Invalid ID Hero<' not in html
    assert 'data-od-editable="true">Missing ID Hero<' not in html


async def test_renderer_native_blocks_semantic_metadata():
    """All 7 native blocks emit exact field identifiers and array indexes."""
    dummy_project = {"name": "Native Blocks Site", "slug": "native-blocks", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                # 1. Hero
                {
                    "id": "blk_hero",
                    "type": "hero",
                    "is_visible": True,
                    "content": {
                        "headline": "Hero Title",
                        "subheadline": "Hero Subtitle",
                        "cta_button": {"label": "Click Me", "target_url": "https://example.com", "style": "primary"},
                    },
                },
                # 2. Text Section
                {
                    "id": "blk_text",
                    "type": "text_section",
                    "is_visible": True,
                    "content": {
                        "heading": "Text Heading",
                        "body": "First paragraph.\n\nSecond paragraph.",
                    },
                },
                # 3. Features Grid
                {
                    "id": "blk_features",
                    "type": "features_grid",
                    "is_visible": True,
                    "content": {
                        "title": "Our Features",
                        "items": [
                            {"title": "Feature 1", "description": "Desc 1"},
                            {"title": "Feature 2", "description": "Desc 2"},
                        ],
                    },
                },
                # 4. Image Gallery
                {
                    "id": "blk_gallery",
                    "type": "image_gallery",
                    "is_visible": True,
                    "content": {
                        "title": "Photo Gallery",
                        "images": [
                            {"image_url": "https://example.com/1.jpg", "alt_text": "Img 1", "caption": "Cap 1"},
                            {"image_url": "https://example.com/2.jpg", "alt_text": "Img 2", "caption": "Cap 2"},
                        ],
                    },
                },
                # 5. Contact Form
                {
                    "id": "blk_contact",
                    "type": "contact_form",
                    "is_visible": True,
                    "content": {
                        "title": "Get In Touch",
                        "submit_label": "Submit Form",
                        "fields": ["name", "email", "message"],
                    },
                },
                # 6. CTA Banner
                {
                    "id": "blk_cta",
                    "type": "cta_banner",
                    "is_visible": True,
                    "content": {
                        "title": "Call To Action",
                        "description": "Action Description",
                        "button": {"label": "Act Now", "target_url": "https://example.com/act"},
                    },
                },
                # 7. Footer
                {
                    "id": "blk_footer",
                    "type": "footer",
                    "is_visible": True,
                    "content": {
                        "copyright_text": "2026 Test Corp",
                        "links": [
                            {"label": "Privacy Policy", "target_url": "https://example.com/privacy"},
                            {"label": "Terms of Service", "target_url": "https://example.com/terms"},
                        ],
                    },
                },
            ],
        },
    }

    html = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://localhost:3000",
        is_preview=True,
    )

    # 1. Hero
    assert 'data-od-block-id="blk_hero" data-od-block-type="hero" data-od-field="headline" data-od-editable="true">Hero Title<' in html
    assert 'data-od-block-id="blk_hero" data-od-block-type="hero" data-od-field="subheadline" data-od-editable="true">Hero Subtitle<' in html
    assert 'data-od-block-id="blk_hero" data-od-block-type="hero" data-od-field="cta_button.label" data-od-editable="true"' in html

    # 2. Text Section
    assert 'data-od-block-id="blk_text" data-od-block-type="text_section" data-od-field="heading" data-od-editable="true">Text Heading<' in html
    assert 'data-od-block-id="blk_text" data-od-block-type="text_section" data-od-field="body" data-od-item-index="0" data-od-editable="true">First paragraph.<' in html
    assert 'data-od-block-id="blk_text" data-od-block-type="text_section" data-od-field="body" data-od-item-index="1" data-od-editable="true">Second paragraph.<' in html

    # 3. Features Grid
    assert 'data-od-block-id="blk_features" data-od-block-type="features_grid" data-od-field="title" data-od-editable="true">Our Features<' in html
    assert 'data-od-block-id="blk_features" data-od-block-type="features_grid" data-od-field="items.title" data-od-item-index="0" data-od-editable="true">Feature 1<' in html
    assert 'data-od-block-id="blk_features" data-od-block-type="features_grid" data-od-field="items.description" data-od-item-index="0" data-od-editable="true">Desc 1<' in html
    assert 'data-od-block-id="blk_features" data-od-block-type="features_grid" data-od-field="items.icon" data-od-item-index="0" data-od-editable="false"' in html

    # 4. Image Gallery
    assert 'data-od-block-id="blk_gallery" data-od-block-type="image_gallery" data-od-field="title" data-od-editable="true">Photo Gallery<' in html
    assert 'data-od-block-id="blk_gallery" data-od-block-type="image_gallery" data-od-field="images.image_url" data-od-item-index="0" data-od-editable="true"' in html
    assert 'data-od-block-id="blk_gallery" data-od-block-type="image_gallery" data-od-field="images.caption" data-od-item-index="0" data-od-editable="true">Cap 1<' in html

    # 5. Contact Form
    assert 'data-od-block-id="blk_contact" data-od-block-type="contact_form" data-od-field="title" data-od-editable="true">Get In Touch<' in html
    assert 'data-od-block-id="blk_contact" data-od-block-type="contact_form" data-od-field="submit_label" data-od-editable="true"' in html
    assert 'data-od-protected="true" data-od-editable="false"' in html  # honeypot

    # 6. CTA Banner
    assert 'data-od-block-id="blk_cta" data-od-block-type="cta_banner" data-od-field="title" data-od-editable="true">Call To Action<' in html
    assert 'data-od-block-id="blk_cta" data-od-block-type="cta_banner" data-od-field="description" data-od-editable="true">Action Description<' in html
    assert 'data-od-block-id="blk_cta" data-od-block-type="cta_banner" data-od-field="button.label" data-od-editable="true"' in html

    # 7. Footer
    assert 'data-od-block-id="blk_footer" data-od-block-type="footer"' in html
    assert 'data-od-block-id="blk_footer" data-od-block-type="footer" data-od-field="links.label" data-od-item-index="0" data-od-editable="true">Privacy Policy<' in html
    assert 'data-od-block-id="blk_footer" data-od-block-type="footer" data-od-field="copyright_text" data-od-editable="true">2026 Test Corp<' in html


async def test_renderer_ad_placement_immunity_and_protection():
    """Ad placements and placeholders strictly enforce data-od-protected='true' and data-od-editable='false'."""
    dummy_project = {"name": "Ad Site", "slug": "ad-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk_ad_1",
                    "type": "ad_placement",
                    "is_visible": True,
                    "content": {
                        "placement_location": "in_article",
                        "format": 1,
                        "ad_slot_id": "slot_test_123",
                    },
                },
            ],
        },
    }

    # 1. Preview mode
    html_preview = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://localhost:3000",
        is_preview=True,
    )
    assert 'data-od-block-id="blk_ad_1" data-od-block-type="ad_placement"' in html_preview
    assert 'data-od-protected="true" data-od-editable="false"' in html_preview
    assert 'class="rivinity-ad-placeholder"' in html_preview
    # Must never have data-od-editable="true"
    assert 'data-od-editable="true"' not in html_preview

    # 2. Preview banner and consent banner are also protected
    assert 'class="preview-banner" role="alert" data-od-protected="true" data-od-editable="false"' in html_preview


async def test_renderer_custom_open_design_section_wrapper_only():
    """open_design_section receives trusted metadata only on outer wrapper; inner HTML is untouched."""
    dummy_project = {"name": "OD Site", "slug": "od-site", "theme_config": {}}
    inner_html = '<div class="custom-card"><h2 class="card-title">Custom Header</h2><p>Custom paragraph</p></div>'
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk_od_custom",
                    "type": "open_design_section",
                    "is_visible": True,
                    "content": {
                        "section_tag": "section",
                        "section_id": "custom-hero",
                        "css_classes": "od-custom-theme",
                        "html_content": inner_html,
                    },
                },
            ],
        },
    }

    html = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://localhost:3000",
        is_preview=True,
    )

    # Outer section wrapper carries block ID and type
    assert 'data-od-block-id="blk_od_custom" data-od-block-type="open_design_section"' in html
    # Inner HTML is preserved intact without injected data-od-field
    assert '<h2 class="card-title">Custom Header</h2>' in html
    assert 'data-od-field' not in html


async def test_html_sanitizer_rejects_injected_metadata_attributes():
    """User-supplied HTML cannot inject data-od-* or data-slot attributes."""
    malicious_snippets = [
        '<div data-od-block-id="hero_fake">Injected block ID</div>',
        '<h1 data-od-field="headline">Injected field</h1>',
        '<span data-od-editable="true">Injected editable</span>',
        '<div data-od-protected="false">Attempt to disable protection</div>',
        '<div data-slot="spoofed_slot">Spoofed ad slot</div>',
        '<div data-ad="true">Spoofed ad</div>',
    ]

    for snippet in malicious_snippets:
        with pytest.raises(ValueError) as excinfo:
            validate_and_sanitize_open_design_html(snippet)
        assert any(term in str(excinfo.value).lower() for term in ["not permitted", "prohibited", "forbidden"])


# ── 2. Integration Tests: Preview Endpoint ───────────────────────────────────

async def test_preview_endpoint_emits_semantic_metadata_with_edit_mode(client: AsyncClient):
    """Preview endpoint with edit_mode=1 renders both CSP nonce and semantic DOM metadata."""
    token, user_id = await _create_user_and_token(client, "p32_user@test.com", role="publisher")
    headers = {"Authorization": f"Bearer {token}"}

    # Create project with template
    create_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={
            "name": "Phase 3.2 Metadata Site",
            "slug": f"p32-meta-{user_id}",
            "template_id": "saas-landing",
        },
    )
    assert create_res.status_code in [200, 201], f"Create project failed: {create_res.text}"
    project_id = create_res.json()["id"]

    # Generate preview token
    token_res = await client.post(
        f"/api/v1/website/projects/{project_id}/preview-token",
        headers=headers,
    )
    assert token_res.status_code == 200, f"Generate preview token failed: {token_res.text}"
    preview_token = token_res.json()["preview_token"]

    # Request editing preview
    preview_res = await client.get(f"/preview/{preview_token}?edit_mode=1")
    assert preview_res.status_code == 200
    html = preview_res.text

    # Verify CSP nonce in CSP and HTML
    csp = preview_res.headers.get("Content-Security-Policy", "")
    assert "nonce-" in csp
    assert "data-od-edit-bridge" in html

    # Verify Semantic DOM Metadata
    assert "data-od-block-id=" in html
    assert "data-od-block-type=" in html
    assert "data-od-field=" in html
    assert 'data-od-editable="true"' in html
    assert 'data-od-protected="true"' in html
