"""
API request schemas.
"""

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

_HEX_64_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")


class DeviceInfo(BaseModel):
    """Device information."""

    os: str = Field(..., description="Operating system (android/ios/web)")
    os_version: str | None = Field(None, description="OS version")
    model: str | None = Field(None, description="Device model")
    brand: str | None = Field(None, description="Device brand")
    screen_width: int | None = Field(None, description="Screen width in pixels")
    screen_height: int | None = Field(None, description="Screen height in pixels")
    language: str | None = Field(None, description="Device language")


class GeoInfo(BaseModel):
    """Geographic information."""

    ip: str | None = Field(None, description="IP address")
    country: str | None = Field(None, description="Country code (ISO 3166-1 alpha-2)")
    region: str | None = Field(None, description="Region/Province")
    state: str | None = Field(None, description="Alias for region/state")
    city: str | None = Field(None, description="City name")
    pincode: str | None = Field(None, description="Postal / ZIP code")
    latitude: float | None = Field(None, description="Latitude")
    longitude: float | None = Field(None, description="Longitude")
    location_source: str | None = Field(None, description="Signal provenance (e.g. 'device_gps', 'coarse_network', 'ip')")
    accuracy_meters: float | None = Field(None, ge=0, description="Estimated accuracy radius in meters")
    consent: bool = Field(False, description="Explicit user consent indicator for geolocation processing")

    @model_validator(mode="after")
    def sync_state_region(self) -> "GeoInfo":
        if self.state and not self.region:
            self.region = self.state
        elif self.region and not self.state:
            self.state = self.region
        return self


class UserContextRequest(BaseModel):
    """User context for ad requests, adhering to Phase 5 privacy standards."""

    id: str | None = Field(
        None,
        description="Privacy-safe SHA-256 hex identifier (64 hex characters)",
    )
    sector_context: list[str] | None = Field(
        None,
        description="Content/conversation sectors (e.g. ['developer', 'fintech'])",
    )
    geo: GeoInfo | None = Field(
        None,
        description="Geographic information (optional, falls back to IP resolution)",
    )

    @field_validator("id")
    @classmethod
    def validate_id_sha256(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("user.id must be a string")
        v_clean = v.strip().lower()
        if not _HEX_64_REGEX.match(v_clean):
            raise ValueError(
                "user.id must be a 64-character hexadecimal SHA-256 hash"
            )
        return v_clean


class ContextInfo(BaseModel):
    """Request context information."""

    app_id: str | None = Field(None, description="App identifier")
    app_name: str | None = Field(None, description="App name")
    app_version: str | None = Field(None, description="App version")
    app_bundle: str | None = Field(None, description="App bundle ID")
    carrier: str | None = Field(None, description="Mobile carrier")
    network: str | None = Field(None, description="Network type (wifi/4g/5g)")
    connection_type: str | None = Field(None, description="Connection type")
    sectors: list[str] | None = Field(
        None, description="Audience/content sectors from SECTORS taxonomy (e.g. ['tech', 'finance'])"
    )


class UserFeatures(BaseModel):
    """User feature information for ML prediction."""

    age: int | None = Field(None, ge=0, le=120, description="User age")
    gender: str | None = Field(None, description="User gender (male/female/unknown)")
    interests: list[str] | None = Field(None, description="User interests")
    app_categories: list[str] | None = Field(None, description="Installed app categories")
    custom: dict[str, Any] | None = Field(None, description="Custom features")


class AdRequest(BaseModel):
    """Ad request schema.

    Supports both classic publisher placements (slot_id) and server-to-server
    AI-platform requests with nested user context (id, sector_context, geo).
    For AI-platform callers set format=2 (native) so that ``title``,
    ``description``, and ``landing_url`` are returned.
    """

    slot_id: str = Field(..., description="Ad slot identifier")
    user_id: str | None = Field(None, description="User identifier (IMEI/IDFA/custom)")
    user: UserContextRequest | None = Field(
        None,
        description="Privacy-safe user context (Phase 5 contract)",
    )
    device: DeviceInfo | None = Field(
        None,
        description="Device information (optional for S2S calls)",
    )
    geo: GeoInfo | None = Field(None, description="Geographic information")
    context: ContextInfo | None = Field(None, description="Context information")
    user_features: UserFeatures | None = Field(None, description="User features for ML")
    num_ads: int = Field(1, ge=1, le=10, description="Number of ads requested")
    format: int | None = Field(
        None,
        ge=1,
        le=4,
        description=(
            "Requested creative_type to filter candidates: "
            "1=banner, 2=native, 3=video, 4=interstitial. "
            "Defaults to the slot's configured format. "
            "Falls back to unfiltered candidates if none match."
        ),
    )

    @field_validator("user_id")
    @classmethod
    def validate_top_level_user_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v_clean = v.strip()
        # Direct PII protection: reject raw emails, phone numbers, or multi-word names
        if "@" in v_clean or v_clean.startswith("+") or " " in v_clean:
            raise ValueError("user_id must not contain direct personal identifiers (email, phone, name)")
        # If 64-char hex, normalize to lowercase
        if _HEX_64_REGEX.match(v_clean):
            return v_clean.lower()
        return v_clean

    @model_validator(mode="after")
    def sync_nested_user_fields(self) -> "AdRequest":
        if self.user:
            if self.user.id and not self.user_id:
                self.user_id = self.user.id
            if self.user.geo and not self.geo:
                self.geo = self.user.geo
            if self.user.sector_context:
                if not self.context:
                    self.context = ContextInfo(sectors=list(self.user.sector_context))
                elif not self.context.sectors:
                    self.context.sectors = list(self.user.sector_context)
                else:
                    for s in self.user.sector_context:
                        if s not in self.context.sectors:
                            self.context.sectors.append(s)
        if not self.device:
            self.device = DeviceInfo(os="web")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "slot_id": "ai_chat_sidebar",
                "num_ads": 1,
                "user": {
                    "id": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    "sector_context": ["developer"],
                    "geo": {
                        "country": "IN",
                        "state": "Maharashtra",
                        "city": "Mumbai",
                        "pincode": "400001",
                    },
                },
                "format": 2,
            }
        }
    }


class EventRequest(BaseModel):
    """Event tracking request schema."""

    request_id: str = Field(..., description="Original ad request ID")
    ad_id: str = Field(..., description="Ad identifier")
    event_type: str = Field(
        ..., description="Event type (impression/click/conversion)"
    )
    timestamp: int | None = Field(None, description="Event timestamp (Unix epoch)")
    user_id: str | None = Field(None, description="User identifier")
    slot_id: str | None = Field(None, description="Slot identifier")
    sig: str | None = Field(None, description="Cryptographic tracking signature")
    extra: dict[str, Any] | None = Field(None, description="Extra event data")

    model_config = {
        "json_schema_extra": {
            "example": {
                "request_id": "req_abc123",
                "ad_id": "ad_456",
                "event_type": "click",
                "timestamp": 1700000000,
                "user_id": "user_123456",
            }
        }
    }


class TextClassifyRequest(BaseModel):
    """Request body for ``POST /api/v1/classify``.

    Classifies free-form text into ad-relevant content sectors.
    Useful for AI platforms doing context-aware ad decisioning server-side.
    """

    text: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Text content to classify into ad-relevant sectors",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": (
                    "Breaking news: The Federal Reserve raised interest rates by 25 basis points, "
                    "sending tech stocks lower while bond yields surged."
                )
            }
        }
    }


class TextClassifyResponse(BaseModel):
    """Response for ``POST /api/v1/classify``."""

    sectors: list[str] = Field(
        description="Ordered list of matching sector labels (most relevant first)"
    )
    scores: dict[str, float] = Field(
        description="Sector → confidence score mapping (0.0 – 1.0)"
    )


class ConversionEventRequest(BaseModel):
    """Conversion tracking request schema for client pixel and S2S postbacks."""

    click_token: str | None = Field(None, description="HMAC-signed click attribution token")
    request_id: str | None = Field(None, description="Click request_id or reference ID")
    campaign_id: int | None = Field(None, description="Target campaign identifier")
    creative_id: int | None = Field(None, description="Target creative identifier")
    order_id: str | None = Field(None, max_length=128, description="Advertiser transaction or order ID for deduplication")
    conversion_value: float | None = Field(None, ge=0, description="Monetary value of the conversion")
    currency: str = Field("INR", min_length=3, max_length=3, description="ISO-4217 3-letter currency code")
    timestamp: int | None = Field(None, description="Conversion event timestamp (Unix epoch in UTC)")
    user_id: str | None = Field(None, description="User identifier (privacy-safe SHA-256)")
    signature: str | None = Field(None, description="HMAC-SHA256 signature for authenticated S2S postback")
    is_test: bool = Field(False, description="Test mode flag (excluded from billing and production metrics)")
    extra: dict[str, Any] | None = Field(None, description="Optional metadata")

