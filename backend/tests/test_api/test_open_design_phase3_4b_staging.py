"""
test_open_design_phase3_4b_staging.py — Open Design Phase 3.4B Parent Draft Receiver & Patch Staging Tests

Verifies:
1. Preview endpoint with edit_mode=1 injects Stage 3.4B confirmation, rejection, and sync handlers bound to CSP nonce.
2. Standard preview without edit_mode=1 strictly omits all editing and sync handlers, enforcing script-src 'none'.
3. SafeWebsiteRenderer unit tests for Stage 3.4B MessageChannel protocol handling:
   - OD_EDIT_APPLY_CONFIRMED applies confirmed text and triggers updatePositions.
   - OD_EDIT_REJECTED rolls back to authoritative value and triggers updatePositions.
   - OD_EDIT_SYNC_VALUE updates preview text and triggers updatePositions.
4. __odOverlayEngine exposes applyConfirmedValue and rollbackValue for deterministic test and runtime invocation.
5. Ad placement blocks and protected structures remain immune to inline edit proposals and patches.
"""

import re
import pytest
from httpx import AsyncClient

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer


async def _setup_test_project_and_token(client: AsyncClient, email: str = "p34b_test@example.com"):
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
        json={"name": "Phase 3.4B Staging Test", "slug": "phase-34b-staging", "template_id": "saas-landing"},
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
async def test_preview_endpoint_injected_staging_handlers_in_edit_mode(client: AsyncClient, test_db):
    """When edit_mode=1, preview endpoint injects Phase 3.4B confirmation, rejection, and sync handlers."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p34b_staging@test.com")

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

    # 3. Stage 3.4B MessageChannel Protocol Handlers
    assert "OD_EDIT_APPLY_CONFIRMED" in res.text, "Must define OD_EDIT_APPLY_CONFIRMED handler"
    assert "OD_EDIT_REJECTED" in res.text, "Must define OD_EDIT_REJECTED handler"
    assert "OD_EDIT_SYNC_VALUE" in res.text, "Must define OD_EDIT_SYNC_VALUE handler"

    # 4. Runtime API exposure
    assert "applyConfirmedValue" in res.text, "Must expose applyConfirmedValue in __odOverlayEngine"
    assert "rollbackValue" in res.text, "Must expose rollbackValue in __odOverlayEngine"


@pytest.mark.asyncio
async def test_preview_endpoint_standard_preview_omits_staging_handlers(client: AsyncClient, test_db):
    """Standard preview without edit_mode=1 strictly omits staging handlers and enforces script-src 'none'."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p34b_std@test.com")

    res = await client.get(f"/preview/{preview_token}")
    assert res.status_code == 200

    csp = res.headers.get("Content-Security-Policy", "")
    assert "script-src 'none';" in csp, "Standard preview must enforce script-src 'none'"
    assert "<script" not in res.text, "Standard preview must contain no script tags"
    assert "OD_EDIT_APPLY_CONFIRMED" not in res.text, "Standard preview must not contain staging handlers"
    assert "OD_EDIT_REJECTED" not in res.text
    assert "OD_EDIT_SYNC_VALUE" not in res.text


@pytest.mark.asyncio
async def test_renderer_unit_staging_handlers_and_dom_patching():
    """SafeWebsiteRenderer includes Stage 3.4B DOM update logic for confirmation and rejection."""
    dummy_project = {"name": "Phase 3.4B Site", "slug": "phase-34b-site", "theme_config": {}}
    dummy_page = {"title": "Home", "slug": "", "is_home": True, "blocks": {"schema_version": 2, "blocks": []}}

    html = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce="mock_test_nonce_34b",
    )

    # 1. Applied confirmation handler extracts appliedValue and sets textContent
    assert "payload.appliedValue" in html
    assert "targetEl.textContent = value" in html or "targetEl.textContent = payload.appliedValue" in html

    # 2. Rejection handler extracts rollbackValue and restores textContent
    assert "payload.rollbackValue" in html

    # 3. Both handlers call updatePositions() to keep overlay outlines aligned
    assert "updatePositions()" in html


@pytest.mark.asyncio
async def test_renderer_unit_ad_placement_immunity_preserved():
    """Stage 3.4B preserves complete immunity for ad placement blocks."""
    dummy_project = {"name": "Immunity Site", "slug": "immunity-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk_ad_immutable",
                    "type": "ad_placement",
                    "is_visible": True,
                    "content": {"placement_location": "header_banner", "format": 1},
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
        bridge_nonce="mock_ad_nonce_34b",
    )

    # Protected markup attributes
    assert 'data-od-protected="true"' in html
    assert 'data-od-editable="false"' in html
    assert 'data-od-block-type="ad_placement"' in html

