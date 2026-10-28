"""Pydantic schemas and block validation rules for the website builder."""

from datetime import datetime
from enum import Enum
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


CONTENT_MODEL_MAP: dict[BlockType, type[BaseModel]] = {
    BlockType.HERO: HeroContent,
    BlockType.TEXT_SECTION: TextSectionContent,
    BlockType.FEATURES_GRID: FeaturesGridContent,
    BlockType.IMAGE_GALLERY: ImageGalleryContent,
    BlockType.CONTACT_FORM: ContactFormContent,
    BlockType.CTA_BANNER: CtaBannerContent,
    BlockType.AD_PLACEMENT: AdPlacementContent,
    BlockType.FOOTER: FooterContent,
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
    schema_version: int = Field(default=1, ge=1)
    blocks: list[BlockItem] = Field(default_factory=list, max_length=50)


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
    template_id: str
    theme_config: dict[str, Any]
    status: int
    monetization_status: int
    current_published_version_id: int | None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    pages: list[PageSummaryResponse] = Field(default_factory=list)


class ProjectSummaryResponse(ORMModel):
    id: int
    owner_user_id: int
    publisher_id: int | None
    name: str
    slug: str
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
        default_factory=lambda: {"advertising": True, "analytics": True},
        description="Visitor opted consent categories",
    )
    version: str = Field(default="v1.0", max_length=32)


class VisitorConsentResponse(BaseModel):
    success: bool
    message: str
    consent_id: int
    categories: dict[str, bool]

