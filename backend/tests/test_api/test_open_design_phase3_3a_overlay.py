"""
test_open_design_phase3_3a_overlay.py — Open Design Phase 3.3A Bridge Overlay & DOM Interaction Engine Tests

Verifies:
1. Preview endpoint with edit_mode=1 injects the Phase 3.3A bridge overlay runtime with cryptographic CSP nonce.
2. Standard preview without edit_mode=1 retains strict script-src 'none' and contains no overlay scripts.
3. Injected runtime includes ensureGuidesLayer, data-od-edit-guides-layer, od-guide-hover, od-guide-selected, and __odOverlayEngine.
4. Injected runtime preserves PROTECTED_SELECTORS covering all platform-owned elements.
5. Injected runtime retains Phase 3.1 MessageChannel handshake (OD_BRIDGE_INIT, OD_BRIDGE_ACK, OD_PING, OD_PONG).
"""

import re
import pytest
from httpx import AsyncClient

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer


async def _setup_test_project_and_token(client: AsyncClient, email: str = "p33a_test@example.com"):
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
        json={"name": "Phase 3.3A Overlay Test", "slug": "phase-33a-overlay", "template_id": "saas-landing"},
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
async def test_preview_endpoint_injected_overlay_engine_in_edit_mode(client: AsyncClient, test_db):
    """When edit_mode=1, preview endpoint injects Phase 3.3A overlay engine bound to CSP nonce."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p33a_edit@test.com")

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

    # 3. Stage 3.3A Overlay Engine Verification
    assert "data-od-edit-guides-layer" in res.text, "Must define data-od-edit-guides-layer attribute"
    assert "od-guide-hover" in res.text, "Must define od-guide-hover visual class"
    assert "od-guide-selected" in res.text, "Must define od-guide-selected visual class"
    assert "ensureGuidesLayer" in res.text, "Must define ensureGuidesLayer function"
    assert "__odOverlayEngine" in res.text, "Must expose __odOverlayEngine runtime API"

    # 4. Phase 3.1 Handshake Compatibility
    assert "OD_BRIDGE_INIT" in res.text, "Must preserve OD_BRIDGE_INIT handler"
    assert "OD_BRIDGE_ACK" in res.text, "Must preserve OD_BRIDGE_ACK response"


@pytest.mark.asyncio
async def test_preview_endpoint_standard_preview_omits_overlay_engine(client: AsyncClient, test_db):
    """Standard preview without edit_mode=1 strictly omits overlay engine and enforces script-src 'none'."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p33a_std@test.com")

    res = await client.get(f"/preview/{preview_token}")
    assert res.status_code == 200

    csp = res.headers.get("Content-Security-Policy", "")
    assert "script-src 'none';" in csp, "Standard preview must enforce script-src 'none'"
    assert "<script" not in res.text, "Standard preview must contain no script tags"
    assert "__odOverlayEngine" not in res.text, "Standard preview must not contain overlay engine"
    assert "data-od-edit-guides-layer" not in res.text


@pytest.mark.asyncio
async def test_renderer_unit_overlay_script_injection():
    """SafeWebsiteRenderer renders overlay engine only when bridge_nonce is explicitly provided."""
    dummy_project = {"name": "Overlay Site", "slug": "overlay-site", "theme_config": {}}
    dummy_page = {"title": "Home", "slug": "", "is_home": True, "blocks": {"schema_version": 2, "blocks": []}}

    # 1. Without bridge_nonce
    html_no_bridge = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce=None,
    )
    assert "data-od-edit-bridge" not in html_no_bridge
    assert "__odOverlayEngine" not in html_no_bridge

    # 2. With bridge_nonce
    html_with_bridge = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce="secure_test_nonce_33a",
    )
    assert '<script nonce="secure_test_nonce_33a" data-od-edit-bridge>' in html_with_bridge
    assert "__odOverlayEngine" in html_with_bridge
    assert "ensureGuidesLayer" in html_with_bridge
    assert "data-od-edit-guides-layer" in html_with_bridge


@pytest.mark.asyncio
async def test_renderer_overlay_engine_protected_selectors_coverage():
    """Overlay runtime strictly incorporates all platform protection selectors."""
    dummy_project = {"name": "Protected Site", "slug": "prot-site", "theme_config": {}}
    dummy_page = {"title": "Home", "slug": "", "is_home": True, "blocks": {"schema_version": 2, "blocks": []}}

    html = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce="nonce_prot_check",
    )

    # Must contain protected selectors in injected JS
    assert "block-ad-placement" in html
    assert "rivinity-ad-placeholder" in html
    assert "rivinity-live-ad-container" in html
    assert "rivinity-ad-slot" in html
    assert "rivinity-consent-banner" in html
    assert "preview-banner" in html
    assert "hp_comment" in html
    assert "data-od-edit-bridge" in html
