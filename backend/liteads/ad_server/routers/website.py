"""Website builder management endpoints under /api/v1/website."""

import base64
import copy
from datetime import datetime, timezone
import json
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from liteads.ad_server.routers.website_public import create_preview_token
from liteads.ad_server.services.website_cache import website_cache
from liteads.ad_server.services.rate_limit_service import RateLimitService
from liteads.ad_server.services.website_ai_service import (
    WebsiteAIService,
    WebsiteAIConfigError,
    WebsiteAIUnavailableError,
    WebsiteAIMalformedResponseError,
    WebsiteAISecurityError,
)
from liteads.ad_server.services.website_storage import (
    website_storage,
    InvalidFileTypeError,
    PathTraversalError,
    StorageFileNotFoundError,
    StorageQuotaExceededError,
    SymlinkEscapeError,
)
from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.logger import get_logger
from liteads.common.security import current_user, require_roles, verified_user
from liteads.ad_server.services.monetization_eligibility import evaluate_monetization_eligibility
from liteads.models.auth import AuditLog, User
from liteads.models.base import Status
from liteads.models.publisher import AdSlot, Publisher, PublisherProperty
from liteads.models.website import (
    CustomDomainStatus,
    WebsiteConsentLog,
    WebsiteMonetizationStatus,
    WebsitePage,
    WebsiteProject,
    WebsiteStatus,
    WebsiteTemplate,
    WebsiteVersion,
)
from liteads.schemas.business_profile import (
    BusinessProfileAudit,
    BusinessProfileIntake,
    CustomDomainConfigureRequest,
    CustomDomainStatusResponse,
    GeoReadinessReport,
    WebmasterToolsConfig,
)
from liteads.ad_server.services.business_profile_service import business_profile_service
from liteads.ad_server.services.domain_verification import verify_dns_txt
from liteads.schemas.website import (
    BoundSlotResponse,
    MonetizationConsentRequest,
    MonetizationStatusResponse,
    PageCreate,
    PageReorderRequest,
    PageResponse,
    PageSummaryResponse,
    PageUpdate,
    ProjectCreate,
    ProjectResponse,
    ProjectSummaryResponse,
    ProjectUpdate,
    PublisherLinkRequest,
    PublishRequest,
    RollbackRequest,
    SlugCheckResponse,
    StorageFileUploadRequest,
    StorageUsageResponse,
    StoredFileResponse,
    VersionSummaryResponse,
    WebsiteAIGenerateSectionRequest,
    WebsiteAIGenerateSectionResponse,
    WebsiteTemplateDetailResponse,
    WebsiteTemplateSummaryResponse,
    RESERVED_SLUGS,
    SLUG_PATTERN,
)
from liteads.ad_server.services.website_templates_catalog import seed_website_templates_if_empty

logger = get_logger(__name__)

router = APIRouter(
    tags=["website"],
    dependencies=[Depends(verified_user), Depends(require_roles("admin", "advertiser", "publisher"))],
)


# ── Ownership & Access Guards ──────────────────────────────────────────────

async def _get_project_with_access(
    project_id: int,
    user: User,
    session: AsyncSession,
    for_update: bool = False,
) -> WebsiteProject:
    """Retrieve project and assert tenant ownership or admin authorization."""
    stmt = (
        select(WebsiteProject)
        .options(selectinload(WebsiteProject.pages))
        .where(WebsiteProject.id == project_id, WebsiteProject.is_deleted.is_(False))
    )
    if for_update:
        stmt = stmt.with_for_update()
    res = await session.execute(stmt)
    project = res.scalar_one_or_none()

    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website project not found")

    if user.role != "admin" and project.owner_user_id != user.id:
        # Return 404 to prevent tenant ID enumeration
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website project not found")

    return project


# ── Project Endpoints ──────────────────────────────────────────────────────

@router.get("/slug/check", response_model=SlugCheckResponse)
@router.get("/check-slug", response_model=SlugCheckResponse)
async def check_slug_availability(
    slug: Annotated[str, Query(min_length=1, max_length=63)],
    session: AsyncSession = Depends(get_session),
) -> SlugCheckResponse:
    """Check if a proposed platform subdomain slug is available."""
    clean_slug = slug.strip().lower()

    if clean_slug in RESERVED_SLUGS:
        return SlugCheckResponse(
            slug=clean_slug,
            available=False,
            reason=f"Subdomain '{clean_slug}' is reserved by the platform.",
        )

    if not SLUG_PATTERN.match(clean_slug) or "--" in clean_slug:
        return SlugCheckResponse(
            slug=clean_slug,
            available=False,
            reason="Slug must be 3-63 characters, start and end with alphanumeric, and have no consecutive hyphens.",
        )

    stmt = select(WebsiteProject.id).where(
        WebsiteProject.slug == clean_slug, WebsiteProject.is_deleted.is_(False)
    )
    existing = await session.execute(stmt)
    if existing.scalar_one_or_none() is not None:
        return SlugCheckResponse(
            slug=clean_slug, available=False, reason="Subdomain slug is already in use."
        )

    return SlugCheckResponse(slug=clean_slug, available=True, reason=None)


# ── Template Catalog Endpoints (Phase 2) ───────────────────────────────────

@router.get("/templates", response_model=list[WebsiteTemplateSummaryResponse])
async def list_templates(
    category: str | None = Query(default=None, description="Filter by category"),
    search: str | None = Query(default=None, description="Search by name or description"),
    session: AsyncSession = Depends(get_session),
) -> list[WebsiteTemplateSummaryResponse]:
    """List active website templates available for website creation."""
    await seed_website_templates_if_empty(session)

    stmt = select(WebsiteTemplate).where(WebsiteTemplate.is_active.is_(True))
    if category and category.strip() and category.strip().lower() != "all":
        stmt = stmt.where(func.lower(WebsiteTemplate.category) == category.strip().lower())
    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(WebsiteTemplate.name).ilike(term)
            | func.lower(WebsiteTemplate.description).ilike(term)
        )
    stmt = stmt.order_by(WebsiteTemplate.order_index, WebsiteTemplate.id)
    res = await session.execute(stmt)
    return list(res.scalars().all())


@router.get("/templates/{template_id}", response_model=WebsiteTemplateDetailResponse)
async def get_template(
    template_id: str,
    session: AsyncSession = Depends(get_session),
) -> WebsiteTemplateDetailResponse:
    """Retrieve full details of an active website template."""
    await seed_website_templates_if_empty(session)

    stmt = select(WebsiteTemplate).where(
        WebsiteTemplate.id == template_id.strip(),
        WebsiteTemplate.is_active.is_(True),
    )
    res = await session.execute(stmt)
    tmpl = res.scalar_one_or_none()
    if tmpl is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Website template '{template_id}' not found.",
        )
    return tmpl


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Create a new website project and initialize default pages from the chosen template."""
    # 1. Authoritative check for slug uniqueness
    stmt = select(WebsiteProject.id).where(
        WebsiteProject.slug == payload.slug, WebsiteProject.is_deleted.is_(False)
    )
    res = await session.execute(stmt)
    if res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Subdomain slug '{payload.slug}' is already taken.",
        )

    # 2. Validate template_id from catalog (idempotent seeding if empty)
    await seed_website_templates_if_empty(session)
    stmt_tmpl = select(WebsiteTemplate).where(
        WebsiteTemplate.id == payload.template_id,
        WebsiteTemplate.is_active.is_(True),
    )
    res_tmpl = await session.execute(stmt_tmpl)
    template = res_tmpl.scalar_one_or_none()
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or inactive template_id '{payload.template_id}'. Please select a valid template from the catalog.",
        )

    # 3. Apply theme config (merging template defaults with any user-provided overrides)
    theme_data = dict(template.default_theme_config)
    payload_theme = payload.theme_config.model_dump()
    for k, v in payload_theme.items():
        if v:
            theme_data[k] = v

    project = WebsiteProject(
        owner_user_id=user.id,
        name=payload.name,
        slug=payload.slug,
        template_id=template.id,
        theme_config=theme_data,
        status=WebsiteStatus.DRAFT,
    )
    session.add(project)
    await session.flush()

    # 4. Deep-copy default pages and blocks from template
    default_pages = template.default_pages or []
    if not default_pages:
        # Fallback default home page
        default_hero_block = {
            "id": "blk_hero_init",
            "type": "hero",
            "is_visible": True,
            "content": {
                "headline": f"Welcome to {payload.name}",
                "subheadline": "We provide exceptional services to our valued clients.",
                "alignment": "center",
            },
        }
        home_page = WebsitePage(
            project_id=project.id,
            title="Home",
            slug="",
            is_home=True,
            order_index=0,
            seo_meta={"meta_title": payload.name, "meta_description": f"Welcome to {payload.name}"},
            blocks={"schema_version": 2, "blocks": [default_hero_block]},
        )
        session.add(home_page)
    else:
        for idx, page_def in enumerate(default_pages):
            p_is_home = page_def.get("is_home", idx == 0)
            p_slug = "" if p_is_home else page_def.get("slug", f"page-{idx}")
            p_title = page_def.get("title", "Home" if p_is_home else f"Page {idx}")
            p_blocks = copy.deepcopy(page_def.get("blocks", {"schema_version": 2, "blocks": []}))
            if template.id == "business_services" and p_is_home:
                for block in p_blocks.get("blocks", []):
                    if block.get("type") == "hero":
                        block.setdefault("content", {})["headline"] = f"Welcome to {payload.name}"
                        break
            p_seo = copy.deepcopy(page_def.get("seo_meta", {}))
            if not p_seo.get("meta_title"):
                p_seo["meta_title"] = payload.name
            if not p_seo.get("meta_description"):
                p_seo["meta_description"] = f"Welcome to {payload.name}"

            page = WebsitePage(
                project_id=project.id,
                title=p_title,
                slug=p_slug,
                is_home=p_is_home,
                order_index=page_def.get("order_index", idx),
                seo_meta=p_seo,
                blocks=p_blocks,
            )
            session.add(page)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Subdomain slug '{payload.slug}' is already taken.",
        )
    await session.refresh(project)

    return ProjectResponse(
        id=project.id,
        owner_user_id=project.owner_user_id,
        publisher_id=project.publisher_id,
        property_id=project.property_id,
        name=project.name,
        slug=project.slug,
        custom_domain=project.custom_domain,
        template_id=project.template_id,
        theme_config=project.theme_config,
        status=project.status,
        monetization_status=project.monetization_status,
        current_published_version_id=project.current_published_version_id,
        is_deleted=project.is_deleted,
        created_at=project.created_at,
        updated_at=project.updated_at,
        pages=[
            PageSummaryResponse(
                id=p.id,
                project_id=p.project_id,
                title=p.title,
                slug=p.slug,
                is_home=p.is_home,
                order_index=p.order_index,
                updated_at=p.updated_at,
            )
            for p in sorted(project.pages, key=lambda x: x.order_index)
        ],
    )


@router.get("/projects", response_model=list[ProjectSummaryResponse])
async def list_projects(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectSummaryResponse]:
    """List all website projects owned by the authenticated tenant."""
    query = (
        select(WebsiteProject)
        .options(selectinload(WebsiteProject.pages))
        .where(WebsiteProject.is_deleted.is_(False))
    )
    if user.role != "admin":
        query = query.where(WebsiteProject.owner_user_id == user.id)

    query = query.order_by(WebsiteProject.created_at.desc())
    res = await session.execute(query)
    projects = res.scalars().all()

    return [
        ProjectSummaryResponse(
            id=p.id,
            owner_user_id=p.owner_user_id,
            publisher_id=p.publisher_id,
            name=p.name,
            slug=p.slug,
            template_id=p.template_id,
            status=p.status,
            monetization_status=p.monetization_status,
            is_deleted=p.is_deleted,
            current_published_version_id=p.current_published_version_id,
            created_at=p.created_at,
            updated_at=p.updated_at,
            page_count=len(p.pages),
        )
        for p in projects
    ]


@router.get("/projects/{id}", response_model=ProjectResponse)
async def get_project(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Retrieve full project details, theme, and page directory."""
    project = await _get_project_with_access(id, user, session)
    return ProjectResponse(
        id=project.id,
        owner_user_id=project.owner_user_id,
        publisher_id=project.publisher_id,
        property_id=project.property_id,
        name=project.name,
        slug=project.slug,
        custom_domain=project.custom_domain,
        template_id=project.template_id,
        theme_config=project.theme_config,
        status=project.status,
        monetization_status=project.monetization_status,
        current_published_version_id=project.current_published_version_id,
        is_deleted=project.is_deleted,
        created_at=project.created_at,
        updated_at=project.updated_at,
        pages=[
            PageSummaryResponse(
                id=page.id,
                project_id=page.project_id,
                title=page.title,
                slug=page.slug,
                is_home=page.is_home,
                order_index=page.order_index,
                updated_at=page.updated_at,
            )
            for page in project.pages
        ],
    )


@router.patch("/projects/{id}", response_model=ProjectResponse)
@router.put("/projects/{id}", response_model=ProjectResponse)
async def update_project(
    id: int,
    payload: ProjectUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Update project name, template, or visual theme configuration."""
    project = await _get_project_with_access(id, user, session)

    if payload.name is not None:
        project.name = payload.name
    if payload.template_id is not None:
        project.template_id = payload.template_id
    if payload.theme_config is not None:
        project.theme_config = payload.theme_config.model_dump()

    # Pre-extract pages before commit to prevent async lazy-load expiration
    pages_summary = [
        PageSummaryResponse(
            id=page.id,
            project_id=page.project_id,
            title=page.title,
            slug=page.slug,
            is_home=page.is_home,
            order_index=page.order_index,
            updated_at=page.updated_at,
        )
        for page in project.pages
    ]

    await session.commit()
    await session.refresh(project)

    return ProjectResponse(
        id=project.id,
        owner_user_id=project.owner_user_id,
        publisher_id=project.publisher_id,
        property_id=project.property_id,
        name=project.name,
        slug=project.slug,
        custom_domain=project.custom_domain,
        template_id=project.template_id,
        theme_config=project.theme_config,
        status=project.status,
        monetization_status=project.monetization_status,
        current_published_version_id=project.current_published_version_id,
        is_deleted=project.is_deleted,
        created_at=project.created_at,
        updated_at=project.updated_at,
        pages=pages_summary,
    )


@router.delete("/projects/{id}", status_code=status.HTTP_200_OK)
async def delete_project(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Soft delete a website project and transition it to ARCHIVED status."""
    project = await _get_project_with_access(id, user, session)
    project.is_deleted = True
    project.status = WebsiteStatus.ARCHIVED
    await session.commit()
    await website_cache.invalidate_project(project.id, project.slug)
    return {"message": "Website project deleted successfully"}


# ── Page Endpoints ─────────────────────────────────────────────────────────

@router.get("/projects/{id}/pages", response_model=list[PageSummaryResponse])
async def list_pages(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PageSummaryResponse]:
    """List all pages belonging to the project ordered by menu order index."""
    project = await _get_project_with_access(id, user, session)
    stmt = (
        select(WebsitePage)
        .where(WebsitePage.project_id == project.id)
        .order_by(WebsitePage.order_index.asc())
    )
    res = await session.execute(stmt)
    pages = res.scalars().all()
    return [
        PageSummaryResponse(
            id=page.id,
            project_id=page.project_id,
            title=page.title,
            slug=page.slug,
            is_home=page.is_home,
            order_index=page.order_index,
            updated_at=page.updated_at,
        )
        for page in pages
    ]


@router.post("/projects/{id}/pages", response_model=PageResponse, status_code=status.HTTP_201_CREATED)
async def create_page(
    id: int,
    payload: PageCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WebsitePage:
    """Create a new page in a website project."""
    project = await _get_project_with_access(id, user, session)

    # 1. Enforce page slug uniqueness within project
    stmt = select(WebsitePage.id).where(
        WebsitePage.project_id == project.id, WebsitePage.slug == payload.slug
    )
    res = await session.execute(stmt)
    if res.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Page with slug '{payload.slug}' already exists in this project.",
        )

    # 2. If is_home is True, unset existing is_home on other pages
    if payload.is_home:
        for p in project.pages:
            if p.is_home:
                p.is_home = False

    # 3. Compute next order index
    next_order = len(project.pages)

    page = WebsitePage(
        project_id=project.id,
        title=payload.title,
        slug=payload.slug,
        is_home=payload.is_home,
        order_index=next_order,
        seo_meta=payload.seo_meta.model_dump(),
        blocks=payload.blocks.model_dump(),
    )
    session.add(page)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Page with slug '{payload.slug}' already exists in this project.",
        )
    await session.refresh(page)
    return page


@router.get("/projects/{id}/pages/{page_id}", response_model=PageResponse)
async def get_page(
    id: int,
    page_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WebsitePage:
    """Retrieve single page details and full draft blocks."""
    project = await _get_project_with_access(id, user, session)
    page = next((p for p in project.pages if p.id == page_id), None)
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")
    return page


@router.put("/projects/{id}/pages/{page_id}", response_model=PageResponse)
async def update_page(
    id: int,
    page_id: int,
    payload: PageUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WebsitePage:
    """Update page title, slug, SEO metadata, and structured blocks."""
    project = await _get_project_with_access(id, user, session)
    page = next((p for p in project.pages if p.id == page_id), None)
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")

    # 1. Slug uniqueness if modified
    if payload.slug is not None and payload.slug != page.slug:
        stmt = select(WebsitePage.id).where(
            WebsitePage.project_id == project.id,
            WebsitePage.slug == payload.slug,
            WebsitePage.id != page.id,
        )
        res = await session.execute(stmt)
        if res.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Page with slug '{payload.slug}' already exists in this project.",
            )
        page.slug = payload.slug

    # 2. Update is_home
    if payload.is_home is not None and payload.is_home != page.is_home:
        if payload.is_home:
            for p in project.pages:
                if p.id != page.id and p.is_home:
                    p.is_home = False
        page.is_home = payload.is_home

    if payload.title is not None:
        page.title = payload.title
    if payload.seo_meta is not None:
        page.seo_meta = payload.seo_meta.model_dump()
    if payload.blocks is not None:
        page.blocks = payload.blocks.model_dump()

    await session.commit()
    await session.refresh(page)
    return page


@router.patch("/projects/{id}/pages/reorder", response_model=list[PageSummaryResponse])
@router.post("/projects/{id}/pages/reorder", response_model=list[PageSummaryResponse])
async def reorder_pages(
    id: int,
    payload: PageReorderRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PageSummaryResponse]:
    """Reorder pages hierarchy for menu and navigation rendering."""
    project = await _get_project_with_access(id, user, session)
    page_map = {p.id: p for p in project.pages}

    # Verify all submitted page IDs belong to this project
    for pid in payload.page_ids:
        if pid not in page_map:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Page ID {pid} does not belong to this project.",
            )

    for idx, pid in enumerate(payload.page_ids):
        page_map[pid].order_index = idx

    # Pre-extract sorted pages before commit to prevent async lazy-load expiration
    sorted_pages = sorted(page_map.values(), key=lambda x: x.order_index)
    result = [
        PageSummaryResponse(
            id=p.id,
            project_id=p.project_id,
            title=p.title,
            slug=p.slug,
            is_home=p.is_home,
            order_index=p.order_index,
            updated_at=p.updated_at,
        )
        for p in sorted_pages
    ]

    await session.commit()
    return result


@router.delete("/projects/{id}/pages/{page_id}", status_code=status.HTTP_200_OK)
async def delete_page(
    id: int,
    page_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Delete a draft page from a project, protecting against deleting the required home page."""
    project = await _get_project_with_access(id, user, session)
    page = next((p for p in project.pages if p.id == page_id), None)
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")

    if page.is_home or page.slug == "":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the required home page. Designate another page as home first.",
        )

    if len(project.pages) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the only page in the project. At least one page must exist.",
        )

    if page in project.pages:
        project.pages.remove(page)
    await session.delete(page)
    await session.commit()
    session.expire(project, ["pages"])
    return {"message": "Page deleted successfully"}


# ── Publication, Versioning & Rollback ────────────────────────────────────

@router.post("/projects/{id}/publish", response_model=VersionSummaryResponse)
async def publish_project(
    id: int,
    payload: PublishRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> VersionSummaryResponse:
    """Freeze current draft project and pages into an immutable publication snapshot."""
    project = await _get_project_with_access(id, user, session, for_update=True)

    if project.status == WebsiteStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot publish a suspended website project. Please contact support.",
        )
    if project.status == WebsiteStatus.ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot publish an archived website project.",
        )

    if not project.pages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot publish a website project with zero pages.",
        )

    has_home = any(p.is_home or p.slug == "" for p in project.pages)
    if not has_home:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot publish a website project without a home page (is_home=True or slug='').",
        )

    # Phase 5.1A: Authoritative fail-closed validation of all ad placement blocks and bound ad slots
    bound_slots_meta: list[dict[str, Any]] = []
    ad_placements_found: list[dict[str, Any]] = []
    for page in project.pages:
        raw_b = page.blocks or {}
        if isinstance(raw_b, dict):
            for blk in raw_b.get("blocks", []):
                if isinstance(blk, dict) and blk.get("type") == "ad_placement":
                    ad_placements_found.append(blk)

    if ad_placements_found:
        has_bound_slots = any(
            bool((blk.get("content") or {}).get("ad_slot_id"))
            for blk in ad_placements_found
        )
        if has_bound_slots or project.monetization_status == WebsiteMonetizationStatus.ACTIVE_MONETIZED:
            if not project.publisher_id or not project.property_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot publish a website with ad placement blocks: project must be linked to a publisher and property first.",
                )

            # Property must exist, be active, and be verified
            prop_stmt = select(PublisherProperty).where(PublisherProperty.id == project.property_id)
            prop_res = await session.execute(prop_stmt)
            prop = prop_res.scalar_one_or_none()
            if prop is None or prop.status != Status.ACTIVE or prop.verification_status != "VERIFIED":
                ver_status = prop.verification_status if prop else "NONE"
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Publisher property for this website is not active or verified (verification status: {ver_status}).",
                )

        validated_slots: dict[str, AdSlot] = {}
        for blk in ad_placements_found:
            content = blk.get("content") or {}
            sid = content.get("ad_slot_id")
            if not sid:
                continue
            sid_str = str(sid).strip()
            if not sid_str:
                continue

            if sid_str not in validated_slots:
                s_stmt = select(AdSlot).where(AdSlot.slot_id == sid_str)
                s_res = await session.execute(s_stmt)
                slot_obj = s_res.scalar_one_or_none()

                if slot_obj is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"AdSlot '{sid_str}' does not exist.",
                    )
                if not project.publisher_id or not project.property_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Cannot publish a website with ad placement blocks: project must be linked to a publisher and property first.",
                    )
                if slot_obj.property_id is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"AdSlot '{sid_str}' is not associated with any publisher property.",
                    )
                if slot_obj.publisher_id != project.publisher_id or slot_obj.property_id != project.property_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"AdSlot '{sid_str}' does not belong to this website's publisher property.",
                    )
                if slot_obj.status != Status.ACTIVE:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"AdSlot '{sid_str}' is not active.",
                    )

                validated_slots[sid_str] = slot_obj
                bound_slots_meta.append({
                    "id": slot_obj.id,
                    "slot_id": slot_obj.slot_id,
                    "name": slot_obj.name,
                    "format": slot_obj.format,
                })
            else:
                slot_obj = validated_slots[sid_str]

            # Format check: placement format must match authoritative slot format
            placement_format = content.get("format")
            if placement_format is not None and int(placement_format) != int(slot_obj.format):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Format mismatch for AdSlot '{sid_str}': placement requires format {placement_format}, but slot is configured as format {slot_obj.format}.",
                )

    # Calculate next version number atomically
    stmt = select(func.max(WebsiteVersion.version_number)).where(
        WebsiteVersion.project_id == project.id
    )
    res = await session.execute(stmt)
    max_ver = res.scalar() or 0
    next_version = max_ver + 1

    # Freeze snapshot
    snapshot_data = {
        "project": {
            "id": project.id,
            "name": project.name,
            "slug": project.slug,
            "template_id": project.template_id,
            "theme_config": project.theme_config,
            "publisher_id": project.publisher_id,
            "property_id": project.property_id,
            "monetization_status": project.monetization_status,
            "business_profile": project.business_profile,
            "webmaster_tools": project.webmaster_tools,
            "primary_language": project.primary_language,
            "custom_domain": project.custom_domain,
        },
        "pages": [
            {
                "id": page.id,
                "title": page.title,
                "slug": page.slug,
                "is_home": page.is_home,
                "order_index": page.order_index,
                "seo_meta": page.seo_meta,
                "blocks": page.blocks,
                "language": getattr(page, "language", "en"),
                "translation_group_id": getattr(page, "translation_group_id", None),
            }
            for page in project.pages
        ],
        "ad_slots": bound_slots_meta,
        "snapshot_version": 1,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }

    version = WebsiteVersion(
        project_id=project.id,
        version_number=next_version,
        published_by_user_id=user.id,
        snapshot_data=snapshot_data,
        notes=payload.notes,
        published_at=datetime.now(timezone.utc),
    )
    session.add(version)
    await session.flush()

    # Update project pointer
    project.current_published_version_id = version.id
    project.status = WebsiteStatus.PUBLISHED

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent publication is in progress for this project. Please retry.",
        )
    await session.refresh(version)
    await website_cache.invalidate_project(project.id, project.slug)

    return VersionSummaryResponse(
        id=version.id,
        project_id=version.project_id,
        version_number=version.version_number,
        published_by_user_id=version.published_by_user_id,
        notes=version.notes,
        published_at=version.published_at,
        rollback_from_version_id=version.rollback_from_version_id,
    )


@router.post("/projects/{id}/unpublish", status_code=status.HTTP_200_OK)
async def unpublish_project(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Take a published site offline without deleting publication history."""
    project = await _get_project_with_access(id, user, session)
    if project.status == WebsiteStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify a suspended website project.",
        )
    project.status = WebsiteStatus.DRAFT
    project.current_published_version_id = None
    await session.commit()
    await website_cache.invalidate_project(project.id, project.slug)
    return {
        "message": "Website unpublished successfully",
        "status": WebsiteStatus.DRAFT,
        "current_published_version_id": None,
    }


@router.get("/projects/{id}/versions", response_model=list[VersionSummaryResponse])
async def list_versions(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[VersionSummaryResponse]:
    """List historical publication snapshots for this project."""
    project = await _get_project_with_access(id, user, session)
    stmt = (
        select(WebsiteVersion)
        .where(WebsiteVersion.project_id == project.id)
        .order_by(WebsiteVersion.version_number.desc())
    )
    res = await session.execute(stmt)
    versions = res.scalars().all()
    return [
        VersionSummaryResponse(
            id=v.id,
            project_id=v.project_id,
            version_number=v.version_number,
            published_by_user_id=v.published_by_user_id,
            notes=v.notes,
            published_at=v.published_at,
            rollback_from_version_id=v.rollback_from_version_id,
        )
        for v in versions
    ]


@router.post("/projects/{id}/versions/{version_id}/rollback", response_model=VersionSummaryResponse)
async def rollback_version(
    id: int,
    version_id: int,
    payload: RollbackRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> VersionSummaryResponse:
    """Restore project and page content from an earlier snapshot while preserving history."""
    project = await _get_project_with_access(id, user, session, for_update=True)

    if project.status == WebsiteStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot rollback a suspended website project. Please contact support.",
        )
    if project.status == WebsiteStatus.ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot rollback an archived website project.",
        )

    # 1. Fetch historical version to restore
    stmt = select(WebsiteVersion).where(
        WebsiteVersion.id == version_id, WebsiteVersion.project_id == project.id
    )
    res = await session.execute(stmt)
    historical_version = res.scalar_one_or_none()
    if historical_version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target version not found")

    snapshot = historical_version.snapshot_data
    proj_snap = snapshot.get("project", {})
    pages_snap = snapshot.get("pages", [])

    # 2. Restore project settings
    if "name" in proj_snap:
        project.name = proj_snap["name"]
    if "template_id" in proj_snap:
        project.template_id = proj_snap["template_id"]
    if "theme_config" in proj_snap:
        project.theme_config = proj_snap["theme_config"]

    # 3. Restore pages safely: match existing pages by ID or slug and restore content in place
    existing_pages = {p.id: p for p in project.pages}
    existing_by_slug = {p.slug: p for p in project.pages}
    restored_page_ids = set()

    for p_data in pages_snap:
        snap_id = p_data.get("id")
        target_page = None
        if snap_id and snap_id in existing_pages:
            target_page = existing_pages[snap_id]
        elif p_data.get("slug", "") in existing_by_slug:
            target_page = existing_by_slug[p_data.get("slug", "")]

        if target_page is not None:
            restored_page_ids.add(target_page.id)
            target_page.title = p_data.get("title", target_page.title)
            target_page.slug = p_data.get("slug", target_page.slug)
            target_page.is_home = p_data.get("is_home", target_page.is_home)
            target_page.order_index = p_data.get("order_index", target_page.order_index)
            target_page.seo_meta = p_data.get("seo_meta", {})
            target_page.blocks = p_data.get("blocks", {"schema_version": 1, "blocks": []})
        else:
            new_page = WebsitePage(
                project_id=project.id,
                title=p_data.get("title", "Untitled Page"),
                slug=p_data.get("slug", ""),
                is_home=p_data.get("is_home", False),
                order_index=p_data.get("order_index", 0),
                seo_meta=p_data.get("seo_meta", {}),
                blocks=p_data.get("blocks", {"schema_version": 1, "blocks": []}),
            )
            session.add(new_page)

    # Delete draft pages that were not part of the restored snapshot
    for page in list(project.pages):
        if page.id not in restored_page_ids:
            await session.delete(page)

    # 4. Generate new version entry recording rollback provenance
    ver_stmt = select(func.max(WebsiteVersion.version_number)).where(
        WebsiteVersion.project_id == project.id
    )
    ver_res = await session.execute(ver_stmt)
    max_ver = ver_res.scalar() or 0
    new_version_num = max_ver + 1

    notes = payload.notes or f"Rollback to version {historical_version.version_number}"
    new_version = WebsiteVersion(
        project_id=project.id,
        version_number=new_version_num,
        published_by_user_id=user.id,
        snapshot_data=snapshot,
        notes=notes,
        published_at=datetime.now(timezone.utc),
        rollback_from_version_id=historical_version.id,
    )
    session.add(new_version)
    await session.flush()

    project.current_published_version_id = new_version.id
    project.status = WebsiteStatus.PUBLISHED

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent rollback or publication is in progress for this project. Please retry.",
        )
    await session.refresh(new_version)
    await website_cache.invalidate_project(project.id, project.slug)

    return VersionSummaryResponse(
        id=new_version.id,
        project_id=new_version.project_id,
        version_number=new_version.version_number,
        published_by_user_id=new_version.published_by_user_id,
        notes=new_version.notes,
        published_at=new_version.published_at,
        rollback_from_version_id=new_version.rollback_from_version_id,
    )


class PreviewTokenResponse(BaseModel):
    preview_token: str
    preview_url: str
    expires_in: int


@router.post("/projects/{id}/preview-token", response_model=PreviewTokenResponse)
async def generate_preview_token(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> PreviewTokenResponse:
    """Generate a short-lived cryptographic preview token for unpublished drafts."""
    project = await _get_project_with_access(id, user, session)
    if project.status == WebsiteStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot preview a suspended website project.",
        )
    settings = get_settings()
    ttl = settings.website.preview_ttl_seconds
    token = create_preview_token(project.id, user.id, ttl_seconds=ttl)
    return PreviewTokenResponse(
        preview_token=token,
        preview_url=f"/preview/{token}",
        expires_in=ttl,
    )


@router.post("/projects/{id}/suspend", status_code=status.HTTP_200_OK)
async def suspend_project(
    id: int,
    user: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Admin moderation takedown endpoint to immediately suspend a website project."""
    project = await _get_project_with_access(id, user, session, for_update=True)
    project.status = WebsiteStatus.SUSPENDED
    await session.commit()
    await website_cache.invalidate_project(project.id, project.slug)
    return {
        "message": "Website project suspended successfully",
        "status": WebsiteStatus.SUSPENDED,
    }


# ── Phase 10E & 10F: Publisher Linkage, Ad-Slot Binding & Monetization Routes ──

@router.post("/projects/{id}/publisher-link", response_model=ProjectResponse)
async def link_project_publisher(
    id: int,
    payload: PublisherLinkRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Associate website project with an authorized publisher account and property."""
    project = await _get_project_with_access(id, user, session, for_update=True)
    settings = get_settings()

    # Verify publisher tenant authorization
    if user.role != "admin" and user.publisher_id != payload.publisher_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Publisher account does not belong to your tenant account.",
        )

    pub = await session.get(Publisher, payload.publisher_id)
    if pub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publisher not found.")

    prop = await session.get(PublisherProperty, payload.property_id)
    if prop is None or prop.publisher_id != payload.publisher_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Property does not exist or does not belong to the specified publisher.",
        )

    # Check for platform subdomain: automatic verification for hosted platform subdomains
    platform_domain = settings.website.platform_domain.lower()
    norm_prop_domain = prop.domain.strip().lower()
    expected_platform_host = f"{project.slug}.{platform_domain}"

    if norm_prop_domain in (expected_platform_host, project.slug):
        prop.is_platform_subdomain = True
        prop.verification_status = "VERIFIED"
        prop.verification_method = "PLATFORM_SUBDOMAIN"
        prop.verified_at = datetime.now(timezone.utc)
        prop.status = Status.ACTIVE
        prop.last_error = None

    project.publisher_id = payload.publisher_id
    project.property_id = payload.property_id

    await session.commit()
    await session.refresh(project)

    pages_summary = [
        PageSummaryResponse(
            id=p.id,
            project_id=p.project_id,
            title=p.title,
            slug=p.slug,
            is_home=p.is_home,
            order_index=p.order_index,
            updated_at=p.updated_at,
        )
        for p in project.pages
    ]

    return ProjectResponse(
        id=project.id,
        owner_user_id=project.owner_user_id,
        publisher_id=project.publisher_id,
        property_id=project.property_id,
        name=project.name,
        slug=project.slug,
        custom_domain=project.custom_domain,
        template_id=project.template_id,
        theme_config=project.theme_config,
        status=project.status,
        monetization_status=project.monetization_status,
        current_published_version_id=project.current_published_version_id,
        is_deleted=project.is_deleted,
        created_at=project.created_at,
        updated_at=project.updated_at,
        pages=pages_summary,
    )


@router.get("/projects/{id}/slots", response_model=list[BoundSlotResponse])
async def list_project_slots(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[BoundSlotResponse]:
    """List eligible active AdSlots available for binding to this website project."""
    project = await _get_project_with_access(id, user, session)

    if not project.publisher_id or not project.property_id:
        return []

    stmt = select(AdSlot).where(
        AdSlot.publisher_id == project.publisher_id,
        AdSlot.property_id == project.property_id,
        AdSlot.status == Status.ACTIVE,
    ).order_by(AdSlot.id.asc())
    res = await session.execute(stmt)
    slots = res.scalars().all()

    return [
        BoundSlotResponse(
            id=s.id,
            slot_id=s.slot_id,
            name=s.name,
            format=s.format,
            width=s.width,
            height=s.height,
            status=s.status,
            floor_price=float(s.floor_price or 0.0),
            property_id=s.property_id,
            is_chat_surface=s.is_chat_surface,
        )
        for s in slots
    ]


@router.get("/projects/{id}/monetization", response_model=MonetizationStatusResponse)
async def get_monetization_status(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MonetizationStatusResponse:
    """Authoritative evaluation of all monetization prerequisites for this project."""
    project = await _get_project_with_access(id, user, session)
    eligibility = await evaluate_monetization_eligibility(session, project)

    return MonetizationStatusResponse(
        project_id=project.id,
        is_eligible=eligibility.is_eligible,
        current_status=eligibility.current_status,
        prerequisites=eligibility.prerequisites,
        failure_reasons=eligibility.failure_reasons,
        slot_count=eligibility.slot_count,
        consent=project.monetization_consent,
    )


@router.post("/projects/{id}/monetization/consent", response_model=MonetizationStatusResponse)
async def record_publisher_consent(
    id: int,
    payload: MonetizationConsentRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MonetizationStatusResponse:
    """Record publisher agreement to monetization terms and ad disclosures."""
    project = await _get_project_with_access(id, user, session, for_update=True)

    consent_record = {
        "accepted_terms": payload.accepted_terms,
        "version": payload.version,
        "scope": payload.scope,
        "agreed_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user.id,
    }
    project.monetization_consent = consent_record

    log = WebsiteConsentLog(
        project_id=project.id,
        consent_type="publisher_terms",
        consent_scope=payload.scope,
        version=payload.version,
        is_granted=payload.accepted_terms,
        recorded_at=datetime.now(timezone.utc),
    )
    session.add(log)

    await session.commit()
    await session.refresh(project)

    eligibility = await evaluate_monetization_eligibility(session, project)
    return MonetizationStatusResponse(
        project_id=project.id,
        is_eligible=eligibility.is_eligible,
        current_status=eligibility.current_status,
        prerequisites=eligibility.prerequisites,
        failure_reasons=eligibility.failure_reasons,
        slot_count=eligibility.slot_count,
        consent=project.monetization_consent,
    )


@router.post("/projects/{id}/monetization/enable", response_model=MonetizationStatusResponse)
async def enable_monetization(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MonetizationStatusResponse:
    """Activate live monetization after verifying all required prerequisites."""
    project = await _get_project_with_access(id, user, session, for_update=True)

    eligibility = await evaluate_monetization_eligibility(session, project)
    if not eligibility.is_eligible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot enable monetization: " + "; ".join(eligibility.failure_reasons),
        )

    project.monetization_status = WebsiteMonetizationStatus.ACTIVE_MONETIZED

    log = WebsiteConsentLog(
        project_id=project.id,
        consent_type="monetization_enabled",
        consent_scope={"ad_delivery": True, "active": True},
        version="v1.0",
        is_granted=True,
        recorded_at=datetime.now(timezone.utc),
    )
    session.add(log)

    await session.commit()
    await session.refresh(project)
    await website_cache.invalidate_project(project.id, project.slug)

    return MonetizationStatusResponse(
        project_id=project.id,
        is_eligible=True,
        current_status=project.monetization_status,
        prerequisites=eligibility.prerequisites,
        failure_reasons=[],
        slot_count=eligibility.slot_count,
        consent=project.monetization_consent,
    )


@router.post("/projects/{id}/monetization/disable", response_model=MonetizationStatusResponse)
async def disable_monetization(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MonetizationStatusResponse:
    """Disable monetization immediately, falling back to non-billable ad placeholders."""
    project = await _get_project_with_access(id, user, session, for_update=True)

    prev_status = project.monetization_status
    project.monetization_status = WebsiteMonetizationStatus.DISABLED
    project.monetization_consent = None

    # Clean out any ad_placement blocks from project draft pages (Task J: informed consent)
    for page in project.pages:
        blocks_data = page.blocks or {}
        if isinstance(blocks_data, dict):
            raw_blocks = blocks_data.get("blocks", [])
            filtered = [b for b in raw_blocks if b.get("type") != "ad_placement"]
            blocks_data["blocks"] = filtered
            page.blocks = blocks_data

    log = WebsiteConsentLog(
        project_id=project.id,
        consent_type="monetization_disabled",
        consent_scope={"ad_delivery": False, "active": False},
        version="v1.0",
        is_granted=False,
        recorded_at=datetime.now(timezone.utc),
    )
    session.add(log)

    session.add(
        AuditLog(
            user_id=user.id,
            action="website_monetization_disabled",
            details=json.dumps({
                "project_id": project.id,
                "target": f"website_project:{project.id}",
                "actor_email": user.email,
                "actor_role": user.role,
                "previous_state": int(prev_status) if prev_status is not None else None,
                "new_state": int(WebsiteMonetizationStatus.DISABLED),
                "reason": "Website monetization disabled",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }),
        )
    )

    await session.commit()
    await session.refresh(project)
    await website_cache.invalidate_project(project.id, project.slug)

    eligibility = await evaluate_monetization_eligibility(session, project)
    return MonetizationStatusResponse(
        project_id=project.id,
        is_eligible=eligibility.is_eligible,
        current_status=project.monetization_status,
        prerequisites=eligibility.prerequisites,
        failure_reasons=eligibility.failure_reasons,
        slot_count=eligibility.slot_count,
        consent=project.monetization_consent,
    )


# ── Tenant-Scoped Storage Endpoints ────────────────────────────────────────

@router.post(
    "/projects/{id}/storage/files",
    response_model=StoredFileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_project_file(
    id: int,
    payload: StorageFileUploadRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> StoredFileResponse:
    """Upload a file to the tenant's isolated website storage directory."""
    project = await _get_project_with_access(id, user, session)

    if payload.content_base64:
        try:
            content_bytes = base64.b64decode(payload.content_base64)
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid base64 payload")
    elif payload.content_text is not None:
        content_bytes = payload.content_text.encode("utf-8")
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either content_base64 or content_text must be provided",
        )

    try:
        meta = website_storage.save_file(
            owner_user_id=project.owner_user_id,
            project_id=project.id,
            relative_path=payload.relative_path,
            content=content_bytes,
        )
    except PathTraversalError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except SymlinkEscapeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except InvalidFileTypeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except StorageQuotaExceededError as e:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(e))

    return StoredFileResponse(**meta)


@router.get(
    "/projects/{id}/storage/files",
    response_model=list[StoredFileResponse],
)
async def list_project_files(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[StoredFileResponse]:
    """List all stored assets belonging strictly to this project."""
    project = await _get_project_with_access(id, user, session)
    files = website_storage.list_files(project.owner_user_id, project.id)
    return [StoredFileResponse(**f) for f in files]


@router.get(
    "/projects/{id}/storage/files/{file_path:path}",
)
async def get_project_file(
    id: int,
    file_path: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Retrieve a stored asset from the tenant's isolated directory."""
    project = await _get_project_with_access(id, user, session)
    try:
        content_bytes, media_type = website_storage.read_file(
            project.owner_user_id, project.id, file_path
        )
    except StorageFileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    except PathTraversalError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except SymlinkEscapeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except InvalidFileTypeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return Response(content=content_bytes, media_type=media_type)


@router.delete(
    "/projects/{id}/storage/files/{file_path:path}",
)
async def delete_project_file(
    id: int,
    file_path: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Delete a stored asset from the project directory."""
    project = await _get_project_with_access(id, user, session)
    try:
        deleted = website_storage.delete_file(project.owner_user_id, project.id, file_path)
    except PathTraversalError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except SymlinkEscapeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except InvalidFileTypeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return {"message": "File deleted successfully"}


@router.get(
    "/projects/{id}/storage/usage",
    response_model=StorageUsageResponse,
)
async def get_project_storage_usage(
    id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> StorageUsageResponse:
    """Retrieve storage quota and byte usage for the website project."""
    project = await _get_project_with_access(id, user, session)
    usage = website_storage.get_project_usage(project.owner_user_id, project.id)
    return StorageUsageResponse(**usage)


# ── AI Generation Proxy ──────────────────────────────────────────────────────

@router.post(
    "/projects/{id}/ai/generate-section",
    response_model=WebsiteAIGenerateSectionResponse,
    status_code=status.HTTP_200_OK,
)
async def generate_ai_section(
    id: int,
    payload: WebsiteAIGenerateSectionRequest,
    response: Response,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WebsiteAIGenerateSectionResponse:
    """Generate an Open Design website section using the server-side AI provider proxy.

    Execution and Authorization Order:
    1. Authenticate user via JWT session dependency (verified_user + require_roles).
    2. Authorize project access via _get_project_with_access(id, user, session).
       Returns 404 for unauthorized or non-owned projects to prevent ID enumeration.
       CRITICAL: Unauthorized attempts terminate immediately with 0 quota consumption
       and 0 AI provider requests.
    3. Assert project is in valid editable state (not archived or suspended).
    4. Retrieve authoritative project theme_config (brand colors, fonts, radius) server-side.
    5. Check and reserve publisher AI quota using the authenticated user.id.
       Enforces burst limit (5 req/min) followed by daily quota (50 req/day, resets 00:00 UTC).
       Note on partial reservation: burst admission records an entry in the rolling window
       even if daily quota subsequently rejects the request.
    6. Call WebsiteAIService.generate_section with craft prompt compilation and Schema v2 sanitization.
    7. Return the validated BlockItem in memory without database persistence or live billing calls.
    """
    # Step 1, 2: Verify tenant ownership / access
    project = await _get_project_with_access(id, user, session)

    # Step 3: Project lifecycle checks
    if project.status == WebsiteStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot generate content for a suspended website project. Please contact support.",
        )
    if project.status == WebsiteStatus.ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot generate content for an archived website project.",
        )

    # Step 4: Authoritative theme config
    theme_config = project.theme_config or {}

    # Step 5: Quota & Rate Limit Reservation using authenticated user.id
    reservation = await RateLimitService.reserve_publisher_ai_quota(user_id=user.id)
    if not reservation.allowed:
        if reservation.reason == "burst_limit_exceeded" and reservation.burst:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="AI generation burst limit exceeded. Maximum 5 requests per minute allowed.",
                headers={
                    "Retry-After": str(reservation.burst.retry_after_seconds),
                    "X-RateLimit-Limit": str(reservation.burst.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reservation.burst.retry_after_seconds),
                },
            )
        elif reservation.reason == "daily_quota_exceeded" and reservation.quota:
            burst_limit = reservation.burst.limit if reservation.burst else 5
            burst_rem = reservation.burst.remaining if reservation.burst else 0
            burst_reset = reservation.burst.retry_after_seconds if reservation.burst else 60
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Daily AI generation quota exceeded. Limit is 50 requests per day (resets at 00:00 UTC).",
                headers={
                    "Retry-After": str(reservation.quota.reset_seconds),
                    "X-RateLimit-Limit": str(burst_limit),
                    "X-RateLimit-Remaining": str(burst_rem),
                    "X-RateLimit-Reset": str(burst_reset),
                    "X-Quota-Limit": str(reservation.quota.limit),
                    "X-Quota-Remaining": "0",
                    "X-Quota-Reset": reservation.quota.reset_timestamp_utc,
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="AI generation quota or rate limit exceeded.",
            )

    # Attach rate limit & quota headers to successful response
    if reservation.burst:
        response.headers["X-RateLimit-Limit"] = str(reservation.burst.limit)
        response.headers["X-RateLimit-Remaining"] = str(reservation.burst.remaining)
        response.headers["X-RateLimit-Reset"] = str(reservation.burst.retry_after_seconds)
    if reservation.quota:
        response.headers["X-Quota-Limit"] = str(reservation.quota.limit)
        response.headers["X-Quota-Remaining"] = str(reservation.quota.remaining)
        response.headers["X-Quota-Reset"] = str(reservation.quota.reset_timestamp_utc)

    # Step 6: Dispatch AI Generation with Craft Prompts & Schema v2 Sanitization
    ai_service = WebsiteAIService()
    try:
        gen_result = await ai_service.generate_section(
            prompt=payload.prompt,
            section_type=payload.section_type,
            theme_config=theme_config,
            context={"project_id": project.id, "project_name": project.name},
        )
    except WebsiteAIConfigError as exc:
        logger.error("website_ai_config_error", project_id=project.id, user_id=user.id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI generation service is not properly configured.",
        ) from exc
    except WebsiteAIUnavailableError as exc:
        logger.warning("website_ai_unavailable", project_id=project.id, user_id=user.id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI generation service temporarily unavailable. Please try again.",
        ) from exc
    except (WebsiteAIMalformedResponseError, WebsiteAISecurityError) as exc:
        logger.warning("website_ai_output_rejected", project_id=project.id, user_id=user.id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI provider generated invalid or unsafe section content. Please try again with different instructions.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Step 7: Return in-memory generation result without database persistence
    return WebsiteAIGenerateSectionResponse(
        block=gen_result.block,
        section_id=gen_result.section_id,
        section_type=gen_result.section_type,
        quota_remaining=reservation.quota.remaining if reservation.quota else 0,
        daily_limit=reservation.quota.limit if reservation.quota else 50,
        reset_timestamp_utc=reservation.quota.reset_timestamp_utc if reservation.quota else "",
    )


# ── Phase 6: Business Profile Questionnaire, Custom Domain, & Audits ──

@router.post(
    "/projects/{project_id}/business-intake",
    status_code=status.HTTP_200_OK,
    summary="Submit guided business questionnaire, audit claims, and generate draft site",
)
async def submit_business_intake(
    project_id: int,
    payload: BusinessProfileIntake,
    generate_draft: bool = Query(default=True, description="Generate complete editable draft pages from verified facts"),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Intake business profile facts (NAP, hours, offerings, etc.).
    Runs completeness audit and anti-fabrication check.
    Generates editable draft website (Home, About, Services, Contact, etc.) with 0 default ads.
    """
    project = await _get_project_with_access(project_id, user, session)
    audit = business_profile_service.audit_business_profile(payload)

    if generate_draft:
        pages = await business_profile_service.generate_draft_website_from_intake(project, payload, session)
        page_count = len(pages)
    else:
        project.business_profile = payload.model_dump()
        project.primary_language = payload.languages[0] if payload.languages else "en"
        await session.commit()
        await session.refresh(project)
        page_count = len(project.pages)

    return {
        "project_id": project.id,
        "audit": audit.model_dump(),
        "page_count": page_count,
        "message": "Business profile intake recorded and draft pages generated successfully.",
    }


@router.get(
    "/projects/{project_id}/business-profile",
    summary="Retrieve current business profile and audit report",
)
async def get_business_profile(
    project_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await _get_project_with_access(project_id, user, session)
    bp = project.business_profile
    audit = None
    if bp:
        audit = business_profile_service.audit_business_profile(BusinessProfileIntake(**bp)).model_dump()

    return {
        "project_id": project.id,
        "business_profile": bp,
        "audit": audit,
    }


@router.get(
    "/projects/{project_id}/accessibility-audit",
    summary="Run accessibility and Core Web Vitals readiness audit",
)
async def get_accessibility_audit(
    project_id: int,
    page_id: int | None = Query(default=None, description="Optional page ID to audit single page"),
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await _get_project_with_access(project_id, user, session)
    pages = project.pages

    if page_id:
        pages = [p for p in pages if p.id == page_id]
        if not pages:
            raise HTTPException(status_code=404, detail="Page not found in this project.")

    audits = []
    total_score = 0
    all_recommendations = set()

    for p in pages:
        p_audit = business_profile_service.audit_page_accessibility(p, project.theme_config)
        audits.append({
            "page_id": p.id,
            "page_title": p.title,
            "slug": p.slug,
            "report": p_audit.model_dump(),
        })
        total_score += p_audit.score
        all_recommendations.update(p_audit.recommendations)

    overall_score = round(total_score / len(pages)) if pages else 100

    return {
        "project_id": project.id,
        "overall_score": overall_score,
        "page_count": len(pages),
        "pages": audits,
        "consolidated_recommendations": sorted(list(all_recommendations)),
    }


@router.get(
    "/projects/{project_id}/geo-readiness",
    response_model=GeoReadinessReport,
    summary="Evaluate factual entity data and GEO / AI Search readiness",
)
async def get_geo_readiness(
    project_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> GeoReadinessReport:
    project = await _get_project_with_access(project_id, user, session)
    return business_profile_service.evaluate_geo_readiness(project)


@router.post(
    "/projects/{project_id}/custom-domain",
    response_model=CustomDomainStatusResponse,
    summary="Configure custom domain and issue cryptographic DNS verification challenge",
)
async def configure_custom_domain(
    project_id: int,
    payload: CustomDomainConfigureRequest,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> CustomDomainStatusResponse:
    project = await _get_project_with_access(project_id, user, session)
    settings = get_settings()

    domain = payload.custom_domain.lower()
    platform_domain = settings.website.platform_domain.lower()

    if domain == platform_domain or domain.endswith(f".{platform_domain}"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Domain cannot be a subdomain of the platform root ({platform_domain}). Use project slug for platform subdomains.",
        )

    # Check uniqueness across projects
    dup_stmt = select(WebsiteProject).where(
        WebsiteProject.custom_domain == domain,
        WebsiteProject.id != project.id,
        WebsiteProject.is_deleted == False,
    )
    dup_res = await session.execute(dup_stmt)
    if dup_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Custom domain '{domain}' is already registered to another website project.",
        )

    # Generate cryptographic challenge token
    challenge_token = secrets.token_urlsafe(24)
    project.custom_domain = domain
    project.custom_domain_token = challenge_token
    project.custom_domain_status = CustomDomainStatus.PENDING_VERIFICATION
    project.custom_domain_verified_at = None

    await session.commit()
    await session.refresh(project)

    dns_record_name = f"_rivinity-challenge.{domain}"
    expected_txt = f"rivinity-verify={challenge_token}"

    return CustomDomainStatusResponse(
        project_id=project.id,
        custom_domain=domain,
        status=CustomDomainStatus.PENDING_VERIFICATION,
        status_label="pending_verification",
        verification_token=challenge_token,
        dns_record_name=dns_record_name,
        expected_txt_value=expected_txt,
        verified_at=None,
        instructions=f"Add a DNS TXT record for '{dns_record_name}' with value '{expected_txt}', then call verify.",
    )


@router.post(
    "/projects/{project_id}/custom-domain/verify",
    response_model=CustomDomainStatusResponse,
    summary="Verify custom domain ownership via DNS TXT lookup",
)
async def verify_custom_domain(
    project_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> CustomDomainStatusResponse:
    project = await _get_project_with_access(project_id, user, session)
    if not project.custom_domain or not project.custom_domain_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No custom domain configured on this project.",
        )

    domain = project.custom_domain
    token = project.custom_domain_token
    expected_txt = f"rivinity-verify={token}"
    dns_record_name = f"_rivinity-challenge.{domain}"

    # Perform DNS verification
    verified, error_msg = await verify_dns_txt(domain, expected_txt)

    if verified:
        project.custom_domain_status = CustomDomainStatus.VERIFIED
        project.custom_domain_verified_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(project)

        return CustomDomainStatusResponse(
            project_id=project.id,
            custom_domain=domain,
            status=CustomDomainStatus.VERIFIED,
            status_label="verified",
            verification_token=token,
            dns_record_name=dns_record_name,
            expected_txt_value=expected_txt,
            verified_at=project.custom_domain_verified_at,
            instructions="Domain ownership verified successfully. Traffic to this domain will now serve your website.",
        )
    else:
        project.custom_domain_status = CustomDomainStatus.FAILED
        await session.commit()
        await session.refresh(project)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Domain verification failed: {error_msg or 'DNS TXT challenge record not found'}. Expected '{expected_txt}' at '{dns_record_name}'.",
        )


@router.delete(
    "/projects/{project_id}/custom-domain",
    summary="Remove custom domain binding",
)
async def remove_custom_domain(
    project_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await _get_project_with_access(project_id, user, session)
    project.custom_domain = None
    project.custom_domain_token = None
    project.custom_domain_status = CustomDomainStatus.UNCONFIGURED
    project.custom_domain_verified_at = None
    await session.commit()
    return {"success": True, "message": "Custom domain removed successfully."}


@router.get(
    "/projects/{project_id}/webmaster-tools",
    response_model=WebmasterToolsConfig,
    summary="Get webmaster tools verification settings",
)
async def get_webmaster_tools(
    project_id: int,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> WebmasterToolsConfig:
    project = await _get_project_with_access(project_id, user, session)
    return WebmasterToolsConfig(**(project.webmaster_tools or {}))


@router.put(
    "/projects/{project_id}/webmaster-tools",
    response_model=WebmasterToolsConfig,
    summary="Update owner-authorized webmaster verification tokens (Google, Bing, IndexNow)",
)
async def update_webmaster_tools(
    project_id: int,
    payload: WebmasterToolsConfig,
    user: User = Depends(verified_user),
    session: AsyncSession = Depends(get_session),
) -> WebmasterToolsConfig:
    project = await _get_project_with_access(project_id, user, session)
    project.webmaster_tools = payload.model_dump(exclude_none=True)
    await session.commit()
    await session.refresh(project)
    return WebmasterToolsConfig(**(project.webmaster_tools or {}))


