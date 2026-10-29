"""Pydantic schemas for tenant-safe Email Marketing API (Phase 6.1D)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ── Lists ─────────────────────────────────────────────────────────────────────

class CreateListRequest(BaseModel):
    """Request payload to create a new mailing list."""
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=255)
    type: str = Field(default="private", pattern="^(public|private)$")
    optin: str = Field(default="single", pattern="^(single|double)$")


class ListResponse(BaseModel):
    """Mailing list response payload."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_type: str
    tenant_id: int
    name: str
    description: Optional[str] = None
    type: str
    optin: str
    contact_count: int = 0
    created_at: datetime


# ── Contacts & CSV Import ─────────────────────────────────────────────────────

class CreateContactRequest(BaseModel):
    """Request payload to add a single contact to an owned list."""
    email: EmailStr
    name: Optional[str] = Field(None, max_length=128)
    consent_source: str = Field(
        ...,
        min_length=2,
        max_length=64,
        description="Declared lawful permission source (e.g., 'customer_opt_in', 'newsletter_signup')",
    )


class ContactResponse(BaseModel):
    """Contact response payload."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    list_id: int
    email: str
    name: Optional[str] = None
    status: str
    consent_source: str
    consent_at: Optional[datetime] = None
    created_at: datetime


class ImportContactsRequest(BaseModel):
    """Request payload for CSV import of permission-based contacts."""
    csv_content: str = Field(..., min_length=5, description="Raw CSV text with 'email' (and optional 'name') header")
    permission_source: str = Field(
        ...,
        min_length=2,
        max_length=64,
        description="Declared lawful permission source for the imported batch",
    )


class ImportContactsResponse(BaseModel):
    """Import summary report."""
    total_rows: int
    valid_count: int
    duplicate_count: int
    error_count: int
    errors: list[str] = Field(default_factory=list)


# ── Campaigns ─────────────────────────────────────────────────────────────────

class CreateCampaignRequest(BaseModel):
    """Request payload to create a draft campaign."""
    list_id: int
    name: str = Field(..., min_length=1, max_length=128)
    subject: str = Field(..., min_length=1, max_length=255)
    body: str = Field(..., min_length=1)
    content_type: str = Field(default="html", pattern="^(html|markdown|plain)$")


class UpdateCampaignRequest(BaseModel):
    """Request payload to update a draft campaign."""
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    subject: Optional[str] = Field(None, min_length=1, max_length=255)
    body: Optional[str] = Field(None, min_length=1)
    content_type: Optional[str] = Field(None, pattern="^(html|markdown|plain)$")


class CampaignResponse(BaseModel):
    """Campaign response payload."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    list_id: int
    name: str
    subject: str
    body: str
    content_type: str
    status: str
    total_recipients: int
    sent_at: Optional[datetime] = None
    created_at: datetime


class CampaignPreviewResponse(BaseModel):
    """Preview of campaign content and audience size."""
    id: int
    name: str
    subject: str
    rendered_body: str
    eligible_recipient_count: int
    has_unsubscribe_notice: bool


class TestSendCampaignRequest(BaseModel):
    """Request payload to test-send campaign to allowlisted email addresses."""
    test_emails: list[EmailStr] = Field(..., min_length=1, max_length=5)


class LaunchCampaignRequest(BaseModel):
    """Explicit confirmation payload to launch a campaign."""
    confirmed: bool = Field(..., description="Must explicitly be True to confirm launch")


class CampaignAnalyticsResponse(BaseModel):
    """Campaign performance and analytics payload."""
    campaign_id: int
    status: str
    total_recipients: int
    views: int = 0
    clicks: int = 0
    unsubscribes: int = 0
    bounces: int = 0

