"""Pydantic schemas for Phase 6 Business Profile intake, audit, custom domain, and SEO/GEO readiness."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from liteads.schemas.website import sanitize_and_validate_text, validate_safe_url

CATEGORY_SCHEMA_MAP: dict[str, str] = {
    "restaurant": "Restaurant",
    "cafe": "CafeOrCoffeeShop",
    "retail_store": "Store",
    "grocery": "GroceryStore",
    "health_clinic": "MedicalClinic",
    "dental_clinic": "Dentist",
    "pharmacy": "Pharmacy",
    "plumber": "Plumber",
    "electrician": "Electrician",
    "contractor": "HomeAndConstructionBusiness",
    "auto_repair": "AutoRepair",
    "salon_spa": "BeautySalon",
    "gym_fitness": "HealthClub",
    "coaching_education": "EducationalOrganization",
    "legal_accounting": "AccountingService",
    "real_estate": "RealEstateAgent",
    "tech_agency": "ProfessionalService",
    "local_service": "LocalBusiness",
    "general_business": "LocalBusiness",
}

PHONE_PATTERN = re.compile(r"^[+]?[0-9\s\-()]{7,25}$")


class NAPDetails(BaseModel):
    """Name, Address, Phone details strictly from verified owner intake."""
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=120)
    street_address: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    state_region: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str = Field(default="IN", max_length=3)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        if not v:
            return None
        clean = v.strip()
        if not PHONE_PATTERN.match(clean):
            raise ValueError(f"Phone number '{v}' has an invalid format")
        return clean

    @field_validator("street_address", "city", "state_region", "postal_code")
    @classmethod
    def check_text(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "address field")


class DayHours(BaseModel):
    """Opening hours for a specific day."""
    day: Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    opens: str | None = Field(default=None, max_length=5, description="HH:MM format in 24h, e.g. 09:00")
    closes: str | None = Field(default=None, max_length=5, description="HH:MM format in 24h, e.g. 20:00")
    is_closed: bool = False

    @field_validator("opens", "closes")
    @classmethod
    def check_time_format(cls, v: str | None) -> str | None:
        if not v:
            return None
        clean = v.strip()
        if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", clean):
            raise ValueError(f"Time '{clean}' must be in HH:MM format (24-hour clock)")
        return clean


class ServiceProductItem(BaseModel):
    """A service or product offering with factual pricing only when supplied."""
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    price: float | None = Field(default=None, ge=0.0, description="Strictly uninvented. Omit if unlisted/custom quote.")
    currency: str = Field(default="INR", max_length=3)
    category: str | None = Field(default=None, max_length=60)
    image_url: str | None = None

    @field_validator("name", "description", "category")
    @classmethod
    def check_text(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "service item")

    @field_validator("image_url")
    @classmethod
    def check_img(cls, v: str | None) -> str | None:
        return validate_safe_url(v, "service image URL")


class FAQItem(BaseModel):
    """Owner-supplied factual FAQ pair."""
    question: str = Field(min_length=3, max_length=200)
    answer: str = Field(min_length=3, max_length=2000)

    @field_validator("question", "answer")
    @classmethod
    def check_text(cls, v: str) -> str:
        return sanitize_and_validate_text(v, "FAQ text") or ""


class BusinessProfileIntake(BaseModel):
    """Guided business questionnaire intake model."""
    business_name: str = Field(min_length=2, max_length=160)
    legal_name: str | None = Field(default=None, max_length=200)
    category: str = Field(
        default="local_service",
        description="Business taxonomy category matching LocalBusiness subtypes",
    )
    tagline: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    nap: NAPDetails = Field(default_factory=NAPDetails)
    service_area_cities: list[str] = Field(default_factory=list, max_length=20)
    service_radius_km: float | None = Field(default=None, ge=0.0, le=1000.0)
    hours: list[DayHours] = Field(default_factory=list, max_length=7)
    services_or_products: list[ServiceProductItem] = Field(default_factory=list, max_length=30)
    languages: list[str] = Field(default_factory=lambda: ["en"], max_length=10)
    logo_url: str | None = None
    cover_image_url: str | None = None
    whatsapp_number: str | None = Field(default=None, max_length=30)
    google_maps_url: str | None = None
    social_links: dict[str, str] = Field(default_factory=dict)
    faqs: list[FAQItem] = Field(default_factory=list, max_length=20)
    factual_declaration: bool = Field(
        default=True,
        description="Owner certifies that all supplied details are factual and accurate",
    )
    ad_monetization_opt_in: bool = Field(
        default=False,
        description="Explicit opt-in to ad monetization. Default is strictly False.",
    )
    ai_search_discovery_opt_in: bool = Field(
        default=True,
        description="Allow generating factual entity context (e.g. llms.txt) for assistive discovery",
    )

    @field_validator("business_name", "legal_name", "tagline", "description")
    @classmethod
    def check_text(cls, v: str | None) -> str | None:
        return sanitize_and_validate_text(v, "business profile text")

    @field_validator("category")
    @classmethod
    def check_cat(cls, v: str) -> str:
        clean = v.strip().lower()
        if clean not in CATEGORY_SCHEMA_MAP:
            # Fallback to local_service if unknown
            return "local_service"
        return clean

    @field_validator("logo_url", "cover_image_url", "google_maps_url")
    @classmethod
    def check_urls(cls, v: str | None) -> str | None:
        return validate_safe_url(v, "business profile URL")

    @field_validator("whatsapp_number")
    @classmethod
    def check_wa(cls, v: str | None) -> str | None:
        if not v:
            return None
        clean = v.strip().replace(" ", "").replace("-", "")
        if not re.match(r"^\+?[0-9]{7,16}$", clean):
            raise ValueError("Invalid WhatsApp number format")
        return clean


class BusinessProfileAudit(BaseModel):
    """Completeness evaluation and anti-fabrication audit report."""
    is_complete: bool
    completeness_score: float = Field(ge=0.0, le=100.0)
    missing_fields: list[str]
    supplied_fields: list[str]
    factual_assertions_count: int
    fabricated_claims_detected: list[str]
    schema_org_type: str


class CustomDomainConfigureRequest(BaseModel):
    """Request to bind and initiate verification for a custom domain."""
    custom_domain: str = Field(min_length=3, max_length=255)

    @field_validator("custom_domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        clean = v.strip().lower()
        if "://" in clean:
            clean = clean.split("://", 1)[1]
        clean = clean.split("/", 1)[0].split(":", 1)[0].rstrip(".")
        if clean in ("localhost", "127.0.0.1", ""):
            raise ValueError("Invalid domain")
        if not re.match(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", clean):
            raise ValueError(f"Domain '{clean}' is not a valid hostname")
        return clean


class CustomDomainStatusResponse(BaseModel):
    """Status and DNS challenge details for a custom domain."""
    project_id: int
    custom_domain: str | None
    status: int
    status_label: str
    verification_token: str | None
    dns_record_name: str | None
    expected_txt_value: str | None
    verified_at: datetime | None
    instructions: str


class WebmasterToolsConfig(BaseModel):
    """Owner-authorized webmaster verification tokens."""
    google_site_verification: str | None = Field(default=None, max_length=120)
    bing_site_verification: str | None = Field(default=None, max_length=120)
    indexnow_key: str | None = Field(default=None, max_length=120)

    @field_validator("google_site_verification", "bing_site_verification", "indexnow_key")
    @classmethod
    def sanitize_tokens(cls, v: str | None) -> str | None:
        if not v:
            return None
        clean = v.strip()
        # Remove any html tags or quotes if pasted raw
        clean = re.sub(r'["<>]', "", clean)
        return clean or None


class AccessibilityAuditReport(BaseModel):
    """Accessibility and Core Web Vitals readiness audit report."""
    score: int = Field(ge=0, le=100)
    all_images_have_alt: bool
    missing_alt_images: list[str]
    heading_hierarchy_valid: bool
    heading_issues: list[str]
    contrast_ratio: float
    contrast_passed: bool
    has_responsive_viewport: bool
    lazy_loading_applied: bool
    font_display_swap: bool
    recommendations: list[str]


class GeoReadinessReport(BaseModel):
    """Evaluation of factual entity data for AI Search and Generative Engine Optimization (GEO)."""
    overall_score: int = Field(ge=0, le=100)
    nap_completeness: bool
    nap_score: int = Field(ge=0, le=100)
    structured_data_valid: bool
    schema_type: str
    factual_services_count: int
    verified_hours_present: bool
    llms_txt_available: bool
    disclaimer: str = (
        "GEO readiness measures factual entity completeness and discoverability for AI search assistants. "
        "It does not guarantee top ranking or AI citation placement."
    )

