"""Webhook CRUD endpoints.

Advertisers and publishers can register webhooks to receive real-time
HTTP POST notifications when ad events (impression, click, conversion)
occur on their campaigns or inventory.

Each webhook payload is signed with ``HMAC-SHA256(secret, body)`` —
the signing secret is returned **once** on creation.
"""

from datetime import datetime, timedelta, timezone
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.webhook_dispatcher import test_webhook
from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles, verified_user
from liteads.common.ssrf import SSRFValidationError, validate_destination_url
from liteads.models import User
from liteads.models.webhook import Webhook, WebhookDelivery
from liteads.schemas.webhook import (
    WebhookCreate,
    WebhookCreateResponse,
    WebhookDeliveryDetailResponse,
    WebhookDeliveryResponse,
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
    try:
        validate_destination_url(payload.url, resolve_dns=True)
    except SSRFValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
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
        try:
            validate_destination_url(payload.url, resolve_dns=True)
        except SSRFValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )
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

    now = datetime.now(timezone.utc)
    payload_dict = {
        "event": "test",
        "message": "RivinityADS webhook verification ping",
        "timestamp": int(time.time()),
    }
    payload_str = json.dumps(payload_dict, separators=(",", ":"))

    delivery = WebhookDelivery(
        webhook_id=wh.id,
        owner_type=owner_type,
        owner_id=owner_id,
        event_id=None,
        event_type="test",
        payload=payload_str,
        status="processing",
        attempts=1,
        max_attempts=1,
        lease_token="test-ping",
        lease_expires_at=now + timedelta(seconds=60),
        next_attempt_at=now,
        last_attempt_at=now,
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)

    ok, status_code, error = await test_webhook(wh.url, wh.secret)

    delivery.status = "delivered" if ok else "failed"
    delivery.delivered_at = datetime.now(timezone.utc) if ok else None
    delivery.last_http_status = status_code
    delivery.last_error = error
    delivery.lease_token = None
    delivery.lease_expires_at = None

    if ok:
        wh.deliveries_ok += 1
    else:
        wh.deliveries_failed += 1

    await session.commit()

    return WebhookTestResponse(
        delivered=ok,
        status_code=status_code,
        error=error,
        delivery_id=delivery.id,
    )


# ── DELIVERIES (VISIBILITY & RETRY) ──────────────────────────────────────────

@router.get(
    "/webhooks/{webhook_id}/deliveries",
    response_model=list[WebhookDeliveryResponse],
    summary="List delivery jobs for a webhook",
    dependencies=[Depends(require_roles("advertiser", "publisher"))],
)
async def list_webhook_deliveries(
    webhook_id: int,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[WebhookDeliveryResponse]:
    """Retrieve durable delivery history for a webhook owned by the caller."""
    owner_type, owner_id = _resolve_owner(user)
    wh = await session.get(Webhook, webhook_id)
    if wh is None or wh.owner_type != owner_type or wh.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Webhook not found")

    stmt = (
        select(WebhookDelivery)
        .where(
            WebhookDelivery.webhook_id == webhook_id,
            WebhookDelivery.owner_type == owner_type,
            WebhookDelivery.owner_id == owner_id,
        )
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    deliveries = result.scalars().all()
    return [WebhookDeliveryResponse.model_validate(d) for d in deliveries]


@router.get(
    "/webhooks/{webhook_id}/deliveries/{delivery_id}",
    response_model=WebhookDeliveryDetailResponse,
    summary="Get delivery job detail",
    dependencies=[Depends(require_roles("advertiser", "publisher"))],
)
async def get_webhook_delivery_detail(
    webhook_id: int,
    delivery_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WebhookDeliveryDetailResponse:
    """Get full details of a specific delivery job, including payload snapshot."""
    owner_type, owner_id = _resolve_owner(user)
    wh = await session.get(Webhook, webhook_id)
    if wh is None or wh.owner_type != owner_type or wh.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Webhook not found")

    delivery = await session.get(WebhookDelivery, delivery_id)
    if (
        delivery is None
        or delivery.webhook_id != webhook_id
        or delivery.owner_type != owner_type
        or delivery.owner_id != owner_id
    ):
        raise HTTPException(status_code=404, detail="Delivery job not found")

    return WebhookDeliveryDetailResponse.model_validate(delivery)


@router.post(
    "/webhooks/{webhook_id}/deliveries/{delivery_id}/retry",
    response_model=WebhookDeliveryResponse,
    summary="Manually retry a failed delivery job",
    dependencies=[Depends(require_roles("advertiser", "publisher"))],
)
async def retry_webhook_delivery(
    webhook_id: int,
    delivery_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WebhookDeliveryResponse:
    """Reset a delivery job back to 'pending' for immediate worker processing."""
    owner_type, owner_id = _resolve_owner(user)
    wh = await session.get(Webhook, webhook_id)
    if wh is None or wh.owner_type != owner_type or wh.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Webhook not found")

    # Atomic conditional update: only reset if status != 'processing'
    now = datetime.now(timezone.utc)
    stmt = (
        update(WebhookDelivery)
        .where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.webhook_id == webhook_id,
            WebhookDelivery.owner_type == owner_type,
            WebhookDelivery.owner_id == owner_id,
            WebhookDelivery.status != "processing",
        )
        .values(
            status="pending",
            attempts=0,
            next_attempt_at=now,
            lease_token=None,
            lease_expires_at=None,
            last_error=None,
        )
    )
    result = await session.execute(stmt)

    if result.rowcount == 0:
        # Check why update matched 0 rows
        check_stmt = select(WebhookDelivery.status).where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.webhook_id == webhook_id,
            WebhookDelivery.owner_type == owner_type,
            WebhookDelivery.owner_id == owner_id,
        )
        check_res = await session.execute(check_stmt)
        current_status = check_res.scalar_one_or_none()
        if current_status is None:
            raise HTTPException(status_code=404, detail="Delivery job not found")
        if current_status == "processing":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Delivery is currently in flight and cannot be retried",
            )
        raise HTTPException(status_code=404, detail="Delivery job not found")

    await session.commit()
    updated_delivery = await session.get(WebhookDelivery, delivery_id)
    if updated_delivery:
        await session.refresh(updated_delivery)
    return WebhookDeliveryResponse.model_validate(updated_delivery)
