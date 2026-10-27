"""Notification API schemas for requests, responses, and pagination."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

from liteads.models.notification import (
    NotificationCategory,
    NotificationChannel,
    NotificationSeverity,
    NotificationType,
)


class NotificationResponse(BaseModel):
    """Safe, explicit representation of an in-app notification.
    
    Excludes internal delivery tracking tokens, worker leases, and dedupe keys.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    type: str
    channel: str
    severity: str
    title: str
    message: str
    link: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    """Paginated notification list response with metadata."""
    items: list[NotificationResponse]
    total: int = Field(ge=0, description="Total matching notifications")
    page: int = Field(ge=1, description="Current page number (1-based)")
    page_size: int = Field(ge=1, le=100, description="Items per page")
    total_pages: int = Field(ge=0, description="Total pages available")
    unread_count: int = Field(ge=0, description="Count of unread notifications for this user")


class UnreadCountResponse(BaseModel):
    """Response containing unread notification count for authenticated user."""
    unread_count: int = Field(ge=0, description="Number of unread notifications")


class MarkAllReadResponse(BaseModel):
    """Response for marking all unread notifications as read."""
    updated_count: int = Field(ge=0, description="Number of notifications marked as read")


class NotificationPreferenceItem(BaseModel):
    """Explicit item schema for a channel, category, and enabled/disabled state."""
    model_config = ConfigDict(from_attributes=True)

    channel: NotificationChannel
    category: NotificationCategory
    is_enabled: bool


class NotificationPreferenceResponse(BaseModel):
    """Safe, explicit response schema for a single notification preference.
    
    Excludes internal identifiers (id, user_id) and database timestamps.
    """
    model_config = ConfigDict(from_attributes=True)

    channel: str
    category: str
    is_enabled: bool


class NotificationPreferenceListResponse(BaseModel):
    """Explicit response schema containing the user's notification preferences."""
    preferences: list[NotificationPreferenceResponse]


class NotificationPreferenceUpdateRequest(BaseModel):
    """Request payload containing a list of notification preference updates."""
    preferences: list[NotificationPreferenceItem]

