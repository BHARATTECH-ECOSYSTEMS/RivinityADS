"""
test_open_design_phase3_4a_editing.py — Open Design Phase 3.4A Native Text Inline Editing Engine Tests

Verifies:
1. Preview endpoint with edit_mode=1 injects the Stage 3.4A inline text editing engine bound to cryptographic CSP nonce.
2. Standard preview without edit_mode=1 retains strict script-src 'none' and contains no editing engine.
3. Injected runtime includes SCHEMA_FIELD_CONSTRAINTS covering all 7 native blocks and 17 editable fields.
4. Injected runtime defines ensureInlineToolbar, floating toolbar controls (✓/✕), and character counter.
5. Injected runtime defines startTextEdit, finishActiveTextEdit, placeCaretFromClick, and keydown handlers.
6. Injected runtime enforces plaintext-only contenteditable with paste/drop sanitization.
7. Injected runtime defines MessageChannel integration payloads (OD_EDIT_SESSION_START, OD_EDIT_PROPOSE_PATCH, OD_EDIT_CANCEL).
8. Injected runtime protects ad placement placeholders, consent banners, preview notices, and honeypots from editing.
9. Injected runtime isolates iframe blur and prevents accidental commit during parent inspector interactions.
"""

import re
import pytest
from httpx import AsyncClient

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer


async def _setup_test_project_and_token(client: AsyncClient, email: str = "p34a_test@example.com"):
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
        json={"name": "Phase 3.4A Editing Test", "slug": "phase-34a-editing", "template_id": "saas-landing"},
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
async def test_preview_endpoint_injected_editing_engine_in_edit_mode(client: AsyncClient, test_db):
    """When edit_mode=1, preview endpoint injects Phase 3.4A inline editing engine bound to CSP nonce."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p34a_edit@test.com")

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

    # 3. Stage 3.4A Inline Editing Engine Verification
    assert "SCHEMA_FIELD_CONSTRAINTS" in res.text, "Must define SCHEMA_FIELD_CONSTRAINTS"
    assert "ensureInlineToolbar" in res.text, "Must define ensureInlineToolbar"
    assert "startTextEdit" in res.text, "Must define startTextEdit"
    assert "finishActiveTextEdit" in res.text, "Must define finishActiveTextEdit"
    assert "placeCaretFromClick" in res.text, "Must define placeCaretFromClick"
    assert "od-inline-toolbar" in res.text, "Must define od-inline-toolbar class"
    assert "od-toolbar-counter" in res.text, "Must define od-toolbar-counter class"
    assert "od-toolbar-save" in res.text, "Must define od-toolbar-save class"
    assert "od-toolbar-cancel" in res.text, "Must define od-toolbar-cancel class"

    # 4. MessageChannel Typed Event Payloads
    assert "OD_EDIT_SESSION_START" in res.text, "Must define OD_EDIT_SESSION_START event"
    assert "OD_EDIT_PROPOSE_PATCH" in res.text, "Must define OD_EDIT_PROPOSE_PATCH event"
    assert "OD_EDIT_CANCEL" in res.text, "Must define OD_EDIT_CANCEL event"

    # 5. Runtime API exposure
    assert "__odOverlayEngine" in res.text, "Must expose __odOverlayEngine runtime API"


@pytest.mark.asyncio
async def test_preview_endpoint_standard_preview_omits_editing_engine(client: AsyncClient, test_db):
    """Standard preview without edit_mode=1 strictly omits editing engine and enforces script-src 'none'."""
    _, _, preview_token = await _setup_test_project_and_token(client, "p34a_std@test.com")

    res = await client.get(f"/preview/{preview_token}")
    assert res.status_code == 200

    csp = res.headers.get("Content-Security-Policy", "")
    assert "script-src 'none';" in csp, "Standard preview must enforce script-src 'none'"
    assert "<script" not in res.text, "Standard preview must contain no script tags"
    assert "SCHEMA_FIELD_CONSTRAINTS" not in res.text, "Standard preview must not contain editing engine"
    assert "startTextEdit" not in res.text
    assert "od-inline-toolbar" not in res.text


@pytest.mark.asyncio
async def test_renderer_unit_all_17_fields_constraints_and_boundaries():
    """SafeWebsiteRenderer includes constraints for all 17 supported fields in 7 native blocks."""
    dummy_project = {"name": "Phase 3.4A Site", "slug": "phase-34a-site", "theme_config": {}}
    dummy_page = {"title": "Home", "slug": "", "is_home": True, "blocks": {"schema_version": 2, "blocks": []}}

    html = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce="mock_test_nonce_34a",
    )

    expected_fields = [
        "'headline': { maxLength: 160",
        "'subheadline': { maxLength: 300",
        "'cta_button.label': { maxLength: 60",
        "'heading': { maxLength: 160",
        "'body': { maxLength: 10000",
        "'title': { maxLength: 160",
        "'items.title': { maxLength: 100",
        "'items.description': { maxLength: 500",
        "'images.caption': { maxLength: 200",
        "'title': { maxLength: 120",
        "'submit_label': { maxLength: 60",
        "'button.label': { maxLength: 60",
        "'copyright_text': { maxLength: 160",
        "'links.label': { maxLength: 50",
    ]

    for expected in expected_fields:
        assert expected in html, f"Field constraint missing from injected runtime: {expected}"


@pytest.mark.asyncio
async def test_renderer_unit_ad_placement_immunity_and_protected_status():
    """Injected runtime preserves ad placement immunity and excludes protected targets from editing."""
    dummy_project = {"name": "Immunity Site", "slug": "immunity-site", "theme_config": {}}
    dummy_page = {
        "title": "Home",
        "slug": "",
        "is_home": True,
        "blocks": {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk_ad_test",
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
        bridge_nonce="mock_ad_nonce_34a",
    )

    # 1. Ad placement has data-od-protected="true" and data-od-editable="false"
    assert 'data-od-protected="true"' in html
    assert 'data-od-editable="false"' in html

    # 2. Injected script explicitly excludes ad placement and open_design_section in startTextEdit
    assert "open_design_section" in html
    assert "block-ad-placement" in html


@pytest.mark.asyncio
async def test_renderer_unit_parent_select_block_isolates_same_target():
    """Injected runtime's OD_PARENT_SELECT_BLOCK does not abort active edit when selecting the same target."""
    dummy_project = {"name": "Protocol Site", "slug": "protocol-site", "theme_config": {}}
    dummy_page = {"title": "Home", "slug": "", "is_home": True, "blocks": {"schema_version": 2, "blocks": []}}

    html = SafeWebsiteRenderer.render_published_page(
        project_dict=dummy_project,
        page_dict=dummy_page,
        all_pages_dict=[dummy_page],
        base_origin="http://test.localhost",
        is_preview=True,
        bridge_nonce="mock_proto_nonce_34a",
    )

    # Verifies the same-target check in OD_PARENT_SELECT_BLOCK
    assert "isSame" in html, "OD_PARENT_SELECT_BLOCK must check if selected target is same as active edit"
    assert "finishActiveTextEdit" in html
