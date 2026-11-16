"""
Channel Connectors and Platform Adapters Router (Phase 5).

API routes for:
- Connecting authorized external advertiser accounts (OpenAI, Google Ads)
- Inspecting and synchronizing external campaigns and reporting
- Previewing and creating external campaigns with deliberate confirmation and idempotency
- Managing AI publisher inventory delivery contracts and verifying approved surfaces
- Enforcing Claude ad-free regulatory policy
"""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.channels.claude_policy import ClaudePolicyGuard
from liteads.channels.google_ads import GoogleAdsConnector
from liteads.channels.openai_ads import OpenAIAdsConnector
from liteads.channels.schemas import (
    CampaignCreateParams,
    CampaignPreviewResult,
    CampaignStatusUpdateRequest,
    ChannelCapabilities,
    ConnectAdvertiserRequest,
    ConnectAdvertiserResponse,
    ConnectionDetailsResponse,
    ExternalAccount,
    ExternalCampaign,
    ExternalReportRow,
    PublisherContractCreateRequest,
    PublisherContractResponse,
)
from liteads.channels.service import ChannelService
from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles, verified_user
from liteads.models.auth import User

router = APIRouter()


def _get_channel_service(session: AsyncSession = Depends(get_session)) -> ChannelService:
    return ChannelService(session)


# -----------------------------------------------------------------------------
# Capabilities Discovery Endpoint (Public / Authenticated)
# -----------------------------------------------------------------------------
@router.get(
    "/capabilities/{provider}",
    response_model=ChannelCapabilities,
    summary="Get provider capabilities and explicit limitations",
)
async def get_provider_capabilities(provider: str) -> ChannelCapabilities:
    """Inspect provider capabilities, supported objectives, and channel limitations."""
    # Enforce Claude ad-free policy
    ClaudePolicyGuard.enforce_ad_free_policy(provider)

    clean_p = provider.lower().strip()
    if clean_p in ("openai", "openai_ads"):
        connector = OpenAIAdsConnector(credentials={"api_key": "dummy"}, account_id="dummy")
        return connector.get_capabilities()
    elif clean_p in ("google", "google_ads"):
        connector = GoogleAdsConnector(credentials={}, account_id="dummy")
        return connector.get_capabilities()
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{provider}' is not supported.",
        )


# -----------------------------------------------------------------------------
# Advertiser Connector Endpoints (Tenant-Scoped)
# -----------------------------------------------------------------------------
@router.post(
    "/advertiser/connect",
    response_model=ConnectAdvertiserResponse,
    summary="Connect an authorized external ad platform account",
)
async def connect_advertiser_account(
    req: ConnectAdvertiserRequest,
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> ConnectAdvertiserResponse:
    """Connect an authorized external ad platform account (e.g. OpenAI Ads Manager)."""
    if not user.advertiser_id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with an advertiser account",
        )
    advertiser_id = user.advertiser_id or 1
    return await service.connect_advertiser_channel(
        advertiser_id=advertiser_id,
        user_id=user.id,
        req=req,
    )


@router.get(
    "/advertiser/connections",
    response_model=list[ConnectionDetailsResponse],
    summary="List connected ad platform accounts",
)
async def list_advertiser_connections(
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> list[ConnectionDetailsResponse]:
    """List connected accounts for the authenticated advertiser (secrets redacted)."""
    if not user.advertiser_id and user.role != "admin":
        return []
    advertiser_id = user.advertiser_id or 1
    return await service.list_advertiser_connections(advertiser_id)


@router.get(
    "/advertiser/connections/{connection_id}",
    response_model=ConnectionDetailsResponse,
    summary="Inspect connection details",
)
async def get_connection_details(
    connection_id: int,
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> ConnectionDetailsResponse:
    """Inspect specific connection details without secret leakage."""
    advertiser_id = user.advertiser_id or 1
    conn = await service.get_connection_scoped(advertiser_id, connection_id)
    caps = ChannelCapabilities(**conn.capabilities) if conn.capabilities else None
    return ConnectionDetailsResponse(
        id=conn.id,
        provider=conn.provider,
        name=conn.name,
        external_account_id=conn.external_account_id,
        external_organization_id=conn.external_organization_id,
        credentials_prefix=conn.credentials_prefix,
        status=conn.status,
        adapter_mode=conn.adapter_mode,
        token_state=conn.token_state,
        last_verified_at=conn.last_verified_at,
        last_sync_at=conn.last_sync_at,
        sync_cursor=conn.sync_cursor,
        last_error=conn.last_error,
        capabilities=caps,
        created_at=conn.created_at,
    )


@router.get(
    "/advertiser/connections/{connection_id}/accounts",
    response_model=list[ExternalAccount],
    summary="Discover accounts in connected platform",
)
async def discover_accounts(
    connection_id: int,
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> list[ExternalAccount]:
    """Discover accessible ad accounts in the connected platform."""
    advertiser_id = user.advertiser_id or 1
    return await service.discover_accounts(advertiser_id, connection_id)


@router.post(
    "/advertiser/connections/{connection_id}/sync",
    response_model=list[ExternalCampaign],
    summary="Synchronize external campaigns",
)
async def sync_campaigns(
    connection_id: int,
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> list[ExternalCampaign]:
    """Synchronize external campaigns into deterministic mapping records."""
    advertiser_id = user.advertiser_id or 1
    return await service.sync_campaigns(advertiser_id, connection_id)


@router.post(
    "/advertiser/connections/{connection_id}/preview",
    response_model=CampaignPreviewResult,
    summary="Preview external campaign creation before spending money",
)
async def preview_campaign_creation(
    connection_id: int,
    params: CampaignCreateParams,
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> CampaignPreviewResult:
    """Preview campaign parameters, spend, and warnings before committing writes."""
    advertiser_id = user.advertiser_id or 1
    return await service.preview_campaign_creation(advertiser_id, connection_id, params)


@router.post(
    "/advertiser/connections/{connection_id}/campaigns",
    response_model=ExternalCampaign,
    summary="Create campaign on external ad platform",
)
async def create_campaign(
    connection_id: int,
    params: CampaignCreateParams,
    idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> ExternalCampaign:
    """
    Create campaign on external ad platform.
    
    Requires explicit user confirmation (confirmed=True) and X-Idempotency-Key header.
    """
    advertiser_id = user.advertiser_id or 1
    return await service.create_campaign(
        advertiser_id=advertiser_id,
        connection_id=connection_id,
        params=params,
        idempotency_key=idempotency_key,
        user_id=user.id,
    )


@router.patch(
    "/advertiser/connections/{connection_id}/campaigns/{external_campaign_id}/status",
    summary="Update external campaign delivery status",
)
async def update_campaign_status(
    connection_id: int,
    external_campaign_id: str,
    req: CampaignStatusUpdateRequest,
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> dict[str, Any]:
    """Pause or activate an external campaign."""
    advertiser_id = user.advertiser_id or 1
    ok = await service.update_campaign_status(
        advertiser_id=advertiser_id,
        connection_id=connection_id,
        external_campaign_id=external_campaign_id,
        status_value=req.status,
    )
    return {"success": ok, "external_campaign_id": external_campaign_id, "status": req.status}


@router.get(
    "/advertiser/connections/{connection_id}/reporting",
    response_model=list[ExternalReportRow],
    summary="Fetch reporting metrics for external campaigns",
)
async def sync_reporting(
    connection_id: int,
    start_date: date = Query(...),
    end_date: date = Query(...),
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> list[ExternalReportRow]:
    """Fetch historical performance reporting metrics from provider."""
    advertiser_id = user.advertiser_id or 1
    return await service.sync_reporting(
        advertiser_id=advertiser_id,
        connection_id=connection_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.delete(
    "/advertiser/connections/{connection_id}",
    summary="Disconnect external ad platform account",
)
async def disconnect_account(
    connection_id: int,
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> dict[str, Any]:
    """Disconnect and revoke an external account connection."""
    advertiser_id = user.advertiser_id or 1
    ok = await service.disconnect(advertiser_id, connection_id, user_id=user.id)
    return {"success": ok, "connection_id": connection_id, "status": "DISCONNECTED"}


# -----------------------------------------------------------------------------
# AI Publisher Contract Endpoints
# -----------------------------------------------------------------------------
@router.post(
    "/publisher/contracts",
    response_model=PublisherContractResponse,
    summary="Register verified AI publisher inventory agreement",
)
async def register_publisher_contract(
    req: PublisherContractCreateRequest,
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> PublisherContractResponse:
    """Register a signed agreement for AI publisher inventory delivery."""
    if not user.publisher_id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with a publisher account",
        )
    publisher_id = user.publisher_id or 1
    return await service.register_publisher_contract(
        publisher_id=publisher_id,
        req=req,
        user_id=user.id,
    )


@router.post(
    "/publisher/verify-surface",
    summary="Verify if an AI publisher surface is authorized",
)
async def verify_publisher_surface(
    surface_id: str = Query(...),
    user: User = Depends(verified_user),
    service: ChannelService = Depends(_get_channel_service),
) -> dict[str, Any]:
    """Verify surface authorization under active signed contract."""
    publisher_id = user.publisher_id or 1
    is_valid, reason = await service.verify_publisher_surface(publisher_id, surface_id)
    return {
        "surface_id": surface_id,
        "is_authorized": is_valid,
        "reason": reason,
    }

