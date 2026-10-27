"""Pydantic schemas for webhook CRUD endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from liteads.common.ssrf import SSRFValidationError, validate_destination_url


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

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        try:
            validate_destination_url(v, resolve_dns=True)
        except SSRFValidationError as exc:
            raise ValueError(str(exc)) from exc
        return v

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

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                validate_destination_url(v, resolve_dns=True)
            except SSRFValidationError as exc:
                raise ValueError(str(exc)) from exc
        return v


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
    delivery_id: int | None = None


class WebhookDeliveryResponse(BaseModel):
    """Summary of a durable webhook delivery attempt."""

    id: int
    webhook_id: int
    owner_type: str
    owner_id: int
    event_id: str | None = None
    event_type: str
    status: str
    attempts: int
    max_attempts: int
    next_attempt_at: datetime
    last_attempt_at: datetime | None = None
    last_http_status: int | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WebhookDeliveryDetailResponse(WebhookDeliveryResponse):
    """Detailed response for a webhook delivery, including payload snapshot."""

    payload: str

