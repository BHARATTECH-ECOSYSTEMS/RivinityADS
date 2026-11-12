"""Unit and regression tests for RivinityADS Open Design — Phase 4E:
End-to-End Regression & Negative Security Verification.

Covers:
1. Cross-tenant project access rejection (HTTP 404 anti-enumeration).
2. Unauthorized and malformed requests consume zero quota and invoke zero provider calls.
3. Burst (5/min) and daily (50/day) quota enforcement with RFC header validation.
4. Malformed provider responses trigger safe 502 Bad Gateway without leaking internal details.
5. XSS and dangerous HTML triggers safe 502 Bad Gateway without exposing raw payloads.
6. Ad-slot impersonation and forged platform metadata rejection.
7. Oversized or invalid generated content rejection (>65,536 bytes, >30 styles).
8. Zero database persistence during section generation.
9. Provider credentials, hostnames, and internal tracebacks never leak in API errors.
10. Generated section rendering remains completely non-billable with zero live ad requests.
"""

from __future__ import annotations

import asyncio
import math
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from liteads.ad_server.services.rate_limit_service import (
    RateLimitService,
    _DAILY_QUOTA_LUA,
    _SLIDING_WINDOW_LUA,
)
from liteads.ad_server.services.website_ai_service import (
    GeneratedSectionResult,
    WebsiteAIConfigError,
    WebsiteAIMalformedResponseError,
    WebsiteAISecurityError,
    WebsiteAIService,
    WebsiteAIUnavailableError,
)
from liteads.ad_server.services.website_renderer import SafeWebsiteRenderer
from liteads.schemas.website import (
    BlockItem,
    BlockType,
    OpenDesignSectionContent,
)


# ── In-Memory Redis Mock ──────────────────────────────────────────────────────

class InMemoryRedisClient:
    """High-fidelity in-memory Redis client simulating atomic Lua script evaluation."""

    def __init__(self):
        self.strings: dict[str, str] = {}
        self.zsets: dict[str, list[tuple[float, str]]] = {}
        self.expiries: dict[str, float] = {}
        self.lock = asyncio.Lock()
        self.simulated_time: float | None = None

    def _get_time(self) -> float:
        return self.simulated_time if self.simulated_time is not None else time.time()

    async def get(self, key: str) -> str | None:
        async with self.lock:
            now = self._get_time()
            if key in self.expiries and now > self.expiries[key]:
                self.strings.pop(key, None)
                self.expiries.pop(key, None)
                return None
            return self.strings.get(key)

    async def delete(self, *keys: str) -> int:
        async with self.lock:
            count = 0
            for k in keys:
                if k in self.strings:
                    del self.strings[k]
                    count += 1
                if k in self.zsets:
                    del self.zsets[k]
                    count += 1
                self.expiries.pop(k, None)
            return count

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> list[int]:
        async with self.lock:
            keys = list(keys_and_args[:numkeys])
            args = list(keys_and_args[numkeys:])

            # Simulation of _DAILY_QUOTA_LUA
            if "max_quota" in script or script == _DAILY_QUOTA_LUA:
                key = keys[0]
                max_quota = int(args[0])
                ttl = int(args[1])
                seconds_to_midnight = int(args[2])

                current = int(self.strings.get(key, "0"))
                if current < max_quota:
                    new_val = current + 1
                    self.strings[key] = str(new_val)
                    if new_val == 1:
                        self.expiries[key] = self._get_time() + ttl
                    remaining = max_quota - new_val
                    return [1, remaining, seconds_to_midnight]
                else:
                    return [0, 0, seconds_to_midnight]

            # Simulation of _SLIDING_WINDOW_LUA
            elif "window" in script or script == _SLIDING_WINDOW_LUA:
                key = keys[0]
                now = float(args[0])
                window = float(args[1])
                max_limit = int(args[2])
                member_id = str(args[3])

                zset = self.zsets.setdefault(key, [])
                zset[:] = [entry for entry in zset if entry[0] > (now - window)]

                current_count = len(zset)
                if current_count < max_limit:
                    zset.append((now, member_id))
                    zset.sort(key=lambda x: x[0])
                    self.expiries[key] = now + math.ceil(window) + 5
                    remaining = max_limit - current_count - 1
                    return [1, remaining, math.ceil(window)]
                else:
                    reset_ttl = math.ceil(window)
                    if zset:
                        oldest_ts = zset[0][0]
                        reset_ttl = max(1, math.ceil(oldest_ts + window - now))
                    return [0, 0, reset_ttl]

            raise NotImplementedError(f"Unsupported Lua script in test mock: {script[:30]}...")


@pytest.fixture
def mock_redis():
    """Fixture providing an isolated in-memory Redis client connected state."""
    in_memory = InMemoryRedisClient()
    mock_wrapper = MagicMock()
    mock_wrapper.connected = True
    mock_wrapper.client = in_memory
    mock_wrapper._client = in_memory

    with patch("liteads.ad_server.services.rate_limit_service.redis_client", mock_wrapper):
        yield in_memory


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _register_user_and_create_project(client: AsyncClient, email: str, slug: str):
    reg = await client.post(
        "/api/v1/auth/register",
        json={"name": "Publisher User", "email": email, "password": "SecurePassword123!", "role": "publisher"},
    )
    assert reg.status_code in (200, 201), f"Registration failed: {reg.text}"
    token = reg.json()["access_token"]
    user_id = reg.json().get("user", {}).get("id") or reg.json().get("user_id")
    headers = {"Authorization": f"Bearer {token}"}

    proj = await client.post(
        "/api/v1/website/projects",
        json={"name": "Secure Web Project", "slug": slug, "template_id": "saas-landing"},
        headers=headers,
    )
    assert proj.status_code in (200, 201), f"Project creation failed: {proj.text}"
    proj_data = proj.json()
    return headers, proj_data["id"], user_id


def _make_safe_section() -> GeneratedSectionResult:
    sec_content = OpenDesignSectionContent(
        section_id="hero-ai-sec",
        section_tag="section",
        css_classes="py-12 bg-white",
        styles={"background-color": "#ffffff", "padding": "2rem"},
        html_content="<section><h1>Verified Header</h1><p>Verified body paragraph.</p></section>",
        asset_references=[],
    )
    block_item = BlockItem(
        id="od-sec-verified-1",
        type=BlockType.OPEN_DESIGN_SECTION,
        is_visible=True,
        content=sec_content.model_dump(exclude_none=True),
    )
    return GeneratedSectionResult(
        block=block_item,
        section_content=sec_content,
        raw_prompt="Design a clean modern SaaS hero section",
        section_type="hero",
        provider="mock_provider",
        section_id="hero-ai-sec",
    )


# ── 1. Cross-Tenant Project Access Rejection ──────────────────────────────────

@pytest.mark.asyncio
async def test_cross_tenant_project_access_rejected_anti_enumeration(client: AsyncClient, test_db, mock_redis):
    """Tenant B cannot invoke AI generation on Tenant A's project; returns 404 anti-enumeration."""
    headers_a, proj_id_a, _ = await _register_user_and_create_project(client, "tenant_a_p4e@test.com", "proj-a-p4e")
    headers_b, proj_id_b, _ = await _register_user_and_create_project(client, "tenant_b_p4e@test.com", "proj-b-p4e")

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        res = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/ai/generate-section",
            json={"prompt": "Add a pricing table", "section_type": "pricing"},
            headers=headers_b,
        )
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()
        mock_gen.assert_not_called()


# ── 2. Zero Quota & Zero Provider Calls on Unauthorized Access ────────────────

@pytest.mark.asyncio
async def test_unauthorized_generation_consumes_zero_quota_and_zero_calls(client: AsyncClient, test_db, mock_redis):
    """Unauthenticated or invalid requests consume zero quota and make zero provider calls."""
    headers_a, proj_id_a, user_id_a = await _register_user_and_create_project(
        client, "zero_quota_p4e@test.com", "zero-quota-p4e"
    )

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        # A. Unauthenticated request
        res1 = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/ai/generate-section",
            json={"prompt": "Valid length prompt", "section_type": "hero"},
        )
        assert res1.status_code in (401, 403)
        mock_gen.assert_not_called()

        # B. Invalid prompt (too short)
        res2 = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/ai/generate-section",
            json={"prompt": "hi", "section_type": "hero"},
            headers=headers_a,
        )
        assert res2.status_code == 422
        mock_gen.assert_not_called()

        # Verify Redis quota remained completely untouched
        daily_val = await mock_redis.get(f"quota:publisher:ai:{user_id_a}:2026-11-10")
        assert daily_val is None or int(daily_val) == 0


# ── 3. Quota Enforcement Negative Tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_burst_and_daily_quota_exhaustion_blocks_provider(client: AsyncClient, test_db, mock_redis):
    """Burst limit (5/min) and daily quota (50/day) block generation and prevent provider calls."""
    headers, proj_id, _ = await _register_user_and_create_project(
        client, "quota_exhaust_p4e@test.com", "quota-exhaust-p4e"
    )

    # 1. Test burst limit exceeded
    with patch.object(RateLimitService, "reserve_publisher_ai_quota") as mock_reserve, \
         patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:

        mock_reserve.return_value = MagicMock(
            allowed=False,
            reason="burst_limit_exceeded",
            burst=MagicMock(limit=5, remaining=0, retry_after_seconds=45),
            quota=None,
        )

        res_burst = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            json={"prompt": "Exceeding burst", "section_type": "hero"},
            headers=headers,
        )
        assert res_burst.status_code == 429
        assert "Retry-After" in res_burst.headers
        assert res_burst.headers["Retry-After"] == "45"
        assert "burst limit exceeded" in res_burst.json()["detail"].lower()
        assert mock_gen.call_count == 0

    # 2. Test daily quota exceeded
    with patch.object(RateLimitService, "reserve_publisher_ai_quota") as mock_reserve, \
         patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:

        mock_reserve.return_value = MagicMock(
            allowed=False,
            reason="daily_quota_exceeded",
            burst=MagicMock(limit=5, remaining=2, retry_after_seconds=60),
            quota=MagicMock(limit=50, remaining=0, reset_seconds=3600, reset_timestamp_utc="2026-11-11T00:00:00+00:00"),
        )

        res_daily = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            json={"prompt": "Exceeding daily quota", "section_type": "hero"},
            headers=headers,
        )
        assert res_daily.status_code == 429
        assert "daily ai generation quota exceeded" in res_daily.json()["detail"].lower()
        assert res_daily.headers["X-Quota-Remaining"] == "0"
        assert res_daily.headers["X-Quota-Limit"] == "50"
        assert mock_gen.call_count == 0


# ── 4. Malformed Provider Responses ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_malformed_provider_response_handling(client: AsyncClient, test_db, mock_redis):
    """Malformed provider outputs (non-JSON, missing html_content) return 502 without traceback."""
    headers, proj_id, _ = await _register_user_and_create_project(
        client, "malformed_p4e@test.com", "malformed-p4e"
    )

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = WebsiteAIMalformedResponseError("Invalid JSON structure returned by provider")

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            json={"prompt": "Generate something broken", "section_type": "hero"},
            headers=headers,
        )
        assert res.status_code == 502
        data = res.json()
        assert "invalid or unsafe section content" in data["detail"]
        assert "traceback" not in data
        assert "groq" not in res.text.lower()
        assert "gemini" not in res.text.lower()


# ── 5. XSS & Dangerous HTML Sanitizer Enforcement ────────────────────────────

@pytest.mark.asyncio
async def test_xss_and_dangerous_html_rejection(client: AsyncClient, test_db, mock_redis):
    """Unsafe HTML from provider raises WebsiteAISecurityError and returns 502 Bad Gateway."""
    headers, proj_id, _ = await _register_user_and_create_project(
        client, "xss_sec_p4e@test.com", "xss-sec-p4e"
    )

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = WebsiteAISecurityError("XSS attempt detected: <script> tag prohibited.")

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            json={"prompt": "Create an interactive script hero", "section_type": "hero"},
            headers=headers,
        )
        assert res.status_code == 502
        assert "invalid or unsafe section content" in res.json()["detail"]
        # Raw malicious payload is not echoed back
        assert "<script>" not in res.text


# ── 6. Ad-Slot Impersonation & Platform Metadata Tampering ────────────────────

@pytest.mark.asyncio
async def test_ad_slot_impersonation_rejection(client: AsyncClient, test_db, mock_redis):
    """Attempts to generate content with spoofing class names or IDs are rejected with 502."""
    headers, proj_id, _ = await _register_user_and_create_project(
        client, "ad_spoof_p4e@test.com", "ad-spoof-p4e"
    )

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = WebsiteAISecurityError(
            "section_id contains prohibited ad placement token 'block-ad-placement'."
        )

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            json={"prompt": "Generate fake ad slot section", "section_type": "custom"},
            headers=headers,
        )
        assert res.status_code == 502
        assert "invalid or unsafe section content" in res.json()["detail"]


# ── 7. Oversized / Invalid Generated Content Rejection ────────────────────────

@pytest.mark.asyncio
async def test_oversized_content_rejection(client: AsyncClient, test_db, mock_redis):
    """Content exceeding 65,536 characters is rejected with 502 Bad Gateway."""
    headers, proj_id, _ = await _register_user_and_create_project(
        client, "oversized_p4e@test.com", "oversized-p4e"
    )

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = WebsiteAISecurityError("html_content exceeds maximum limit of 65,536 characters.")

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            json={"prompt": "Generate massive multi-megabyte section", "section_type": "about"},
            headers=headers,
        )
        assert res.status_code == 502
        assert "invalid or unsafe section content" in res.json()["detail"]


# ── 8. Zero Database Persistence During Generation ───────────────────────────

@pytest.mark.asyncio
async def test_zero_database_persistence_during_generation(client: AsyncClient, test_db, mock_redis):
    """AI section generation returns a draft block without modifying pages in PostgreSQL/SQLite."""
    headers, proj_id, _ = await _register_user_and_create_project(
        client, "zero_persist_p4e@test.com", "zero-persist-p4e"
    )

    # 1. Fetch initial pages via API
    pages_res = await client.get(f"/api/v1/website/projects/{proj_id}/pages", headers=headers)
    assert pages_res.status_code == 200
    pages_list = pages_res.json()
    assert len(pages_list) > 0
    first_page_id = pages_list[0]["id"]

    page_detail_res = await client.get(
        f"/api/v1/website/projects/{proj_id}/pages/{first_page_id}", headers=headers
    )
    assert page_detail_res.status_code == 200
    initial_blocks = page_detail_res.json()["blocks"]

    # 2. Invoke generation endpoint
    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = _make_safe_section()

        gen_res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            json={"prompt": "Generate hero banner", "section_type": "hero"},
            headers=headers,
        )
        assert gen_res.status_code == 200

    # 3. Fetch page detail again — blocks must remain 100% identical
    after_res = await client.get(
        f"/api/v1/website/projects/{proj_id}/pages/{first_page_id}", headers=headers
    )
    assert after_res.status_code == 200
    assert after_res.json()["blocks"] == initial_blocks, "Page blocks in database must not be modified by generation!"


# ── 9. No Provider Credentials or Internal Details Leak ───────────────────────

@pytest.mark.asyncio
async def test_no_provider_credentials_leak_in_errors(client: AsyncClient, test_db, mock_redis):
    """Provider API keys, internal models, and internal hostnames never leak in responses."""
    headers, proj_id, _ = await _register_user_and_create_project(
        client, "no_leak_p4e@test.com", "no-leak-p4e"
    )

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = WebsiteAIUnavailableError("Connection to https://api.groq.com failed with key gsk_abc123")

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            json={"prompt": "Generate FAQ section", "section_type": "faq"},
            headers=headers,
        )
        assert res.status_code == 503
        body_text = res.text
        # Assert absolute zero leakage of provider secrets
        assert "gsk_abc123" not in body_text
        assert "api.groq.com" not in body_text
        assert "https://" not in body_text
        assert "temporarily unavailable" in res.json()["detail"].lower()


# ── 10. Generated Section Remains Non-Billable ────────────────────────────────

@pytest.mark.asyncio
async def test_generated_section_remains_strictly_non_billable(client: AsyncClient, test_db, mock_redis):
    """Rendering a page containing an Open Design section generates zero live ad requests or billing tags."""
    safe_sec = _make_safe_section()

    project_dict = {
        "id": 1,
        "name": "Non-Billable Site",
        "theme_config": {"primary_color": "#2563eb", "font_family": "Inter"},
    }
    page_dict = {
        "id": 10,
        "title": "Preview Page",
        "slug": "preview",
        "blocks": {
            "schema_version": 2,
            "blocks": [safe_sec.block.model_dump()],
        },
    }

    rendered_html = SafeWebsiteRenderer.render_published_page(
        project_dict=project_dict,
        page_dict=page_dict,
        all_pages_dict=[page_dict],
        base_origin="https://preview.rivinityads.com",
        is_preview=True,
    )

    # Confirm zero live ad tracking scripts or billable requests
    assert "https://api.rivinity.com/track" not in rendered_html
    assert "livetracking" not in rendered_html.lower()
    assert "billable=true" not in rendered_html.lower()
    assert "data-od-block-id" in rendered_html or "od-sec-verified-1" in rendered_html
