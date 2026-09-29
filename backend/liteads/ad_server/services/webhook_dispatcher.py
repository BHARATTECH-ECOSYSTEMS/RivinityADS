"""Asynchronous webhook dispatcher with HMAC signing and retries.

Fire-and-forget: delivery runs in a background task so the main
event-tracking path is never blocked.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.webhook import Webhook

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
_DELIVERY_TIMEOUT = 10  # seconds per attempt
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
    """POST the payload to a single URL.

    Returns ``(success, status_code, error_message)``.
    """
    headers = {
        "Content-Type": "application/json",
        "X-RivinityADS-Signature": signature,
        "X-RivinityADS-Timestamp": str(int(time.time())),
        "User-Agent": "RivinityADS-Webhook/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, content=body, headers=headers)
            if 200 <= resp.status_code < 300:
                return True, resp.status_code, None
            return False, resp.status_code, f"HTTP {resp.status_code}"
    except httpx.TimeoutException:
        return False, None, "Timeout"
    except Exception as exc:
        return False, None, str(exc)[:200]


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
) -> None:
    """Fire matching webhooks in the background.

    Called from ``EventService.track_event`` after the event has been
    persisted.  This method launches fire-and-forget background tasks
    so the tracking response is not delayed.

    Args:
        session: Current DB session (used to query webhooks).
        event_type: ``"impression"``, ``"click"``, or ``"conversion"``.
        payload: Dict built by :func:`build_event_payload`.
        advertiser_id: Advertiser who owns the campaign (if known).
        publisher_id: Publisher who owns the slot (if known).
        session_factory: The async session factory for background stat
            updates.  When ``None``, stat counters are skipped.
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
        return

    from sqlalchemy import or_

    stmt = select(Webhook).where(*conditions, or_(*owner_filters))
    result = await session.execute(stmt)
    webhooks: list[Webhook] = list(result.scalars().all())

    if not webhooks:
        return

    body = json.dumps(payload, separators=(",", ":")).encode()

    for wh in webhooks:
        # Check if webhook is subscribed to this event type
        if event_type not in wh.event_list:
            continue

        sig = _sign_payload(wh.secret, body)

        # Fire and forget — don't await in the hot path
        asyncio.create_task(
            _background_deliver(wh.id, wh.url, body, sig, session_factory)
        )


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
