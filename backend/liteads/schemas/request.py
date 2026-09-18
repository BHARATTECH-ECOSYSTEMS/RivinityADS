"""
API request schemas.
"""

from typing import Any

from pydantic import BaseModel, Field


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
    city: str | None = Field(None, description="City name")
    pincode: str | None = Field(None, description="Postal / ZIP code")
    latitude: float | None = Field(None, description="Latitude")
    longitude: float | None = Field(None, description="Longitude")


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
    AI-platform requests. For AI-platform callers set format=2 (native) so that
    ``title``, ``description``, and ``landing_url`` are returned; the client is
    responsible for rendering them as chat-inline, sidebar, or conversation-starter
    blocks — those are delivery templates, not separate creative types.
    """

    slot_id: str = Field(..., description="Ad slot identifier")
    user_id: str | None = Field(None, description="User identifier (IMEI/IDFA/custom)")
    device: DeviceInfo = Field(..., description="Device information")
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

    model_config = {
        "json_schema_extra": {
            "example": {
                "slot_id": "banner_home_top",
                "user_id": "user_123456",
                "device": {
                    "os": "android",
                    "os_version": "13.0",
                    "model": "Pixel 7",
                    "brand": "Google",
                    "screen_width": 1080,
                    "screen_height": 2400,
                },
                "geo": {
                    "ip": "1.2.3.4",
                    "country": "CN",
                    "city": "shanghai",
                },
                "context": {
                    "app_id": "com.example.app",
                    "app_version": "1.0.0",
                    "network": "wifi",
                    "sectors": ["tech", "finance"],
                },
                "num_ads": 1,
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
