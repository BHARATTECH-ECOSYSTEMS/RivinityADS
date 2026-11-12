"""
Pydantic schemas for channel adapters and connector APIs (Phase 5).
"""

from datetime import date, datetime
from typing import Any
from pydantic import BaseModel, Field


class ChannelCapabilities(BaseModel):
    """Surfaces provider capabilities, supported features, and explicit limitations."""

    provider: str
    connector_type: str = "advertiser_management"  # "advertiser_management" | "publisher_delivery"
    supports_inventory_injection: bool = False  # True ONLY for approved publisher platforms
    supports_account_discovery: bool = True
    supports_campaign_management: bool = True
    supports_reporting_sync: bool = True
    supported_objectives: list[str] = Field(default_factory=list)
    supported_bidding_strategies: list[str] = Field(default_factory=list)
    supported_targeting: list[str] = Field(default_factory=list)
    supported_creative_types: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    auth_requirements: list[str] = Field(default_factory=list)


class ExternalAccount(BaseModel):
    """External ad platform account or workspace."""

    account_id: str
    name: str
    currency: str = "USD"
    timezone: str = "UTC"
    organization_id: str | None = None
    status: str = "ACTIVE"


class ExternalCampaign(BaseModel):
    """Synchronized campaign record from external platform."""

    external_id: str
    name: str
    status: str  # "ACTIVE", "PAUSED", "ARCHIVED", "DRAFT"
    objective: str | None = None
    daily_budget: float | None = None
    total_spend: float | None = None
    currency: str = "USD"
    local_campaign_id: int | None = None


class CampaignCreateParams(BaseModel):
    """Parameters for creating an external campaign."""

    name: str
    objective: str = "TRAFFIC"
    daily_budget: float = Field(gt=0.0)
    bid_amount: float | None = None
    targeting: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False  # Deliberate user confirmation required


class CampaignPreviewResult(BaseModel):
    """Preview of external campaign creation before committing spend."""

    name: str
    objective: str
    daily_budget: float
    currency: str = "USD"
    bid_amount: float | None = None
    targeting_summary: dict[str, Any] = Field(default_factory=dict)
    estimated_daily_impressions: tuple[int, int] = (1000, 5000)
    estimated_daily_clicks: tuple[int, int] = (20, 100)
    disclaimer: str
    requires_confirmation: bool = True


class ExternalReportRow(BaseModel):
    """Reporting metric row for external ad platform."""

    date: str
    external_campaign_id: str
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    spend: float = 0.0
    currency: str = "USD"


# API Request/Response Schemas

class ConnectAdvertiserRequest(BaseModel):
    """Request to connect an advertiser's authorized external ad platform account."""

    provider: str = Field(..., description="Provider identifier (e.g. 'openai', 'google_ads')")
    name: str = Field(..., description="Friendly label for this connection")
    api_key: str = Field(..., description="External API Key or OAuth access token")
    account_id: str = Field(..., description="External Ad Account ID")
    organization_id: str | None = Field(default=None, description="Optional Organization ID")
    additional_credentials: dict[str, Any] = Field(default_factory=dict)


class ConnectAdvertiserResponse(BaseModel):
    """Response returned upon connecting an account. Raw secrets are NEVER returned."""

    id: int
    provider: str
    name: str
    external_account_id: str
    credentials_prefix: str
    status: str
    capabilities: ChannelCapabilities
    created_at: datetime


class ConnectionDetailsResponse(BaseModel):
    """Connection details without raw secret material."""

    id: int
    provider: str
    name: str
    external_account_id: str
    external_organization_id: str | None = None
    credentials_prefix: str
    status: str
    last_sync_at: datetime | None = None
    last_error: str | None = None
    capabilities: ChannelCapabilities | None = None
    created_at: datetime


class CampaignStatusUpdateRequest(BaseModel):
    """Request to update external campaign delivery status."""

    status: str = Field(..., pattern="^(ACTIVE|PAUSED|ARCHIVED)$")


class PublisherContractCreateRequest(BaseModel):
    """Request to register a signed agreement for AI publisher inventory."""

    platform_name: str
    signed_agreement_id: str
    approved_surfaces: list[str]
    policy_terms_hash: str
    contract_valid_until: datetime


class PublisherContractResponse(BaseModel):
    """Verified publisher contract record."""

    id: int
    publisher_id: int
    platform_name: str
    signed_agreement_id: str
    agreement_status: str
    approved_surfaces: list[str]
    contract_signed_at: datetime
    contract_valid_until: datetime
    is_valid: bool

