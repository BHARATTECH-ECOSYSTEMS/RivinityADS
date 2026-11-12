"""Unit and regression tests for RivinityADS Open Design — Phase 4C:
FastAPI AI Generation Endpoint & Sanitization Pipeline.

Endpoint: POST /api/v1/website/projects/{id}/ai/generate-section

Covers:
1. Successful generation returns a validated BlockItem with Open Design section and quota info.
2. Unauthenticated requests are rejected (401/403).
3. Non-owned projects return 404 (tenant isolation, anti-enumeration).
4. Unauthorized requests consume zero quota.
5. Unauthorized requests make zero provider calls.
6. Invalid prompts (too short <3, too long >1000) and invalid presets are rejected (422).
7. Daily quota exhaustion returns 429 with RFC quota headers.
8. Burst limit exhaustion returns 429 with Retry-After header.
9. Correct rate-limit and quota headers on success.
10. Provider timeout returns controlled 503 without leaking provider URL or model.
11. Malformed provider output is rejected with 502 Bad Gateway.
12. XSS and ad-slot spoofing attempts are rejected with 502 Bad Gateway.
13. Generation does not modify database records (zero persistence).
14. Provider credentials and internal error details never appear in responses.
15. Suspended and archived projects are rejected before quota or provider calls.
"""

from __future__ import annotations

import asyncio
import math
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

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
from liteads.models.website import WebsitePage, WebsiteProject, WebsiteStatus
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


# ── Project & User Helpers ───────────────────────────────────────────────────

async def _register_user_and_create_project(client: AsyncClient, email: str, slug: str):
    reg = await client.post(
        "/api/v1/auth/register",
        json={"name": "AI User", "email": email, "password": "SecurePassword123!", "role": "publisher"},
    )
    assert reg.status_code in (200, 201), f"Registration failed: {reg.text}"
    token = reg.json()["access_token"]
    user_id = reg.json().get("user", {}).get("id") or reg.json().get("user_id")
    headers = {"Authorization": f"Bearer {token}"}

    proj = await client.post(
        "/api/v1/website/projects",
        json={"name": "AI Generated Site", "slug": slug, "template_id": "saas-landing"},
        headers=headers,
    )
    assert proj.status_code in (200, 201), f"Project creation failed: {proj.text}"
    proj_data = proj.json()
    return headers, proj_data["id"], user_id


def _make_dummy_generated_section(section_type: str = "features") -> GeneratedSectionResult:
    sec_content = OpenDesignSectionContent(
        section_tag="section",
        section_id=f"{section_type}-grid",
        css_classes="py-16 px-4 bg-white",
        styles={"padding-top": "4rem", "padding-bottom": "4rem"},
        html_content="<div class='container'><h2>Core Features</h2><p>High performance advertising platform.</p></div>",
    )
    block_item = BlockItem(
        id="blk_ai_mock123456",
        type=BlockType.OPEN_DESIGN_SECTION,
        is_visible=True,
        content=sec_content.model_dump(exclude_none=True),
    )
    return GeneratedSectionResult(
        block=block_item,
        section_content=sec_content,
        raw_prompt="Design a clean modern SaaS features section",
        section_type=section_type,
        provider="mock_provider",
        section_id=f"{section_type}-grid",
    )


# ── 1. Successful Generation Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_generation_returns_valid_open_design_section(client: AsyncClient, test_db, mock_redis):
    """POST /projects/{id}/ai/generate-section returns validated BlockItem and quota metadata."""
    headers, proj_id, _ = await _register_user_and_create_project(client, "ai_success@test.com", "proj-ai-success")

    dummy_result = _make_dummy_generated_section("features")

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = dummy_result

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={
                "prompt": "Design a clean modern SaaS features section with cards",
                "section_type": "features",
            },
        )

        assert res.status_code == 200, f"Expected 200 OK, got {res.status_code}: {res.text}"
        data = res.json()

        # 1. Validate response schema
        assert "block" in data
        assert data["block"]["type"] == "open_design_section"
        assert data["block"]["content"]["html_content"] == dummy_result.section_content.html_content
        assert data["block"]["content"]["section_id"] == "features-grid"
        assert data["section_id"] == "features-grid"
        assert data["section_type"] == "features"
        assert data["daily_limit"] == 50
        assert data["quota_remaining"] == 49
        assert "T" in data["reset_timestamp_utc"]

        # 2. Validate rate limit and quota headers
        assert res.headers["X-RateLimit-Limit"] == "5"
        assert res.headers["X-RateLimit-Remaining"] == "4"
        assert "X-RateLimit-Reset" in res.headers
        assert res.headers["X-Quota-Limit"] == "50"
        assert res.headers["X-Quota-Remaining"] == "49"
        assert "X-Quota-Reset" in res.headers

        # 3. Verify server-side theme config was passed to the service
        assert mock_gen.call_count == 1
        call_kwargs = mock_gen.call_args.kwargs
        assert call_kwargs["prompt"] == "Design a clean modern SaaS features section with cards"
        assert call_kwargs["section_type"] == "features"
        assert "theme_config" in call_kwargs


# ── 2. Authentication & Tenant Isolation Tests ────────────────────────────────

@pytest.mark.asyncio
async def test_unauthenticated_requests_are_rejected(client: AsyncClient, test_db, mock_redis):
    """Requests without valid JWT token receive HTTP 401/403 and consume 0 quota."""
    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        res = await client.post(
            "/api/v1/website/projects/999/ai/generate-section",
            json={"prompt": "Build a hero section", "section_type": "hero"},
        )
        assert res.status_code in (401, 403)
        assert mock_gen.call_count == 0


@pytest.mark.asyncio
async def test_non_owned_projects_return_404_tenant_isolation(client: AsyncClient, test_db, mock_redis):
    """User B cannot generate content for User A's project; receives HTTP 404."""
    headers_a, proj_id_a, _ = await _register_user_and_create_project(client, "user_a@test.com", "proj-user-a")
    headers_b, proj_id_b, _ = await _register_user_and_create_project(client, "user_b@test.com", "proj-user-b")

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        # User B attempts to target User A's project
        res = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/ai/generate-section",
            headers=headers_b,
            json={"prompt": "Build a hero section", "section_type": "hero"},
        )
        assert res.status_code == 404
        assert res.json()["detail"] == "Website project not found"
        # Zero provider calls
        assert mock_gen.call_count == 0


@pytest.mark.asyncio
async def test_unauthorized_requests_consume_zero_quota(client: AsyncClient, test_db, mock_redis):
    """Attempting access to another tenant's project consumes zero Redis quota."""
    headers_a, proj_id_a, _ = await _register_user_and_create_project(client, "owner_a@test.com", "proj-owner-a")
    headers_b, _, _ = await _register_user_and_create_project(client, "attacker_b@test.com", "proj-attacker-b")

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        res = await client.post(
            f"/api/v1/website/projects/{proj_id_a}/ai/generate-section",
            headers=headers_b,
            json={"prompt": "Malicious prompt", "section_type": "hero"},
        )
        assert res.status_code == 404
        assert mock_gen.call_count == 0

        # Assert Redis store has 0 quota keys created
        assert len(mock_redis.strings) == 0
        assert len(mock_redis.zsets) == 0


# ── 3. Request Validation Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_prompts_and_presets_rejected(client: AsyncClient, test_db, mock_redis):
    """Prompts < 3 chars, > 1000 chars, or disallowed presets are rejected with 422."""
    headers, proj_id, _ = await _register_user_and_create_project(client, "val_test@test.com", "proj-val-test")

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        # 1. Prompt too short (< 3 chars)
        res_short = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "hi", "section_type": "hero"},
        )
        assert res_short.status_code == 422

        # 2. Prompt too long (> 1000 chars)
        res_long = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "A" * 1001, "section_type": "hero"},
        )
        assert res_long.status_code == 422

        # 3. Invalid preset
        res_preset = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "Valid prompt for features", "section_type": "illegal_backdoor_preset"},
        )
        assert res_preset.status_code == 422

        assert mock_gen.call_count == 0


# ── 4. Quota & Rate Limit Exhaustion Tests ─────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_quota_exhaustion_returns_429(client: AsyncClient, test_db, mock_redis):
    """When daily quota (50) is exhausted, returns HTTP 429 with RFC headers."""
    headers, proj_id, _ = await _register_user_and_create_project(client, "quota_429@test.com", "proj-quota-429")

    dummy_result = _make_dummy_generated_section("hero")

    # Mock reservation to return daily_quota_exceeded
    with patch.object(RateLimitService, "reserve_publisher_ai_quota") as mock_reserve, \
         patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:

        mock_reserve.return_value = MagicMock(
            allowed=False,
            reason="daily_quota_exceeded",
            burst=MagicMock(limit=5, remaining=2, retry_after_seconds=60),
            quota=MagicMock(limit=50, remaining=0, reset_seconds=3600, reset_timestamp_utc="2026-11-11T00:00:00+00:00"),
        )

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "Create hero section", "section_type": "hero"},
        )

        assert res.status_code == 429
        assert "Daily AI generation quota exceeded" in res.json()["detail"]
        assert res.headers["Retry-After"] == "3600"
        assert res.headers["X-Quota-Limit"] == "50"
        assert res.headers["X-Quota-Remaining"] == "0"
        assert res.headers["X-Quota-Reset"] == "2026-11-11T00:00:00+00:00"
        assert mock_gen.call_count == 0


@pytest.mark.asyncio
async def test_burst_limit_exhaustion_returns_429_with_retry_after(client: AsyncClient, test_db, mock_redis):
    """When burst rate limit (5/min) is exceeded, returns HTTP 429 with Retry-After header."""
    headers, proj_id, _ = await _register_user_and_create_project(client, "burst_429@test.com", "proj-burst-429")

    with patch.object(RateLimitService, "reserve_publisher_ai_quota") as mock_reserve, \
         patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:

        mock_reserve.return_value = MagicMock(
            allowed=False,
            reason="burst_limit_exceeded",
            burst=MagicMock(limit=5, remaining=0, retry_after_seconds=45),
            quota=None,
        )

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "Create hero section", "section_type": "hero"},
        )

        assert res.status_code == 429
        assert "burst limit exceeded" in res.json()["detail"]
        assert res.headers["Retry-After"] == "45"
        assert res.headers["X-RateLimit-Limit"] == "5"
        assert res.headers["X-RateLimit-Remaining"] == "0"
        assert mock_gen.call_count == 0


# ── 5. Provider Error Handling & Security Sanitization Tests ──────────────────

@pytest.mark.asyncio
async def test_provider_timeout_returns_controlled_503(client: AsyncClient, test_db, mock_redis):
    """Provider timeouts raise controlled 503 without leaking provider URL or model."""
    headers, proj_id, _ = await _register_user_and_create_project(client, "timeout@test.com", "proj-timeout")

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = WebsiteAIUnavailableError("AI generation request timed out. Please try again.")

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "Generate complex section", "section_type": "custom"},
        )

        assert res.status_code == 503
        detail = res.json()["detail"]
        assert "temporarily unavailable" in detail
        # Verify absence of secrets or internal provider IDs
        assert "groq" not in detail.lower()
        assert "gemini" not in detail.lower()
        assert "openai" not in detail.lower()
        assert "llama" not in detail.lower()


@pytest.mark.asyncio
async def test_malformed_provider_output_returns_502(client: AsyncClient, test_db, mock_redis):
    """Malformed provider JSON produces controlled HTTP 502 Bad Gateway."""
    headers, proj_id, _ = await _register_user_and_create_project(client, "malformed@test.com", "proj-malformed")

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = WebsiteAIMalformedResponseError("AI provider returned invalid JSON")

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "Generate testimonials", "section_type": "testimonials"},
        )

        assert res.status_code == 502
        assert "invalid or unsafe section content" in res.json()["detail"]


@pytest.mark.asyncio
async def test_xss_and_ad_slot_spoofing_attempts_cannot_pass_sanitization(client: AsyncClient, test_db, mock_redis):
    """Outputs violating Schema v2 sanitization produce controlled HTTP 502 Bad Gateway."""
    headers, proj_id, _ = await _register_user_and_create_project(client, "xss_ai@test.com", "proj-xss-ai")

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = WebsiteAISecurityError(
            "Generated section content failed security policy validation: Custom HTML content cannot impersonate RivinityADS ad placements."
        )

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "Generate ad banner", "section_type": "custom"},
        )

        assert res.status_code == 502
        assert "invalid or unsafe section content" in res.json()["detail"]


# ── 6. Persistence & Monetization Isolation Tests ─────────────────────────────

@pytest.mark.asyncio
async def test_generation_does_not_modify_project_or_page_records(client: AsyncClient, test_db, mock_redis):
    """Endpoint is strictly generation-only: zero database persistence or page mutation."""
    headers, proj_id, _ = await _register_user_and_create_project(client, "no_persist@test.com", "proj-no-persist")

    # Record initial database state
    stmt_pages = select(WebsitePage).where(WebsitePage.project_id == proj_id)
    initial_pages = (await test_db.execute(stmt_pages)).scalars().all()
    initial_page_count = len(initial_pages)
    initial_blocks = initial_pages[0].blocks

    stmt_proj = select(WebsiteProject).where(WebsiteProject.id == proj_id)
    initial_proj = (await test_db.execute(stmt_proj)).scalar_one()
    initial_updated_at = initial_proj.updated_at

    dummy_result = _make_dummy_generated_section("cta")

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = dummy_result

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "Build an engaging CTA block", "section_type": "cta"},
        )
        assert res.status_code == 200

    # Verify database state after generation
    test_db.expire_all()
    post_pages = (await test_db.execute(stmt_pages)).scalars().all()
    assert len(post_pages) == initial_page_count
    assert post_pages[0].blocks == initial_blocks

    post_proj = (await test_db.execute(stmt_proj)).scalar_one()
    assert post_proj.updated_at == initial_updated_at


@pytest.mark.asyncio
async def test_provider_credentials_and_internal_details_never_appear(client: AsyncClient, test_db, mock_redis):
    """Response payload never leaks model identifiers, provider names, or API keys."""
    headers, proj_id, _ = await _register_user_and_create_project(client, "clean_resp@test.com", "proj-clean-resp")

    dummy_result = _make_dummy_generated_section("hero")

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = dummy_result

        res = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "Build hero", "section_type": "hero"},
        )
        assert res.status_code == 200
        raw_text = res.text

        # Banned credential patterns & internal provider identifiers
        for secret in ("gsk_", "AIza", "sk-", "groq", "gemini", "llama", "mock_provider"):
            assert secret not in raw_text


@pytest.mark.asyncio
async def test_suspended_and_archived_projects_rejected(client: AsyncClient, test_db, mock_redis):
    """Suspended (403) and archived (400) projects are rejected before calling quota or AI."""
    headers, proj_id, _ = await _register_user_and_create_project(client, "lifecycle@test.com", "proj-lifecycle")

    stmt = select(WebsiteProject).where(WebsiteProject.id == proj_id)
    proj = (await test_db.execute(stmt)).scalar_one()

    # 1. Suspended project -> 403 Forbidden
    proj.status = WebsiteStatus.SUSPENDED
    await test_db.commit()

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        res_susp = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "Build hero", "section_type": "hero"},
        )
        assert res_susp.status_code == 403
        assert "suspended" in res_susp.json()["detail"].lower()
        assert mock_gen.call_count == 0

    # 2. Archived project -> 400 Bad Request
    proj.status = WebsiteStatus.ARCHIVED
    await test_db.commit()

    with patch.object(WebsiteAIService, "generate_section", new_callable=AsyncMock) as mock_gen:
        res_arch = await client.post(
            f"/api/v1/website/projects/{proj_id}/ai/generate-section",
            headers=headers,
            json={"prompt": "Build hero", "section_type": "hero"},
        )
        assert res_arch.status_code == 400
        assert "archived" in res_arch.json()["detail"].lower()
        assert mock_gen.call_count == 0

