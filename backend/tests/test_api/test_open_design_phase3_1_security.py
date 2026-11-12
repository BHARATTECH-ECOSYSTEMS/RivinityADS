"""
test_open_design_phase3_1_security.py — RivinityADS Open Design Phase 3.1 Security Tests

Tests:
1. Standard preview (edit_mode=0 or omitted) enforces strict script-free CSP (script-src 'none') and renders no scripts.
2. Editing preview (edit_mode=1) generates a cryptographically secure nonce in CSP (script-src 'nonce-...').
3. Editing preview HTML contains the authorized bridge script with the exact matching nonce.
4. Consecutive editing preview responses generate unique, distinct nonces (never reused).
5. Editing preview CSP disallows unsafe-inline and unsafe-eval, and enforces connect-src 'none'.
6. Cache-Control: no-store, no-cache, must-revalidate is present on all preview responses.
7. Unauthorized or invalid preview tokens with edit_mode=1 are rejected with 403 Forbidden.
8. SafeWebsiteRenderer renders the bridge script only when bridge_nonce is provided.
"""

from __future__ import annotations

import re
import pytest
from httpx import AsyncClient

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer

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


async def _setup_test_project_and_token(client: AsyncClient, email: str):
    token, user_id = await _create_user_and_token(client, email, role="publisher")
    headers = {"Authorization": f"Bearer {token}"}

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=headers,
        json={
            "name": "Phase 3.1 Security Test Site",
            "slug": f"p31-sec-{user_id}",
            "template_id": "saas-landing",
        },
    )
    assert create_res.status_code in [200, 201], f"Create project failed: {create_res.text}"
    proj_id = create_res.json()["id"]

    tok_res = await client.post(
        f"/api/v1/website/projects/{proj_id}/preview-token",
        headers=headers,
    )
    assert tok_res.status_code == 200, f"Preview token generation failed: {tok_res.text}"
    preview_token = tok_res.json()["preview_token"]
    return headers, proj_id, preview_token


async def test_standard_preview_strict_csp_and_no_scripts(client: AsyncClient, test_db):
    """Standard preview without edit_mode must enforce script-src 'none' and render no scripts."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p31_std@test.com")

    res = await client.get(f"/preview/{preview_token}")
    assert res.status_code == 200

    csp = res.headers.get("Content-Security-Policy", "")
    assert "script-src 'none';" in csp, "Standard preview must enforce script-src 'none'"
    assert "nonce-" not in csp, "Standard preview must not contain any nonce in CSP"

    cache_ctrl = res.headers.get("Cache-Control", "")
    assert "no-store" in cache_ctrl, "Preview must enforce Cache-Control: no-store"

    # HTML body must not contain any script tags
    assert "<script" not in res.text, "Standard preview HTML must not contain any script tags"
    assert "data-od-edit-bridge" not in res.text, "Standard preview must not contain bridge script"


async def test_visual_edit_preview_generates_cryptographic_nonce_and_injects_bridge(client: AsyncClient, test_db):
    """Visual edit preview (edit_mode=1) must generate a fresh CSP nonce and inject the authorized bridge."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p31_edit@test.com")

    res = await client.get(f"/preview/{preview_token}?edit_mode=1")
    assert res.status_code == 200

    csp = res.headers.get("Content-Security-Policy", "")
    match = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)';", csp)
    assert match is not None, f"Editing preview CSP must contain valid script-src nonce directive. Got: {csp}"
    nonce = match.group(1)
    assert len(nonce) >= 24, "Nonce must be high-entropy (at least 24 characters)"

    # CSP restrictions
    assert "'unsafe-inline'" not in re.search(r"script-src [^;]+", csp).group(0), "script-src must not allow unsafe-inline"
    assert "'unsafe-eval'" not in csp, "CSP must not allow unsafe-eval anywhere"
    assert "connect-src 'none';" in csp, "CSP must disallow network connections"
    assert "form-action 'none';" in csp, "CSP must disallow form actions"

    # Injected bridge in HTML
    expected_tag = f'<script nonce="{nonce}" data-od-edit-bridge>'
    assert expected_tag in res.text, f"HTML must contain bridge script with matching nonce: {expected_tag}"
    assert "OD_BRIDGE_INIT" in res.text, "Bridge script must contain handshake listener"
    assert "OD_BRIDGE_ACK" in res.text, "Bridge script must contain handshake acknowledgment"


async def test_nonce_uniqueness_per_response(client: AsyncClient, test_db):
    """Every visual edit preview request must generate a distinct, non-reused cryptographic nonce."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p31_nonce_unique@test.com")

    res1 = await client.get(f"/preview/{preview_token}?edit_mode=1")
    res2 = await client.get(f"/preview/{preview_token}?edit_mode=1")
    assert res1.status_code == 200
    assert res2.status_code == 200

    csp1 = res1.headers.get("Content-Security-Policy", "")
    csp2 = res2.headers.get("Content-Security-Policy", "")

    nonce1 = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)';", csp1).group(1)
    nonce2 = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)';", csp2).group(1)

    assert nonce1 != nonce2, "Nonces must never be reused across different responses"


async def test_unauthorized_preview_token_rejected_with_edit_mode(client: AsyncClient, test_db):
    """Requests with invalid or expired preview tokens must be rejected with 403 Forbidden even with edit_mode=1."""
    res = await client.get("/preview/invalid.token.1234.abcd.sig?edit_mode=1")
    assert res.status_code == 403
    assert "Invalid or expired preview token" in res.json()["detail"]


async def test_renderer_bridge_nonce_injection_unit():
    """SafeWebsiteRenderer renders the bridge script only when bridge_nonce is explicitly passed."""
    dummy_project = {"name": "Test", "slug": "test", "theme_config": {}}
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
    assert "data-od-edit-bridge" not in html_no_bridge

    # 2. With nonce
    html_with_bridge = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce="secure_test_nonce_9999",
    )
    assert '<script nonce="secure_test_nonce_9999" data-od-edit-bridge>' in html_with_bridge
    assert "OD_BRIDGE_INIT" in html_with_bridge
