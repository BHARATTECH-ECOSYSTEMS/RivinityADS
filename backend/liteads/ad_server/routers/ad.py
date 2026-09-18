"""
Ad serving endpoints.

Includes:
- POST /request   — main ad decision endpoint (classic + S2S AI-platform)
- GET  /serve     — zero-integration iframe for classic web publishers
- POST /classify  — text → sector classification for context targeting
"""

import hashlib
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from liteads.ad_server.services.ad_service import AdService
from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.logger import get_logger, log_context
from liteads.common.security import (
    authenticate_s2s_or_user,
    optional_publisher_from_api_key,
    verify_hmac_signature,
)
from liteads.common.utils import generate_request_id
from liteads.models import AdSlot, Publisher
from liteads.schemas.request import (
    AdRequest,
    DeviceInfo,
    TextClassifyRequest,
    TextClassifyResponse,
)
from liteads.schemas.response import (
    AdListResponse,
    AdResponse,
    CreativeResponse,
    TrackingUrls,
)

logger = get_logger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Sector keyword map for the lightweight rule-based classifier.
# Replace / extend with an ML model when available.
# ---------------------------------------------------------------------------
_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "finance": [
        "bank", "investment", "stock", "market", "fund", "loan", "mortgage",
        "interest rate", "federal reserve", "bond", "yield", "equity", "crypto",
        "bitcoin", "trading", "portfolio", "dividend", "ipo", "hedge", "inflation",
    ],
    "tech": [
        "software", "hardware", "ai", "machine learning", "cloud", "startup",
        "app", "api", "developer", "programming", "code", "data", "neural",
        "smartphone", "chip", "semiconductor", "saas", "cybersecurity", "tech",
    ],
    "health": [
        "health", "medical", "hospital", "doctor", "drug", "pharma", "cancer",
        "treatment", "vaccine", "fitness", "wellness", "diet", "nutrition",
        "exercise", "mental health", "diabetes", "heart", "surgery",
    ],
    "sports": [
        "game", "match", "tournament", "championship", "player", "team",
        "football", "basketball", "soccer", "baseball", "tennis", "golf",
        "nfl", "nba", "fifa", "olympic", "score", "league", "athlete",
    ],
    "travel": [
        "travel", "flight", "hotel", "vacation", "tourism", "airline",
        "destination", "resort", "cruise", "booking", "trip", "passport",
        "visa", "backpack", "itinerary", "airport",
    ],
    "entertainment": [
        "movie", "film", "music", "concert", "streaming", "netflix", "spotify",
        "celebrity", "award", "album", "podcast", "gaming", "video game",
        "tv show", "series", "box office",
    ],
    "automotive": [
        "car", "vehicle", "automobile", "electric vehicle", "ev", "tesla",
        "toyota", "ford", "bmw", "driving", "engine", "fuel", "suv", "truck",
        "dealership", "insurance",
    ],
    "real_estate": [
        "property", "real estate", "house", "apartment", "rent", "mortgage",
        "landlord", "tenant", "condo", "listing", "homebuyer", "realtor",
        "square foot", "neighborhood",
    ],
}


def _classify_text(text: str) -> tuple[list[str], dict[str, float]]:
    """Lightweight keyword-frequency sector classifier.

    Returns a ranked list of sector labels and a score dict (0.0–1.0).
    Normalises each sector score against total matches so scores are comparable.
    """
    text_lower = text.lower()
    raw_scores: dict[str, int] = {}

    for sector, keywords in _SECTOR_KEYWORDS.items():
        count = sum(
            len(re.findall(r"\b" + re.escape(kw) + r"\b", text_lower))
            for kw in keywords
        )
        if count > 0:
            raw_scores[sector] = count

    if not raw_scores:
        return [], {}

    max_score = max(raw_scores.values())
    scores = {k: round(v / max_score, 4) for k, v in sorted(raw_scores.items(), key=lambda x: -x[1])}
    return list(scores.keys()), scores


def get_ad_service(session: AsyncSession = Depends(get_session)) -> AdService:
    """Dependency to get ad service."""
    return AdService(session)


# ---------------------------------------------------------------------------
# POST /api/v1/ad/request
# ---------------------------------------------------------------------------

@router.post("/request", response_model=AdListResponse)
async def request_ads(
    request: Request,
    ad_request: AdRequest,
    ad_service: AdService = Depends(get_ad_service),
    session: AsyncSession = Depends(get_session),
    publisher_id: int | None = Depends(optional_publisher_from_api_key),
) -> AdListResponse:
    """Request ads for a given slot.

    This is the main ad serving endpoint. It:

    1. Optionally authenticates via ``X-API-Key`` (soft — logs publisher ID).
    2. Validates an ``X-Signature`` HMAC when present (for AI-platform S2S callers).
    3. Retrieves candidate ads based on targeting.
    4. Filters by requested ``format`` (creative_type).
    5. Filters by budget, frequency, and quality.
    6. Predicts CTR/CVR using ML models.
    7. Ranks ads by eCPM.
    8. Returns top ads with signed tracking URLs.

    **AI-platform note:** Use ``format=2`` (native) to receive
    ``title / description / landing_url`` blocks for rendering as
    chat-inline, sidebar, or conversation-starter units. Billing and
    attribution happen server-to-server via the tracking URLs.
    """
    request_id = generate_request_id()
    settings = get_settings()

    # ── Phase 4.3 — HMAC signature check (when present) ──────────────────
    api_key_header = request.headers.get("X-API-Key", "")
    signature = request.headers.get("X-Signature", "")
    timestamp = request.headers.get("X-Timestamp", "")
    if signature and api_key_header:
        body_bytes = await request.body()
        verify_hmac_signature(
            raw_key=api_key_header,
            timestamp_str=timestamp,
            body_bytes=body_bytes,
            signature=signature,
        )

    # Add request context for logging
    log_context(
        request_id=request_id,
        slot_id=ad_request.slot_id,
        user_id=ad_request.user_id,
        publisher_id=publisher_id,   # None when no key is present
    )

    logger.info(
        "Ad request received",
        num_requested=ad_request.num_ads,
        os=ad_request.device.os if ad_request.device else None,
        authenticated=publisher_id is not None,
    )

    # Get client IP from request
    client_ip = request.client.host if request.client else None
    if ad_request.geo and not ad_request.geo.ip:
        ad_request.geo.ip = client_ip

    # Slot authorization if authenticated
    if publisher_id is not None:
        from sqlalchemy import select
        slot_res = await session.execute(select(AdSlot).where(AdSlot.slot_id == ad_request.slot_id))
        slot = slot_res.scalar_one_or_none()
        if slot and slot.publisher_id != publisher_id:
            raise HTTPException(status_code=403, detail="Not authorized for this slot")

    # Serve ads
    candidates = await ad_service.serve_ads(
        request=ad_request,
        request_id=request_id,
    )

    # Build response
    ads = []
    base_url = str(request.base_url).rstrip("/")

    for candidate in candidates[: ad_request.num_ads]:
        ad_id = f"ad_{candidate.campaign_id}_{candidate.creative_id}"
        slot_q = quote(ad_request.slot_id)
        sig_param = ""
        try:
            from liteads.common.tracking_sig import sign_tracking
            sig = sign_tracking(request_id, ad_id, ad_request.slot_id)
            sig_param = f"&sig={sig}"
        except Exception:
            pass

        tracking = TrackingUrls(
            impression_url=(
                f"{base_url}/api/v1/event/track?type=impression"
                f"&req={request_id}&ad={ad_id}&slot={slot_q}{sig_param}"
            ),
            click_url=(
                f"{base_url}/api/v1/event/track?type=click"
                f"&req={request_id}&ad={ad_id}&slot={slot_q}{sig_param}"
            ),
            conversion_url=(
                f"{base_url}/api/v1/event/track?type=conversion"
                f"&req={request_id}&ad={ad_id}&slot={slot_q}{sig_param}"
            ),
        )

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
            ad_id=ad_id,
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

    logger.info("Ad request completed", num_returned=len(ads))

    return AdListResponse(
        request_id=request_id,
        ads=ads,
        count=len(ads),
    )


# ---------------------------------------------------------------------------
# GET /api/v1/ad/serve  — zero-integration iframe for classic web publishers
# ---------------------------------------------------------------------------

@router.get(
    "/serve",
    response_class=HTMLResponse,
    summary="Zero-integration iframe ad (for classic websites)",
    description=(
        "Returns a self-contained HTML page with a single ad creative. "
        "Embed via ``<iframe src='/api/v1/ad/serve?slot_id=banner_home_top'>``. "
        "No JavaScript or API key required on the publisher side."
    ),
    tags=["ad"],
)
async def serve_ad_iframe(
    slot_id: str = Query(..., description="Ad slot identifier"),
    w: int | None = Query(None, description="Override iframe width"),
    h: int | None = Query(None, description="Override iframe height"),
    request: Request = None,
    ad_service: AdService = Depends(get_ad_service),
) -> HTMLResponse:
    """Iframe-compatible ad serving endpoint.

    Renders a lightweight HTML page that fires an impression pixel immediately
    and wraps the creative in a click-tracking link.  Suitable for embedding
    in ``<iframe>`` tags on any website with zero JS dependency.
    """
    from liteads.schemas.request import DeviceInfo

    request_id = generate_request_id()
    base_url = str(request.base_url).rstrip("/") if request else ""

    ad_req = AdRequest(
        slot_id=slot_id,
        device=DeviceInfo(os="web"),
        num_ads=1,
    )

    if request and request.client and ad_req.geo and not ad_req.geo.ip:
        ad_req.geo.ip = request.client.host

    # Optional domain validation for publisher site_url
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        slot_res = await ad_service.session.execute(
            select(AdSlot).options(selectinload(AdSlot.publisher)).where(AdSlot.slot_id == slot_id)
        )
        slot = slot_res.scalar_one_or_none()
        if slot and slot.publisher and slot.publisher.site_url and request:
            referer = request.headers.get("referer") or request.headers.get("origin")
            if referer and not referer.startswith(slot.publisher.site_url):
                logger.warning("domain_validation_failed", slot=slot_id, site_url=slot.publisher.site_url, referer=referer)
                return HTMLResponse(content="<!-- domain validation failed -->", status_code=204)
    except Exception:
        pass

    candidates = await ad_service.serve_ads(request=ad_req, request_id=request_id)

    if not candidates:
        html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/></head>
<body style="margin:0;background:transparent;"><!-- no ad available --></body></html>"""
        return HTMLResponse(content=html, status_code=200)

    c = candidates[0]
    ad_id = f"ad_{c.campaign_id}_{c.creative_id}"
    slot_q = quote(slot_id)

    sig_param = ""
    try:
        from liteads.common.tracking_sig import sign_tracking
        sig = sign_tracking(request_id, ad_id, slot_id)
        sig_param = f"&sig={sig}"
    except Exception:
        pass

    imp_url = f"{base_url}/api/v1/event/track?type=impression&req={request_id}&ad={ad_id}&slot={slot_q}{sig_param}"
    clk_url = f"{base_url}/api/v1/event/track?type=click&req={request_id}&ad={ad_id}&slot={slot_q}{sig_param}"
    landing = c.landing_url or "#"
    title = c.title or "Advertisement"
    description = c.description or ""
    image_url = c.image_url or ""
    creative_w = w or c.width or 300
    creative_h = h or c.height or 250

    if image_url:
        creative_html = (
            f'<div class="ad-container" style="position:relative;display:inline-block;overflow:hidden;border-radius:4px;">'
            f'<a href="{landing}" target="_blank" rel="noopener sponsored" '
            f'onclick="t(\'{clk_url}\')">'
            f'<img src="{image_url}" width="{creative_w}" height="{creative_h}" '
            f'alt="{title}" style="border:0;display:block;max-width:100%;height:auto;" loading="lazy"/>'
            f'</a>'
            f'<span style="position:absolute;top:4px;right:4px;background:rgba(0,0,0,0.55);color:#fff;font-size:9px;padding:2px 5px;border-radius:3px;letter-spacing:.5px;">Ad</span>'
            f'</div>'
        )
    else:
        # Native fallback (text-only)
        creative_html = (
            f'<a href="{landing}" target="_blank" rel="noopener sponsored" '
            f'onclick="t(\'{clk_url}\')" '
            f'style="display:block;padding:8px;font-family:sans-serif;'
            f'border:1px solid #ddd;border-radius:4px;text-decoration:none;color:#000;">'
            f'<strong>{title}</strong><br/>'
            f'<span style="font-size:0.85em;color:#555;">{description}</span><br/>'
            f'<span style="font-size:0.75em;color:#888;">Sponsored</span>'
            f'</a>'
        )

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <meta name="robots" content="noindex"/>
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ display:flex; align-items:center; justify-content:center; min-height:100vh; background:transparent; font-family:system-ui,sans-serif; overflow:hidden; }}
  </style>
</head>
<body>
  {creative_html}
  <img src="{imp_url}" width="1" height="1" style="position:absolute;opacity:0;" alt=""/>
  <script>function t(u){{new Image().src=u;}}</script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


# ---------------------------------------------------------------------------
# POST /api/v1/classify  — text → sectors
# ---------------------------------------------------------------------------

@router.post(
    "/classify",
    response_model=TextClassifyResponse,
    summary="Classify text into ad-relevant sectors",
    description=(
        "Accepts free-form text and returns a ranked list of content sectors "
        "with confidence scores (0.0 – 1.0). "
        "Use the ``sectors`` list in ``ContextInfo.sectors`` on subsequent ad "
        "requests for context-aware targeting without disclosing raw page content."
    ),
    tags=["ad"],
)
async def classify_text(payload: TextClassifyRequest) -> TextClassifyResponse:
    """Classify text into ad-relevant sectors.

    This is a stateless, DB-free endpoint — no session required.
    Provide the output (``sectors``) in ``context.sectors`` on your next
    ``POST /api/v1/ad/request`` call to enable contextual ad targeting.
    """
    sectors, scores = _classify_text(payload.text)
    return TextClassifyResponse(sectors=sectors, scores=scores)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_creative_type_name(creative_type: int) -> str:
    """Convert creative type int to string label."""
    types = {1: "banner", 2: "native", 3: "video", 4: "interstitial"}
    return types.get(creative_type, "banner")
