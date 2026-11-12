"""Management API request and response schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

def _validate_https_url(v: str | None) -> str | None:
    if not v:
        return v
    parsed = urlparse(v.strip())
    if parsed.scheme.lower() != "https":
        raise ValueError("Only HTTPS URLs are allowed")
    if not parsed.netloc:
        raise ValueError("Invalid URL: missing host")
    return v.strip()


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AdvertiserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    company: str | None = None
    contact_email: str | None = None
    balance: Decimal = Field(default=Decimal("0"), ge=0)
    daily_budget: Decimal = Field(default=Decimal("0"), ge=0)


class AdvertiserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    company: str | None = None
    contact_email: str | None = None
    balance: Decimal | None = Field(default=None, ge=0)
    daily_budget: Decimal | None = Field(default=None, ge=0)
    status: int | None = Field(default=None, ge=0, le=4)


class AdvertiserResponse(ORMModel):
    id: int
    name: str
    company: str | None
    contact_email: str | None
    balance: Decimal
    daily_budget: Decimal
    status: int
    created_at: datetime
    updated_at: datetime


class CampaignCreate(BaseModel):
    advertiser_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    budget_daily: Decimal = Field(default=Decimal("0"), ge=0)
    budget_total: Decimal = Field(default=Decimal("0"), ge=0)
    bid_type: int = Field(default=1, ge=1, le=4)
    bid_amount: Decimal = Field(default=Decimal("0"), ge=0)
    freq_cap_daily: int = Field(default=10, ge=0)
    freq_cap_hourly: int = Field(default=3, ge=0)
    start_time: datetime | None = None
    end_time: datetime | None = None


class CampaignUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    budget_daily: Decimal | None = Field(default=None, ge=0)
    budget_total: Decimal | None = Field(default=None, ge=0)
    bid_type: int | None = Field(default=None, ge=1, le=4)
    bid_amount: Decimal | None = Field(default=None, ge=0)
    freq_cap_daily: int | None = Field(default=None, ge=0)
    freq_cap_hourly: int | None = Field(default=None, ge=0)
    start_time: datetime | None = None
    end_time: datetime | None = None
    autopilot: bool | None = None


class CampaignResponse(ORMModel):
    id: int
    advertiser_id: int
    name: str
    description: str | None
    budget_daily: Decimal
    budget_total: Decimal
    spent_today: Decimal
    spent_total: Decimal
    bid_type: int
    bid_amount: Decimal
    freq_cap_daily: int | None = None
    freq_cap_hourly: int | None = None
    start_time: datetime | None
    end_time: datetime | None
    status: int
    autopilot: bool
    impressions: int
    clicks: int
    conversions: int
    created_at: datetime
    updated_at: datetime


class SuggestionResponse(ORMModel):
    id: int
    campaign_id: int
    action: dict
    status: str
    dedup_key: str | None = None
    created_at: datetime
    updated_at: datetime


class SuggestionStatusPatch(BaseModel):
    status: str


class AdSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    status: int = Field(default=1, ge=0, le=5)
    daily_budget: Decimal | None = Field(default=None, ge=0)
    bid_type: int = Field(default=1, ge=1, le=4)
    bid_amount: Decimal = Field(default=Decimal("0"), ge=0)
    start_time: datetime | None = None
    end_time: datetime | None = None
    freq_cap_daily: int | None = Field(default=10, ge=0)
    freq_cap_hourly: int | None = Field(default=3, ge=0)
    delivery_mode: str = Field(default="standard", max_length=32)
    targeting_summary: dict[str, Any] | None = None


class AdSetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: int | None = Field(default=None, ge=0, le=5)
    daily_budget: Decimal | None = Field(default=None, ge=0)
    bid_type: int | None = Field(default=None, ge=1, le=4)
    bid_amount: Decimal | None = Field(default=None, ge=0)
    start_time: datetime | None = None
    end_time: datetime | None = None
    freq_cap_daily: int | None = Field(default=None, ge=0)
    freq_cap_hourly: int | None = Field(default=None, ge=0)
    delivery_mode: str | None = Field(default=None, max_length=32)
    targeting_summary: dict[str, Any] | None = None


class AdSetResponse(ORMModel):
    id: int
    campaign_id: int
    name: str
    status: int
    daily_budget: Decimal | None = None
    bid_type: int
    bid_amount: Decimal
    start_time: datetime | None = None
    end_time: datetime | None = None
    freq_cap_daily: int | None = None
    freq_cap_hourly: int | None = None
    delivery_mode: str
    targeting_summary: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class CreativeCreate(BaseModel):
    campaign_id: int = Field(gt=0)
    ad_set_id: int | None = None
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    landing_url: str
    category: str | None = None
    format: str | None = None
    creative_type: int = Field(default=1, ge=1, le=7)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    status: int = Field(default=1, ge=0, le=4)
    quality_score: int = Field(default=80, ge=0, le=100)

    @field_validator("landing_url", "image_url", "video_url")
    @classmethod
    def validate_urls(cls, v: str | None) -> str | None:
        return _validate_https_url(v)

    @model_validator(mode="after")
    def validate_format_and_dimensions(self) -> "CreativeCreate":
        from liteads.common.creative_formats import validate_and_normalize_creative_format
        cat, fmt, ctype, w, h = validate_and_normalize_creative_format(
            category=self.category,
            format_name=self.format,
            creative_type=self.creative_type,
            width=self.width,
            height=self.height,
            is_new=True,
        )
        self.category = cat
        self.format = fmt
        self.creative_type = ctype
        self.width = w
        self.height = h
        return self


class CreativeUpdate(BaseModel):
    ad_set_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    landing_url: str | None = None
    category: str | None = None
    format: str | None = None
    creative_type: int | None = Field(default=None, ge=1, le=7)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    status: int | None = Field(default=None, ge=0, le=4)
    quality_score: int | None = Field(default=None, ge=0, le=100)

    @field_validator("landing_url", "image_url", "video_url")
    @classmethod
    def validate_urls(cls, v: str | None) -> str | None:
        return _validate_https_url(v)

    @model_validator(mode="after")
    def validate_format_and_dimensions(self) -> "CreativeUpdate":
        if self.category or self.format or self.creative_type or self.width or self.height:
            from liteads.common.creative_formats import validate_and_normalize_creative_format
            cat, fmt, ctype, w, h = validate_and_normalize_creative_format(
                category=self.category,
                format_name=self.format,
                creative_type=self.creative_type,
                width=self.width,
                height=self.height,
                is_new=False,
            )
            self.category = cat
            self.format = fmt
            self.creative_type = ctype
            self.width = w
            self.height = h
        return self


class CreativeResponse(ORMModel):
    id: int
    campaign_id: int
    ad_set_id: int | None = None
    title: str
    description: str | None
    image_url: str | None
    video_url: str | None
    landing_url: str
    creative_type: int
    category: str | None = None
    format: str | None = None
    width: int
    height: int
    status: int
    moderation_status: int
    moderation_reason: str | None
    moderated_at: datetime | None
    moderated_by_id: int | None
    quality_score: int
    created_at: datetime
    updated_at: datetime


class PublisherCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str | None = None
    site_url: str | None = None
    is_sandbox: bool = Field(default=False)


class PublisherUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = None
    site_url: str | None = None
    status: int | None = Field(default=None, ge=0, le=4)
    is_sandbox: bool | None = None


class PublisherResponse(ORMModel):
    id: int
    name: str
    email: str | None
    site_url: str | None
    status: int
    is_sandbox: bool = False
    created_at: datetime
    updated_at: datetime


class PublisherPropertyCreate(BaseModel):
    publisher_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=255)
    domain: str = Field(min_length=1, max_length=255)
    status: int = Field(default=1, ge=0, le=4)


class PublisherPropertyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    domain: str | None = Field(default=None, min_length=1, max_length=255)
    status: int | None = Field(default=None, ge=0, le=4)


class PublisherPropertyResponse(ORMModel):
    id: int
    publisher_id: int
    name: str
    domain: str
    status: int
    verification_status: str = "PENDING"
    verification_method: str = "DNS_TXT"
    verification_token: str | None = None
    challenge_expires_at: datetime | None = None
    verified_at: datetime | None = None
    last_error: str | None = None
    is_platform_subdomain: bool = False
    created_at: datetime
    updated_at: datetime


class PropertyChallengeRequest(BaseModel):
    method: str = Field(default="DNS_TXT", pattern="^(DNS_TXT|HTML_META)$")


class PropertyChallengeResponse(BaseModel):
    property_id: int
    domain: str
    verification_method: str
    verification_token: str
    dns_record_name: str
    dns_record_value: str
    html_meta_tag: str
    challenge_expires_at: datetime
    instructions: str


class SlotCreate(BaseModel):
    publisher_id: int | None = Field(default=None, gt=0)
    property_id: int | None = None
    slot_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    format: int = Field(default=1, ge=1, le=4)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    status: int = Field(default=1, ge=0, le=4)
    floor_price: Decimal = Field(default=Decimal("0.0"), ge=0)
    is_chat_surface: bool = Field(default=False)


class SlotUpdate(BaseModel):
    property_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    format: int | None = Field(default=None, ge=1, le=4)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    status: int | None = Field(default=None, ge=0, le=4)
    floor_price: Decimal | None = Field(default=None, ge=0)
    is_chat_surface: bool | None = None


class SlotResponse(ORMModel):
    id: int
    publisher_id: int
    property_id: int | None = None
    slot_id: str
    name: str
    format: int
    width: int
    height: int
    status: int
    floor_price: Decimal
    is_chat_surface: bool = False
    created_at: datetime
    updated_at: datetime


class DailyStatResponse(BaseModel):
    stat_date: date
    impressions: int
    clicks: int
    conversions: int
    total_conversions: int = 0
    attributed_conversions: int = 0
    conversion_value: Decimal = Decimal("0")
    spend: Decimal | None = None
    earnings: Decimal | None = None
    cpa: Decimal | None = None
    roas: Decimal | None = None


class PublisherReportingResponse(BaseModel):
    publisher_id: int
    slot_id: int | None = None
    start_date: date
    end_date: date
    impressions: int
    clicks: int
    conversions: int
    earnings: Decimal
    ecpm: Decimal
    daily_breakdown: list[DailyStatResponse] = []


class AdvertiserReportingResponse(BaseModel):
    advertiser_id: int
    campaign_id: int | None = None
    ad_set_id: int | None = None
    start_date: date
    end_date: date
    impressions: int
    clicks: int
    conversions: int
    total_conversions: int = 0
    attributed_conversions: int = 0
    conversion_value: Decimal = Decimal("0")
    spend: Decimal
    currency: str = "INR"
    ctr: Decimal
    cvr: Decimal
    cpa: Decimal | None = None
    roas: Decimal | None = None
    daily_breakdown: list[DailyStatResponse] = []


class AdSetReportingItem(BaseModel):
    ad_set_id: int
    ad_set_name: str
    status: int
    impressions: int
    clicks: int
    conversions: int
    total_conversions: int = 0
    attributed_conversions: int = 0
    conversion_value: Decimal = Decimal("0")
    spend: Decimal
    currency: str = "INR"
    ctr: Decimal
    cvr: Decimal
    cpa: Decimal | None = None
    roas: Decimal | None = None


class AdSetReportingResponse(BaseModel):
    campaign_id: int
    start_date: date
    end_date: date
    ad_sets: list[AdSetReportingItem] = []
    total_impressions: int
    total_clicks: int
    total_conversions: int
    total_attributed_conversions: int = 0
    total_conversion_value: Decimal = Decimal("0")
    total_spend: Decimal
    currency: str = "INR"
    ctr: Decimal = Decimal("0")
    cvr: Decimal = Decimal("0")
    cpa: Decimal | None = None
    roas: Decimal | None = None



class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    is_sandbox: bool = Field(default=False)
    rate_limit_per_minute: int = Field(default=60, gt=0)
    daily_quota: int = Field(default=10000, gt=0)


class ApiKeyResponse(ORMModel):
    id: int
    publisher_id: int
    name: str
    key_prefix: str
    raw_key: str | None = None
    is_sandbox: bool = False
    rate_limit_per_minute: int = 60
    daily_quota: int = 10000
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ApiKeyUsageBreakdown(BaseModel):
    total_requests: int = 0
    admitted_requests: int = 0
    rate_limited_requests: int = 0
    quota_exceeded_requests: int = 0
    served_ads: int = 0


class ApiKeyUsageResponse(BaseModel):
    key_id: int
    publisher_id: int
    name: str
    key_prefix: str
    is_sandbox: bool = False
    rate_limit_per_minute: int = 60
    daily_quota: int = 10000
    current_minute_usage: int = 0
    rate_limit_remaining: int = 60
    rate_limit_reset_seconds: int = 60
    today_usage: int = 0
    daily_quota_remaining: int = 10000
    daily_reset_time_utc: str
    daily_reset_seconds: int
    breakdown: ApiKeyUsageBreakdown = Field(default_factory=ApiKeyUsageBreakdown)
    last_used_at: datetime | None = None
    status: int = 1
    revoked_at: datetime | None = None
    created_at: datetime


# ── Publisher API Key schemas (Phase 4.2 compatibility) ─────────────────────────

class PublisherApiKeyCreate(BaseModel):
    """Input for creating a new publisher API key."""

    publisher_id: int | None = Field(default=None, gt=0, description="Publisher who owns this key")
    label: str | None = Field(
        default=None, min_length=1, max_length=128, description="Human-readable label"
    )
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_sandbox: bool = Field(default=False)
    rate_limit_per_minute: int = Field(default=60, gt=0)
    daily_quota: int = Field(default=10000, gt=0)

    def __init__(self, **data):
        if "label" in data and "name" not in data:
            data["name"] = data["label"]
        elif "name" in data and "label" not in data:
            data["label"] = data["name"]
        super().__init__(**data)


# ── Advertiser API Key schemas (Phase 1) ───────────────────────────────────────

class AdvertiserApiKeyCreate(BaseModel):
    """Input for creating a new advertiser API key."""

    name: str = Field(..., min_length=1, max_length=255, description="Human-readable label for key")
    campaign_id: int | None = Field(default=None, gt=0, description="Optional campaign scope")
    is_sandbox: bool = Field(default=False)
    rate_limit_per_minute: int = Field(default=60, gt=0)
    daily_quota: int = Field(default=10000, gt=0)


class AdvertiserApiKeyResponse(ORMModel):
    """Read-only view of an advertiser API key."""

    id: int
    advertiser_id: int
    campaign_id: int | None = None
    name: str
    key_prefix: str
    is_sandbox: bool = False
    rate_limit_per_minute: int = 60
    daily_quota: int = 10000
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class AdvertiserApiKeyCreateResponse(BaseModel):
    """Returned exactly once when a key is created; contains raw key and raw signing secret."""

    id: int
    advertiser_id: int
    campaign_id: int | None = None
    name: str
    key_prefix: str
    is_sandbox: bool = False
    rate_limit_per_minute: int = 60
    daily_quota: int = 10000
    created_at: datetime
    raw_key: str = Field(
        description="The full API key — copy it now, it cannot be retrieved again."
    )
    raw_secret: str = Field(
        description="The HMAC signing secret — copy it now, it cannot be retrieved again."
    )


class PublisherApiKeyResponse(ORMModel):
    """Read-only view of a publisher API key."""

    id: int
    publisher_id: int
    name: str | None = None
    label: str | None = None
    key_prefix: str
    is_sandbox: bool = False
    rate_limit_per_minute: int = 60
    daily_quota: int = 10000
    last_used_at: datetime | None = None
    status: int = 1
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    def __init__(self, **data):
        if "name" in data and "label" not in data:
            data["label"] = data["name"]
        elif "label" in data and "name" not in data:
            data["name"] = data["label"]
        super().__init__(**data)


class PublisherApiKeyCreateResponse(BaseModel):
    """Returned exactly once when a key is created; contains the raw key."""

    id: int
    publisher_id: int
    name: str | None = None
    label: str | None = None
    key_prefix: str
    is_sandbox: bool = False
    rate_limit_per_minute: int = 60
    daily_quota: int = 10000
    status: int = 1
    created_at: datetime
    raw_key: str = Field(
        description="The full API key — copy it now, it cannot be retrieved again."
    )

    def __init__(self, **data):
        if "name" in data and "label" not in data:
            data["label"] = data["name"]
        elif "label" in data and "name" not in data:
            data["name"] = data["label"]
        super().__init__(**data)


class CampaignAssignmentCreate(BaseModel):
    campaign_id: int = Field(gt=0)
    publisher_id: int = Field(gt=0)
    slot_id: str | None = None


class CampaignAssignmentResponse(ORMModel):
    id: int
    campaign_id: int
    publisher_id: int
    slot_id: str | None
    created_at: datetime
    updated_at: datetime

class PublisherAssignmentResponse(BaseModel):
    id: int
    campaign_id: int
    campaign_name: str
    advertiser_name: str
    slot_id: str | None = None
    slot_name: str | None = None
    status: str


# ── Phase 6 WU3 — Placement Health schemas ─────────────────────────────────────

class FormatBenchmarkSchema(BaseModel):
    format: int
    format_name: str | None = None
    eligible_slots_count: int
    median_ctr: float | None = None
    median_ecpm: float | None = None


class PlacementHealthItemSchema(BaseModel):
    slot_id: str
    slot_name: str
    format: int
    format_name: str
    impressions: int
    clicks: int
    conversions: int
    earnings: float
    ctr: float
    ecpm: float
    verdict: str
    recommendation: str
    benchmark: FormatBenchmarkSchema


class PlacementHealthResponse(BaseModel):
    publisher_id: int
    window_days: int
    start_date: str
    end_date: str
    total_placements: int
    placements: list[PlacementHealthItemSchema]
    benchmarks: dict[str, FormatBenchmarkSchema] = {}
