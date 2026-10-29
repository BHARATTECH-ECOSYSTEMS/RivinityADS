"""Marketing Consent and Subscription Endpoints (Phase 6.1C).

Provides:
- Authenticated user marketing preference management (`/api/v1/marketing/preferences`)
- Public newsletter double opt-in subscription (`/api/v1/marketing/public/subscribe`)
- Public subscription confirmation (`/api/v1/marketing/public/confirm`)
- Admin reconciliation trigger (`/api/v1/marketing/reconcile`)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.marketing_consent_service import MarketingConsentService
from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles, resolve_client_ip
from liteads.models.auth import User
from liteads.schemas.auth import MessageResponse
from liteads.schemas.marketing import (
    MarketingPreferenceUpdateRequest,
    MarketingPreferencesResponse,
    PublicConfirmRequest,
    PublicSubscribeRequest,
    PublicSubscribeResponse,
    ReconciliationResultResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()
service = MarketingConsentService()


# ── Authenticated User Preferences ────────────────────────────────────────────

@router.get("/marketing/preferences", response_model=MarketingPreferencesResponse)
async def get_preferences(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MarketingPreferencesResponse:
    """Retrieve marketing consent status across all categories for the authenticated user."""
    return await service.get_user_preferences(session=session, user=user)


@router.put("/marketing/preferences", response_model=MarketingPreferencesResponse)
async def update_preference(
    payload: MarketingPreferenceUpdateRequest,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MarketingPreferencesResponse:
    """Affirmatively opt in or revoke consent for a specific marketing category."""
    ip_address = resolve_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    try:
        await service.update_user_preference(
            session=session,
            user=user,
            category=payload.category,
            opted_in=payload.opted_in,
            ip_address=ip_address if ip_address != "unknown" else None,
            user_agent=user_agent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return await service.get_user_preferences(session=session, user=user)


# ── Public Subscription & Double Opt-In ───────────────────────────────────────

@router.post("/marketing/public/subscribe", response_model=PublicSubscribeResponse)
async def public_subscribe(
    payload: PublicSubscribeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PublicSubscribeResponse:
    """Public subscription endpoint with double opt-in verification.

    Returns generic success to prevent email-enumeration abuse.
    """
    ip_address = resolve_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    success, message = await service.public_subscribe(
        session=session,
        email=str(payload.email),
        name=payload.name,
        category=payload.category,
        ip_address=ip_address if ip_address != "unknown" else None,
        user_agent=user_agent,
    )

    return PublicSubscribeResponse(message=message, status="received")


@router.get("/marketing/public/confirm", response_model=MessageResponse)
async def public_confirm_get(
    token: str = Query(..., min_length=16, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    """Confirm double opt-in subscription via link clicked from confirmation email."""
    success, message = await service.public_confirm(session=session, token=token)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return MessageResponse(message=message)


@router.post("/marketing/public/confirm", response_model=MessageResponse)
async def public_confirm_post(
    payload: PublicConfirmRequest,
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    """Confirm double opt-in subscription via JSON payload."""
    success, message = await service.public_confirm(session=session, token=payload.token)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return MessageResponse(message=message)


# ── Unsubscribe Reconciliation ────────────────────────────────────────────────

@router.post("/marketing/reconcile", response_model=ReconciliationResultResponse)
async def reconcile_unsubscribes(
    user: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> ReconciliationResultResponse:
    """Admin-only endpoint to reconcile Listmonk-side unsubscribes and update local records."""
    return await service.reconcile_unsubscribes(session=session)

