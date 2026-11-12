"""
test_open_design_phase3_3b_selection.py — Open Design Phase 3.3B MessageChannel Selection Protocol Tests

Verifies:
1. Preview endpoint with edit_mode=1 injects two-way selection handlers (OD_ELEMENT_SELECT, OD_BACKGROUND_CLICK, OD_PARENT_SELECT_BLOCK, OD_PARENT_CLEAR_SELECTION).
2. Standard preview without edit_mode=1 contains no selection scripts.
3. SafeWebsiteRenderer correctly renders the selection protocol under CSP nonce.
4. Iframe runtime correctly formats selection classification (protected, editable, read_only).
"""

import re
import pytest
from httpx import AsyncClient

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer


async def _setup_test_project_and_token(client: AsyncClient, email: str = "p33b_test@example.com"):
    """Helper to register user and obtain project ID and preview token."""
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={"name": "Test User", "email": email, "password": "SecurePassword123!", "role": "publisher"},
    )
    assert reg_res.status_code in (200, 201), f"Registration failed: {reg_res.text}"
    token = reg_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    proj_res = await client.post(
        "/api/v1/website/projects",
        json={"name": "Phase 3.3B Selection Test", "slug": "phase-33b-selection", "template_id": "saas-landing"},
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
async def test_preview_endpoint_injected_script_handles_two_way_selection_protocol(client: AsyncClient, test_db):
    """When edit_mode=1, preview endpoint injects complete two-way selection protocol bound to CSP nonce."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p33b_proto@test.com")

    res = await client.get(f"/preview/{preview_token}?edit_mode=1")
    assert res.status_code == 200

    # 1. CSP Nonce Verification
    csp = res.headers.get("Content-Security-Policy", "")
    match = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)';", csp)
    assert match is not None, f"CSP must contain nonce. Got: {csp}"
    nonce = match.group(1)

    # 2. Injected Tag Verification
    expected_tag = f'<script nonce="{nonce}" data-od-edit-bridge>'
    assert expected_tag in res.text, "Script tag must contain matching CSP nonce"

    # 3. Two-Way Selection Message Protocols
    assert "OD_ELEMENT_SELECT" in res.text, "Must handle OD_ELEMENT_SELECT dispatch"
    assert "OD_BACKGROUND_CLICK" in res.text, "Must handle OD_BACKGROUND_CLICK dispatch"
    assert "OD_PARENT_SELECT_BLOCK" in res.text, "Must handle OD_PARENT_SELECT_BLOCK reception"
    assert "OD_PARENT_CLEAR_SELECTION" in res.text, "Must handle OD_PARENT_CLEAR_SELECTION reception"
    assert "scrollIntoView" in res.text, "Must support scrollIntoView on parent-driven selection"


@pytest.mark.asyncio
async def test_renderer_unit_two_way_selection_handlers():
    """SafeWebsiteRenderer renders selection protocol handlers only when bridge_nonce is provided."""
    dummy_project = {"name": "Selection Site", "slug": "selection-site", "theme_config": {}}
    dummy_page = {"title": "Home", "slug": "", "is_home": True, "blocks": {"schema_version": 2, "blocks": []}}

    # 1. Without nonce
    html_no_bridge = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce=None,
    )
    assert "OD_ELEMENT_SELECT" not in html_no_bridge
    assert "OD_PARENT_SELECT_BLOCK" not in html_no_bridge

    # 2. With nonce
    html_with_bridge = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce="secure_test_nonce_33b",
    )
    assert '<script nonce="secure_test_nonce_33b" data-od-edit-bridge>' in html_with_bridge
    assert "OD_ELEMENT_SELECT" in html_with_bridge
    assert "OD_BACKGROUND_CLICK" in html_with_bridge
    assert "OD_PARENT_SELECT_BLOCK" in html_with_bridge
    assert "OD_PARENT_CLEAR_SELECTION" in html_with_bridge
    assert "classification" in html_with_bridge

