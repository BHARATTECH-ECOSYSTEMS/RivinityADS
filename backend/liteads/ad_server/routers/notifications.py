"""Notification retrieval and management API endpoints (Phase 6, Work Unit 6.3).

Provides authenticated, tenant-isolated endpoints for listing in-app notifications,
fetching unread counts, and marking individual or all notifications as read.
"""

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.notification_service import NotificationService
from liteads.common.database import get_session
from liteads.common.security import verified_user
from liteads.models.auth import User
from liteads.models.notification import NotificationCategory
from liteads.schemas.notification import (
    MarkAllReadResponse,
    NotificationListResponse,
    NotificationPreferenceItem,
    NotificationPreferenceListResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdateRequest,
    NotificationResponse,
    UnreadCountResponse,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "/preferences",
    response_model=NotificationPreferenceListResponse,
    summary="Get notification preferences for the current user",
)
@router.get(
    "/preferences/",
    response_model=NotificationPreferenceListResponse,
    include_in_schema=False,
)
async def get_preferences(
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> NotificationPreferenceListResponse:
    """Retrieve complete notification preferences for the authenticated user.

    Returns explicit channel/category preferences. Missing records evaluate to
    system defaults (is_enabled=True) without persisting database records during reads.
    Internal fields (id, user_id, timestamps) are strictly excluded.
    """
    prefs = await NotificationService.get_preferences(session=session, user_id=user.id)
    return NotificationPreferenceListResponse(preferences=prefs)


@router.put(
    "/preferences",
    response_model=NotificationPreferenceListResponse,
    summary="Update notification preferences for the current user",
)
@router.put(
    "/preferences/",
    response_model=NotificationPreferenceListResponse,
    include_in_schema=False,
)
async def update_preferences(
    payload: list[NotificationPreferenceItem] | NotificationPreferenceUpdateRequest = Body(...),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> NotificationPreferenceListResponse:
    """Update notification preferences for the authenticated user.

    Accepts either a JSON array of preference items or an object with a 'preferences' list.
    Enforces uniqueness constraint on (user_id, channel, category) and rejects duplicates
    in a single request with HTTP 422. Disabling mandatory security notifications is rejected
    with HTTP 422. Updates are applied atomically within a single database transaction.
    """
    items = payload.preferences if isinstance(payload, NotificationPreferenceUpdateRequest) else payload

    try:
        updated = await NotificationService.update_preferences(
            session=session,
            user_id=user.id,
            items=items,
        )
        await session.commit()
        return NotificationPreferenceListResponse(preferences=updated)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception:
        await session.rollback()
        raise



@router.get(
    "",
    response_model=NotificationListResponse,
    summary="List notifications for the current authenticated user",
)
@router.get(
    "/",
    response_model=NotificationListResponse,
    include_in_schema=False,
)
async def list_notifications(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    unread_only: bool = Query(False, description="Filter for unread notifications only"),
    category: Optional[NotificationCategory] = Query(None, description="Optional category filter"),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> NotificationListResponse:
    """Retrieve paginated notifications strictly scoped to the authenticated user.
    
    Notifications are ordered newest-first, with ID as a deterministic tie-breaker.
    """
    cat_val = category.value if category else None
    items, total = await NotificationService.list_notifications(
        session=session,
        user_id=user.id,
        page=page,
        page_size=page_size,
        unread_only=unread_only,
        category=cat_val,
    )
    unread_count = await NotificationService.get_unread_count(session=session, user_id=user.id)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        unread_count=unread_count,
    )


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Get unread notification count for the current user",
)
async def get_unread_count(
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> UnreadCountResponse:
    """Return the exact count of unread notifications belonging to the authenticated user."""
    count = await NotificationService.get_unread_count(session=session, user_id=user.id)
    return UnreadCountResponse(unread_count=count)


@router.patch(
    "/read-all",
    response_model=MarkAllReadResponse,
    summary="Mark all unread notifications as read",
)
async def mark_all_read(
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> MarkAllReadResponse:
    """Mark all unread notifications for the authenticated user as read in an atomic database update."""
    updated = await NotificationService.mark_all_as_read(session=session, user_id=user.id)
    await session.commit()
    return MarkAllReadResponse(updated_count=updated)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark a single notification as read",
)
async def mark_notification_read(
    notification_id: int = Path(..., ge=1, description="ID of the notification to mark read"),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> NotificationResponse:
    """Mark a specific notification as read, enforcing strict ownership isolation.
    
    Returns 404 if the notification does not exist or belongs to another user.
    Repeated calls are idempotent and return the notification without modifying read_at.
    """
    notif = await NotificationService.mark_as_read(
        session=session,
        notification_id=notification_id,
        user_id=user.id,
    )
    if not notif:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    await session.commit()
    return NotificationResponse.model_validate(notif)
