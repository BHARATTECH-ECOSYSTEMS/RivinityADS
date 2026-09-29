"""Pydantic schemas for webhook CRUD endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class WebhookCreate(BaseModel):
    """Create a new webhook subscription."""

    url: str = Field(
        ...,
        min_length=8,
        max_length=2048,
        description="HTTPS endpoint that will receive event POST payloads",
    )
    events: list[str] = Field(
        default=["impression", "click", "conversion"],
        description="Event types to subscribe to",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "url": "https://example.com/webhook/ads",
                "events": ["click", "conversion"],
            }
        }
    }


class WebhookUpdate(BaseModel):
    """Update an existing webhook."""

    url: str | None = Field(None, min_length=8, max_length=2048)
    events: list[str] | None = None
    is_active: bool | None = None


class WebhookResponse(BaseModel):
    """Webhook returned from list/get endpoints (secret is NOT exposed)."""

    id: int
    owner_type: str
    owner_id: int
    url: str
    events: list[str]
    is_active: bool
    deliveries_ok: int
    deliveries_failed: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WebhookCreateResponse(WebhookResponse):
    """Response for webhook creation — includes the signing secret ONCE."""

    secret: str = Field(
        ...,
        description=(
            "HMAC-SHA256 signing secret. Store this securely — "
            "it is shown only on creation."
        ),
    )


class WebhookTestResponse(BaseModel):
    """Result of a webhook test ping."""

    delivered: bool
    status_code: int | None = None
    error: str | None = None
