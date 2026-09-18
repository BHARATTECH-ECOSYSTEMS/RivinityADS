"""
Ad serving endpoints.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from urllib.parse import quote
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.ad_service import AdService
from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.logger import get_logger, log_context
from liteads.common.utils import generate_request_id
from liteads.common.security import authenticate_s2s_or_user
from liteads.schemas.request import AdRequest, DeviceInfo
from liteads.schemas.response import AdListResponse, AdResponse, CreativeResponse, TrackingUrls
from liteads.models import AdSlot, Publisher
from fastapi import HTTPException
from sqlalchemy.orm import selectinload

logger = get_logger(__name__)
router = APIRouter()


def get_ad_service(session: AsyncSession = Depends(get_session)) -> AdService:
    """Dependency to get ad service."""
    return AdService(session)


@router.post("/request", response_model=AdListResponse)
async def request_ads(
    request: Request,
    ad_request: AdRequest,
    ad_service: AdService = Depends(get_ad_service),
    auth: dict = Depends(authenticate_s2s_or_user),
    session: AsyncSession = Depends(get_session),
) -> AdListResponse:
    """
    Request ads for a given slot.

    This is the main ad serving endpoint. It:
    1. Retrieves candidate ads based on targeting
    2. Filters ads by budget, frequency, and quality
    3. Predicts CTR/CVR using ML models
    4. Ranks ads by eCPM
    5. Returns top ads with tracking URLs
    """
    request_id = generate_request_id()
    settings = get_settings()

    # Add request context for logging
    log_context(
        request_id=request_id,
        slot_id=ad_request.slot_id,
        user_id=ad_request.user_id,
    )

    logger.info(
        "Ad request received",
        num_requested=ad_request.num_ads,
        os=ad_request.device.os if ad_request.device else None,
    )

    # Get client IP from request
    client_ip = request.client.host if request.client else None
    if ad_request.geo and not ad_request.geo.ip:
        ad_request.geo.ip = client_ip

    # Slot validation and authorization
    from sqlalchemy import select
    slot = await session.execute(select(AdSlot).where(AdSlot.slot_id == ad_request.slot_id))
    slot = slot.scalar_one_or_none()
    if not slot or slot.status != 1:
        raise HTTPException(status_code=404, detail="AdSlot not found or inactive")

    if auth["type"] == "publisher":
        if slot.publisher_id != auth["entity"].id:
            raise HTTPException(status_code=403, detail="Not authorized for this slot")
    elif auth["type"] == "user":
        # Check if the user is an admin or publisher owning the slot (AdPreview could be used by admin/advertiser)
        # For simplicity, if it's a JWT, allow it (AdPreview use case), but we can restrict further if needed.
        pass

    # Serve ads
    candidates = await ad_service.serve_ads(
        request=ad_request,
        request_id=request_id,
    )

    # Build response
    ads = []
    base_url = str(request.base_url).rstrip("/")

    for candidate in candidates[: ad_request.num_ads]:
        # Build tracking URLs
        from liteads.common.tracking_sig import sign_tracking
        sig = sign_tracking(request_id, f"ad_{candidate.campaign_id}_{candidate.creative_id}", ad_request.slot_id)
        tracking = TrackingUrls(
            impression_url=f"{base_url}/api/v1/event/track?type=impression&req={request_id}&ad=ad_{candidate.campaign_id}_{candidate.creative_id}&slot={quote(ad_request.slot_id)}&sig={sig}",
            click_url=f"{base_url}/api/v1/event/track?type=click&req={request_id}&ad=ad_{candidate.campaign_id}_{candidate.creative_id}&slot={quote(ad_request.slot_id)}&sig={sig}",
            conversion_url=f"{base_url}/api/v1/event/track?type=conversion&req={request_id}&ad=ad_{candidate.campaign_id}_{candidate.creative_id}&slot={quote(ad_request.slot_id)}&sig={sig}",
        )

        # Build creative response
        creative = CreativeResponse(
            title=candidate.title,
            description=candidate.description,
            image_url=candidate.image_url,
            video_url=candidate.video_url,
            landing_url=candidate.landing_url,
            width=candidate.width,
            height=candidate.height,
            creative_type=_get_creative_type_name(candidate.creative_type),
        )

        ad = AdResponse(
            ad_id=f"ad_{candidate.campaign_id}_{candidate.creative_id}",
            campaign_id=candidate.campaign_id,
            creative_id=candidate.creative_id,
            creative=creative,
            tracking=tracking,
            metadata={
                "ecpm": round(candidate.ecpm, 4),
                "pctr": round(candidate.pctr, 6),
            }
            if settings.debug
            else None,
        )
        ads.append(ad)

    logger.info(
        "Ad request completed",
        num_returned=len(ads),
    )

    return AdListResponse(
        request_id=request_id,
        ads=ads,
        count=len(ads),
    )


@router.get("/serve", response_class=HTMLResponse)
async def serve_ad(
    request: Request,
    slot_id: str = Query(..., description="Ad slot identifier"),
    ad_service: AdService = Depends(get_ad_service),
) -> HTMLResponse:
    """
    Serve an ad as ready-to-render HTML.

    Publishers embed this via:
        <iframe src="http://your-server/api/v1/ad/serve?slot_id=my_slot"></iframe>
    or load it dynamically via a script tag.

    Returns a self-contained HTML page with:
    - The ad creative (image/banner)
    - An impression tracking pixel (fires automatically)
    - A click-through link with click tracking
    """
    request_id = generate_request_id()
    base_url = str(request.base_url).rstrip("/")

    # Build a minimal ad request
    ad_request = AdRequest(
        slot_id=slot_id,
        device=DeviceInfo(os="web"),
        num_ads=1,
    )

    # Get client IP
    client_ip = request.client.host if request.client else None
    if ad_request.geo and not ad_request.geo.ip:
        ad_request.geo.ip = client_ip

    # Slot validation for IFrame / SDK domain validation
    from sqlalchemy import select
    slot = await ad_service.session.execute(select(AdSlot).options(selectinload(AdSlot.publisher)).where(AdSlot.slot_id == slot_id))
    slot = slot.scalar_one_or_none()
    if not slot or slot.status != 1:
        return HTMLResponse(content="<!-- invalid slot -->", status_code=204)

    if slot.publisher.site_url:
        referer = request.headers.get("referer") or request.headers.get("origin")
        if not referer or not referer.startswith(slot.publisher.site_url):
            logger.warning("domain_validation_failed", slot=slot_id, site_url=slot.publisher.site_url, referer=referer)
            return HTMLResponse(content="<!-- domain validation failed -->", status_code=204)

    # Serve
    candidates = await ad_service.serve_ads(
        request=ad_request,
        request_id=request_id,
    )

    if not candidates:
        # Return a transparent 1x1 pixel when no ad is available
        return HTMLResponse(
            content="<!-- no ad available -->",
            status_code=204,
        )

    winner = candidates[0]
    ad_id = f"ad_{winner.campaign_id}_{winner.creative_id}"
    from liteads.common.tracking_sig import sign_tracking
    sig = sign_tracking(request_id, ad_id, slot_id)
    imp_url = f"{base_url}/api/v1/event/track?type=impression&req={request_id}&ad={ad_id}&slot={quote(slot_id)}&sig={sig}"
    click_url = f"{base_url}/api/v1/event/track?type=click&req={request_id}&ad={ad_id}&slot={quote(slot_id)}&sig={sig}"
    landing_url = winner.landing_url or "#"
    image_url = winner.image_url or ""
    title = winner.title or "Advertisement"
    width = winner.width or 300
    height = winner.height or 250

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ display:flex; align-items:center; justify-content:center; min-height:100vh; background:transparent; font-family:system-ui,sans-serif; }}
  .ad-container {{ position:relative; display:inline-block; overflow:hidden; border-radius:4px; }}
  .ad-container img {{ display:block; max-width:100%; height:auto; }}
  .ad-label {{ position:absolute; top:4px; right:4px; background:rgba(0,0,0,0.55); color:#fff; font-size:9px; padding:2px 5px; border-radius:3px; letter-spacing:.5px; }}
  a {{ text-decoration:none; }}
</style>
</head>
<body>
  <div class="ad-container">
    <a href="{landing_url}" target="_blank" rel="noopener noreferrer"
       onclick="new Image().src='{click_url}'">
      <img src="{image_url}" alt="{title}" width="{width}" height="{height}" />
    </a>
    <span class="ad-label">Ad</span>
  </div>
  <!-- Impression pixel -->
  <img src="{imp_url}" width="1" height="1" style="position:absolute;opacity:0;" alt="" />
</body>
</html>"""

    return HTMLResponse(content=html)


def _get_creative_type_name(creative_type: int) -> str:
    """Convert creative type enum to string."""
    types = {1: "banner", 2: "native", 3: "video", 4: "interstitial"}
    return types.get(creative_type, "banner")
