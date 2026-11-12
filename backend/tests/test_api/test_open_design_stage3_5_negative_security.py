"""Stage 3.5 negative security regression tests for Open Design."""

from __future__ import annotations

import re
from typing import Any
from httpx import AsyncClient
import pytest
from pydantic import ValidationError

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer
from liteads.schemas.website import (
    BlockItem,
    BlockType,
    OpenDesignSectionContent,
    PageBlocksPayload,
    validate_and_sanitize_open_design_html,
)


# ── Section A: Forged Metadata & Platform Identifiers ────────────────────────

@pytest.mark.parametrize(
    "snippet",
    [
        '<DIV DATA-OD-BLOCK-ID="forged">Forged block</DIV>',
        '<span data-od-editable = "true">Forged editable marker</span>',
        '<div data-od-protected = "false">Protection bypass</div>',
        '<div data-slot = "forged-slot">Forged slot</div>',
        '<div data&#45;slot="encoded-slot">Encoded slot</div>',
        '<div DATA-AD="true">Forged ad metadata</div>',
        '<div data-format="1">Forged format</div>',
        '<div data-rivinity="platform">Forged platform</div>',
        '<div data-od-item-index="0">Forged index</div>',
        '<div data-od-field="headline">Forged field</div>',
    ],
)
def test_custom_html_rejects_case_whitespace_and_encoded_platform_metadata(snippet: str):
    """Platform metadata cannot be smuggled through attribute spelling variations."""
    with pytest.raises(ValueError):
        validate_and_sanitize_open_design_html(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        '<a href=" JAVASCRIPT:alert(1) ">Unsafe link</a>',
        '<a href="vbscript:msgbox(1)">VBScript link</a>',
        '<img src="data:text/html;base64,Zm9v" alt="unsafe">',
        '<div oN cLiCk = "alert(1)">Event handler</div>',
        '<span ONMOUSEOVER="evil()">Mouseover</span>',
        '<div class=" BLOCK-AD-PLACEMENT ">Ad impersonation</div>',
        '<div class="rivinity-ad-slot">Ad slot impersonation</div>',
        '<div class="rivinity-live-ad-container">Live container impersonation</div>',
        '<div id="rivinity-ad-slot-1">Ad slot ID impersonation</div>',
        '<div id="ad&#95;placement&#95;fake">Encoded ad impersonation</div>',
    ],
)
def test_custom_html_rejects_unsafe_urls_handlers_and_ad_impersonation_variants(snippet: str):
    """Equivalent unsafe HTML forms remain rejected by the backend sanitizer."""
    with pytest.raises(ValueError):
        validate_and_sanitize_open_design_html(snippet)


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("section_id", " block-ad-placement "),
        ("section_id", "ad&#95;placement"),
        ("section_id", "rivinity_ad"),
        ("css_classes", " rivinity-ad-slot "),
        ("css_classes", "block-ad-placement custom"),
    ],
)
def test_open_design_section_metadata_rejects_platform_impersonation(field_name: str, value: str):
    """Section identifiers and classes cannot impersonate protected platform elements."""
    with pytest.raises(ValueError):
        OpenDesignSectionContent(
            **{
                "html_content": "<div>Safe content</div>",
                field_name: value,
            }
        )


def test_block_item_rejects_forged_or_invalid_block_id_and_type():
    """BlockItem schema strictly rejects empty IDs, oversized IDs, and invalid block types."""
    # 1. Empty ID
    with pytest.raises(ValidationError):
        BlockItem(id="", type=BlockType.HERO, content={"headline": "Test"})

    # 2. Oversized ID (> 64 chars)
    with pytest.raises(ValidationError):
        BlockItem(id="b" * 65, type=BlockType.HERO, content={"headline": "Test"})

    # 3. Invalid block type
    with pytest.raises(ValidationError):
        BlockItem.model_validate({"id": "valid_id", "type": "malicious_type", "content": {}})


def test_renderer_sanitizes_or_drops_forged_block_id_and_attributes():
    """SafeWebsiteRenderer does not emit data-od-block-id or editable markers for invalid/forged IDs."""
    dummy_project = {"name": "Test Site", "slug": "test-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": 'forged" onfocus="alert(1)',
                    "type": "hero",
                    "is_visible": True,
                    "content": {"headline": "Forged Block", "alignment": "left"},
                },
                {
                    "id": "hero with spaces",
                    "type": "hero",
                    "is_visible": True,
                    "content": {"headline": "Space Block", "alignment": "left"},
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

    # Forged and space-containing IDs must not produce data-od-block-id attributes
    assert 'data-od-block-id="forged' not in html
    assert 'onfocus="alert(1)' not in html
    assert 'data-od-block-id="hero with spaces"' not in html
    # These blocks must NOT be marked editable
    assert 'data-od-editable="true">Forged Block<' not in html
    assert 'data-od-editable="true">Space Block<' not in html


# ── Section B: Protected Content & Placement Immunity ─────────────────────────

def test_renderer_strictly_enforces_protection_on_platform_elements():
    """Ad placements, honeypots, preview banners, and consent banners are strictly protected."""
    dummy_project = {"name": "Immunity Site", "slug": "immunity-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk_ad_placement_1",
                    "type": "ad_placement",
                    "is_visible": True,
                    "content": {
                        "placement_location": "in_article",
                        "format": 1,
                        "ad_slot_id": "slot_secure_1",
                    },
                },
                {
                    "id": "blk_contact_1",
                    "type": "contact_form",
                    "is_visible": True,
                    "content": {
                        "title": "Contact",
                        "submit_label": "Submit",
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

    # Ad placement is protected and placeholder only
    assert 'data-od-block-id="blk_ad_placement_1"' in html
    assert 'data-od-block-type="ad_placement"' in html
    assert 'data-od-protected="true" data-od-editable="false"' in html
    assert 'class="rivinity-ad-placeholder"' in html

    # Honeypot input is protected
    assert 'name="hp_comment"' in html
    assert 'data-od-protected="true" data-od-editable="false"' in html

    # Preview banner is protected
    assert 'class="preview-banner" role="alert" data-od-protected="true" data-od-editable="false"' in html

    # Ad placement must never be marked editable
    assert 'data-od-block-type="ad_placement" data-od-editable="true"' not in html


def test_renderer_injected_editor_runtime_guards_protected_elements():
    """Injected runtime script includes PROTECTED_SELECTORS and blocks ad placement editing."""
    dummy_project = {"name": "Runtime Site", "slug": "runtime-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {"schema_version": 2, "blocks": []},
    }

    html = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://localhost:3000",
        is_preview=True,
        bridge_nonce="test-security-nonce-1234",
    )

    # Injected runtime script must guard against all protected selectors
    assert "PROTECTED_SELECTORS" in html
    assert ".block-ad-placement" in html
    assert ".rivinity-ad-placeholder" in html
    assert ".rivinity-live-ad-container" in html
    assert ".rivinity-ad-slot" in html
    assert "#rivinity-consent-banner" in html
    assert ".preview-banner" in html
    assert '[name="hp_comment"]' in html
    assert "[data-od-edit-bridge]" in html

    # Ad placement check before text edit start
    assert "meta.blockType === 'ad_placement'" in html


# ── Section C: Sanitizer Consistency & Malformed Payloads ─────────────────────

@pytest.mark.parametrize(
    "tag",
    [
        "iframe", "object", "embed", "style", "link", "form", "input",
        "button", "textarea", "select", "meta", "base", "applet",
        "frame", "frameset", "svg", "canvas", "video", "audio", "portal",
    ],
)
def test_sanitizer_rejects_disallowed_tags(tag: str):
    """Sanitizer rejects every tag listed in DISALLOWED_OD_TAGS."""
    snippet = f"<{tag}>Content</{tag}>" if tag not in ("input", "meta", "base", "link") else f"<{tag} />"
    with pytest.raises(ValueError):
        validate_and_sanitize_open_design_html(snippet)


def test_sanitizer_rejects_oversized_payload():
    """Payloads exceeding 64 KB (65,536 bytes) fail validation."""
    large_html = "<div>" + "A" * 65540 + "</div>"
    with pytest.raises(ValueError, match="exceeds maximum allowed size"):
        validate_and_sanitize_open_design_html(large_html)


def test_sanitizer_rejects_unclosed_tags():
    """Unclosed or mismatched tags fail closed."""
    with pytest.raises(ValueError):
        validate_and_sanitize_open_design_html("<section><div class='wrapper'><p>Unclosed paragraph")


def test_sanitizer_accepts_clean_valid_markup():
    """Clean markup with safe links and formatting passes sanitization."""
    clean_snippet = (
        '<div class="safe-card">'
        '<h2 class="title">Safe Heading</h2>'
        '<p>Paragraph with <strong>bold</strong> and <em>italic</em>.</p>'
        '<a href="https://example.com" target="_blank">External Link</a>'
        '</div>'
    )
    result = validate_and_sanitize_open_design_html(clean_snippet)
    assert '<h2 class="title">Safe Heading</h2>' in result
    assert 'rel="noopener noreferrer"' in result
    assert '<script' not in result


# ── Section D: Persistence & Live Billing Protection ──────────────────────────

async def _register_and_create_project(client: AsyncClient, email: str):
    reg = await client.post(
        "/api/v1/auth/register",
        json={"name": "Security User", "email": email, "password": "SecurePassword123!", "role": "publisher"},
    )
    assert reg.status_code in (200, 201), f"Registration failed: {reg.text}"
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    clean_slug = f"proj-{email.split('@')[0].replace('_', '-')}"
    proj = await client.post(
        "/api/v1/website/projects",
        json={"name": "Persistence Site", "slug": clean_slug, "template_id": "saas-landing"},
        headers=headers,
    )
    assert proj.status_code in (200, 201), f"Project creation failed: {proj.text}"
    proj_data = proj.json()
    page_id = proj_data["pages"][0]["id"]
    return headers, proj_data["id"], page_id


@pytest.mark.asyncio
async def test_api_page_update_rejects_invalid_custom_section_and_forged_content(client: AsyncClient, test_db):
    """PUT /projects/{id}/pages/{page_id} rejects unsafe custom HTML and maintains persistence integrity."""
    headers, proj_id, page_id = await _register_and_create_project(client, "neg_persist@test.com")

    # Fetch original page blocks
    orig_res = await client.get(f"/api/v1/website/projects/{proj_id}/pages/{page_id}", headers=headers)
    assert orig_res.status_code == 200
    orig_blocks = orig_res.json()["blocks"]

    # 1. Attempt update with XSS in custom HTML
    malicious_blocks = {
        "schema_version": 2,
        "blocks": [
            {
                "id": "blk_custom_xss",
                "type": "open_design_section",
                "is_visible": True,
                "content": {
                    "html_content": "<div class='content'><script>alert('pwned')</script></div>",
                },
            }
        ],
    }
    put_xss = await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{page_id}",
        headers=headers,
        json={"blocks": malicious_blocks},
    )
    assert put_xss.status_code == 422, f"Expected 422 for XSS, got {put_xss.status_code}"

    # 2. Attempt update with spoofing class
    spoof_blocks = {
        "schema_version": 2,
        "blocks": [
            {
                "id": "blk_custom_spoof",
                "type": "open_design_section",
                "is_visible": True,
                "content": {
                    "html_content": "<div class='block-ad-placement'>Spoofed Placement</div>",
                },
            }
        ],
    }
    put_spoof = await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{page_id}",
        headers=headers,
        json={"blocks": spoof_blocks},
    )
    assert put_spoof.status_code == 422, f"Expected 422 for spoofing, got {put_spoof.status_code}"

    # 3. Attempt update with invalid block type
    invalid_type_blocks = {
        "schema_version": 2,
        "blocks": [
            {
                "id": "blk_invalid_type",
                "type": "admin_backdoor",
                "is_visible": True,
                "content": {},
            }
        ],
    }
    put_invalid = await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{page_id}",
        headers=headers,
        json={"blocks": invalid_type_blocks},
    )
    assert put_invalid.status_code == 422, f"Expected 422 for invalid type, got {put_invalid.status_code}"

    # Verify original page blocks remained completely unchanged (persistence protection)
    final_res = await client.get(f"/api/v1/website/projects/{proj_id}/pages/{page_id}", headers=headers)
    assert final_res.status_code == 200
    assert final_res.json()["blocks"] == orig_blocks


@pytest.mark.asyncio
async def test_preview_never_invokes_billing_or_live_ad_serving(client: AsyncClient, test_db):
    """Preview endpoints (standard & edit_mode) render placeholders and never invoke live ad serving or billing."""
    headers, proj_id, _ = await _register_and_create_project(client, "neg_billing@test.com")

    # Generate preview token
    tok_res = await client.post(f"/api/v1/website/projects/{proj_id}/preview-token", headers=headers)
    assert tok_res.status_code == 200
    preview_token = tok_res.json()["preview_token"]

    # 1. Preview in edit mode
    res_edit = await client.get(f"/preview/{preview_token}?edit_mode=1")
    assert res_edit.status_code == 200
    html_edit = res_edit.text

    # Verify placeholder is rendered
    assert "rivinity-ad-placeholder" in html_edit
    assert 'data-od-protected="true"' in html_edit
    assert 'data-od-editable="false"' in html_edit

    # Verify absence of live ad serving requests, SDK, or billing triggers
    assert "/api/v1/ad-request" not in html_edit
    assert "/api/v1/track" not in html_edit
    assert '<script src="/api/v1/sdk.js">' not in html_edit
    assert 'class="rivinity-live-ad-container"' not in html_edit

    # 2. Preview in standard mode
    res_std = await client.get(f"/preview/{preview_token}")
    assert res_std.status_code == 200
    html_std = res_std.text

    assert "rivinity-ad-placeholder" in html_std
    assert "/api/v1/ad-request" not in html_std
    assert "/api/v1/track" not in html_std
    assert '<script' not in html_std
