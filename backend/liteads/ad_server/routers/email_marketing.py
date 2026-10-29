"""Tenant-Safe Backend Email Marketing API Endpoints (Phase 6.1D).

Provides controlled, tenant-isolated operations for:
- Mailing lists: `/api/v1/email-marketing/lists`
- Contacts and CSV imports: `/api/v1/email-marketing/lists/{id}/...`
- Campaigns (draft, preview, test-send, confirmed launch, analytics):
  `/api/v1/email-marketing/campaigns/...`

Strict isolation:
- Resolves tenant context (tenant_type, tenant_id) from authenticated credentials.
- Rejects cross-tenant access with 404 (preventing ID enumeration).
- Publishers are rejected with 403 Forbidden by default.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.email_marketing_service import EmailMarketingService
from liteads.common.database import get_session
from liteads.common.security import current_user
from liteads.models.auth import User
from liteads.schemas.auth import MessageResponse
from liteads.schemas.email_marketing import (
    CampaignAnalyticsResponse,
    CampaignPreviewResponse,
    CampaignResponse,
    ContactResponse,
    CreateCampaignRequest,
    CreateContactRequest,
    CreateListRequest,
    ImportContactsRequest,
    ImportContactsResponse,
    LaunchCampaignRequest,
    ListResponse,
    TestSendCampaignRequest,
    UpdateCampaignRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/email-marketing", tags=["email-marketing"])
service = EmailMarketingService()


# ── Lists ─────────────────────────────────────────────────────────────────────

@router.get("/lists", response_model=list[ListResponse])
async def list_mailing_lists(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ListResponse]:
    """List all mailing lists owned by the authenticated tenant."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    return await service.get_lists(session=session, tenant_type=tenant_type, tenant_id=tenant_id)


@router.post("/lists", response_model=ListResponse, status_code=status.HTTP_201_CREATED)
async def create_mailing_list(
    payload: CreateListRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ListResponse:
    """Create a new mailing list scoped to the authenticated tenant."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    created = await service.create_list(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        payload=payload,
    )
    return ListResponse(
        id=created.id,
        tenant_type=created.tenant_type,
        tenant_id=created.tenant_id,
        name=created.name,
        description=created.description,
        type=created.type,
        optin=created.optin,
        contact_count=0,
        created_at=created.created_at,
    )


@router.get("/lists/{list_id}", response_model=ListResponse)
async def get_mailing_list(
    list_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ListResponse:
    """Retrieve details for a specific owned list."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    lst = await service.get_list(session=session, tenant_type=tenant_type, tenant_id=tenant_id, list_id=list_id)
    return ListResponse(
        id=lst.id,
        tenant_type=lst.tenant_type,
        tenant_id=lst.tenant_id,
        name=lst.name,
        description=lst.description,
        type=lst.type,
        optin=lst.optin,
        contact_count=len(lst.contacts) if lst.contacts else 0,
        created_at=lst.created_at,
    )


@router.delete("/lists/{list_id}", response_model=MessageResponse)
async def delete_mailing_list(
    list_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    """Delete an owned mailing list."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    await service.delete_list(session=session, tenant_type=tenant_type, tenant_id=tenant_id, list_id=list_id)
    return MessageResponse(message="List deleted successfully")


# ── Contacts & CSV Import ─────────────────────────────────────────────────────

@router.get("/lists/{list_id}/contacts", response_model=list[ContactResponse])
async def list_contacts(
    list_id: int,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    query: Optional[str] = Query(None, description="Optional search filter for contact email or name"),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ContactResponse]:
    """List contacts in an owned mailing list with pagination and optional search."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    return await service.get_contacts(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        list_id=list_id,
        limit=limit,
        offset=offset,
        query=query,
    )


@router.post("/lists/{list_id}/contacts", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def add_contact(
    list_id: int,
    payload: CreateContactRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ContactResponse:
    """Add a single contact with declared lawful permission."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    return await service.add_contact(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        list_id=list_id,
        payload=payload,
    )


@router.post("/lists/{list_id}/import", response_model=ImportContactsResponse)
async def import_contacts(
    list_id: int,
    payload: ImportContactsRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ImportContactsResponse:
    """Import a batch of permission-based contacts from CSV text."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    return await service.import_contacts_csv(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        list_id=list_id,
        csv_content=payload.csv_content,
        permission_source=payload.permission_source,
    )


@router.delete("/lists/{list_id}/contacts/{contact_id}", response_model=MessageResponse)
async def remove_contact(
    list_id: int,
    contact_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    """Unsubscribe and remove a contact from an owned list."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    await service.remove_contact(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        list_id=list_id,
        contact_id=contact_id,
    )
    return MessageResponse(message="Contact removed from list")


# ── Campaigns ─────────────────────────────────────────────────────────────────

@router.get("/campaigns", response_model=list[CampaignResponse])
async def list_campaigns(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CampaignResponse]:
    """List marketing campaigns owned by the authenticated tenant."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    return await service.get_campaigns(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
    )


@router.post("/campaigns", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CreateCampaignRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CampaignResponse:
    """Create a draft marketing campaign linked to an owned mailing list."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    return await service.create_campaign(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        payload=payload,
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CampaignResponse:
    """Retrieve details for an owned campaign."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    c = await service.get_campaign(session=session, tenant_type=tenant_type, tenant_id=tenant_id, campaign_id=campaign_id)
    return CampaignResponse.model_validate(c)


@router.put("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: int,
    payload: UpdateCampaignRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CampaignResponse:
    """Update draft campaign fields."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    return await service.update_campaign(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        payload=payload,
    )


@router.post("/campaigns/{campaign_id}/preview", response_model=CampaignPreviewResponse)
async def preview_campaign(
    campaign_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CampaignPreviewResponse:
    """Preview campaign rendered body, audience size, and unsubscribe notice."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    return await service.preview_campaign(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
    )


@router.post("/campaigns/{campaign_id}/test-send", response_model=MessageResponse)
async def test_send_campaign(
    campaign_id: int,
    payload: TestSendCampaignRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    """Send test email preview to allowlisted recipients."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    await service.test_send_campaign(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        test_emails=[str(e) for e in payload.test_emails],
    )
    return MessageResponse(message="Test preview sent successfully")


@router.post("/campaigns/{campaign_id}/launch", response_model=CampaignResponse)
async def launch_campaign(
    campaign_id: int,
    payload: LaunchCampaignRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CampaignResponse:
    """Launch campaign following explicit confirmation and audience validation."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    return await service.launch_campaign(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        confirmed=payload.confirmed,
    )


@router.get("/campaigns/{campaign_id}/analytics", response_model=CampaignAnalyticsResponse)
async def get_campaign_analytics(
    campaign_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CampaignAnalyticsResponse:
    """Retrieve delivery and engagement metrics for an owned campaign."""
    tenant_type, tenant_id = service.resolve_tenant(user)
    return await service.get_campaign_analytics(
        session=session,
        tenant_type=tenant_type,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
    )

