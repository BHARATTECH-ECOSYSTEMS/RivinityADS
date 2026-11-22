"""Pydantic schemas and block validation rules for the website builder."""

from datetime import datetime
from enum import Enum
import html
from html.parser import HTMLParser
import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Reserved Subdomain Slugs ───────────────────────────────────────────────
RESERVED_SLUGS: set[str] = {
    "admin", "administrator", "api", "app", "assets", "auth", "billing", "blog",
    "cdn", "chat", "checkout", "cloud", "config", "console", "dashboard", "data",
    "dev", "docs", "domain", "email", "event", "faq", "finance", "graphql", "help",
    "home", "hosting", "hub", "internal", "investor", "jobs", "legal", "liteads",
    "login", "logout", "mail", "mailpit", "management", "media", "metrics", "monitoring",
    "moderation", "news", "notification", "notifications", "ns", "ns1", "ns2",
    "oauth", "origin", "panel", "payment", "payments", "portal", "preview", "pricing",
    "privacy", "public", "publisher", "publishers", "redis", "register", "reset",
    "rivinity", "rivinityads", "root", "router", "sandbox", "secure", "security",
    "server", "service", "services", "settings", "shop", "signin", "signup", "sitemap",
    "sites", "slot", "smtp", "ssl", "staging", "static", "status", "store", "stripe",
    "support", "system", "takedown", "terms", "test", "testing", "tools", "track",
    "tracking", "user", "users", "verify", "web", "webhook", "webhooks", "website",
    "websites", "well-known", "wiki", "www", "www1", "www2", "xmlrpc",
}

SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
PAGE_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")
HEX_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

DISALLOWED_SCRIPT_PATTERNS = [
    re.compile(r"<\s*script", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"vbscript\s*:", re.IGNORECASE),
    re.compile(r"data\s*:\s*text\/html", re.IGNORECASE),
    re.compile(r"<\s*iframe", re.IGNORECASE),
    re.compile(r"<\s*object", re.IGNORECASE),
    re.compile(r"<\s*embed", re.IGNORECASE),
    re.compile(r"<\s*style", re.IGNORECASE),
    re.compile(r"\bon[a-z]+\s*=", re.IGNORECASE),  # onload=, onerror=, onclick=
]

ALLOWED_FONTS = {
    "Inter",
    "Roboto",
    "Open Sans",
    "Lato",
    "Poppins",
    "Montserrat",
    "Playfair Display",
    "Merriweather",
    "system-ui",
}


def sanitize_and_validate_text(v: str | None, field_name: str = "text") -> str | None:
    """Ensure text content contains no executable scripts or malicious tags."""
    if v is None:
        return None
    for pattern in DISALLOWED_SCRIPT_PATTERNS:
        if pattern.search(v):
            raise ValueError(
                f"Potentially unsafe HTML/JavaScript tags or event handlers detected in '{field_name}'."
            )
    return v


def validate_safe_url(url: str | None, field_name: str = "url") -> str | None:
    """Validate that URLs use safe protocols and no executable schemes."""
    if not url:
        return None
    url_stripped = url.strip()
    if any(pattern.search(url_stripped) for pattern in DISALLOWED_SCRIPT_PATTERNS):
        raise ValueError(f"Unsafe script or scheme in '{field_name}'.")
    if url_stripped.startswith(("/", "http://", "https://", "mailto:", "tel:")):
        return url_stripped
    raise ValueError(
        f"'{field_name}' must begin with http://, https://, mailto:, tel:, or a relative path (/)."
    )


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Theme Configuration ────────────────────────────────────────────────────

class ThemeConfig(BaseModel):
    """Visual theme tokens for website rendering."""
    primary_color: str = Field(default="#2563eb", description="Primary brand hex color")
    secondary_color: str = Field(default="#475569", description="Secondary accent hex color")
    background_color: str = Field(default="#ffffff", description="Page background hex color")
    text_color: str = Field(default="#0f172a", description="Default text hex color")
    font_family: str = Field(default="Inter", description="Whitelisted typography family")
    logo_url: str | None = Field(default=None, description="Header logo image URL")
    favicon_url: str | None = Field(default=None, description="Browser tab favicon URL")
    border_radius: str = Field(default="0.5rem", description="Component corner radius token")

    @field_validator("primary_color", "secondary_color", "background_color", "text_color")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        if not HEX_COLOR_PATTERN.match(v):
            raise ValueError(f"Color must be a valid hex string (e.g. #2563eb), got '{v}'")
        return v

    @field_validator("font_family")
    @classmethod
    def validate_font(cls, v: str) -> str:
        if v not in ALLOWED_FONTS:
            raise ValueError(f"Font family '{v}' is not supported. Allowed: {', '.join(sorted(ALLOWED_FONTS))}")
        return v

    @field_validator("logo_url", "favicon_url")
    @classmethod
    def validate_theme_urls(cls, v: str | None) -> str | None:
        return validate_safe_url(v, "theme URL")


# ── Structured Page Blocks ─────────────────────────────────────────────────

class BlockType(str, Enum):
    HERO = "hero"
    TEXT_SECTION = "text_section"
    FEATURES_GRID = "features_grid"
    IMAGE_GALLERY = "image_gallery"
    CONTACT_FORM = "contact_form"
    CTA_BANNER = "cta_banner"
    AD_PLACEMENT = "ad_placement"
    FOOTER = "footer"
    OPEN_DESIGN_SECTION = "open_design_section"


class CtaButton(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    target_url: str = Field(min_length=1, max_length=500)
    style: Literal["primary", "secondary", "outline"] = "primary"

    @field_validator("label")
    @classmethod
    def check_label(cls, v: str) -> str:
        return sanitize_and_validate_text(v, "CTA button label") or ""

    @field_validator("target_url")
    @classmethod
    def check_url(cls, v: str) -> str:
        return validate_safe_url(v, "CTA target URL") or ""


class FeatureItem(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    icon: str | None = Field(default=None, max_length=50)

    @field_validator("title", "description")
    @classmethod
    def check_text(cls, v: str) -> str:
        return sanitize_and_validate_text(v, "feature item") or ""


class GalleryImage(BaseModel):
    image_url: str = Field(min_length=1, max_length=500)
    alt_text: str = Field(min_length=1, max_length=200, description="Mandatory accessible image description")
    caption: str | None = Field(default=None, max_length=200)

    @field_validator("image_url")
    @classmethod
    def check_url(cls, v: str) -> str:
        return validate_safe_url(v, "gallery image URL") or ""

    @field_validator("alt_text", "caption")
    @classmethod
    def check_text(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "image text")


class NavLink(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    target_url: str = Field(min_length=1, max_length=500)

    @field_validator("label")
    @classmethod
    def check_label(cls, v: str) -> str:
        return sanitize_and_validate_text(v, "nav link label") or ""

    @field_validator("target_url")
    @classmethod
    def check_url(cls, v: str) -> str:
        return validate_safe_url(v, "nav target URL") or ""


# Block Content Schemas

class HeroContent(BaseModel):
    headline: str = Field(min_length=1, max_length=160)
    subheadline: str | None = Field(default=None, max_length=300)
    cta_button: CtaButton | None = None
    background_image_url: str | None = None
    alignment: Literal["left", "center", "right"] = "left"

    @field_validator("headline", "subheadline")
    @classmethod
    def check_text(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "hero text")

    @field_validator("background_image_url")
    @classmethod
    def check_bg(cls, v: str | None) -> str | None:
        return validate_safe_url(v, "background image URL")


class TextSectionContent(BaseModel):
    heading: str | None = Field(default=None, max_length=160)
    body: str = Field(min_length=1, max_length=10000, description="Markdown/plain text body")

    @field_validator("heading", "body")
    @classmethod
    def check_text(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "text section")


class FeaturesGridContent(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    items: list[FeatureItem] = Field(default_factory=list, max_length=16)

    @field_validator("title")
    @classmethod
    def check_title(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "features grid title")


class ImageGalleryContent(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    images: list[GalleryImage] = Field(default_factory=list, max_length=24)

    @field_validator("title")
    @classmethod
    def check_title(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "gallery title")


class ContactFormContent(BaseModel):
    title: str = Field(default="Contact Us", max_length=120)
    submit_label: str = Field(default="Send Message", max_length=60)
    fields: list[str] = Field(default_factory=lambda: ["name", "email", "phone", "message"])

    @field_validator("title", "submit_label")
    @classmethod
    def check_text(cls, v: str) -> str:
        return sanitize_and_validate_text(v, "contact form text") or ""


class CtaBannerContent(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=300)
    button: CtaButton

    @field_validator("title", "description")
    @classmethod
    def check_text(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "CTA banner text")


class AdPlacementContent(BaseModel):
    placement_location: Literal["header_banner", "in_article", "sidebar", "footer"] = "in_article"
    format: int = Field(default=1, ge=1, le=4, description="Creative format (1=banner, 2=native)")
    ad_slot_id: str | None = Field(default=None, max_length=255, description="Associated AdSlot key")


class FooterContent(BaseModel):
    copyright_text: str | None = Field(default=None, max_length=160)
    links: list[NavLink] = Field(default_factory=list, max_length=8)

    @field_validator("copyright_text")
    @classmethod
    def check_text(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "footer copyright")


# ── Open Design Section & HTML Sanitizer (Schema v2) ────────────────────────

ALLOWED_OD_TAGS: set[str] = {
    "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "a", "img", "blockquote", "code", "pre",
    "hr", "br", "strong", "b", "em", "i", "table", "thead",
    "tbody", "tr", "th", "td", "figure", "figcaption",
    "section", "article", "aside", "header", "footer",
}

SELF_CLOSING_OD_TAGS: set[str] = {"img", "hr", "br"}

DISALLOWED_OD_TAGS: set[str] = {
    "script", "iframe", "object", "embed", "style", "link",
    "form", "input", "button", "textarea", "select", "meta",
    "base", "applet", "frame", "frameset", "svg", "canvas",
    "video", "audio", "portal",
}

ALLOWED_OD_ATTRIBUTES: set[str] = {
    "id", "class", "title", "aria-label", "aria-hidden",
    "aria-describedby", "role", "href", "target", "rel",
    "src", "alt", "width", "height", "loading",
    "colspan", "rowspan", "scope",
}

SPOOFING_CLASS_OR_ID_PATTERNS = [
    re.compile(r"\brivinity\b", re.IGNORECASE),
    re.compile(r"rivinity-", re.IGNORECASE),
    re.compile(r"block-ad-placement", re.IGNORECASE),
    re.compile(r"\bad-placement\b", re.IGNORECASE),
    re.compile(r"\bad-slot\b", re.IGNORECASE),
    re.compile(r"rivinity_ad", re.IGNORECASE),
    re.compile(r"ad_placement", re.IGNORECASE),
]

ALLOWED_CSS_PROPERTIES: set[str] = {
    "background", "background-color", "color",
    "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
    "width", "min-width", "max-width", "height", "min-height", "max-height",
    "display", "flex-direction", "justify-content", "align-items", "flex-wrap", "gap",
    "grid-template-columns", "grid-template-rows", "grid-gap",
    "border", "border-top", "border-bottom", "border-left", "border-right",
    "border-radius", "border-color", "border-width", "border-style",
    "text-align", "font-size", "font-weight", "font-style", "line-height", "letter-spacing",
    "box-shadow", "opacity", "overflow", "z-index", "position", "top", "bottom", "left", "right",
}


class OpenDesignHTMLSanitizer(HTMLParser):
    """Strict zero-dependency HTML parser, validator, and sanitizer."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output_parts: list[str] = []
        self.tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower().strip()
        if tag_lower in DISALLOWED_OD_TAGS or tag_lower not in ALLOWED_OD_TAGS:
            raise ValueError(f"HTML tag <{tag_lower}> is forbidden in Open Design sections.")

        clean_attrs: list[str] = []
        has_blank_target = False

        for raw_name, raw_val in attrs:
            name = raw_name.lower().strip()
            val = str(raw_val or "").strip()

            if name.startswith("on"):
                raise ValueError(f"Event handler attribute '{name}' is forbidden.")

            if (
                name.startswith("data-slot")
                or name.startswith("data-format")
                or name.startswith("data-rivinity")
                or name.startswith("data-ad")
            ):
                raise ValueError(f"Prohibited ad placement attribute '{name}' detected.")

            if name not in ALLOWED_OD_ATTRIBUTES:
                raise ValueError(f"Attribute '{name}' is not permitted on <{tag_lower}>.")

            if name in ("id", "class"):
                for pat in SPOOFING_CLASS_OR_ID_PATTERNS:
                    if pat.search(val):
                        raise ValueError(
                            f"Prohibited token detected in '{name}': '{val}'. "
                            f"Impersonation of ad components or tracking elements is strictly forbidden."
                        )
                if not re.match(r"^[a-zA-Z0-9_\-\s]*$", val):
                    raise ValueError(f"Invalid characters in attribute '{name}': '{val}'.")
                clean_attrs.append(f'{name}="{html.escape(val, quote=True)}"')

            elif name in ("href", "src"):
                clean_u = validate_safe_url(val, f"<{tag_lower}> {name}")
                if clean_u:
                    clean_attrs.append(f'{name}="{html.escape(clean_u, quote=True)}"')

            elif name == "target":
                if val in ("_blank", "_self"):
                    clean_attrs.append(f'{name}="{val}"')
                    if val == "_blank":
                        has_blank_target = True

            elif name == "rel":
                if not has_blank_target:
                    clean_attrs.append(f'{name}="{html.escape(val, quote=True)}"')

            else:
                clean_attrs.append(f'{name}="{html.escape(val, quote=True)}"')

        if has_blank_target:
            clean_attrs.append('rel="noopener noreferrer"')

        attr_str = (" " + " ".join(clean_attrs)) if clean_attrs else ""
        if tag_lower in SELF_CLOSING_OD_TAGS:
            self.output_parts.append(f"<{tag_lower}{attr_str} />")
        else:
            self.tag_stack.append(tag_lower)
            self.output_parts.append(f"<{tag_lower}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower().strip()
        if tag_lower in SELF_CLOSING_OD_TAGS:
            return
        if self.tag_stack and self.tag_stack[-1] == tag_lower:
            self.tag_stack.pop()
            self.output_parts.append(f"</{tag_lower}>")
        else:
            raise ValueError(f"Mismatched or unclosed tag </{tag_lower}>.")

    def handle_data(self, data: str) -> None:
        sanitize_and_validate_text(data, "HTML text content")
        self.output_parts.append(html.escape(data, quote=False))

    def get_sanitized_html(self) -> str:
        if self.tag_stack:
            raise ValueError(f"Unclosed HTML tags detected: {', '.join(self.tag_stack)}")
        return "".join(self.output_parts)


def validate_and_sanitize_open_design_html(raw_html: str) -> str:
    """Validate and sanitize Open Design HTML before persistence."""
    if not raw_html or not raw_html.strip():
        return ""
    if len(raw_html.encode("utf-8")) > 65536:
        raise ValueError("HTML content exceeds maximum allowed size (64 KB).")

    for pattern in DISALLOWED_SCRIPT_PATTERNS:
        if pattern.search(raw_html):
            raise ValueError("Unsafe script or executable tags detected in HTML content.")

    for pat in SPOOFING_CLASS_OR_ID_PATTERNS:
        if pat.search(raw_html):
            raise ValueError("Custom HTML content cannot impersonate RivinityADS ad placements.")

    sanitizer = OpenDesignHTMLSanitizer()
    try:
        sanitizer.feed(raw_html)
        sanitizer.close()
        return sanitizer.get_sanitized_html()
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        raise ValueError(f"Malformed or unsafe HTML: {str(e)}")


class OpenDesignSectionContent(BaseModel):
    """Schema v2 section block generated or edited via Open Design."""
    section_id: str | None = Field(default=None, max_length=64)
    section_tag: Literal["section", "article", "div", "aside", "header", "footer"] = "section"
    css_classes: str | None = Field(default=None, max_length=500)
    styles: dict[str, str] = Field(default_factory=dict)
    html_content: str = Field(min_length=1, max_length=65536)
    asset_references: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("section_id")
    @classmethod
    def check_section_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        if not re.match(r"^[a-zA-Z0-9_\-]+$", clean):
            raise ValueError("section_id must contain only alphanumeric characters, underscores, and hyphens.")
        for pat in SPOOFING_CLASS_OR_ID_PATTERNS:
            if pat.search(clean):
                raise ValueError(f"section_id '{clean}' contains prohibited ad placement tokens.")
        return clean

    @field_validator("css_classes")
    @classmethod
    def check_css_classes(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        if not re.match(r"^[a-zA-Z0-9_\-\s]*$", clean):
            raise ValueError("css_classes may contain only alphanumeric characters, underscores, hyphens, and spaces.")
        for pat in SPOOFING_CLASS_OR_ID_PATTERNS:
            if pat.search(clean):
                raise ValueError(f"css_classes contains prohibited ad placement token: '{clean}'.")
        return clean

    @field_validator("styles")
    @classmethod
    def check_styles(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 30:
            raise ValueError("Maximum 30 style declarations allowed per section.")
        sanitized = {}
        for prop, val in v.items():
            clean_prop = str(prop).strip().lower()
            clean_val = str(val).strip()
            if clean_prop not in ALLOWED_CSS_PROPERTIES:
                raise ValueError(f"CSS property '{clean_prop}' is not in the allowed properties list.")
            val_lower = clean_val.lower()
            if any(forbidden in val_lower for forbidden in (";", "url(", "expression(", "@import", "javascript:", "<", ">", "behavior", "-moz-binding")):
                raise ValueError(f"Unsafe CSS value detected for property '{clean_prop}': '{clean_val}'.")
            sanitized[clean_prop] = clean_val
        return sanitized

    @field_validator("html_content")
    @classmethod
    def check_html(cls, v: str) -> str:
        return validate_and_sanitize_open_design_html(v)

    @field_validator("asset_references")
    @classmethod
    def check_assets(cls, v: list[str]) -> list[str]:
        sanitized = []
        for ref in v:
            clean = validate_safe_url(ref, "asset reference")
            if clean:
                sanitized.append(clean)
        return sanitized


CONTENT_MODEL_MAP: dict[BlockType, type[BaseModel]] = {
    BlockType.HERO: HeroContent,
    BlockType.TEXT_SECTION: TextSectionContent,
    BlockType.FEATURES_GRID: FeaturesGridContent,
    BlockType.IMAGE_GALLERY: ImageGalleryContent,
    BlockType.CONTACT_FORM: ContactFormContent,
    BlockType.CTA_BANNER: CtaBannerContent,
    BlockType.AD_PLACEMENT: AdPlacementContent,
    BlockType.FOOTER: FooterContent,
    BlockType.OPEN_DESIGN_SECTION: OpenDesignSectionContent,
}


class BlockItem(BaseModel):
    """An individual block within a page's layout."""
    id: str = Field(min_length=1, max_length=64, description="Unique client-assigned block ID")
    type: BlockType
    is_visible: bool = True
    content: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_typed_content(self) -> "BlockItem":
        content_cls = CONTENT_MODEL_MAP.get(self.type)
        if content_cls:
            # Validate dict content strictly against the typed schema
            validated = content_cls(**self.content)
            self.content = validated.model_dump(exclude_none=True)
        return self


class PageBlocksPayload(BaseModel):
    """Schema-versioned block container for page content."""
    schema_version: int = Field(default=1, ge=1, le=2)
    blocks: list[BlockItem] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_schema_version_compatibility(self) -> "PageBlocksPayload":
        has_od = any(b.type == BlockType.OPEN_DESIGN_SECTION for b in self.blocks)
        if has_od and self.schema_version < 2:
            raise ValueError(
                "Block type 'open_design_section' requires schema_version 2. "
                "Schema version 1 only supports standard structured blocks."
            )
        return self


# ── Page Schemas ───────────────────────────────────────────────────────────

class SeoMeta(BaseModel):
    meta_title: str | None = Field(default=None, max_length=120)
    meta_description: str | None = Field(default=None, max_length=300)
    og_image_url: str | None = None
    noindex: bool = False

    @field_validator("meta_title", "meta_description")
    @classmethod
    def check_seo_text(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "SEO text")

    @field_validator("og_image_url")
    @classmethod
    def check_og_image(cls, v: str | None) -> str | None:
        return validate_safe_url(v, "OG Image URL")


class PageCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    slug: str = Field(default="", max_length=100, description="Page path segment. Empty string '' for homepage.")
    is_home: bool = False
    seo_meta: SeoMeta = Field(default_factory=SeoMeta)
    blocks: PageBlocksPayload = Field(default_factory=PageBlocksPayload)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Page title cannot be empty")
        return sanitize_and_validate_text(clean, "page title") or ""

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        clean = v.strip().lower()
        if clean == "":
            return ""  # Allowed for home page
        if not PAGE_SLUG_PATTERN.match(clean):
            raise ValueError(
                f"Page slug '{clean}' is invalid. Must contain only lowercase letters, numbers, and hyphens."
            )
        return clean


class PageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=100)
    is_home: bool | None = None
    seo_meta: SeoMeta | None = None
    blocks: PageBlocksPayload | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            raise ValueError("Page title cannot be empty")
        return sanitize_and_validate_text(clean, "page title")

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip().lower()
        if clean == "":
            return ""
        if not PAGE_SLUG_PATTERN.match(clean):
            raise ValueError(
                f"Page slug '{clean}' is invalid. Must contain only lowercase letters, numbers, and hyphens."
            )
        return clean


class PageResponse(ORMModel):
    id: int
    project_id: int
    title: str
    slug: str
    is_home: bool
    order_index: int
    language: str = "en"
    translation_group_id: str | None = None
    seo_meta: dict[str, Any]
    blocks: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class PageSummaryResponse(ORMModel):
    id: int
    project_id: int
    title: str
    slug: str
    is_home: bool
    order_index: int
    language: str = "en"
    translation_group_id: str | None = None
    updated_at: datetime


class PageReorderRequest(BaseModel):
    page_ids: list[int] = Field(min_length=1, max_length=100, description="Ordered list of page IDs")


# ── Project Schemas ────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    slug: str = Field(min_length=3, max_length=63, description="Subdomain handle")
    template_id: str = Field(default="business_services", max_length=64)
    theme_config: ThemeConfig = Field(default_factory=ThemeConfig)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        clean = v.strip()
        if len(clean) < 2:
            raise ValueError("Project name must be at least 2 characters")
        return sanitize_and_validate_text(clean, "project name") or ""

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        clean = v.strip().lower()
        if clean in RESERVED_SLUGS:
            raise ValueError(f"Subdomain slug '{clean}' is reserved by the platform.")
        if not SLUG_PATTERN.match(clean):
            raise ValueError(
                f"Slug '{clean}' is invalid. Must be 3-63 characters, start/end with an alphanumeric character, and contain only lowercase letters, numbers, and hyphens."
            )
        if "--" in clean:
            raise ValueError("Slug cannot contain consecutive hyphens.")
        return clean


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    template_id: str | None = Field(default=None, max_length=64)
    theme_config: ThemeConfig | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        if len(clean) < 2:
            raise ValueError("Project name must be at least 2 characters")
        return sanitize_and_validate_text(clean, "project name")


class ProjectResponse(ORMModel):
    id: int
    owner_user_id: int
    publisher_id: int | None
    property_id: int | None
    name: str
    slug: str
    custom_domain: str | None
    custom_domain_status: int = 0
    template_id: str
    theme_config: dict[str, Any]
    status: int
    monetization_status: int
    current_published_version_id: int | None
    is_deleted: bool
    business_profile: dict[str, Any] | None = None
    webmaster_tools: dict[str, Any] = Field(default_factory=dict)
    primary_language: str = "en"
    created_at: datetime
    updated_at: datetime
    pages: list[PageSummaryResponse] = Field(default_factory=list)


class ProjectSummaryResponse(ORMModel):
    id: int
    owner_user_id: int
    publisher_id: int | None
    name: str
    slug: str
    custom_domain: str | None = None
    custom_domain_status: int = 0
    template_id: str
    status: int
    monetization_status: int
    is_deleted: bool
    current_published_version_id: int | None
    created_at: datetime
    updated_at: datetime
    page_count: int = 0


class SlugCheckResponse(BaseModel):
    slug: str
    available: bool
    reason: str | None = None


# ── Website Template Schemas (Phase 2) ────────────────────────────────────

class WebsiteTemplateSummaryResponse(ORMModel):
    id: str
    name: str
    description: str
    category: str
    thumbnail_url: str | None = None
    badge: str | None = None
    default_theme_config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    order_index: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WebsiteTemplateDetailResponse(WebsiteTemplateSummaryResponse):
    default_pages: list[dict[str, Any]] = Field(default_factory=list)



# ── Publication & Version Schemas ──────────────────────────────────────────

class PublishRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=255, description="Publication changelog note")

    @field_validator("notes")
    @classmethod
    def check_notes(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "publish notes")


class VersionSummaryResponse(ORMModel):
    id: int
    project_id: int
    version_number: int
    published_by_user_id: int
    notes: str | None
    published_at: datetime
    rollback_from_version_id: int | None


class RollbackRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=255, description="Rollback reason or audit comment")

    @field_validator("notes")
    @classmethod
    def check_notes(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "rollback notes")


# ── Phase 10E & 10F: Publisher Linkage, Ad-Slot Binding & Monetization Schemas ──

class PublisherLinkRequest(BaseModel):
    publisher_id: int = Field(gt=0, description="Publisher account ID to associate with project")
    property_id: int = Field(gt=0, description="Publisher property ID to associate with project")


class BoundSlotResponse(ORMModel):
    id: int
    slot_id: str
    name: str
    format: int
    width: int
    height: int
    status: int
    floor_price: float
    property_id: int | None = None
    is_chat_surface: bool = False


class MonetizationConsentRequest(BaseModel):
    accepted_terms: bool = Field(description="Explicit agreement to publisher monetization terms")
    version: str = Field(default="v1.0", max_length=32)
    scope: dict[str, Any] = Field(
        default_factory=lambda: {"ad_delivery": True, "revenue_share": True},
        description="Agreed monetization and disclosure scopes",
    )


class MonetizationStatusResponse(BaseModel):
    project_id: int
    is_eligible: bool
    current_status: int
    prerequisites: dict[str, bool]
    failure_reasons: list[str]
    slot_count: int
    consent: dict[str, Any] | None = None


class VisitorConsentPayload(BaseModel):
    categories: dict[str, bool] = Field(
        default_factory=lambda: {"necessary": True, "advertising": False, "analytics": False},
        description="Visitor opted consent categories (defaults to denied)",
    )
    version: str = Field(default="v1.0", max_length=32)
    visitor_id: str | None = Field(
        default=None,
        max_length=64,
        description="Optional persistent visitor identifier for deterministic revocation tracking",
    )

    @field_validator("categories", mode="before")
    @classmethod
    def normalize_categories(cls, v: Any) -> dict[str, bool]:
        if not isinstance(v, dict):
            return {"necessary": True, "advertising": False, "analytics": False}
        return {
            "necessary": True,
            "advertising": bool(v.get("advertising", False)),
            "analytics": bool(v.get("analytics", False)),
        }


class VisitorConsentResponse(BaseModel):
    success: bool
    message: str
    consent_id: int
    categories: dict[str, bool]
    is_granted: bool = False
    consent_token: str | None = None
    expires_at: int | None = None
    visitor_id: str | None = None


# ── Tenant-Scoped Storage Schemas ──────────────────────────────────────────

class StorageFileUploadRequest(BaseModel):
    relative_path: str = Field(min_length=1, max_length=500, description="Project-relative destination path")
    content_base64: str | None = Field(default=None, description="Base64 encoded binary file payload")
    content_text: str | None = Field(default=None, description="UTF-8 plain text file payload")

    @field_validator("relative_path")
    @classmethod
    def validate_path_syntax(cls, v: str) -> str:
        clean = v.strip().replace("\\", "/")
        if "\0" in clean:
            raise ValueError("Null bytes are forbidden in file paths")
        if ".." in clean.split("/"):
            raise ValueError("Path traversal sequences ('..') are forbidden")
        return clean


class StoredFileResponse(BaseModel):
    path: str
    size_bytes: int
    content_type: str
    updated_at: str


class StorageUsageResponse(BaseModel):
    project_id: int
    total_size_bytes: int
    file_count: int
    quota_bytes: int


# ── Open Design AI Generation Schemas ────────────────────────────────────────

ALLOWED_AI_SECTION_TYPES: set[str] = {
    "hero",
    "features",
    "testimonials",
    "pricing",
    "cta",
    "footer",
    "faq",
    "gallery",
    "stats",
    "contact",
    "about",
    "custom",
}


class WebsiteAIGenerateSectionRequest(BaseModel):
    """Request model for server-side Open Design AI section generation."""
    prompt: str = Field(
        min_length=3,
        max_length=1000,
        description="Natural language prompt describing the desired section layout and copy",
    )
    section_type: str = Field(
        default="custom",
        description="Section preset category (e.g. hero, features, testimonials, pricing, cta, footer, faq, custom)",
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, v: str) -> str:
        clean = (v or "").strip()
        if len(clean) < 3:
            raise ValueError("Prompt must be at least 3 characters long.")
        if len(clean) > 1000:
            raise ValueError("Prompt cannot exceed 1000 characters.")
        return clean

    @field_validator("section_type")
    @classmethod
    def validate_section_type(cls, v: str) -> str:
        clean = (v or "").strip().lower()
        if clean not in ALLOWED_AI_SECTION_TYPES:
            raise ValueError(
                f"Invalid section_type '{v}'. Allowed presets: {', '.join(sorted(ALLOWED_AI_SECTION_TYPES))}."
            )
        return clean


class WebsiteAIGenerateSectionResponse(BaseModel):
    """Response model for server-side Open Design AI section generation."""
    block: BlockItem = Field(description="Validated BlockItem representing the generated Open Design section")
    section_id: str = Field(description="Identifier for the generated section container")
    section_type: str = Field(description="Section preset used during generation")
    quota_remaining: int = Field(description="Remaining generation quota for the current UTC day")
    daily_limit: int = Field(description="Maximum generation quota allowed per UTC day")
    reset_timestamp_utc: str = Field(description="ISO 8601 timestamp for the next 00:00 UTC daily quota reset")



