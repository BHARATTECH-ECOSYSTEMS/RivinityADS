"""Asynchronous webhook dispatcher with HMAC signing and retries.

Fire-and-forget: delivery runs in a background task so the main
event-tracking path is never blocked.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from typing import Any, Optional
import uuid

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.ssrf import (
    SSRFConnectionError,
    SSRFError,
    SSRFSafeTransport,
    SSRFValidationError,
    validate_destination_url,
)
from liteads.models.webhook import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
_DELIVERY_TIMEOUT = 10  # seconds total per attempt
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 5.0
_MAX_RESPONSE_BYTES = 65536  # 64 KB response limit
_MAX_REDIRECTS = 3
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 5, 15]  # seconds between retries


def _sign_payload(secret: str, body: bytes) -> str:
    """Compute HMAC-SHA256 of the raw JSON body using the webhook secret."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _deliver(
    url: str,
    body: bytes,
    signature: str,
    *,
    timeout: int = _DELIVERY_TIMEOUT,
) -> tuple[bool, int | None, str | None]:
    """POST the payload to a single URL with strict SSRF protections and redirect safety.

    Returns ``(success, status_code, error_message)``.
    """
    # 1. Pre-validation of the initial destination URL
    try:
        validate_destination_url(url, resolve_dns=True)
    except SSRFValidationError as exc:
        logger.warning(
            "Webhook destination rejected by SSRF validation",
            extra={"url": url, "reason": str(exc)},
        )
        return False, None, f"Destination rejected: {exc}"

    headers = {
        "Content-Type": "application/json",
        "X-RivinityADS-Signature": signature,
        "X-RivinityADS-Timestamp": str(int(time.time())),
        "User-Agent": "RivinityADS-Webhook/1.0",
    }

    curr_url = url
    curr_body: bytes | None = body
    curr_method = "POST"

    transport = SSRFSafeTransport()
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=10.0)
    client_timeout = httpx.Timeout(float(timeout), connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT)

    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=client_timeout,
            limits=limits,
            follow_redirects=False,
        ) as client:
            for hop in range(_MAX_REDIRECTS + 1):
                async with client.stream(
                    curr_method,
                    curr_url,
                    content=curr_body,
                    headers=headers,
                ) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        if hop >= _MAX_REDIRECTS:
                            return False, resp.status_code, "Too many redirects"
                        loc = resp.headers.get("Location")
                        if not loc:
                            return False, resp.status_code, "Redirect missing Location header"
                        next_url = urllib.parse.urljoin(curr_url, loc)
                        try:
                            validate_destination_url(next_url, resolve_dns=True)
                        except SSRFValidationError as exc:
                            logger.warning(
                                "Webhook redirect blocked by SSRF validation",
                                extra={"url": next_url, "reason": str(exc)},
                            )
                            return False, resp.status_code, f"Redirect destination rejected: {exc}"

                        curr_url = next_url
                        if resp.status_code == 303:
                            curr_method = "GET"
                            curr_body = None
                        continue

                    # Bounded response consumption
                    bytes_read = 0
                    async for chunk in resp.aiter_bytes():
                        bytes_read += len(chunk)
                        if bytes_read > _MAX_RESPONSE_BYTES:
                            return False, resp.status_code, "Response payload exceeded maximum allowed size"

                    if 200 <= resp.status_code < 300:
                        return True, resp.status_code, None
                    return False, resp.status_code, f"HTTP {resp.status_code}"
    except (SSRFError, SSRFConnectionError) as exc:
        return False, None, f"Destination rejected: {exc}"
    except httpx.TimeoutException:
        return False, None, "Timeout"
    except httpx.ConnectError as exc:
        if "prohibited" in str(exc).lower() or "aborted" in str(exc).lower():
            return False, None, "Destination rejected: prohibited address"
        return False, None, "Connection failed"
    except Exception as exc:
        logger.error("Outbound webhook delivery unexpected error", extra={"url": curr_url, "error": str(exc)})
        return False, None, "Delivery failed"


async def _deliver_with_retries(
    url: str,
    body: bytes,
    signature: str,
) -> tuple[bool, int | None, str | None]:
    """Attempt delivery with exponential back-off."""
    for attempt in range(_MAX_RETRIES + 1):
        ok, status, err = await _deliver(url, body, signature)
        if ok:
            return ok, status, err
        if err and "Destination rejected" in err:
            # Permanent security failure — do not retry
            return False, status, err
        if attempt < _MAX_RETRIES:
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            logger.warning(
                "Webhook delivery failed, retrying",
                extra={"url": url, "attempt": attempt + 1, "delay": delay, "error": err},
            )
            await asyncio.sleep(delay)
    return False, status, err


async def _update_delivery_stats(
    webhook_id: int,
    success: bool,
    session_factory: Any,
) -> None:
    """Increment OK or FAILED counter on the webhook row."""
    try:
        async with session_factory() as session:
            col = Webhook.deliveries_ok if success else Webhook.deliveries_failed
            await session.execute(
                update(Webhook)
                .where(Webhook.id == webhook_id)
                .values({col.key: col + 1})
            )
            await session.commit()
    except Exception as exc:
        logger.error(f"Failed to update webhook delivery stats: {exc}")


# ── Public API ───────────────────────────────────────────────────────────────

def build_event_payload(
    event_type: str,
    request_id: str,
    ad_id: str,
    campaign_id: int | None,
    creative_id: int | None,
    user_id: str | None,
    cost: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical JSON payload sent to webhook subscribers."""
    return {
        "event": event_type,
        "request_id": request_id,
        "ad_id": ad_id,
        "campaign_id": campaign_id,
        "creative_id": creative_id,
        "user_id": user_id,
        "cost": float(cost),
        "timestamp": int(time.time()),
        "extra": extra or {},
    }


async def dispatch_webhooks(
    session: AsyncSession,
    event_type: str,
    payload: dict[str, Any],
    advertiser_id: int | None = None,
    publisher_id: int | None = None,
    session_factory: Any = None,
) -> list[WebhookDelivery]:
    """Persist matching webhook delivery jobs to the durable outbox table.

    Called from ``EventService.track_event`` within the active transaction.
    Jobs are written to ``webhook_deliveries`` with status ``pending`` and
    committed atomically with the source event. The background
    :class:`WebhookOutboxWorker` claims and delivers the jobs.

    Args:
        session: Current DB session (participates in event transaction).
        event_type: ``"impression"``, ``"click"``, or ``"conversion"``.
        payload: Dict built by :func:`build_event_payload`.
        advertiser_id: Advertiser who owns the campaign (if known).
        publisher_id: Publisher who owns the slot (if known).
        session_factory: Unused in Phase 4B outbox, kept for backward compatibility.

    Returns:
        List of created :class:`WebhookDelivery` job instances.
    """
    # Collect matching webhooks for both advertiser and publisher owners
    conditions = [Webhook.is_active.is_(True)]
    owner_filters = []
    if advertiser_id is not None:
        owner_filters.append(
            (Webhook.owner_type == "advertiser") & (Webhook.owner_id == advertiser_id)
        )
    if publisher_id is not None:
        owner_filters.append(
            (Webhook.owner_type == "publisher") & (Webhook.owner_id == publisher_id)
        )
    if not owner_filters:
        return []

    stmt = select(Webhook).where(*conditions, or_(*owner_filters))
    result = await session.execute(stmt)
    webhooks: list[Webhook] = list(result.scalars().all())

    if not webhooks:
        return []

    payload_str = json.dumps(payload, separators=(",", ":"))
    event_id = str(payload.get("request_id")) if payload.get("request_id") else None
    now = datetime.now(timezone.utc)

    created_deliveries: list[WebhookDelivery] = []

    for wh in webhooks:
        # Check if webhook is subscribed to this event type
        if event_type not in wh.event_list:
            continue

        # Prevent duplicate jobs for the same (webhook_id, event_id, event_type)
        if event_id:
            exists_stmt = select(WebhookDelivery.id).where(
                WebhookDelivery.webhook_id == wh.id,
                WebhookDelivery.event_id == event_id,
                WebhookDelivery.event_type == event_type,
            )
            exists_res = await session.execute(exists_stmt)
            if exists_res.scalar_one_or_none() is not None:
                continue

        delivery = WebhookDelivery(
            webhook_id=wh.id,
            owner_type=wh.owner_type,
            owner_id=wh.owner_id,
            event_id=event_id,
            event_type=event_type,
            payload=payload_str,
            status="pending",
            attempts=0,
            max_attempts=5,
            next_attempt_at=now,
        )
        session.add(delivery)
        created_deliveries.append(delivery)

    if created_deliveries:
        await session.flush()

    return created_deliveries


async def _background_deliver(
    webhook_id: int,
    url: str,
    body: bytes,
    signature: str,
    session_factory: Any,
) -> None:
    """Background coroutine that delivers + updates stats."""
    try:
        ok, status, err = await _deliver_with_retries(url, body, signature)
        if ok:
            logger.info("Webhook delivered", extra={"webhook_id": webhook_id, "url": url})
        else:
            logger.warning(
                "Webhook delivery failed after retries",
                extra={"webhook_id": webhook_id, "url": url, "error": err},
            )
        if session_factory is not None:
            await _update_delivery_stats(webhook_id, ok, session_factory)
    except Exception as exc:
        logger.error(f"Background webhook delivery error: {exc}")


async def test_webhook(url: str, secret: str) -> tuple[bool, int | None, str | None]:
    """Send a test ping to verify the webhook endpoint is reachable."""
    payload = {
        "event": "test",
        "message": "RivinityADS webhook verification ping",
        "timestamp": int(time.time()),
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = _sign_payload(secret, body)
    return await _deliver(url, body, sig)
