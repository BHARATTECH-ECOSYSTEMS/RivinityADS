"""Management API request and response schemas."""

from datetime import date, datetime
from decimal import Decimal

from urllib.parse import urlparse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

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
    created_at: datetime
    updated_at: datetime


class SuggestionStatusPatch(BaseModel):
    status: str



class CreativeCreate(BaseModel):
    campaign_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    landing_url: str
    creative_type: int = Field(default=1, ge=1, le=4)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    status: int = Field(default=1, ge=0, le=4)
    quality_score: int = Field(default=80, ge=0, le=100)

    @field_validator("landing_url", "image_url", "video_url")
    @classmethod
    def validate_urls(cls, v: str | None) -> str | None:
        return _validate_https_url(v)


class CreativeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    landing_url: str | None = None
    creative_type: int | None = Field(default=None, ge=1, le=4)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    status: int | None = Field(default=None, ge=0, le=4)
    quality_score: int | None = Field(default=None, ge=0, le=100)

    @field_validator("landing_url", "image_url", "video_url")
    @classmethod
    def validate_urls(cls, v: str | None) -> str | None:
        return _validate_https_url(v)


class CreativeResponse(ORMModel):
    id: int
    campaign_id: int
    title: str
    description: str | None
    image_url: str | None
    video_url: str | None
    landing_url: str
    creative_type: int
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


class PublisherUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = None
    site_url: str | None = None
    status: int | None = Field(default=None, ge=0, le=4)


class PublisherResponse(ORMModel):
    id: int
    name: str
    email: str | None
    site_url: str | None
    status: int
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
    created_at: datetime
    updated_at: datetime


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


class SlotUpdate(BaseModel):
    property_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    format: int | None = Field(default=None, ge=1, le=4)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    status: int | None = Field(default=None, ge=0, le=4)
    floor_price: Decimal | None = Field(default=None, ge=0)


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
    created_at: datetime
    updated_at: datetime


class DailyStatResponse(BaseModel):
    stat_date: date
    impressions: int
    clicks: int
    conversions: int
    spend: Decimal | None = None
    earnings: Decimal | None = None


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
    start_date: date
    end_date: date
    impressions: int
    clicks: int
    conversions: int
    spend: Decimal
    ctr: Decimal
    cvr: Decimal
    daily_breakdown: list[DailyStatResponse] = []


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ApiKeyResponse(ORMModel):
    id: int
    publisher_id: int
    name: str
    key_prefix: str
    raw_key: str | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


# ── Publisher API Key schemas (Phase 4.2 compatibility) ─────────────────────────

class PublisherApiKeyCreate(BaseModel):
    """Input for creating a new publisher API key."""

    publisher_id: int | None = Field(default=None, gt=0, description="Publisher who owns this key")
    label: str | None = Field(
        default=None, min_length=1, max_length=128, description="Human-readable label"
    )
    name: str | None = Field(default=None, min_length=1, max_length=255)

    def __init__(self, **data):
        if "label" in data and "name" not in data:
            data["name"] = data["label"]
        elif "name" in data and "label" not in data:
            data["label"] = data["name"]
        super().__init__(**data)


class PublisherApiKeyResponse(ORMModel):
    """Read-only view of a publisher API key."""

    id: int
    publisher_id: int
    name: str | None = None
    label: str | None = None
    key_prefix: str
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
