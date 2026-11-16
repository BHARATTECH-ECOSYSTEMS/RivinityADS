"""
API response schemas.
"""

from typing import Any

from pydantic import BaseModel, Field


class CreativeResponse(BaseModel):
    """Creative response schema."""

    title: str | None = Field(None, description="Ad title")
    description: str | None = Field(None, description="Ad description")
    image_url: str | None = Field(None, description="Image URL")
    video_url: str | None = Field(None, description="Video URL")
    asset_url: str | None = Field(None, description="Direct URL to media asset (image or video)")
    landing_url: str = Field(..., description="Landing page URL")
    width: int | None = Field(None, description="Creative width")
    height: int | None = Field(None, description="Creative height")
    creative_type: str = Field(..., description="Creative type (banner/native/video/interstitial/display_card/label_ad/element_ad)")
    format: str | None = Field(None, description="Normalized format label (e.g. 'banner_300x250', 'native_card')")
    cta_text: str | None = Field("Learn More", description="Call to action text")


class TrackingUrls(BaseModel):
    """Tracking URLs for ad events."""

    impression_url: str = Field(..., description="URL to call on impression")
    click_url: str = Field(..., description="URL to call on click")
    conversion_url: str | None = Field(None, description="URL to call on conversion")
    click_token: str | None = Field(None, description="HMAC-signed click attribution token")


class AdResponse(BaseModel):
    """Single ad response schema."""

    ad_id: str = Field(..., description="Ad identifier")
    campaign_id: int = Field(..., description="Campaign identifier")
    creative_id: int = Field(..., description="Creative identifier")
    ad_set_id: int | None = Field(None, description="Ad set identifier")
    advertiser_id: int | None = Field(None, description="Advertiser identifier")
    advertiser_name: str | None = Field(None, description="Advertiser display name")
    creative: CreativeResponse = Field(..., description="Creative content")
    tracking: TrackingUrls = Field(..., description="Tracking URLs")
    sponsored: bool = Field(True, description="Strict boolean indicating this is sponsored content")
    disclosure_label: str = Field("Sponsored", description="Mandatory label for ad disclosure")
    review_status: str = Field("APPROVED", description="Ad creative moderation review status")
    metadata: dict[str, Any] | None = Field(None, description="Additional metadata")


class AdListResponse(BaseModel):
    """Ad list response schema."""

    request_id: str = Field(default="", description="Request identifier")
    ads: list[AdResponse] = Field(default_factory=list, description="List of ads")
    count: int = Field(default=0, description="Number of ads returned")
    total: int = Field(default=0, description="Total ads count")

    model_config = {
        "json_schema_extra": {
            "example": {
                "request_id": "req_abc123",
                "ads": [
                    {
                        "ad_id": "ad_456",
                        "campaign_id": 100,
                        "creative_id": 200,
                        "creative": {
                            "title": "Amazing Product",
                            "description": "Buy now!",
                            "image_url": "https://example.com/ad.jpg",
                            "landing_url": "https://example.com/landing",
                            "creative_type": "banner",
                        },
                        "tracking": {
                            "impression_url": "https://api.liteads.com/track/imp?id=xxx",
                            "click_url": "https://api.liteads.com/track/click?id=xxx",
                        },
                        "sponsored": True,
                        "disclosure_label": "Sponsored",
                    }
                ],
                "count": 1,
            }
        }
    }


class EventResponse(BaseModel):
    """Event tracking response schema."""

    success: bool = Field(..., description="Whether the event was recorded")
    message: str | None = Field(None, description="Optional message")


class ConversionEventResponse(BaseModel):
    """Conversion event response schema."""

    success: bool = Field(..., description="Whether conversion event was processed")
    event_id: int | None = Field(None, description="Recorded conversion ad_event id")
    is_attributed: bool = Field(False, description="Whether conversion was attributed to an ad interaction")
    attribution_reason: str | None = Field(None, description="Explanation of attribution determination")
    campaign_id: int | None = Field(None, description="Campaign associated with the conversion")
    creative_id: int | None = Field(None, description="Creative associated with the conversion")
    conversion_value: float | None = Field(None, description="Recorded monetary value")
    currency: str | None = Field(None, description="ISO currency code")
    is_test: bool = Field(False, description="Whether processed in test mode")
    is_duplicate: bool = Field(False, description="Whether this conversion was an idempotent retry")
    message: str | None = Field(None, description="Informational message")



class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str = Field(..., description="Service status")
    version: str = Field(..., description="Service version")
    database: bool = Field(..., description="Database connection status")
    redis: bool = Field(..., description="Redis connection status")


class DependencyStatus(BaseModel):
    """Sanitized individual dependency status."""

    status: str = Field(..., description="'ok' or 'unreachable'")
    latency_ms: float | None = Field(None, description="Check round-trip latency in milliseconds")


class DependenciesHealthResponse(BaseModel):
    """Detailed dependencies health response."""

    status: str = Field(..., description="Overall health: 'healthy', 'degraded', or 'unhealthy'")
    dependencies: dict[str, DependencyStatus] = Field(..., description="Status breakdown per dependency")


class OutboxQueueMetrics(BaseModel):
    """Metrics for an individual outbox queue."""

    pending_count: int = Field(0, description="Total pending jobs awaiting delivery")
    delayed_count: int = Field(0, description="Pending jobs currently delayed in retry backoff")
    ready_count: int = Field(0, description="Pending jobs ready for immediate delivery")
    processing_count: int = Field(0, description="Jobs currently leased by active workers")
    failed_count: int = Field(0, description="Permanently failed / dead-lettered jobs")
    oldest_pending_age_seconds: float = Field(0.0, description="Age in seconds of the oldest pending job")


class OutboxHealthResponse(BaseModel):
    """Outbox and background processing health response."""

    status: str = Field(..., description="Overall outbox status: 'healthy' or 'degraded'")
    queues: dict[str, OutboxQueueMetrics] = Field(..., description="Metrics per outbox queue")


class ErrorResponse(BaseModel):
    """Error response schema."""

    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    details: dict[str, Any] | None = Field(None, description="Error details")
    request_id: str | None = Field(None, description="Request identifier")

    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "validation_error",
                "message": "Invalid request parameters",
                "details": {"slot_id": "This field is required"},
                "request_id": "req_abc123",
            }
        }
    }
