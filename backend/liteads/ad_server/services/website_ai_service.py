"""
Website AI Generation Service.

Provides an authoritative server-side proxy for generating Open Design website
sections using multi-provider LLMs (Groq, Google Gemini, OpenAI). Compiles
structured prompts using Open Design craft guidelines (anti-ai-slop, typography,
color, accessibility), enforces strict Schema v2 HTML sanitization, and returns
in-memory BlockItem payloads without database persistence.
"""

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from liteads.common.config import get_settings
from liteads.common.exceptions import LiteAdsError
from liteads.common.logger import get_logger
from liteads.schemas.website import (
    ALLOWED_CSS_PROPERTIES,
    BlockItem,
    BlockType,
    OpenDesignSectionContent,
    ThemeConfig,
    validate_and_sanitize_open_design_html,
)

logger = get_logger(__name__)


# ── Exceptions ───────────────────────────────────────────────────────────────

class WebsiteAIError(LiteAdsError):
    """Base exception for Website AI generation errors."""
    pass


class WebsiteAIConfigError(WebsiteAIError):
    """Raised when the configured AI provider lacks credentials or is invalid."""
    pass


class WebsiteAIUnavailableError(WebsiteAIError):
    """Raised when the AI provider times out, fails network calls, or is down."""
    pass


class WebsiteAIMalformedResponseError(WebsiteAIError):
    """Raised when the AI provider returns unparseable or schema-violating output."""
    pass


class WebsiteAISecurityError(WebsiteAIError):
    """Raised when generated output contains unsafe scripts, forbidden tags, or spoofing."""
    pass


# ── Data Transfer Objects ────────────────────────────────────────────────────

@dataclass
class GeneratedSectionResult:
    """In-memory result of an AI section generation."""
    block: BlockItem
    section_content: OpenDesignSectionContent
    raw_prompt: str
    section_type: str
    provider: str
    section_id: str


# ── JSON Schema for Section Output ──────────────────────────────────────────

SECTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "section_tag": {
            "type": "string",
            "enum": ["section", "article", "div", "aside", "header", "footer"],
            "description": "HTML semantic container tag for the section",
        },
        "section_id": {
            "type": "string",
            "description": "Short kebab-case identifier for the section (e.g. features-grid, pricing-matrix)",
        },
        "css_classes": {
            "type": "string",
            "description": "Space-separated CSS class names for custom layout styling",
        },
        "styles": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Map of safe CSS property declarations (e.g. padding, background-color, gap)",
        },
        "html_content": {
            "type": "string",
            "description": "Semantic, accessible HTML5 snippet representing the inner visual structure",
        },
    },
    "required": ["section_tag", "section_id", "css_classes", "styles", "html_content"],
    "additionalProperties": False,
}


# ── Craft Prompt Compiler ───────────────────────────────────────────────────

class WebsiteAICraftEngine:
    """Compiles structured system and user prompts incorporating Open Design craft rules."""

    @staticmethod
    def build_system_prompt(theme: ThemeConfig) -> str:
        """
        Build a comprehensive system prompt synthesizing:
        - anti-ai-slop rules
        - typography hierarchy rules
        - color and accent discipline
        - accessibility baseline rules
        - authorized brand theme tokens
        """
        return f"""You are an expert digital product designer and frontend engineer for RivinityADS Open Design.
You generate clean, accessible, modern web sections conforming strictly to Schema v2 and Open Design craft standards.

=== 1. BRAND THEME TOKENS ===
You MUST style the section using the publisher's authorized CSS theme variables:
- Primary Brand Color: {theme.primary_color} (use `var(--primary)`)
- Secondary Accent Color: {theme.secondary_color} (use `var(--secondary)`)
- Page Background Color: {theme.background_color} (use `var(--bg)`)
- Text Color: {theme.text_color} (use `var(--text)`)
- Border Radius Token: {theme.border_radius} (use `var(--radius)`)
- Font Family: {theme.font_family} (use `var(--font-family, sans-serif)`)

=== 2. OPEN DESIGN CRAFT GUIDELINES (ANTI-AI-SLOP) ===
Follow these rules to produce human-crafted, distinctive design rather than generic AI output:
1. NO DEFAULT INDIGO: Never use generic Tailwind indigo accents (#6366f1, #4f46e5, #4338ca, #8b5cf6, #7c3aed). Always use `var(--primary)` or `var(--secondary)`.
2. NO CLICHÉ GRADIENTS: Never use two-stop purple-to-blue, blue-to-cyan, or indigo-to-pink gradients on heroes or section cards. Use flat surfaces, subtle tonal contrast, or clean border outlines.
3. NO EMOJIS AS ICONS: Never use emojis (such as 🚀, ✨, 🔥, ⚡, 💡, 🎯) inside headings, buttons, or feature lists. Use clean semantic layout, SVG markup, or typographic symbols instead.
4. NO AI DASHBOARD TILES: Never generate rounded cards with colored left-border accents. Use clean borders or background surfaces.
5. NO INVENTED METRICS: Do not invent exaggerated metrics ("10x faster", "99.9% uptime").
6. NO FILLER COPY: Never use "Lorem ipsum", "Feature 1/2/3", or generic marketing buzzwords ("Unlock your potential"). Write realistic, concrete copy tailored to the user's business context.
7. 80/20 RULE: Aim for 80% proven, readable layout patterns + 20% distinctive editorial voice and visual rhythm.

=== 3. TYPOGRAPHY & COLOR DISCIPLINE ===
1. TYPOGRAPHY SCALE:
   - Section Title / H2: 24–32px, font-weight 700, tight line-height (1.2–1.3)
   - Subheadings / H3: 18–22px, font-weight 600, line-height 1.35–1.45
   - Body copy: 15–18px, font-weight 400, generous line-height (1.5–1.6)
   - Small labels / captions: 12–14px, font-weight 500
2. COLOR & ACCENT DISCIPLINE:
   - Neutrals (70–90%): background (`var(--bg)`), text (`var(--text)`), subtle borders.
   - Accent (5–10%): `var(--primary)`. Limit accent usage to at most 2 prominent elements per section (e.g. one primary CTA link and one badge).
   - Ensure minimum 4.5:1 contrast ratio for body text against backgrounds.

=== 4. ACCESSIBILITY BASELINE ===
1. Use semantic HTML5 elements (`<section>`, `<article>`, `<header>`, `<div>`, `<p>`, `<h1>`-`<h6>`, `<ul>`, `<ol>`, `<li>`, `<a>`, `<img>`).
2. Maintain sequential heading order without skipping levels (e.g., do not place an `<h4>` directly after an `<h2>`).
3. Every `<img>` tag must have a descriptive `alt` attribute.
4. Hyperlinks must have descriptive, meaningful anchor text (never generic "Click here").

=== 5. STRICT PLATFORM SECURITY BOUNDARIES ===
You MUST NOT generate any of the following; any violation will cause immediate rejection:
1. FORBIDDEN TAGS: Never include `<script>`, `<iframe>`, `<object>`, `<embed>`, `<style>`, `<link>`, `<form>`, `<input>`, `<button>`, `<textarea>`, `<select>`, `<video>`, `<audio>`. Interactive buttons must be styled `<a>` links.
2. FORBIDDEN ATTRIBUTES: Never include inline `on*` event handlers (`onclick`, `onload`, `onerror`, `onmouseover`, etc.).
3. FORBIDDEN PROTOCOLS: Never use `javascript:`, `vbscript:`, or `data:text/html` URLs in `href` or `src`.
4. FORBIDDEN AD IDENTIFIERS: Never output attributes or classes matching `block-ad-placement`, `rivinity-ad-slot`, `data-slot`, `data-format`, `data-rivinity`, or `data-ad`.
5. SCOPED INLINE STYLES: CSS properties must only use standard layout properties (`padding`, `background-color`, `gap`, `display`, etc.).

=== 6. REQUIRED OUTPUT FORMAT ===
You MUST return ONLY a valid JSON object matching this exact structure:
{{
  "section_tag": "section",
  "section_id": "short-kebab-id",
  "css_classes": "custom-section-class-names",
  "styles": {{
    "background-color": "var(--bg)",
    "padding": "4rem 2rem"
  }},
  "html_content": "<div class=\\"custom-container\\"><h2 class=\\"section-title\\">Title</h2><p class=\\"section-desc\\">Description</p></div>"
}}
Do not wrap the JSON in markdown code blocks. Output pure, parseable JSON only."""

    @staticmethod
    def build_user_prompt(
        prompt: str,
        section_type: str = "custom",
        context: dict[str, Any] | None = None,
    ) -> str:
        """Build the user prompt incorporating section type and contextual instructions."""
        clean_prompt = prompt.strip()
        sec_type_str = section_type.strip().lower() if section_type else "custom"

        user_msg = (
            f"Generate a modern, responsive website section.\n"
            f"- Section Category: {sec_type_str}\n"
            f"- Publisher Requirements: {clean_prompt}\n"
        )
        if context:
            additional_notes = []
            if "page_title" in context and context["page_title"]:
                additional_notes.append(f"Page Title: {context['page_title']}")
            if "project_name" in context and context["project_name"]:
                additional_notes.append(f"Website Name: {context['project_name']}")
            if "existing_sections" in context and context["existing_sections"]:
                additional_notes.append(f"Existing Sections on Page: {', '.join(str(s) for s in context['existing_sections'])}")
            if additional_notes:
                user_msg += "- Page Context:\n  " + "\n  ".join(additional_notes) + "\n"

        user_msg += (
            "Follow all Craft Rules and return only the requested JSON structure with safe, semantic HTML."
        )
        return user_msg


# ── Website AI Service ──────────────────────────────────────────────────────

class WebsiteAIService:
    """
    Dedicated server-side AI generation service for Open Design website sections.
    
    Routes generation through Groq, Google Gemini, or OpenAI using existing
    system settings, compiles craft guidelines, sanitizes HTML via Schema v2,
    and returns in-memory BlockItem payloads.
    """

    SUPPORTED_PROVIDERS = ("openai", "gemini", "groq")

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        provider: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.settings = get_settings()
        self.provider = (provider or self.settings.llm_provider).lower()
        self.timeout = max(1.0, min(float(timeout), 60.0))
        self._client = client

        # Validate provider choice against supported matrix
        if self.provider not in self.SUPPORTED_PROVIDERS:
            raise WebsiteAIConfigError(
                f"Unsupported AI provider '{self.provider}'. "
                f"Must be one of: {', '.join(self.SUPPORTED_PROVIDERS)}"
            )

        # Resolve credentials server-side; never expose to clients
        if self.provider == "gemini":
            self.api_key = self.settings.gemini.api_key if hasattr(self.settings, "gemini") else ""
            self.base_url = "https://generativelanguage.googleapis.com/v1beta"
            self.model = getattr(self.settings.gemini, "model", "gemini-flash-latest")
        elif self.provider == "groq":
            self.api_key = self.settings.groq.api_key if hasattr(self.settings, "groq") else ""
            self.base_url = "https://api.groq.com/openai/v1"
            self.model = getattr(self.settings.groq, "model", "openai/gpt-oss-120b")
        else:  # openai
            self.api_key = self.settings.openai.api_key if hasattr(self.settings, "openai") else ""
            self.base_url = "https://api.openai.com/v1"
            self.model = "gpt-4o-mini"

    def _verify_configuration(self) -> None:
        """Verify that the selected provider has a configured API key."""
        if not self.api_key or self.api_key.startswith("<PASTE_") or not self.api_key.strip():
            raise WebsiteAIConfigError(
                f"AI provider '{self.provider}' is not configured with a valid API key."
            )

    @staticmethod
    def _strip_code_fence(raw_text: str) -> str:
        """Strip markdown code fence wrappers (```json ... ```) from model output."""
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    async def _dispatch_gemini(
        self,
        system_prompt: str,
        user_prompt: str,
        client: httpx.AsyncClient,
    ) -> str:
        """Dispatch structured request to Google Gemini API."""
        url = f"{self.base_url}/models/{self.model}:generateContent"
        payload = {
            "systemInstruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        headers = {"Content-Type": "application/json"}
        params = {"key": self.api_key}

        response = await client.post(url, json=payload, headers=headers, params=params)
        response.raise_for_status()

        try:
            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("No generation candidates returned from Gemini")
            return candidates[0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, ValueError) as exc:
            raise WebsiteAIMalformedResponseError(
                "Gemini response structure is invalid or missing candidate content"
            ) from exc

    async def _dispatch_groq(
        self,
        system_prompt: str,
        user_prompt: str,
        client: httpx.AsyncClient,
    ) -> str:
        """Dispatch structured request to Groq API (OpenAI-compatible)."""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()

        try:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise WebsiteAIMalformedResponseError(
                "Groq response structure is missing choice content"
            ) from exc

    async def _dispatch_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        client: httpx.AsyncClient,
    ) -> str:
        """Dispatch structured request to OpenAI API with strict JSON schema."""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "open_design_section",
                    "strict": True,
                    "schema": SECTION_JSON_SCHEMA,
                },
            },
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()

        try:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise WebsiteAIMalformedResponseError(
                "OpenAI response structure is missing choice content"
            ) from exc

    async def _dispatch_provider(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Route generation to the configured provider with controlled error mapping."""
        self._verify_configuration()

        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            if self.provider == "gemini":
                return await self._dispatch_gemini(system_prompt, user_prompt, client)
            elif self.provider == "groq":
                return await self._dispatch_groq(system_prompt, user_prompt, client)
            elif self.provider == "openai":
                return await self._dispatch_openai(system_prompt, user_prompt, client)
            else:
                raise WebsiteAIConfigError(f"Unsupported provider '{self.provider}'")

        except httpx.TimeoutException as exc:
            logger.warning("Website AI provider timed out", provider=self.provider)
            raise WebsiteAIUnavailableError(
                "AI generation request timed out. Please try again."
            ) from exc

        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            logger.warning("Website AI provider HTTP error", provider=self.provider, status_code=code)
            if code == 429:
                raise WebsiteAIUnavailableError(
                    "AI provider rate limit reached. Please try again shortly."
                ) from exc
            elif code >= 500:
                raise WebsiteAIUnavailableError(
                    "AI generation service is temporarily unavailable. Please try again later."
                ) from exc
            else:
                raise WebsiteAIUnavailableError(
                    f"AI generation service encountered an upstream error (HTTP {code})."
                ) from exc

        except httpx.RequestError as exc:
            logger.warning("Website AI network request failed", provider=self.provider, error_cls=type(exc).__name__)
            raise WebsiteAIUnavailableError(
                "AI generation network request failed. Please check connectivity."
            ) from exc

        finally:
            if self._client is None:
                await client.aclose()

    def _parse_and_sanitize_response(
        self,
        raw_json_str: str,
    ) -> OpenDesignSectionContent:
        """
        Parse raw model response JSON, validate keys, pass HTML through
        authoritative Schema v2 sanitizer, and construct OpenDesignSectionContent.
        """
        clean_text = self._strip_code_fence(raw_json_str)

        try:
            parsed = json.loads(clean_text)
        except json.JSONDecodeError as exc:
            raise WebsiteAIMalformedResponseError(
                f"AI provider returned invalid JSON: {exc.msg}"
            ) from exc

        if not isinstance(parsed, dict):
            raise WebsiteAIMalformedResponseError("AI provider response must be a JSON object")

        # Required fields check
        required_keys = ("section_tag", "section_id", "css_classes", "styles", "html_content")
        for key in required_keys:
            if key not in parsed:
                raise WebsiteAIMalformedResponseError(f"Missing required key '{key}' in AI response")

        raw_tag = str(parsed["section_tag"]).strip().lower()
        allowed_tags = ("section", "article", "div", "aside", "header", "footer")
        if raw_tag not in allowed_tags:
            raw_tag = "section"

        raw_id = str(parsed["section_id"]).strip().lower()
        if not re.match(r"^[a-zA-Z0-9_\-]+$", raw_id):
            raw_id = f"sec-{uuid.uuid4().hex[:8]}"

        raw_css = str(parsed["css_classes"]).strip()
        raw_styles = parsed["styles"]
        if not isinstance(raw_styles, dict):
            raw_styles = {}

        # Filter styles against allowed properties
        clean_styles: dict[str, str] = {}
        for prop, val in raw_styles.items():
            prop_str = str(prop).strip().lower()
            val_str = str(val).strip()
            if prop_str in ALLOWED_CSS_PROPERTIES:
                val_lower = val_str.lower()
                if not any(f in val_lower for f in (";", "url(", "expression(", "@import", "javascript:", "<", ">")):
                    clean_styles[prop_str] = val_str

        # Authoritative HTML sanitization
        raw_html = str(parsed["html_content"])
        try:
            sanitized_html = validate_and_sanitize_open_design_html(raw_html)
        except ValueError as exc:
            raise WebsiteAISecurityError(
                f"Generated section content failed security policy validation: {exc}"
            ) from exc

        if not sanitized_html or not sanitized_html.strip():
            raise WebsiteAIMalformedResponseError("Generated section produced empty HTML content")

        try:
            return OpenDesignSectionContent(
                section_tag=raw_tag,
                section_id=raw_id,
                css_classes=raw_css or None,
                styles=clean_styles,
                html_content=sanitized_html,
            )
        except Exception as exc:
            raise WebsiteAIMalformedResponseError(
                f"Failed to instantiate OpenDesignSectionContent: {exc}"
            ) from exc

    async def generate_section(
        self,
        prompt: str,
        section_type: str = "custom",
        theme_config: ThemeConfig | dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> GeneratedSectionResult:
        """
        Generate an Open Design website section.
        
        Args:
            prompt: Publisher natural language instructions (1–2000 chars).
            section_type: High-level section category (hero, features, pricing, etc.).
            theme_config: Authorized project brand theme tokens (colors, font, radius).
            context: Optional contextual page/project metadata.
            
        Returns:
            GeneratedSectionResult containing the constructed BlockItem.
            
        Raises:
            ValueError: If input prompt is invalid or too long.
            WebsiteAIConfigError: If provider credentials are missing.
            WebsiteAIUnavailableError: If provider times out or fails.
            WebsiteAIMalformedResponseError: If output JSON is malformed.
            WebsiteAISecurityError: If output contains unrecoverable unsafe HTML.
        """
        # Validate input prompt
        if not prompt or not prompt.strip():
            raise ValueError("Prompt must not be empty")
        clean_prompt = prompt.strip()
        if len(clean_prompt) > 2000:
            raise ValueError("Prompt exceeds maximum allowed length of 2000 characters")

        # Resolve ThemeConfig
        if isinstance(theme_config, ThemeConfig):
            active_theme = theme_config
        elif isinstance(theme_config, dict):
            active_theme = ThemeConfig(**theme_config)
        else:
            active_theme = ThemeConfig()

        # Compile Craft prompts
        system_prompt = WebsiteAICraftEngine.build_system_prompt(active_theme)
        user_prompt = WebsiteAICraftEngine.build_user_prompt(clean_prompt, section_type, context)

        logger.info(
            "Dispatching website section generation",
            provider=self.provider,
            section_type=section_type,
            prompt_len=len(clean_prompt),
        )

        # Dispatch to provider
        raw_output = await self._dispatch_provider(system_prompt, user_prompt)

        # Parse & sanitize output
        section_content = self._parse_and_sanitize_response(raw_output)

        # Wrap into BlockItem (Draft in-memory block; NOT persisted to DB)
        block_id = f"blk_ai_{uuid.uuid4().hex[:12]}"
        block_item = BlockItem(
            id=block_id,
            type=BlockType.OPEN_DESIGN_SECTION,
            is_visible=True,
            content=section_content.model_dump(exclude_none=True),
        )

        return GeneratedSectionResult(
            block=block_item,
            section_content=section_content,
            raw_prompt=clean_prompt,
            section_type=section_type,
            provider=self.provider,
            section_id=section_content.section_id or block_id,
        )

