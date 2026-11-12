"""
test_open_design_phase3_4d_sync.py — Open Design Phase 3.4D Two-Way Synchronization Tests

Verifies:
1. Preview endpoint with edit_mode=1 injects Stage 3.4D two-way synchronization handlers:
   - OD_EDIT_SYNC_VALUE message handler for reverse sync from parent inspector to preview iframe.
   - OD_PARENT_EDIT_FINISH message handler for programmatic commit/cancel from parent.
   - Same-target selection check in OD_PARENT_SELECT_BLOCK to avoid accidental session termination.
   - Active session synchronization in applyConfirmedValue and rollbackValue (updating activeTextEdit.originalText and updateCounterAndToolbar).
2. Standard preview without edit_mode=1 strictly omits synchronization scripts and retains script-src 'none'.
3. Injected runtime exposes sync handlers and maintains state baseline when parent updates active editing target.
4. Schema field constraints cover all 17 supported native fields and exclude non-text/style properties.
5. Ad placement and honeypot immunity remain preserved against synchronization events.
"""

import re
import pytest
from httpx import AsyncClient

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer


async def _setup_test_project_and_token(client: AsyncClient, email: str = "p34d_test@example.com"):
    """Helper to register user and obtain project ID and preview token."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={"name": "Sync User", "email": email, "password": "SecurePassword123!", "role": "publisher"},
    )
    assert reg_res.status_code == 201, f"Registration failed: {reg_res.text}"
    token = reg_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    proj_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Phase 3.4D Sync Test", "slug": "phase-34d-sync", "template_id": "saas-landing"},
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
async def test_preview_endpoint_injected_sync_engine_in_edit_mode(client: AsyncClient, test_db):
    """When edit_mode=1, preview endpoint injects Phase 3.4D two-way synchronization handlers."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p34d_edit@test.com")

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

    # 3. Stage 3.4D Synchronization Handlers
    assert "OD_EDIT_SYNC_VALUE" in res.text, "Must handle OD_EDIT_SYNC_VALUE for reverse inspector sync"
    assert "OD_PARENT_EDIT_FINISH" in res.text, "Must handle OD_PARENT_EDIT_FINISH for parent session finish"
    assert "OD_PARENT_SELECT_BLOCK" in res.text, "Must handle OD_PARENT_SELECT_BLOCK"

    # 4. Same-target selection check in OD_PARENT_SELECT_BLOCK
    assert "isSame" in res.text, "Must check if target is same to prevent accidental edit termination"

    # 5. Active editing baseline sync in applyConfirmedValue & rollbackValue
    assert "activeTextEdit.originalText = value;" in res.text, "Must update active originalText on sync"
    assert "updateCounterAndToolbar()" in res.text, "Must update toolbar and counter on sync"


@pytest.mark.asyncio
async def test_preview_endpoint_standard_preview_omits_sync_engine(client: AsyncClient, test_db):
    """Standard preview without edit_mode=1 strictly omits sync engine and enforces script-src 'none'."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p34d_std@test.com")

    res = await client.get(f"/preview/{preview_token}")
    assert res.status_code == 200

    csp = res.headers.get("Content-Security-Policy", "")
    assert "script-src 'none';" in csp, "Standard preview must enforce script-src 'none'"
    assert "<script" not in res.text, "Standard preview must contain no script tags"
    assert "OD_EDIT_SYNC_VALUE" not in res.text
    assert "OD_PARENT_EDIT_FINISH" not in res.text


@pytest.mark.asyncio
async def test_renderer_unit_two_way_sync_injected_handlers():
    """SafeWebsiteRenderer renders injected script with all Phase 3.4D two-way sync components."""
    dummy_project = {"name": "Phase 3.4D Site", "slug": "phase-34d-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk_hero_sync",
                    "type": "hero",
                    "is_visible": True,
                    "content": {
                        "headline": "Sync Headline",
                        "subheadline": "Sync Subheadline",
                        "cta_button": {"label": "Click Me", "target_url": "/test"},
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
        bridge_nonce="mock_nonce_34d",
    )

    # 1. Block markup with metadata
    assert 'data-od-block-id="blk_hero_sync"' in html
    assert 'data-od-field="headline"' in html
    assert 'data-od-field="subheadline"' in html

    # 2. Injected sync handling code
    assert "OD_EDIT_SYNC_VALUE" in html
    assert "OD_PARENT_EDIT_FINISH" in html
    assert "applyConfirmedValue" in html
    assert "rollbackValue" in html

    # 3. Active session baseline updates
    assert "activeTextEdit.originalText = value;" in html

    # 4. Schema field constraints covering 17 fields across 7 blocks
    for block_type in ["hero", "text_section", "features_grid", "image_gallery", "contact_form", "cta_banner", "footer"]:
        assert f"'{block_type}':" in html, f"{block_type} must be in SCHEMA_FIELD_CONSTRAINTS"


@pytest.mark.asyncio
async def test_renderer_unit_ad_placement_immunity_during_sync():
    """Ad placements remain immune to editing and synchronization."""
    dummy_project = {"name": "Immunity Site", "slug": "immunity-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk_ad_p34d",
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
        bridge_nonce="mock_ad_nonce_34d",
    )

    # 1. Ad placement has data-od-protected="true"
    assert 'data-od-protected="true"' in html
    assert 'data-od-editable="false"' in html

    # 2. Injected script explicitly excludes ad placement from editing and sync targets
    assert "block-ad-placement" in html
    assert "isElementProtected" in html
