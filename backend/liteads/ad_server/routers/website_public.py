"""Public website hosting router: subdomain resolution, SSR delivery, caching, SEO, and contact handling."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.website_cache import website_cache
from liteads.ad_server.services.website_renderer import website_renderer
from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.logger import get_logger
from liteads.common.security import is_trusted_proxy, resolve_client_ip
from liteads.models.website import (
    WebsiteConsentLog,
    WebsiteContactSubmission,
    WebsiteMonetizationStatus,
    WebsitePage,
    WebsiteProject,
    WebsiteStatus,
    WebsiteVersion,
)
from liteads.schemas.website import (
    RESERVED_SLUGS,
    SLUG_PATTERN,
    VisitorConsentPayload,
    VisitorConsentResponse,
)

logger = get_logger(__name__)

router = APIRouter()

# Security headers applied to all public website HTML responses
PUBLIC_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' https:; "
        "script-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "base-uri 'none';"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}


def _get_preview_hmac_key() -> bytes:
    """Derive a dedicated HMAC signing key for preview tokens using domain separation."""
    settings = get_settings()
    return hmac.new(
        settings.auth.jwt_secret.encode("utf-8"),
        b"rivinity:preview-token-v1",
        hashlib.sha256,
    ).digest()


def create_preview_token(project_id: int, user_id: int, ttl_seconds: int = 900) -> str:
    """Generate an HMAC-SHA256 signed, project-scoped preview token with high-entropy nonce."""
    expiry = int(time.time()) + ttl_seconds
    nonce = secrets.token_hex(8)
    raw = f"{project_id}:{user_id}:{expiry}:{nonce}"
    key = _get_preview_hmac_key()
    sig = hmac.new(key, raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{project_id}.{user_id}.{expiry}.{nonce}.{sig}"


def verify_preview_token(token: str) -> tuple[int, int] | None:
    """
    Verify preview token signature and expiration with constant-time comparison.
    Supports both 5-part (with nonce) and 4-part (legacy) tokens.
    Returns (project_id, user_id) or None.
    """
    settings = get_settings()
    parts = token.split(".")
    if len(parts) == 5:
        project_id_str, user_id_str, expiry_str, nonce, sig = parts
        expected_raw = f"{project_id_str}:{user_id_str}:{expiry_str}:{nonce}"
        key = _get_preview_hmac_key()
    elif len(parts) == 4:
        project_id_str, user_id_str, expiry_str, sig = parts
        expected_raw = f"{project_id_str}:{user_id_str}:{expiry_str}"
        key = settings.auth.jwt_secret.encode("utf-8")
    else:
        return None

    try:
        project_id = int(project_id_str)
        user_id = int(user_id_str)
        expiry = int(expiry_str)
    except ValueError:
        return None

    now = time.time()
    if expiry <= now:
        return None
    # Reject absurdly large expiry timestamps (max 7 days)
    if expiry > now + (86400 * 7):
        return None

    expected_sig = hmac.new(key, expected_raw.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(sig, expected_sig):
        return None

    return (project_id, user_id)


def resolve_subdomain_from_request(request: Request) -> str | None:
    """
    Safely extract tenant subdomain from request Host or trusted proxy header.

    Rejects:
    - User-info syntax (@).
    - Trailing-dot ambiguities (.), leading dots, and consecutive dots (..).
    - Malformed hosts and non-ASCII / control characters.
    - Unsupported IDN/punycode (xn--).
    - Non-numeric or unexpected ports.
    - Reserved platform slugs and root platform domain.
    """
    settings = get_settings()
    raw_host_header = request.headers.get("Host", "")

    # Inspect X-Forwarded-Host ONLY when direct client IP is a verified trusted proxy
    client_ip = request.client.host if request.client else ""
    trusted = settings.server.trusted_proxies
    if client_ip and is_trusted_proxy(client_ip, trusted):
        xfh = request.headers.get("X-Forwarded-Host")
        if xfh:
            raw_host_header = xfh.split(",")[0].strip()

    host_header = raw_host_header.strip().lower()
    if not host_header:
        return None

    # 1. Reject userinfo syntax (@)
    if "@" in host_header:
        return None

    # 2. Reject trailing-dot ambiguities, leading dots, and double dots
    if host_header.startswith(".") or host_header.endswith(".") or ".." in host_header:
        return None

    # 3. Reject unsupported IDN / Punycode forms and non-ASCII characters
    if "xn--" in host_header or not host_header.isascii():
        return None

    # 4. Parse and validate host and port
    if ":" in host_header:
        parts = host_header.split(":")
        if len(parts) != 2:
            return None
        host, port_str = parts
        if not port_str.isdigit():
            return None
        port = int(port_str)
        if port < 1 or port > 65535:
            return None
        # In production, only permit standard HTTP/HTTPS ports (80, 443)
        if settings.env == "prod" and port not in (80, 443):
            return None
    else:
        host = host_header

    if host.endswith("."):
        return None

    # Recognized platform base domains
    platform_domains = [settings.website.platform_domain.lower()]
    if settings.env in ("dev", "test") or settings.debug:
        platform_domains.extend(["localhost", "127.0.0.1"])

    for p_domain in platform_domains:
        if host == p_domain or host == f"www.{p_domain}":
            return None
        suffix = f".{p_domain}"
        if host.endswith(suffix):
            subdomain = host[:-len(suffix)]
            if "." in subdomain:
                return None
            if not SLUG_PATTERN.match(subdomain):
                return None
            if subdomain in RESERVED_SLUGS:
                return None
            return subdomain

    return None


async def _resolve_published_site_and_snapshot(
    slug: str, session: AsyncSession
) -> tuple[WebsiteProject, WebsiteVersion, dict[str, Any]]:
    """
    Look up project by slug, verify publication state, and return immutable snapshot.

    Fails closed (HTTP 404 or 403) on:
    - Non-existent or deleted project.
    - Draft or archived status.
    - Suspended status (takedown).
    - Missing or cross-project version reference pointer.
    """
    clean_slug = slug.strip().lower()
    stmt = select(WebsiteProject).where(
        WebsiteProject.slug == clean_slug,
        WebsiteProject.is_deleted == False,
    )
    res = await session.execute(stmt)
    project = res.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Website not found",
        )

    if project.status == WebsiteStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Website unavailable: Project has been suspended.",
        )

    if project.status != WebsiteStatus.PUBLISHED:
        # Draft or archived projects are never accessible to public requests
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Website not found",
        )

    if not project.current_published_version_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Website not published",
        )

    # Ownership-verified published snapshot lookup
    v_stmt = select(WebsiteVersion).where(
        WebsiteVersion.id == project.current_published_version_id,
        WebsiteVersion.project_id == project.id,
    )
    v_res = await session.execute(v_stmt)
    version = v_res.scalar_one_or_none()

    if version is None:
        logger.warning(
            "Published version pointer mismatch or missing",
            project_id=project.id,
            pointer=project.current_published_version_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Published website snapshot unavailable",
        )

    snapshot = version.snapshot_data
    if not isinstance(snapshot, dict) or "pages" not in snapshot:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Corrupted publication snapshot",
        )

    return project, version, snapshot


async def _serve_published_page_response(
    slug: str,
    page_slug: str,
    request: Request,
    session: AsyncSession,
) -> HTMLResponse:
    """Fetch, render, and cache a published page response with strict security headers."""
    settings = get_settings()
    clean_page_slug = page_slug.strip().strip("/").lower()

    # 1. Check cached site routing metadata
    cached_info = await website_cache.get_site_info(slug)
    project_id: int | None = None
    version_id: int | None = None

    if cached_info:
        if cached_info.get("status") != WebsiteStatus.PUBLISHED:
            raise HTTPException(status_code=404, detail="Website not found")
        project_id = cached_info.get("project_id")
        version_id = cached_info.get("version_id")

    # 2. Check cached HTML
    if project_id and version_id:
        cached_html = await website_cache.get_rendered_page(
            project_id=project_id,
            version_id=version_id,
            page_slug=clean_page_slug,
        )
        if cached_html:
            return HTMLResponse(
                content=cached_html,
                headers={
                    **PUBLIC_SECURITY_HEADERS,
                    "Cache-Control": f"public, max-age={settings.website.cache_ttl_seconds}",
                    "X-Cache": "HIT",
                },
            )

    # 3. Resolve from DB with fail-closed checks
    project, version, snapshot = await _resolve_published_site_and_snapshot(slug, session)

    # Cache site info
    await website_cache.set_site_info(
        slug=slug,
        info={
            "project_id": project.id,
            "version_id": version.id,
            "status": project.status,
        },
        ttl_seconds=60,
    )

    pages = snapshot.get("pages", [])
    if not clean_page_slug:
        # Resolve homepage
        selected_page = next((p for p in pages if p.get("is_home") or p.get("slug") == ""), None)
        if selected_page is None and pages:
            selected_page = pages[0]
    else:
        # Resolve secondary page
        selected_page = next(
            (p for p in pages if p.get("slug", "").lower() == clean_page_slug and not (p.get("is_home") and p.get("slug") == "")),
            None,
        )

    if selected_page is None:
        err_html = website_renderer.render_error_page(
            status_code=404,
            title="Page Not Found",
            message=f"The requested page '/{clean_page_slug}' does not exist on this website.",
            home_url="/",
        )
        return HTMLResponse(
            content=err_html,
            status_code=404,
            headers=PUBLIC_SECURITY_HEADERS,
        )

    # Base origin for canonical URLs and absolute references
    base_origin = f"https://{slug}.{settings.website.platform_domain}"

    is_monetized = (project.monetization_status == WebsiteMonetizationStatus.ACTIVE_MONETIZED)

    rendered_html = website_renderer.render_published_page(
        project_dict=snapshot.get("project", {
            "name": project.name,
            "slug": project.slug,
            "theme_config": project.theme_config,
        }),
        page_dict=selected_page,
        all_pages_dict=pages,
        base_origin=base_origin,
        is_preview=False,
        is_monetized=is_monetized,
    )

    # Cache pre-rendered HTML
    await website_cache.set_rendered_page(
        project_id=project.id,
        version_id=version.id,
        page_slug=clean_page_slug,
        html_content=rendered_html,
        ttl_seconds=settings.website.cache_ttl_seconds,
    )

    headers = dict(PUBLIC_SECURITY_HEADERS)
    if is_monetized:
        headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "img-src 'self' https: data:; "
            "script-src 'self'; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "connect-src 'self'; "
            "base-uri 'none';"
        )

    headers["Cache-Control"] = f"public, max-age={settings.website.cache_ttl_seconds}"
    headers["X-Cache"] = "MISS"

    return HTMLResponse(
        content=rendered_html,
        headers=headers,
    )


# ── Public Contact Form Submission ─────────────────────────────────────────

_IN_MEMORY_CONTACT_RATE_LIMITS: dict[str, list[float]] = {}
_MAX_IN_MEMORY_RATE_ENTRIES = 1000


def _check_in_memory_contact_rate_limit(key: str, max_limit: int, window_seconds: float = 60.0) -> bool:
    """
    Process-local sliding window rate limiter fallback when Redis is offline.
    Provides defense-in-depth on a single worker node; not globally consistent across multi-worker clusters.
    """
    now = time.time()
    cutoff = now - window_seconds
    timestamps = _IN_MEMORY_CONTACT_RATE_LIMITS.setdefault(key, [])
    _IN_MEMORY_CONTACT_RATE_LIMITS[key] = [t for t in timestamps if t > cutoff]
    if len(_IN_MEMORY_CONTACT_RATE_LIMITS[key]) >= max_limit:
        return False
    _IN_MEMORY_CONTACT_RATE_LIMITS[key].append(now)
    if len(_IN_MEMORY_CONTACT_RATE_LIMITS) > _MAX_IN_MEMORY_RATE_ENTRIES:
        for k in list(_IN_MEMORY_CONTACT_RATE_LIMITS.keys())[:200]:
            _IN_MEMORY_CONTACT_RATE_LIMITS.pop(k, None)
    return True


async def check_contact_submission_rate_limit(request: Request, site_slug: str) -> None:
    """
    Enforce rate limits on public contact form submissions by client IP and site slug.
    Limits: 5 submissions per 60s per site, 20 submissions per 60s globally.
    """
    ip = resolve_client_ip(request)
    site_key = f"rate_limit:contact:site:{site_slug}:{ip}"
    global_key = f"rate_limit:contact:global:{ip}"
    window = 60
    max_per_site = 5
    max_global = 20

    if redis_client.connected:
        try:
            pipe = redis_client.pipeline()
            pipe.incr(site_key)
            pipe.incr(global_key)
            site_count, global_count = await pipe.execute()

            expire_pipe = redis_client.pipeline()
            if site_count == 1:
                expire_pipe.expire(site_key, window)
            if global_count == 1:
                expire_pipe.expire(global_key, window)
            if len(expire_pipe.command_stack) > 0:
                await expire_pipe.execute()

            if site_count > max_per_site or global_count > max_global:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many contact submissions. Please try again later.",
                )
            return
        except HTTPException:
            raise
        except Exception as e:
            logger.debug("Redis contact rate limit error, using in-memory fallback", error=str(e))

    # Process-local fallback
    site_ok = _check_in_memory_contact_rate_limit(site_key, max_limit=max_per_site, window_seconds=window)
    global_ok = _check_in_memory_contact_rate_limit(global_key, max_limit=max_global, window_seconds=window)
    if not site_ok or not global_ok:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many contact submissions. Please try again later.",
        )


class ContactFormPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr = Field(max_length=254)
    phone: str | None = Field(default=None, max_length=50)
    message: str = Field(min_length=1, max_length=5000)
    hp_comment: str | None = Field(default=None, max_length=200)


@router.post("/api/v1/website/public/{site_slug}/contact", status_code=status.HTTP_201_CREATED)
async def submit_contact_form(
    site_slug: str,
    payload: ContactFormPayload,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Record a visitor contact submission on a published website with anti-abuse defenses."""
    # 1. Enforce rate limiting
    await check_contact_submission_rate_limit(request, site_slug)

    # 2. Resolve published site
    project, _, _ = await _resolve_published_site_and_snapshot(site_slug, session)

    # 3. Anti-spam honeypot evaluation
    if payload.hp_comment:
        logger.info(
            "Contact form honeypot triggered; silently dropping bot submission",
            site_slug=site_slug,
            project_id=project.id,
        )
        return {
            "success": True,
            "message": "Thank you for reaching out. Your message has been received.",
            "id": 0,
        }

    # 4. Save submission
    submission = WebsiteContactSubmission(
        project_id=project.id,
        sender_name=payload.name.strip(),
        sender_email=payload.email.strip().lower(),
        sender_phone=payload.phone.strip() if payload.phone else None,
        message=payload.message.strip(),
    )
    session.add(submission)
    try:
        await session.commit()
        await session.refresh(submission)
    except Exception as e:
        await session.rollback()
        logger.error(
            "Failed saving contact form submission",
            site_slug=site_slug,
            project_id=project.id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to process contact submission at this time.",
        )

    # 5. Safe audit logging without PII (email, phone, message)
    logger.info(
        "Contact form submission recorded",
        project_id=project.id,
        site_slug=site_slug,
        submission_id=submission.id,
    )

    return {
        "success": True,
        "message": "Thank you for reaching out. Your message has been received.",
        "id": submission.id,
    }


# ── Public Visitor Consent Endpoint (Phase 10F) ───────────────────────────

@router.post(
    "/api/v1/website/public/{site_slug}/consent",
    response_model=VisitorConsentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record visitor consent preferences for a published website",
)
async def record_visitor_consent(
    site_slug: str,
    payload: VisitorConsentPayload,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> VisitorConsentResponse:
    """Record visitor consent choices with privacy hashing (no raw PII persisted)."""
    project, _, _ = await _resolve_published_site_and_snapshot(site_slug, session)

    client_ip = resolve_client_ip(request)
    ua = request.headers.get("User-Agent", "")
    ip_hash = hashlib.sha256(client_ip.encode("utf-8")).hexdigest() if client_ip else None
    ua_hash = hashlib.sha256(ua.encode("utf-8")).hexdigest() if ua else None

    is_advertising_granted = payload.categories.get("advertising", True)

    log = WebsiteConsentLog(
        project_id=project.id,
        consent_type="visitor_consent",
        consent_scope=payload.categories,
        version=payload.version,
        is_granted=is_advertising_granted,
        client_ip_hash=ip_hash,
        user_agent_hash=ua_hash,
        recorded_at=datetime.now(timezone.utc),
    )
    session.add(log)
    await session.commit()
    await session.refresh(log)

    return VisitorConsentResponse(
        success=True,
        message="Visitor consent recorded successfully",
        consent_id=log.id,
        categories=payload.categories,
    )


# ── Lightweight Publisher JS SDK Route (Phase 10E) ─────────────────────────

@router.get("/api/v1/sdk.js", summary="LiteAds / RivinityADS JS SDK file")
async def get_publisher_sdk():
    """Serve the lightweight JavaScript SDK for classic and hosted website publisher ad placements."""
    from pathlib import Path

    static_dir = Path(__file__).parent.parent / "static"
    sdk_path = static_dir / "rivinity.js"
    if not sdk_path.exists():
        sdk_path = static_dir / "liteads.js"

    if sdk_path.exists():
        content = sdk_path.read_text(encoding="utf-8")
        return Response(
            content=content,
            media_type="application/javascript",
            headers={
                "Cache-Control": "public, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return Response(
        content="// RivinityADS JS SDK v1\n(function(){console.info('RivinityADS SDK initialized.');})();",
        media_type="application/javascript",
        headers={"X-Content-Type-Options": "nosniff"},
    )


# ── Authenticated Draft Preview Route ──────────────────────────────────────

@router.get("/preview/{token}", response_class=HTMLResponse)
async def preview_draft_website(
    token: str,
    request: Request,
    page: str = "",
    path: str = "",
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """
    Secure token-based preview route for unpublished drafts.

    Guarantees:
    - Never cached in public or CDN caches (no-store, no-cache, must-revalidate, max-age=0).
    - Prevents search indexing (X-Robots-Tag: noindex, nofollow, noarchive).
    - Prevents referrer leakage of preview token (Referrer-Policy: no-referrer).
    - Requires valid HMAC signature and unexpired token.
    - Displays a clear draft indicator banner.
    - Renders mock ad placements only.
    """
    verified = verify_preview_token(token)
    if verified is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired preview token.",
        )

    project_id, user_id = verified
    stmt = select(WebsiteProject).where(
        WebsiteProject.id == project_id,
        WebsiteProject.is_deleted == False,
    )
    res = await session.execute(stmt)
    project = res.scalar_one_or_none()

    if project is None:
        raise HTTPException(status_code=404, detail="Website project not found")

    if project.status in (WebsiteStatus.SUSPENDED, WebsiteStatus.ARCHIVED):
        raise HTTPException(status_code=403, detail="Website project is suspended or archived.")

    # Load draft pages from DB
    p_stmt = (
        select(WebsitePage)
        .where(WebsitePage.project_id == project.id)
        .order_by(WebsitePage.order_index.asc())
    )
    p_res = await session.execute(p_stmt)
    draft_pages = p_res.scalars().all()

    if not draft_pages:
        raise HTTPException(status_code=404, detail="Website project has no draft pages.")

    pages_dict = [
        {
            "id": p.id,
            "title": p.title,
            "slug": p.slug,
            "is_home": p.is_home,
            "seo_meta": p.seo_meta,
            "blocks": p.blocks,
        }
        for p in draft_pages
    ]

    target_page = page or path or ""
    clean_page_slug = target_page.strip().strip("/").lower()
    if not clean_page_slug:
        selected_page = next((p for p in pages_dict if p.get("is_home") or p.get("slug") == ""), None)
        if selected_page is None:
            selected_page = pages_dict[0]
    else:
        selected_page = next(
            (p for p in pages_dict if p.get("slug", "").strip("/").lower() == clean_page_slug and not (p.get("is_home") and p.get("slug") == "")),
            None,
        )

    if selected_page is None:
        raise HTTPException(status_code=404, detail=f"Draft page '/{clean_page_slug}' not found.")

    settings = get_settings()
    base_origin = f"{request.url.scheme}://{project.slug}.{settings.website.platform_domain}"

    rendered = website_renderer.render_published_page(
        project_dict={
            "name": project.name,
            "slug": project.slug,
            "theme_config": project.theme_config,
        },
        page_dict=selected_page,
        all_pages_dict=pages_dict,
        base_origin=base_origin,
        is_preview=True,
    )

    # Restrict iframe embedding solely to the authenticated editor origin
    editor_origins = ["'self'"]
    if settings.server.frontend_url and settings.server.frontend_url not in editor_origins:
        editor_origins.append(settings.server.frontend_url)
    if settings.debug or settings.env in ("dev", "test"):
        for dev_origin in ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"):
            if dev_origin not in editor_origins:
                editor_origins.append(dev_origin)
    frame_ancestors_str = " ".join(editor_origins)

    preview_headers = {
        **PUBLIC_SECURITY_HEADERS,
        "Content-Security-Policy": (
            "default-src 'none'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "img-src 'self' https:; "
            "script-src 'none'; "
            f"frame-ancestors {frame_ancestors_str}; "
            "form-action 'self'; "
            "base-uri 'none';"
        ),
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
        "Referrer-Policy": "no-referrer",
    }
    # Omit X-Frame-Options: DENY on preview responses so frame-ancestors governs permitted editor origins
    preview_headers.pop("X-Frame-Options", None)

    return HTMLResponse(
        content=rendered,
        headers=preview_headers,
    )


# ── SEO Endpoints: Sitemap & Robots ────────────────────────────────────────

@router.get("/sitemap.xml")
async def get_sitemap(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Generate dynamic XML sitemap containing strictly indexable published pages."""
    slug = resolve_subdomain_from_request(request)
    if not slug:
        raise HTTPException(status_code=404, detail="Sitemap not available on base platform domain.")

    project, version, snapshot = await _resolve_published_site_and_snapshot(slug, session)
    pages = snapshot.get("pages", [])
    settings = get_settings()
    base_origin = f"https://{slug}.{settings.website.platform_domain}"

    xml_content = website_renderer.render_sitemap_xml(
        project_dict=snapshot.get("project", {"name": project.name, "slug": project.slug}),
        pages_dict=pages,
        base_origin=base_origin,
        published_at_iso=version.published_at.isoformat() if version.published_at else None,
    )

    return Response(
        content=xml_content,
        media_type="application/xml",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/robots.txt", response_class=PlainTextResponse)
async def get_robots_txt(request: Request) -> PlainTextResponse:
    """Generate robots.txt directing search engines to the published sitemap."""
    slug = resolve_subdomain_from_request(request)
    if not slug:
        # Standard root platform robots.txt
        return PlainTextResponse("User-agent: *\nDisallow: /api/\nDisallow: /preview/\n")

    settings = get_settings()
    base_origin = f"https://{slug}.{settings.website.platform_domain}"
    robots_content = website_renderer.render_robots_txt(base_origin=base_origin)
    return PlainTextResponse(
        content=robots_content,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=86400",
        },
    )


# ── Direct Path Routing (For testing and local environments without DNS) ──

@router.get("/sites/{site_slug}", response_class=HTMLResponse)
async def serve_site_home_by_path(
    site_slug: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Direct path access to published homepage (e.g. /sites/mysite)."""
    return await _serve_published_page_response(site_slug, "", request, session)


@router.get("/sites/{site_slug}/{page_slug:path}", response_class=HTMLResponse)
async def serve_site_page_by_path(
    site_slug: str,
    page_slug: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Direct path access to published secondary page (e.g. /sites/mysite/about)."""
    return await _serve_published_page_response(site_slug, page_slug, request, session)


# ── Subdomain Root & Page Catch-All ────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def serve_subdomain_home(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Serve published homepage when accessed via tenant subdomain."""
    subdomain = resolve_subdomain_from_request(request)
    if not subdomain:
        # Accessing root platform domain
        raise HTTPException(status_code=404, detail="Platform root. Visit /public-api-docs or dashboard.")
    return await _serve_published_page_response(subdomain, "", request, session)


@router.get("/{page_slug:path}", response_class=HTMLResponse)
async def serve_subdomain_page(
    page_slug: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Serve published secondary page when accessed via tenant subdomain."""
    subdomain = resolve_subdomain_from_request(request)
    if not subdomain:
        raise HTTPException(status_code=404, detail="Not found")
    return await _serve_published_page_response(subdomain, page_slug, request, session)
