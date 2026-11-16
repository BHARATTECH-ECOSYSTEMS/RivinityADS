"""
Ad serving endpoints.

Includes:
- POST /request   — main ad decision endpoint (classic + S2S AI-platform)
- GET  /serve     — zero-integration iframe for classic web publishers
- POST /classify  — text → sector classification for context targeting
"""

import hashlib
import html as html_lib
import re
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from liteads.ad_server.services.ad_service import AdService
from liteads.ad_server.services.rate_limit_service import RateLimitService
from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.logger import get_logger, log_context
from liteads.common.security import (
    AuthenticatedAdCaller,
    authenticate_ad_request,
    authenticate_s2s_or_user,
    optional_publisher_from_api_key,
    verify_hmac_signature,
)
from liteads.common.utils import generate_request_id
from liteads.models import AdSlot, Publisher, Status
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


def _extract_and_normalize_host(url_or_origin: str | None) -> str | None:
    """Extract and normalize hostname from Origin or Referer header."""
    if not url_or_origin or not isinstance(url_or_origin, str):
        return None
    clean = url_or_origin.strip().lower()
    if not clean:
        return None

    # Handle userinfo if present
    if "@" in clean:
        clean = clean.split("@")[-1]

    # Parse using urllib.parse
    try:
        if "://" in clean:
            parsed = urlparse(clean)
            host = parsed.hostname or parsed.netloc
        else:
            parsed = urlparse(f"https://{clean}")
            host = parsed.hostname or parsed.netloc
    except Exception:
        host = clean.split("://", 1)[-1]

    if not host:
        return None

    # Strip port if present
    if host.startswith("[") and "]" in host:
        bracket_end = host.find("]")
        host = host[1:bracket_end]
    elif ":" in host:
        host = host.split(":", 1)[0]

    for sep in ("/", "?", "#"):
        if sep in host:
            host = host.split(sep, 1)[0]

    host = host.strip().rstrip(".")
    if not host:
        return None

    # IDN punycode normalization
    try:
        host = host.encode("idna").decode("ascii")
    except Exception:
        pass

    return host


def _is_domain_match(request_host: str, authorized_domain: str) -> bool:
    """Check if request_host matches authorized_domain via exact normalized hostname match.

    Enforces:
    - Exact normalized hostname matching (e.g. example.com == example.com).
    - example.com authorizes ONLY example.com.
    - www.example.com authorizes ONLY www.example.com.
    - No implicit bidirectional www alias authorization.
    - Subdomains, attacker hosts, and aliases require separate registration and verification.
    """
    norm_req = _extract_and_normalize_host(request_host)
    norm_auth = _extract_and_normalize_host(authorized_domain)
    if not norm_req or not norm_auth:
        return False
    return norm_req == norm_auth


def get_ad_service(session: AsyncSession = Depends(get_session)) -> AdService:
    """Dependency to get ad service."""
    return AdService(session)


# ---------------------------------------------------------------------------
# POST /api/v1/ad/request
# ---------------------------------------------------------------------------

@router.post("/request", response_model=AdListResponse)
async def request_ads(
    request: Request,
    response: Response,
    ad_request: AdRequest,
    caller: AuthenticatedAdCaller = Depends(authenticate_ad_request),
    ad_service: AdService = Depends(get_ad_service),
    session: AsyncSession = Depends(get_session),
) -> AdListResponse:
    """Request ads for a given slot.

    This is the main ad serving endpoint. It:

    1. Validates and enforces S2S partner HMAC authentication (X-API-Key,
       X-Timestamp, X-Signature) or dashboard JWT authentication.
    2. Strict fail-closed security: malformed credentials, unknown keys,
       revoked keys, expired timestamps, or invalid signatures are rejected (HTTP 401).
    3. Anonymous callers with no credentials follow a separated unauthenticated path.
    4. Enforces publisher slot ownership authorization (HTTP 403 on mismatch).
    5. Retrieves candidate ads based on targeting and contextual sector affinities.
    6. Filters by requested format (creative_type), budget, frequency, and quality.
    7. Predicts CTR/CVR using ML models and ranks by eCPM.
    8. Returns top ads with signed tracking URLs.

    **AI-platform note:** Use ``format=2`` (native) to receive
    ``title / description / landing_url`` blocks for rendering as
    chat-inline, sidebar, or conversation-starter units. Billing and
    attribution happen server-to-server via the tracking URLs.
    """
    request_id = generate_request_id()
    settings = get_settings()

    # Identify sandbox mode for publisher keys
    is_sandbox = False
    if caller.is_authenticated and caller.auth_type == "api_key" and caller.api_key:
        db_key = caller.api_key
        is_sandbox = bool(
            db_key.get("is_sandbox", False)
            if isinstance(db_key, dict)
            else getattr(db_key, "is_sandbox", False)
        )
    if is_sandbox:
        request_id = f"test_{request_id}"
    setattr(ad_request, "is_sandbox", is_sandbox)

    publisher_id = caller.publisher_id

    # Add request context for logging (mask full user_id to prevent PII exposure in logs)
    masked_user = (
        f"{ad_request.user_id[:8]}...{ad_request.user_id[-4:]}"
        if ad_request.user_id and len(ad_request.user_id) >= 16
        else ("<user_id>" if ad_request.user_id else None)
    )
    log_context(
        request_id=request_id,
        slot_id=ad_request.slot_id,
        user_id=masked_user,
        publisher_id=publisher_id,   # None when unauthenticated
    )

    logger.info(
        "Ad request received",
        num_requested=ad_request.num_ads,
        os=ad_request.device.os if ad_request.device else None,
        authenticated=caller.is_authenticated,
        auth_type=caller.auth_type,
    )

    # ── Phase 5.3: Rate Limiting & Daily Quota Enforcement ───────────────
    if caller.is_authenticated and caller.auth_type == "api_key" and caller.api_key:
        db_key = caller.api_key
        key_id = db_key.get("id") if isinstance(db_key, dict) else getattr(db_key, "id", None)
        rate_limit = (
            db_key.get("rate_limit_per_minute", 60)
            if isinstance(db_key, dict)
            else getattr(db_key, "rate_limit_per_minute", 60)
        )
        daily_quota = (
            db_key.get("daily_quota", 10000)
            if isinstance(db_key, dict)
            else getattr(db_key, "daily_quota", 10000)
        )

        if key_id:
            # 1. Total request admitted to quota/rate limit evaluation
            await RateLimitService.record_usage_metric(key_id, "total_requests", 1)

            # 2. Check per-minute rate limit (atomic sliding window)
            try:
                allowed_rl, rem_rl, reset_rl = await RateLimitService.check_rate_limit(
                    key_id=key_id,
                    rate_limit_per_minute=rate_limit,
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.error("rate_limit_fail_closed_error", error=str(e))
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Rate limit service unavailable",
                )

            if not allowed_rl:
                await RateLimitService.record_usage_metric(key_id, "rate_limited_requests", 1)
                rl_headers = {
                    "Retry-After": str(reset_rl),
                    "X-RateLimit-Limit": str(rate_limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_rl),
                }
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded. Please retry after {reset_rl} seconds.",
                    headers=rl_headers,
                )

            # 3. Check daily quota (atomic counter)
            try:
                allowed_quota, rem_quota, reset_quota = await RateLimitService.check_daily_quota(
                    key_id=key_id,
                    daily_quota=daily_quota,
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.error("daily_quota_fail_closed_error", error=str(e))
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Daily quota service unavailable",
                )

            if not allowed_quota:
                await RateLimitService.record_usage_metric(key_id, "quota_exceeded_requests", 1)
                quota_headers = {
                    "Retry-After": str(reset_quota),
                    "X-Quota-Limit": str(daily_quota),
                    "X-Quota-Remaining": "0",
                    "X-Quota-Reset": str(reset_quota),
                }
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Daily quota exceeded. Resets at 00:00 UTC.",
                    headers=quota_headers,
                )

            # 4. Admitted to ad serving pipeline
            await RateLimitService.record_usage_metric(key_id, "admitted_requests", 1)

            # Attach rate-limit and quota headers to response
            response.headers["X-RateLimit-Limit"] = str(rate_limit)
            response.headers["X-RateLimit-Remaining"] = str(rem_rl)
            response.headers["X-RateLimit-Reset"] = str(reset_rl)
            response.headers["X-Quota-Limit"] = str(daily_quota)
            response.headers["X-Quota-Remaining"] = str(rem_quota)
            response.headers["X-Quota-Reset"] = str(reset_quota)

    # Get client IP from request
    client_ip = request.client.host if request.client else None
    if ad_request.geo and not ad_request.geo.ip:
        ad_request.geo.ip = client_ip

    # Phase 5.1B: Slot authorization, verified property enforcement, and domain matching
    from sqlalchemy import select
    from liteads.models.publisher import Publisher, PublisherProperty

    slot_res = await session.execute(select(AdSlot).where(AdSlot.slot_id == ad_request.slot_id))
    slot = slot_res.scalar_one_or_none()

    if slot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ad slot not found")

    if slot.status != Status.ACTIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ad slot is not active")

    # Slot publisher ownership authorization (must precede property checks to ensure cross-publisher
    # access attempts receive HTTP 403 Forbidden consistently)
    if caller.is_authenticated:
        if publisher_id is not None and slot.publisher_id != publisher_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this slot")
    else:
        # Anonymous callers are prohibited from private / chat surface slots
        if slot.is_chat_surface:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required for chat surface slots")

    # Task J: Chat/AI Surface Opt-In at Slot Level
    # Ordinary web slots must not be treated as permission to appear in conversational assistants.
    is_chat_request = (
        ad_request.is_chat_surface is True
        or (ad_request.surface and ad_request.surface.lower() in ("chat", "ai", "conversation", "assistant"))
        or (ad_request.context and getattr(ad_request.context, "surface", None) and str(ad_request.context.surface).lower() in ("chat", "ai", "conversation", "assistant"))
    )
    if is_chat_request:
        if not slot.is_chat_surface:
            logger.warning(
                "chat_surface_opt_in_required",
                slot_id=ad_request.slot_id,
                publisher_id=slot.publisher_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ad slot is not configured for conversational or chat surfaces. Explicit publisher opt-in required.",
            )

    # Verify publisher is active
    pub_res = await session.execute(
        select(Publisher).where(Publisher.id == slot.publisher_id)
    )
    pub = pub_res.scalar_one_or_none()
    if pub is None or pub.status != Status.ACTIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Publisher is not active")

    # Determine if caller is an authenticated S2S partner
    is_trusted_s2s = caller.is_authenticated and caller.auth_type == "api_key"

    # Origin and Referer validation against the verified publisher property domain
    origin_header = request.headers.get("origin") if request else None
    referer_header = request.headers.get("referer") if request else None

    # VULN-5.1-01: Headerless request remediation
    if not origin_header and not referer_header:
        if not is_trusted_s2s:
            logger.warning(
                "ad_request_headerless_rejected",
                slot_id=ad_request.slot_id,
                auth_type=caller.auth_type,
                authenticated=caller.is_authenticated,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ad request missing Origin or Referer header. Unauthenticated or non-S2S headerless requests are not permitted.",
            )
        # Permitted for authenticated S2S integrations (without logging credentials/signatures)
        logger.info(
            "ad_request_s2s_headerless_admitted",
            slot_id=ad_request.slot_id,
            publisher_id=caller.publisher_id,
        )

    # Property resolution and verification:
    # Website / anonymous / non-S2S callers strictly require an associated, verified publisher property.
    # Authenticated S2S callers may call slots without a property_id (e.g. backend S2S slots),
    # but if property_id is configured, the property must be active and verified.
    prop = None
    if slot.property_id:
        prop_res = await session.execute(
            select(PublisherProperty).where(PublisherProperty.id == slot.property_id)
        )
        prop = prop_res.scalar_one_or_none()
        if prop is None or prop.status != Status.ACTIVE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Publisher property is not active")
        if prop.verification_status != "VERIFIED":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Publisher property is not verified")
    elif not is_trusted_s2s:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ad slot is not associated with a publisher property",
        )

    # Validate Origin domain if present and property is bound
    if origin_header and prop:
        origin_host = _extract_and_normalize_host(origin_header)
        if not origin_host or not _is_domain_match(origin_host, prop.domain):
            logger.warning(
                "ad_request_origin_mismatch",
                origin_host=origin_host,
                prop_domain=prop.domain,
                slot_id=ad_request.slot_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Origin '{origin_host or origin_header}' is not authorized for this ad slot.",
            )

    # Validate Referer domain if present and property is bound
    if referer_header and prop:
        referer_host = _extract_and_normalize_host(referer_header)
        if not referer_host or not _is_domain_match(referer_host, prop.domain):
            logger.warning(
                "ad_request_referer_mismatch",
                referer_host=referer_host,
                prop_domain=prop.domain,
                slot_id=ad_request.slot_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Referer '{referer_host or referer_header}' is not authorized for this ad slot.",
            )

    # Phase 1: Visitor Consent Gate for Website Placements
    # Ad delivery on websites strictly requires verified visitor consent.
    from liteads.models.website import WebsiteProject, WebsiteConsentLog
    from liteads.common.tracking_sig import verify_consent_token

    website_proj = None
    if slot.property_id:
        proj_res = await session.execute(
            select(WebsiteProject).where(WebsiteProject.property_id == slot.property_id)
        )
        website_proj = proj_res.scalar_one_or_none()

    if website_proj and not is_sandbox and not is_trusted_s2s:
        # 1. Check for signed visitor consent token proof
        consent_token = ad_request.consent_token or request.headers.get("X-Consent-Token")
        is_consented = False
        if consent_token:
            token_data = verify_consent_token(
                consent_token,
                expected_site_slug=website_proj.slug,
                expected_project_id=website_proj.id,
            )
            if token_data.get("is_granted"):
                is_consented = True
            else:
                logger.info(
                    "Ad request suppressed: visitor declined advertising consent",
                    slot_id=ad_request.slot_id,
                    site_slug=website_proj.slug,
                )
                return AdListResponse(ads=[], total=0)
        else:
            # 2. Check for trusted stored consent state via privacy hash
            raw_ip = request.client.host if request.client else None
            ip_h = hashlib.sha256(raw_ip.encode("utf-8")).hexdigest() if raw_ip else None
            if ip_h:
                c_stmt = (
                    select(WebsiteConsentLog)
                    .where(
                        WebsiteConsentLog.project_id == website_proj.id,
                        WebsiteConsentLog.consent_type == "visitor_consent",
                        WebsiteConsentLog.client_ip_hash == ip_h,
                    )
                    .order_by(WebsiteConsentLog.recorded_at.desc())
                    .limit(1)
                )
                stored_log = (await session.execute(c_stmt)).scalar_one_or_none()
                if stored_log and stored_log.is_granted:
                    is_consented = True

        if not is_consented:
            logger.info(
                "Ad request suppressed: visitor consent missing or not granted (default to denied)",
                slot_id=ad_request.slot_id,
                site_slug=website_proj.slug,
            )
            return AdListResponse(ads=[], total=0)

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
        click_token = None
        try:
            from liteads.common.tracking_sig import sign_tracking, sign_click_token
            sig = sign_tracking(request_id, ad_id, ad_request.slot_id)
            sig_param = f"&sig={sig}"
            click_token = sign_click_token(request_id, candidate.campaign_id, candidate.creative_id)
        except Exception:
            pass

        sandbox_param = "&sandbox=1" if is_sandbox else ""
        tracking = TrackingUrls(
            impression_url=(
                f"{base_url}/api/v1/event/track?type=impression"
                f"&req={request_id}&ad={ad_id}&slot={slot_q}{sig_param}{sandbox_param}"
            ),
            click_url=(
                f"{base_url}/api/v1/event/track?type=click"
                f"&req={request_id}&ad={ad_id}&slot={slot_q}{sig_param}{sandbox_param}"
            ),
            conversion_url=(
                f"{base_url}/api/v1/event/conversion"
            ),
            click_token=click_token,
        )

        format_label = _get_creative_type_name(candidate.creative_type)
        if candidate.width and candidate.height:
            format_descriptor = f"{format_label}_{candidate.width}x{candidate.height}"
        else:
            format_descriptor = format_label

        creative = CreativeResponse(
            title=candidate.title,
            description=candidate.description,
            image_url=candidate.image_url,
            video_url=candidate.video_url,
            asset_url=candidate.image_url or candidate.video_url,
            landing_url=candidate.landing_url,
            width=candidate.width,
            height=candidate.height,
            creative_type=format_label,
            format=format_descriptor,
            cta_text="Learn More",
        )

        ad = AdResponse(
            ad_id=ad_id,
            campaign_id=candidate.campaign_id,
            creative_id=candidate.creative_id,
            ad_set_id=candidate.ad_set_id,
            advertiser_id=candidate.advertiser_id,
            advertiser_name=getattr(candidate, "advertiser_name", None),
            creative=creative,
            tracking=tracking,
            sponsored=True,
            disclosure_label="Sponsored",
            review_status=getattr(candidate, "review_status", "APPROVED"),
            metadata={
                "ecpm": round(candidate.ecpm, 4),
                "pctr": round(candidate.pctr, 6),
                "is_sandbox": is_sandbox,
            }
            if (settings.debug or is_sandbox)
            else None,
        )
        ads.append(ad)

    logger.info("Ad request completed", num_returned=len(ads))

    if caller.is_authenticated and caller.auth_type == "api_key" and caller.api_key:
        db_key = caller.api_key
        key_id = db_key.get("id") if isinstance(db_key, dict) else getattr(db_key, "id", None)
        if len(ads) > 0 and key_id:
            await RateLimitService.record_usage_metric(key_id, "served_ads", len(ads))

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

    # Normalized domain validation for publisher property or site_url (Task C)
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        slot_res = await ad_service.session.execute(
            select(AdSlot)
            .options(selectinload(AdSlot.publisher), selectinload(AdSlot.property))
            .where(AdSlot.slot_id == slot_id)
        )
        slot = slot_res.scalar_one_or_none()
        if slot:
            if slot.status != Status.ACTIVE:
                logger.warning("iframe_slot_inactive", slot_id=slot_id)
                return HTMLResponse(content="<!-- slot inactive -->", status_code=400)
            if slot.publisher and slot.publisher.status != Status.ACTIVE:
                logger.warning("iframe_publisher_inactive_or_suspended", slot_id=slot_id)
                return HTMLResponse(content="<!-- publisher inactive or suspended -->", status_code=400)
            if slot.property and slot.property.status != Status.ACTIVE:
                logger.warning("iframe_property_inactive_or_suspended", slot_id=slot_id)
                return HTMLResponse(content="<!-- publisher property inactive or suspended -->", status_code=400)
            if slot.property and slot.property.verification_status != "VERIFIED":
                logger.warning("iframe_property_not_verified", slot_id=slot_id)
                return HTMLResponse(content="<!-- publisher property not verified -->", status_code=403)

        if slot and request:
            target_domain = (
                (slot.property.domain if slot.property and slot.property.domain else None)
                or (slot.publisher.site_url if slot.publisher else None)
            )
            if target_domain:
                referer = request.headers.get("referer") or request.headers.get("origin")
                if referer:
                    ref_host = _extract_and_normalize_host(referer)
                    if not ref_host or not _is_domain_match(ref_host, target_domain):
                        logger.warning(
                            "domain_validation_failed",
                            slot=slot_id,
                            target_domain=target_domain,
                            referer=referer,
                            ref_host=ref_host,
                        )
                        return HTMLResponse(
                            content="<!-- domain validation failed -->",
                            status_code=403,
                        )
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
    landing = html_lib.escape(c.landing_url or "#", quote=True)
    title = html_lib.escape(c.title or "Advertisement", quote=True)
    description = html_lib.escape(c.description or "", quote=True)
    image_url = html_lib.escape(c.image_url or "", quote=True)
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
    types = {
        1: "banner",
        2: "native",
        3: "video",
        4: "interstitial",
        5: "display_card",
        6: "label_ad",
        7: "element_ad",
    }
    return types.get(creative_type, "banner")
