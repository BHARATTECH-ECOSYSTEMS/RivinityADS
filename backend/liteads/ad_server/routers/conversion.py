"""
Conversion tracking and diagnostics router for Phase 11.

Endpoints:
- POST /api/v1/event/conversion : Client conversion event tracking
- POST /api/v1/conversion/postback : Server-to-server authenticated postback
- GET /api/v1/conversion/pixel : 1x1 transparent GIF pixel for conversions
- POST /api/v1/conversion/diagnostics/test-event : Test mode simulation
- GET /api/v1/conversion/diagnostics/timeline : Interaction and conversion event timeline
- GET /api/v1/conversion/diagnostics/summary : Campaign attribution summary
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, and_, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.conversion_service import ConversionService
from liteads.common.database import get_session
from liteads.common.logger import get_logger, log_context
from liteads.common.security import current_user, require_roles, resolve_client_ip, verified_user
from liteads.models.ad import AdEvent, Campaign, Creative
from liteads.models.base import EventType
from liteads.models.auth import User
from liteads.schemas.request import ConversionEventRequest
from liteads.schemas.response import ConversionEventResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["conversions"])

# 1x1 transparent GIF bytes
GIF_1X1_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
    b"\x00\x00\x02\x02D\x01\x00;"
)


async def _assert_campaign_ownership(user: User, campaign_id: int, session: AsyncSession) -> Campaign:
    """Ensure user is admin or the advertiser who owns this campaign."""
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if user.role != "admin" and campaign.advertiser_id != user.advertiser_id:
        raise HTTPException(status_code=403, detail="Campaign belongs to another advertiser")
    return campaign


@router.post("/event/conversion", response_model=ConversionEventResponse)
async def track_conversion_event(
    req: ConversionEventRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ConversionEventResponse:
    """
    Client conversion tracking endpoint.
    Invoked by landing pages or advertiser JavaScript SDK via click token or order ID.
    """
    client_ip = resolve_client_ip(request)
    log_context(
        request_id=req.request_id,
        campaign_id=req.campaign_id,
        order_id=req.order_id,
    )

    service = ConversionService(session)
    result = await service.track_conversion(
        click_token=req.click_token,
        request_id=req.request_id,
        campaign_id=req.campaign_id,
        creative_id=req.creative_id,
        order_id=req.order_id,
        conversion_value=req.conversion_value,
        currency=req.currency,
        timestamp=req.timestamp,
        user_id=req.user_id,
        signature=req.signature,
        is_test=req.is_test,
        ip_address=client_ip,
        extra=req.extra,
    )

    return ConversionEventResponse(
        success=result.success,
        event_id=result.event_id,
        is_attributed=result.is_attributed,
        attribution_reason=result.attribution_reason,
        campaign_id=result.campaign_id,
        creative_id=result.creative_id,
        conversion_value=float(result.conversion_value) if result.conversion_value is not None else None,
        currency=result.currency,
        is_test=result.is_test,
        is_duplicate=result.is_duplicate,
        message=result.message,
    )


@router.post("/conversion/postback", response_model=ConversionEventResponse)
async def track_conversion_postback(
    req: ConversionEventRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ConversionEventResponse:
    """
    Server-to-server (S2S) authenticated conversion postback endpoint.
    Requires HMAC-signed conversion payloads bound to advertiser, campaign,
    order ID, value, currency, timestamp, and click identifier.
    Supports advertiser API key authentication via X-API-Key or Authorization header.
    """
    client_ip = resolve_client_ip(request)

    api_key_header = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    raw_key = None
    if api_key_header:
        raw_key = api_key_header.strip()
    elif auth_header and auth_header.startswith("Bearer "):
        raw_key = auth_header[7:].strip()

    api_key_advertiser_id = None
    if raw_key and raw_key.startswith(("ak_live_", "ak_test_")):
        from liteads.management.advertiser_api_key_service import AdvertiserApiKeyService
        matching_key, adv = await AdvertiserApiKeyService.resolve_advertiser_api_key(raw_key, session)
        api_key_advertiser_id = adv.id

    service = ConversionService(session)
    result = await service.track_conversion(
        click_token=req.click_token,
        request_id=req.request_id,
        campaign_id=req.campaign_id,
        creative_id=req.creative_id,
        order_id=req.order_id,
        conversion_value=req.conversion_value,
        currency=req.currency,
        timestamp=req.timestamp,
        user_id=req.user_id,
        signature=req.signature,
        is_test=req.is_test,
        is_s2s=True,
        api_key_advertiser_id=api_key_advertiser_id,
        ip_address=client_ip,
        extra=req.extra,
    )

    return ConversionEventResponse(
        success=result.success,
        event_id=result.event_id,
        is_attributed=result.is_attributed,
        attribution_reason=result.attribution_reason,
        campaign_id=result.campaign_id,
        creative_id=result.creative_id,
        conversion_value=float(result.conversion_value) if result.conversion_value is not None else None,
        currency=result.currency,
        is_test=result.is_test,
        is_duplicate=result.is_duplicate,
        message=result.message,
    )


@router.get("/conversion/pixel")
async def track_conversion_pixel(
    request: Request,
    token: str | None = Query(None, description="Signed click attribution token"),
    req: str | None = Query(None, description="Click request_id"),
    camp: int | None = Query(None, description="Campaign ID"),
    order: str | None = Query(None, description="External order / transaction ID"),
    val: float | None = Query(None, description="Conversion value"),
    curr: str = Query("INR", description="3-letter currency code"),
    test: bool = Query(False, description="Test mode flag"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """
    1x1 transparent GIF pixel conversion tracking endpoint.
    Returns binary image/gif data with caching disabled.
    """
    client_ip = resolve_client_ip(request)
    service = ConversionService(session)
    try:
        await service.track_conversion(
            click_token=token,
            request_id=req,
            campaign_id=camp,
            order_id=order,
            conversion_value=val,
            currency=curr,
            is_test=test,
            ip_address=client_ip,
        )
    except Exception as e:
        logger.warning(f"Conversion pixel tracking issue: {e}")

    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate, private",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    return Response(content=GIF_1X1_BYTES, media_type="image/gif", headers=headers)


@router.post(
    "/conversion/diagnostics/test-event",
    response_model=ConversionEventResponse,
    dependencies=[Depends(require_roles("admin", "advertiser"))],
)
async def test_conversion_diagnostics(
    req: ConversionEventRequest,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> ConversionEventResponse:
    """
    Diagnostic endpoint allowing advertisers to simulate a conversion event in test mode.
    Forces is_test=True to ensure no billing or reporting contamination.
    """
    if req.campaign_id:
        await _assert_campaign_ownership(user, req.campaign_id, session)

    service = ConversionService(session)
    result = await service.track_conversion(
        click_token=req.click_token,
        request_id=req.request_id,
        campaign_id=req.campaign_id,
        creative_id=req.creative_id,
        order_id=req.order_id,
        conversion_value=req.conversion_value,
        currency=req.currency,
        timestamp=req.timestamp,
        user_id=req.user_id,
        signature=req.signature,
        is_test=True,  # Forced test mode
        extra=req.extra,
    )

    return ConversionEventResponse(
        success=result.success,
        event_id=result.event_id,
        is_attributed=result.is_attributed,
        attribution_reason=result.attribution_reason,
        campaign_id=result.campaign_id,
        creative_id=result.creative_id,
        conversion_value=float(result.conversion_value) if result.conversion_value is not None else None,
        currency=result.currency,
        is_test=True,
        is_duplicate=result.is_duplicate,
        message=f"Test conversion simulated: {result.attribution_reason}",
    )


@router.get(
    "/conversion/diagnostics/timeline",
    dependencies=[Depends(require_roles("admin", "advertiser"))],
)
async def campaign_event_timeline(
    campaign_id: int,
    limit: int = Query(50, ge=1, le=200),
    include_test: bool = Query(True),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Returns a unified chronological timeline of impressions, clicks, and conversions for a campaign.
    Requires authenticated advertiser ownership or admin role.
    """
    await _assert_campaign_ownership(user, campaign_id, session)

    query = select(AdEvent).where(AdEvent.campaign_id == campaign_id)
    if not include_test:
        query = query.where(AdEvent.is_test == False)

    query = query.order_by(AdEvent.event_time.desc()).limit(limit)
    res = await session.execute(query)
    events = res.scalars().all()

    timeline = []
    type_names = {1: "impression", 2: "click", 3: "conversion"}
    for ev in events:
        timeline.append({
            "id": ev.id,
            "request_id": ev.request_id,
            "event_type": type_names.get(ev.event_type, "unknown"),
            "event_time": ev.event_time.isoformat() if ev.event_time else None,
            "creative_id": ev.creative_id,
            "ad_set_id": ev.ad_set_id,
            "cost": float(ev.cost),
            "is_attributed": ev.is_attributed,
            "attribution_reason": ev.attribution_reason,
            "attributed_event_id": ev.attributed_event_id,
            "external_order_id": ev.external_order_id,
            "conversion_value": float(ev.conversion_value) if ev.conversion_value is not None else None,
            "currency": ev.currency,
            "is_test": ev.is_test,
        })

    return {
        "campaign_id": campaign_id,
        "count": len(timeline),
        "timeline": timeline,
    }


@router.get(
    "/conversion/diagnostics/summary",
    dependencies=[Depends(require_roles("admin", "advertiser"))],
)
async def campaign_attribution_summary(
    campaign_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Operational summary of conversion metrics, attribution breakdowns, test events, and deduplications.
    """
    await _assert_campaign_ownership(user, campaign_id, session)

    # Aggregations
    q_total = select(func.count()).where(
        AdEvent.campaign_id == campaign_id,
        AdEvent.event_type == EventType.CONVERSION,
    )
    q_attributed = select(func.count()).where(
        AdEvent.campaign_id == campaign_id,
        AdEvent.event_type == EventType.CONVERSION,
        AdEvent.is_attributed == True,
        AdEvent.is_test == False,
    )
    q_unattributed = select(func.count()).where(
        AdEvent.campaign_id == campaign_id,
        AdEvent.event_type == EventType.CONVERSION,
        AdEvent.is_attributed == False,
        AdEvent.is_test == False,
    )
    q_test = select(func.count()).where(
        AdEvent.campaign_id == campaign_id,
        AdEvent.event_type == EventType.CONVERSION,
        AdEvent.is_test == True,
    )
    q_value = select(func.coalesce(func.sum(AdEvent.conversion_value), Decimal("0"))).where(
        AdEvent.campaign_id == campaign_id,
        AdEvent.event_type == EventType.CONVERSION,
        AdEvent.is_attributed == True,
        AdEvent.is_test == False,
    )

    total_convs = (await session.execute(q_total)).scalar() or 0
    attr_convs = (await session.execute(q_attributed)).scalar() or 0
    unattr_convs = (await session.execute(q_unattributed)).scalar() or 0
    test_convs = (await session.execute(q_test)).scalar() or 0
    total_val = (await session.execute(q_value)).scalar() or Decimal("0")

    # Reason breakdown
    q_reasons = (
        select(AdEvent.attribution_reason, func.count().label("cnt"))
        .where(
            AdEvent.campaign_id == campaign_id,
            AdEvent.event_type == EventType.CONVERSION,
        )
        .group_by(AdEvent.attribution_reason)
    )
    reason_rows = (await session.execute(q_reasons)).all()
    attribution_reasons = {str(row[0] or "unknown"): row[1] for row in reason_rows}

    return {
        "campaign_id": campaign_id,
        "total_conversions_recorded": total_convs,
        "production_attributed_conversions": attr_convs,
        "production_unattributed_conversions": unattr_convs,
        "test_conversions": test_convs,
        "attributed_conversion_value": float(total_val),
        "attribution_reasons_breakdown": attribution_reasons,
    }
