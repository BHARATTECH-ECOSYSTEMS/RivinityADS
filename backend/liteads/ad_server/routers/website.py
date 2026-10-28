"""Website builder management endpoints under /api/v1/website."""

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from liteads.ad_server.routers.website_public import create_preview_token
from liteads.ad_server.services.website_cache import website_cache
from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles, verified_user
from liteads.ad_server.services.monetization_eligibility import evaluate_monetization_eligibility
from liteads.models.auth import User
from liteads.models.base import Status
from liteads.models.publisher import AdSlot, Publisher, PublisherProperty
from liteads.models.website import (
    WebsiteConsentLog,
    WebsiteMonetizationStatus,
    WebsitePage,
    WebsiteProject,
    WebsiteStatus,
    WebsiteVersion,
)
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
    VersionSummaryResponse,
    RESERVED_SLUGS,
    SLUG_PATTERN,
)

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


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Create a new website project and initialize a default Home page."""
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

    # 2. Create project
    project = WebsiteProject(
        owner_user_id=user.id,
        name=payload.name,
        slug=payload.slug,
        template_id=payload.template_id,
        theme_config=payload.theme_config.model_dump(),
        status=WebsiteStatus.DRAFT,
    )
    session.add(project)
    await session.flush()

    # 3. Initialize default Home page with a welcoming hero section
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
        blocks={"schema_version": 1, "blocks": [default_hero_block]},
    )
    session.add(home_page)
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
                id=home_page.id,
                project_id=home_page.project_id,
                title=home_page.title,
                slug=home_page.slug,
                is_home=home_page.is_home,
                order_index=home_page.order_index,
                updated_at=home_page.updated_at,
            )
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

    # Phase 10E: Validate all ad placement blocks and bound ad slots
    bound_slots_meta: list[dict[str, Any]] = []
    slot_ids_in_pages: set[str] = set()
    for page in project.pages:
        raw_b = page.blocks or {}
        if isinstance(raw_b, dict):
            for blk in raw_b.get("blocks", []):
                if isinstance(blk, dict) and blk.get("type") == "ad_placement":
                    sid = (blk.get("content") or {}).get("ad_slot_id")
                    if sid:
                        slot_ids_in_pages.add(str(sid).strip())

    if slot_ids_in_pages:
        for sid in sorted(slot_ids_in_pages):
            s_stmt = select(AdSlot).where(AdSlot.slot_id == sid)
            s_res = await session.execute(s_stmt)
            slot_obj = s_res.scalar_one_or_none()
            if slot_obj is not None:
                if not project.publisher_id or not project.property_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Cannot publish a website with ad placement blocks: project must be linked to a publisher and property first.",
                    )
                if slot_obj.publisher_id != project.publisher_id or slot_obj.property_id != project.property_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"AdSlot '{sid}' does not belong to this website's publisher property.",
                    )
                if slot_obj.status != Status.ACTIVE:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"AdSlot '{sid}' is not active.",
                    )
                bound_slots_meta.append({
                    "id": slot_obj.id,
                    "slot_id": slot_obj.slot_id,
                    "name": slot_obj.name,
                    "format": slot_obj.format,
                })
        if project.monetization_status == WebsiteMonetizationStatus.ACTIVE_MONETIZED:
            if not project.publisher_id or not project.property_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot publish a website with ad placement blocks: project must be linked to a publisher and property first.",
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

    project.monetization_status = WebsiteMonetizationStatus.DISABLED

    log = WebsiteConsentLog(
        project_id=project.id,
        consent_type="monetization_disabled",
        consent_scope={"ad_delivery": False, "active": False},
        version="v1.0",
        is_granted=False,
        recorded_at=datetime.now(timezone.utc),
    )
    session.add(log)

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

