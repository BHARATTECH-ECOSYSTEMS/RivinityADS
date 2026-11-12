"""
test_open_design_phase3_4c_patching.py — Open Design Phase 3.4C Custom Section Localized HTML Patching Tests

Verifies:
1. Preview endpoint with edit_mode=1 injects Stage 3.4C localized section patching utilities (startLocalizedSectionEdit, buildRelativeDomPath, isTextLeafElement).
2. Standard preview without edit_mode=1 strictly omits patching engine and retains script-src 'none'.
3. Injected runtime exposes startLocalizedSectionEdit, buildRelativeDomPath, and isTextLeafElement on window.__odOverlayEngine.
4. Injected runtime supports localized DOM updates via applyConfirmedValue and rollbackValue with targetSelector and targetOdId.
5. Injected runtime preserves native block field constraints and separates custom section localized patching from native field editing.
6. Ad placement immunity and protected status remain strictly enforced.
"""

import re
import pytest
from httpx import AsyncClient

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer


async def _setup_test_project_and_token(client: AsyncClient, email: str = "p34c_test@example.com"):
    """Helper to register user and obtain project ID and preview token."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={"name": "Test User", "email": email, "password": "SecurePassword123!", "role": "publisher"},
    )
    assert reg_res.status_code == 201, f"Registration failed: {reg_res.text}"
    token = reg_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    proj_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Phase 3.4C Patching Test", "slug": "phase-34c-patching", "template_id": "saas-landing"},
        headers=headers,
    )
    assert proj_res.status_code in (200, 201), f"Project creation failed: {proj_res.text}"
    proj_id = proj_res.json()["id"]

    tok_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/preview-token",
        headers=headers,
    )
    assert tok_res.status_code == 200, f"Preview token generation failed: {tok_res.text}"
    preview_token = tok_res.json()["preview_token"]
    return headers, proj_id, preview_token


@pytest.mark.asyncio
async def test_preview_endpoint_injected_patching_engine_in_edit_mode(client: AsyncClient, test_db):
    """When edit_mode=1, preview endpoint injects Phase 3.4C localized HTML patching engine."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p34c_edit@test.com")

    res = await client.get(f"/preview/{preview_token}?edit_mode=1")
    assert res.status_code == 200

    # 1. CSP Header Verification
    csp = res.headers.get("Content-Security-Policy", "")
    match = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)';", csp)
    assert match is not None, f"CSP must contain nonce. Got: {csp}"
    nonce = match.group(1)

    # 2. Injected Script Tag Verification
    expected_tag = f'<script nonce="{nonce}" data-od-edit-bridge>'
    assert expected_tag in res.text, "Script tag must contain matching CSP nonce and data-od-edit-bridge attribute"

    # 3. Stage 3.4C Localized Patching Utilities
    assert "startLocalizedSectionEdit" in res.text, "Must define startLocalizedSectionEdit"
    assert "buildRelativeDomPath" in res.text, "Must define buildRelativeDomPath"
    assert "isTextLeafElement" in res.text, "Must define isTextLeafElement"
    assert "patchKind" in res.text, "Must reference patchKind in proposal handling"
    assert "targetSelector" in res.text, "Must reference targetSelector in proposal handling"
    assert "targetOdId" in res.text, "Must reference targetOdId in proposal handling"

    # 4. Runtime API Exposure
    assert "startLocalizedSectionEdit: startLocalizedSectionEdit" in res.text
    assert "buildRelativeDomPath: buildRelativeDomPath" in res.text
    assert "isTextLeafElement: isTextLeafElement" in res.text


@pytest.mark.asyncio
async def test_preview_endpoint_standard_preview_omits_patching_engine(client: AsyncClient, test_db):
    """Standard preview without edit_mode=1 strictly omits patching engine and enforces script-src 'none'."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p34c_std@test.com")

    res = await client.get(f"/preview/{preview_token}")
    assert res.status_code == 200

    csp = res.headers.get("Content-Security-Policy", "")
    assert "script-src 'none';" in csp, "Standard preview must enforce script-src 'none'"
    assert "<script" not in res.text, "Standard preview must contain no script tags"
    assert "startLocalizedSectionEdit" not in res.text
    assert "buildRelativeDomPath" not in res.text


@pytest.mark.asyncio
async def test_renderer_unit_localized_patching_injected_handlers():
    """SafeWebsiteRenderer includes Stage 3.4C localized section patching utilities and DOM handlers."""
    dummy_project = {"name": "Phase 3.4C Site", "slug": "phase-34c-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk_custom_sec",
                    "type": "open_design_section",
                    "is_visible": True,
                    "content": {
                        "html_content": '<div class="banner"><h2 data-od-id="head-1">Editable Heading</h2><p>Editable Paragraph</p></div>'
                    },
                }
            ],
        },
    }

    html = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce="mock_nonce_34c",
    )

    # 1. Custom section markup
    assert 'data-od-block-id="blk_custom_sec"' in html
    assert 'data-od-block-type="open_design_section"' in html

    # 2. Stage 3.4C Localized Patching Utilities
    assert "startLocalizedSectionEdit" in html
    assert "buildRelativeDomPath" in html
    assert "isTextLeafElement" in html

    # 3. Target resolution in applyConfirmedValue & rollbackValue
    assert "targetSelector" in html
    assert "targetOdId" in html
    assert "data-od-id" in html

    # 4. Native field separation preserved
    assert "'open_design_section': {" not in html, "open_design_section must not be in SCHEMA_FIELD_CONSTRAINTS"


@pytest.mark.asyncio
async def test_renderer_unit_ad_placement_immunity_and_protected_status():
    """Protected ad placement elements and honeypots remain immune to localized editing."""
    dummy_project = {"name": "Immunity Site", "slug": "immunity-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk_ad_p34c",
                    "type": "ad_placement",
                    "is_visible": True,
                    "content": {"placement_location": "in_article", "format": 1},
                }
            ],
        },
    }

    html = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce="mock_ad_nonce_34c",
    )

    # 1. Ad placement has data-od-protected="true"
    assert 'data-od-protected="true"' in html
    assert 'data-od-editable="false"' in html

    # 2. Injected script explicitly excludes ad placement from editing
    assert "block-ad-placement" in html
    assert "isElementProtected" in html

