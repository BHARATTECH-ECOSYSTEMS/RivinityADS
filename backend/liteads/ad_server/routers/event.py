"""
Event tracking endpoints.
"""

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.event_service import EventService
from liteads.ad_server.services.replay_guard import acquire_replay_guard
from liteads.common.database import get_session
from liteads.common.logger import get_logger, log_context
from liteads.common.tracking_sig import verify_tracking
from liteads.common.utils import current_timestamp
from liteads.schemas.request import EventRequest
from liteads.schemas.response import EventResponse

logger = get_logger(__name__)
router = APIRouter()

VALID_EVENT_TYPES = {"impression", "imp", "click", "clk", "conversion", "conv"}


def get_event_service(session: AsyncSession = Depends(get_session)) -> EventService:
    """Dependency to get event service."""
    return EventService(session)


@router.post("/track", response_model=EventResponse)
async def track_event(
    event: EventRequest,
    event_service: EventService = Depends(get_event_service),
) -> EventResponse:
    """
    Track an ad event (impression, click, or conversion).

    Events are used for:
    - Billing calculation
    - Performance metrics
    - ML model training data
    """
    log_context(
        request_id=event.request_id,
        ad_id=event.ad_id,
        event_type=event.event_type,
    )

    logger.info("Event received")

    # 1. Validate event type upfront before signature or replay guard
    if not event.event_type or event.event_type.lower() not in VALID_EVENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unknown event type: {event.event_type}",
        )

    # 2. Verify tracking signature
    if not event.sig:
        raise HTTPException(status_code=400, detail="Missing signature")
    verify_tracking(event.request_id, event.ad_id, event.slot_id or "", event.sig)

    # 3. Acquire replay guard reservation
    reservation = await acquire_replay_guard(event.event_type, event.request_id, event.ad_id)

    # Prepare extra data, preserving caller extra without mutation
    extra = dict(event.extra) if event.extra else {}
    if event.slot_id:
        if "slot_id" in extra and extra["slot_id"] != event.slot_id:
            logger.warning(
                "Conflicting slot_id in event.extra; overriding with signed root slot_id",
                root_slot_id=event.slot_id,
                extra_slot_id=extra["slot_id"],
            )
        extra["slot_id"] = event.slot_id

    # 4. Record the event with transactional retry safety
    try:
        success = await event_service.track_event(
            request_id=event.request_id,
            ad_id=event.ad_id,
            event_type=event.event_type,
            user_id=event.user_id,
            timestamp=event.timestamp or current_timestamp(),
            extra=extra if extra else None,
            seq=reservation.seq,
            raise_on_duplicate=True,
        )
        if success:
            await reservation.finalize()
        else:
            await reservation.release()
    except HTTPException as he:
        if he.status_code == 429:
            try:
                await reservation.finalize()
            except Exception:
                pass
        else:
            await reservation.release()
        raise
    except Exception:
        await reservation.release()
        raise

    return EventResponse(
        success=success,
        message="Event recorded" if success else "Failed to record event",
    )


@router.get("/track")
async def track_event_get(
    type: str = Query(..., alias="type", description="Event type"),
    req: str = Query(..., description="Request ID"),
    ad: str = Query(..., description="Ad ID"),
    sig: str = Query(..., description="Tracking signature"),
    slot: str | None = Query(None, description="Publisher slot ID"),
    sandbox: int | None = Query(None, description="Sandbox indicator flag"),
    event_service: EventService = Depends(get_event_service),
) -> EventResponse:
    """
    Track event via GET request (for pixel tracking).

    This endpoint is used for tracking URLs embedded in ads.
    """
    log_context(
        request_id=req,
        ad_id=ad,
        event_type=type,
    )

    logger.info("Pixel event received")

    # 1. Validate event type upfront before signature or replay guard
    if not type or type.lower() not in VALID_EVENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unknown event type: {type}",
        )

    # 2. Verify tracking signature
    verify_tracking(req, ad, slot or "", sig)

    # 3. Acquire replay guard reservation
    reservation = await acquire_replay_guard(type, req, ad)

    extra_data: dict[str, Any] = {}
    if slot:
        extra_data["slot_id"] = slot
    if sandbox:
        extra_data["is_sandbox"] = True

    # 4. Record the event with transactional retry safety
    try:
        success = await event_service.track_event(
            request_id=req,
            ad_id=ad,
            event_type=type,
            user_id=None,
            timestamp=current_timestamp(),
            extra=extra_data if extra_data else None,
            seq=reservation.seq,
            raise_on_duplicate=True,
        )

        if success:
            await reservation.finalize()
        else:
            await reservation.release()
    except HTTPException as he:
        if he.status_code == 429:
            try:
                await reservation.finalize()
            except Exception:
                pass
        else:
            await reservation.release()
        raise
    except Exception:
        await reservation.release()
        raise

    return EventResponse(
        success=success,
        message="Event recorded" if success else "Failed to record event",
    )

