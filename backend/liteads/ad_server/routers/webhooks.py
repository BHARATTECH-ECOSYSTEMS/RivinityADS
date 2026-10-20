"""Webhook CRUD endpoints.

Advertisers and publishers can register webhooks to receive real-time
HTTP POST notifications when ad events (impression, click, conversion)
occur on their campaigns or inventory.

Each webhook payload is signed with ``HMAC-SHA256(secret, body)`` —
the signing secret is returned **once** on creation.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.webhook_dispatcher import test_webhook
from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles, verified_user
from liteads.models import User
from liteads.models.webhook import Webhook
from liteads.schemas.webhook import (
    WebhookCreate,
    WebhookCreateResponse,
    WebhookResponse,
    WebhookTestResponse,
    WebhookUpdate,
)

router = APIRouter(dependencies=[Depends(verified_user)])

_VALID_EVENTS = {"impression", "click", "conversion"}


def _resolve_owner(user: User) -> tuple[str, int]:
    """Determine webhook owner from the authenticated user."""
    if user.role == "advertiser":
        if not user.advertiser_id:
            raise HTTPException(
                status_code=400,
                detail="Advertiser account not linked",
            )
        return "advertiser", user.advertiser_id
    if user.role == "publisher":
        if not user.publisher_id:
            raise HTTPException(
                status_code=400,
                detail="Publisher account not linked",
            )
        return "publisher", user.publisher_id
    # Admin: cannot own webhooks directly
    raise HTTPException(
        status_code=403,
        detail="Admins cannot register webhooks; use an advertiser or publisher account",
    )


def _validate_events(events: list[str]) -> str:
    """Validate and normalise event list to a comma-separated string."""
    normalised = [e.strip().lower() for e in events if e.strip()]
    invalid = set(normalised) - _VALID_EVENTS
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid event types: {', '.join(sorted(invalid))}. "
            f"Allowed: {', '.join(sorted(_VALID_EVENTS))}",
        )
    if not normalised:
        raise HTTPException(status_code=422, detail="At least one event type required")
    return ",".join(sorted(set(normalised)))


# ── LIST ─────────────────────────────────────────────────────────────────────

@router.get(
    "/webhooks",
    response_model=list[WebhookResponse],
    summary="List my webhooks",
    dependencies=[Depends(require_roles("advertiser", "publisher"))],
)
async def list_webhooks(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[WebhookResponse]:
    """Return all webhooks belonging to the authenticated user."""
    owner_type, owner_id = _resolve_owner(user)
    result = await session.execute(
        select(Webhook).where(
            Webhook.owner_type == owner_type,
            Webhook.owner_id == owner_id,
        ).order_by(Webhook.created_at.desc())
    )
    webhooks = result.scalars().all()
    return [
        WebhookResponse(
            id=wh.id,
            owner_type=wh.owner_type,
            owner_id=wh.owner_id,
            url=wh.url,
            events=wh.event_list,
            is_active=wh.is_active,
            deliveries_ok=wh.deliveries_ok,
            deliveries_failed=wh.deliveries_failed,
            created_at=wh.created_at,
            updated_at=wh.updated_at,
        )
        for wh in webhooks
    ]


# ── CREATE ───────────────────────────────────────────────────────────────────

@router.post(
    "/webhooks",
    response_model=WebhookCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new webhook",
    dependencies=[Depends(require_roles("advertiser", "publisher"))],
)
async def create_webhook(
    payload: WebhookCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WebhookCreateResponse:
    """Register a webhook endpoint.

    The response contains a ``secret`` field — **store it securely**.
    It is used to verify authenticity of incoming payloads via
    ``X-RivinityADS-Signature`` and is **never shown again**.
    """
    owner_type, owner_id = _resolve_owner(user)
    events_str = _validate_events(payload.events)
    signing_secret = secrets.token_hex(32)  # 64-char hex

    webhook = Webhook(
        owner_type=owner_type,
        owner_id=owner_id,
        url=payload.url,
        events=events_str,
        secret=signing_secret,
        is_active=True,
    )
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)

    return WebhookCreateResponse(
        id=webhook.id,
        owner_type=webhook.owner_type,
        owner_id=webhook.owner_id,
        url=webhook.url,
        events=webhook.event_list,
        is_active=webhook.is_active,
        deliveries_ok=webhook.deliveries_ok,
        deliveries_failed=webhook.deliveries_failed,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
        secret=signing_secret,
    )


# ── UPDATE ───────────────────────────────────────────────────────────────────

@router.patch(
    "/webhooks/{webhook_id}",
    response_model=WebhookResponse,
    summary="Update a webhook",
    dependencies=[Depends(require_roles("advertiser", "publisher"))],
)
async def update_webhook(
    webhook_id: int,
    payload: WebhookUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WebhookResponse:
    owner_type, owner_id = _resolve_owner(user)
    wh = await session.get(Webhook, webhook_id)
    if wh is None or wh.owner_type != owner_type or wh.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Webhook not found")

    if payload.url is not None:
        wh.url = payload.url
    if payload.events is not None:
        wh.events = _validate_events(payload.events)
    if payload.is_active is not None:
        wh.is_active = payload.is_active

    await session.commit()
    await session.refresh(wh)

    return WebhookResponse(
        id=wh.id,
        owner_type=wh.owner_type,
        owner_id=wh.owner_id,
        url=wh.url,
        events=wh.event_list,
        is_active=wh.is_active,
        deliveries_ok=wh.deliveries_ok,
        deliveries_failed=wh.deliveries_failed,
        created_at=wh.created_at,
        updated_at=wh.updated_at,
    )


# ── DELETE ───────────────────────────────────────────────────────────────────

@router.delete(
    "/webhooks/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook",
    dependencies=[Depends(require_roles("advertiser", "publisher"))],
)
async def delete_webhook(
    webhook_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    owner_type, owner_id = _resolve_owner(user)
    wh = await session.get(Webhook, webhook_id)
    if wh is None or wh.owner_type != owner_type or wh.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await session.delete(wh)
    await session.commit()


# ── TEST ─────────────────────────────────────────────────────────────────────

@router.post(
    "/webhooks/{webhook_id}/test",
    response_model=WebhookTestResponse,
    summary="Send a test ping to a webhook endpoint",
    dependencies=[Depends(require_roles("advertiser", "publisher"))],
)
async def test_webhook_endpoint(
    webhook_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WebhookTestResponse:
    """Deliver a test payload to verify the webhook URL is reachable."""
    owner_type, owner_id = _resolve_owner(user)
    wh = await session.get(Webhook, webhook_id)
    if wh is None or wh.owner_type != owner_type or wh.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Webhook not found")

    ok, status_code, error = await test_webhook(wh.url, wh.secret)
    return WebhookTestResponse(delivered=ok, status_code=status_code, error=error)
