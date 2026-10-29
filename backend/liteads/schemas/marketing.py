"""Pydantic schemas for marketing consent and public subscription flows (Phase 6.1C)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class MarketingConsentItem(BaseModel):
    """Individual marketing consent status item."""
    model_config = ConfigDict(from_attributes=True)

    category: str
    status: str
    opted_in: bool
    consented_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    policy_version: str = "2026.1"


class MarketingPreferencesResponse(BaseModel):
    """User-facing marketing preferences response."""
    email: str
    items: list[MarketingConsentItem]


class MarketingPreferenceUpdateRequest(BaseModel):
    """Request to opt in or opt out of a specific marketing purpose."""
    category: str = Field(..., description="Consent category (e.g. product_newsletter, advertiser_education)")
    opted_in: bool = Field(..., description="True to opt in, False to revoke consent")


class PublicSubscribeRequest(BaseModel):
    """Public subscription request for newsletters/updates."""
    email: EmailStr
    name: Optional[str] = Field(None, max_length=100)
    category: str = Field(default="product_newsletter", max_length=64)


class PublicSubscribeResponse(BaseModel):
    """Generic public response preventing email enumeration."""
    message: str
    status: str = "received"


class PublicConfirmRequest(BaseModel):
    """Confirmation request for double opt-in."""
    token: str = Field(..., min_length=16, max_length=128)


class ReconciliationResultResponse(BaseModel):
    """Result summary of Listmonk unsubscribe reconciliation."""
    checked_count: int
    updated_opt_outs: int
    errors: list[str] = Field(default_factory=list)

