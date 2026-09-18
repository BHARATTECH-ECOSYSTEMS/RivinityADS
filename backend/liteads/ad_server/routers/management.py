"""CRUD and publisher reporting endpoints."""

import hashlib
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles
from liteads.models import (
    AdSlot,
    Advertiser,
    Campaign,
    Creative,
    Publisher,
    PublisherApiKey,
    PublisherDailyStat,
    User,
)
from liteads.management.api_key_service import ApiKeyService
from liteads.schemas.management import (
    AdvertiserCreate,
    AdvertiserResponse,
    AdvertiserUpdate,
    ApiKeyCreate,
    ApiKeyResponse,
    CampaignCreate,
    CampaignResponse,
    CampaignUpdate,
    CreativeCreate,
    CreativeResponse,
    CreativeUpdate,
    PublisherApiKeyCreate,
    PublisherApiKeyCreateResponse,
    PublisherApiKeyResponse,
    PublisherCreate,
    PublisherReportingResponse,
    PublisherResponse,
    PublisherUpdate,
    SlotCreate,
    SlotResponse,
    SlotUpdate,
)

router = APIRouter(dependencies=[Depends(current_user)])


def _not_found(entity: str, entity_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{entity} {entity_id} not found")


def _assert_advertiser_access(user: User, advertiser_id: int) -> None:
    if user.role != "admin" and user.advertiser_id != advertiser_id:
        raise HTTPException(status_code=403, detail="Advertiser resource is outside your account")


def _assert_publisher_access(user: User, publisher_id: int) -> None:
    if user.role != "admin" and user.publisher_id != publisher_id:
        raise HTTPException(status_code=403, detail="Publisher resource is outside your account")


async def _assert_campaign_access(user: User, campaign_id: int, session: AsyncSession) -> Campaign:
    """Admin: any campaign. Advertiser: only own campaigns. Publisher: never."""
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if user.role == "advertiser" and campaign.advertiser_id != user.advertiser_id:
        raise HTTPException(status_code=403, detail="Campaign belongs to another advertiser")
    if user.role == "publisher":
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return campaign


@router.get("/advertiser", response_model=list[AdvertiserResponse], dependencies=[Depends(require_roles("admin"))])
async def list_advertisers(session: AsyncSession = Depends(get_session)) -> list[Advertiser]:
    result = await session.execute(select(Advertiser).order_by(Advertiser.id))
    return list(result.scalars().all())


@router.post("/advertiser", response_model=AdvertiserResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin"))])
async def create_advertiser(
    payload: AdvertiserCreate, session: AsyncSession = Depends(get_session)
) -> Advertiser:
    advertiser = Advertiser(**payload.model_dump())
    session.add(advertiser)
    await session.flush()
    return advertiser


@router.get("/advertiser/{advertiser_id}", response_model=AdvertiserResponse, dependencies=[Depends(require_roles("admin"))])
async def get_advertiser(advertiser_id: int, session: AsyncSession = Depends(get_session)) -> Advertiser:
    advertiser = await session.get(Advertiser, advertiser_id)
    if advertiser is None:
        raise _not_found("Advertiser", advertiser_id)
    return advertiser


@router.patch("/advertiser/{advertiser_id}", response_model=AdvertiserResponse, dependencies=[Depends(require_roles("admin"))])
async def update_advertiser(
    advertiser_id: int,
    payload: AdvertiserUpdate,
    session: AsyncSession = Depends(get_session),
) -> Advertiser:
    advertiser = await session.get(Advertiser, advertiser_id)
    if advertiser is None:
        raise _not_found("Advertiser", advertiser_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(advertiser, key, value)
    await session.flush()
    return advertiser


@router.get("/campaign", response_model=list[CampaignResponse], dependencies=[Depends(require_roles("admin", "advertiser"))])
async def list_campaigns(
    advertiser_id: int | None = Query(default=None),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Campaign]:
    query = select(Campaign).order_by(Campaign.id)
    if user.role == "advertiser":
        advertiser_id = user.advertiser_id
    if advertiser_id is not None:
        query = query.where(Campaign.advertiser_id == advertiser_id)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("/campaign", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def create_campaign(
    payload: CampaignCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> Campaign:
    _assert_advertiser_access(user, payload.advertiser_id)
    if await session.get(Advertiser, payload.advertiser_id) is None:
        raise _not_found("Advertiser", payload.advertiser_id)
    campaign = Campaign(**payload.model_dump())
    session.add(campaign)
    await session.flush()
    return campaign


@router.get("/campaign/{campaign_id}", response_model=CampaignResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_campaign(campaign_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    _assert_advertiser_access(user, campaign.advertiser_id)
    return campaign


@router.patch("/campaign/{campaign_id}", response_model=CampaignResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    _assert_advertiser_access(user, campaign.advertiser_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, key, value)
    await session.flush()
    return campaign


@router.get("/creative", response_model=list[CreativeResponse], dependencies=[Depends(require_roles("admin", "advertiser"))])
async def list_creatives(
    campaign_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Creative]:
    await _assert_campaign_access(user, campaign_id, session)
    query = select(Creative).order_by(Creative.id).where(Creative.campaign_id == campaign_id)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("/creative", response_model=CreativeResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def create_creative(
    payload: CreativeCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session)
) -> Creative:
    await _assert_campaign_access(user, payload.campaign_id, session)
    creative = Creative(**payload.model_dump())
    session.add(creative)
    await session.flush()
    return creative


@router.get("/creative/{creative_id}", response_model=CreativeResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_creative(creative_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> Creative:
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise _not_found("Creative", creative_id)
    await _assert_campaign_access(user, creative.campaign_id, session)
    return creative


@router.patch("/creative/{creative_id}", response_model=CreativeResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def update_creative(
    creative_id: int,
    payload: CreativeUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Creative:
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise _not_found("Creative", creative_id)
    await _assert_campaign_access(user, creative.campaign_id, session)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(creative, key, value)
    await session.flush()
    return creative

@router.delete("/creative/{creative_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def delete_creative(
    creative_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    creative = await session.get(Creative, creative_id)
    if creative is None:
        raise _not_found("Creative", creative_id)
    await _assert_campaign_access(user, creative.campaign_id, session)
    await session.delete(creative)
    await session.flush()


@router.get("/publisher", response_model=list[PublisherResponse], dependencies=[Depends(require_roles("admin"))])
async def list_publishers(session: AsyncSession = Depends(get_session)) -> list[Publisher]:
    result = await session.execute(select(Publisher).order_by(Publisher.id))
    return list(result.scalars().all())


@router.post("/publisher", response_model=PublisherResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin"))])
async def create_publisher(
    payload: PublisherCreate, session: AsyncSession = Depends(get_session)
) -> Publisher:
    publisher = Publisher(**payload.model_dump())
    session.add(publisher)
    await session.flush()
    return publisher


@router.get("/publisher/{publisher_id}", response_model=PublisherResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def get_publisher(publisher_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> Publisher:
    publisher = await session.get(Publisher, publisher_id)
    if publisher is None:
        raise _not_found("Publisher", publisher_id)
    _assert_publisher_access(user, publisher_id)
    return publisher


@router.patch("/publisher/{publisher_id}", response_model=PublisherResponse, dependencies=[Depends(require_roles("admin"))])
async def update_publisher(
    publisher_id: int,
    payload: PublisherUpdate,
    session: AsyncSession = Depends(get_session),
) -> Publisher:
    publisher = await session.get(Publisher, publisher_id)
    if publisher is None:
        raise _not_found("Publisher", publisher_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(publisher, key, value)
    await session.flush()
    return publisher


@router.get("/publisher/{publisher_id}/keys", response_model=list[ApiKeyResponse], dependencies=[Depends(require_roles("admin", "publisher"))])
async def list_publisher_keys(
    publisher_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[ApiKeyResponse]:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    return await ApiKeyService.list_api_keys(session, publisher_id)


@router.post("/publisher/{publisher_id}/keys", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "publisher"))])
async def create_publisher_key(
    publisher_id: int, payload: ApiKeyCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> ApiKeyResponse:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    
    db_key, raw_key = await ApiKeyService.create_api_key(session, publisher_id, payload)
    response_model = ApiKeyResponse.model_validate(db_key)
    response_model.raw_key = raw_key
    return response_model


@router.delete("/publisher/{publisher_id}/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_roles("admin", "publisher"))])
async def revoke_publisher_key(
    publisher_id: int, key_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> None:
    _assert_publisher_access(user, publisher_id)
    if not await ApiKeyService.revoke_api_key(session, publisher_id, key_id):
        raise HTTPException(status_code=404, detail="API Key not found or already revoked")


@router.get("/slot", response_model=list[SlotResponse], dependencies=[Depends(require_roles("admin", "publisher"))])
async def list_slots(
    publisher_id: int | None = Query(default=None),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AdSlot]:
    query = select(AdSlot).order_by(AdSlot.id)
    if user.role == "publisher":
        publisher_id = user.publisher_id
    if publisher_id is not None:
        query = query.where(AdSlot.publisher_id == publisher_id)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("/slot", response_model=SlotResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "publisher"))])
async def create_slot(payload: SlotCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> AdSlot:
    publisher_id = payload.publisher_id or user.publisher_id
    if not publisher_id:
        raise HTTPException(status_code=400, detail="publisher_id is required")
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    data = payload.model_dump()
    data["publisher_id"] = publisher_id
    slot = AdSlot(**data)
    session.add(slot)
    await session.flush()
    return slot


@router.get("/slot/{slot_id}", response_model=SlotResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def get_slot(slot_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> AdSlot:
    slot = await session.get(AdSlot, slot_id)
    if slot is None:
        raise _not_found("Slot", slot_id)
    _assert_publisher_access(user, slot.publisher_id)
    return slot


@router.patch("/slot/{slot_id}", response_model=SlotResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def update_slot(
    slot_id: int, payload: SlotUpdate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> AdSlot:
    slot = await session.get(AdSlot, slot_id)
    if slot is None:
        raise _not_found("Slot", slot_id)
    _assert_publisher_access(user, slot.publisher_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(slot, key, value)
    await session.flush()
    return slot


@router.get("/publisher/{publisher_id}/reporting", response_model=PublisherReportingResponse, dependencies=[Depends(require_roles("admin", "publisher"))])
async def publisher_reporting(
    publisher_id: int,
    user: User = Depends(current_user),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> PublisherReportingResponse:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)
    today = date.today()
    start = start_date or today
    end = end_date or today
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    result = await session.execute(
        select(
            func.coalesce(func.sum(PublisherDailyStat.impressions), 0),
            func.coalesce(func.sum(PublisherDailyStat.clicks), 0),
            func.coalesce(func.sum(PublisherDailyStat.conversions), 0),
            func.coalesce(func.sum(PublisherDailyStat.earnings), Decimal("0")),
        ).where(
            PublisherDailyStat.publisher_id == publisher_id,
            PublisherDailyStat.stat_date >= start,
            PublisherDailyStat.stat_date <= end,
        )
    )
    impressions, clicks, conversions, earnings = result.one()
    earnings = Decimal(earnings or 0)
    ecpm = (earnings / Decimal(impressions) * Decimal(1000)) if impressions else Decimal("0")
    return PublisherReportingResponse(
        publisher_id=publisher_id,
        start_date=start,
        end_date=end,
        impressions=int(impressions),
        clicks=int(clicks),
        conversions=int(conversions),
        earnings=earnings,
        ecpm=ecpm,
    )


# ── Phase 4.2 — Publisher API key management ────────────────────────────────────

@router.post(
    "/publisher/{publisher_id}/keys",
    response_model=ApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Create an API key for a publisher",
)
async def create_api_key_for_publisher(
    publisher_id: int,
    payload: ApiKeyCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyResponse:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)

    db_key, raw_key = await ApiKeyService.create_api_key(session, publisher_id, payload)
    return ApiKeyResponse(
        id=db_key.id,
        publisher_id=db_key.publisher_id,
        name=db_key.name,
        key_prefix=db_key.key_prefix,
        raw_key=raw_key,
        revoked_at=db_key.revoked_at,
        created_at=db_key.created_at,
        updated_at=db_key.updated_at,
    )


@router.post(
    "/publisher/keys",
    response_model=PublisherApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Create a publisher API key",
    description=(
        "Generates a new API key for authenticating ad/event API calls from "
        "third-party integrations and AI-platform server-to-server callers. "
        "**The raw key is returned exactly once** — copy it now."
    ),
)
async def create_publisher_api_key(
    payload: PublisherApiKeyCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> PublisherApiKeyCreateResponse:
    pub_id = payload.publisher_id or user.id
    _assert_publisher_access(user, pub_id)
    if await session.get(Publisher, pub_id) is None:
        raise _not_found("Publisher", pub_id)

    key_name = payload.name or payload.label or "Default Key"
    db_key, raw_key = await ApiKeyService.create_api_key(session, pub_id, ApiKeyCreate(name=key_name))
    return PublisherApiKeyCreateResponse(
        id=db_key.id,
        publisher_id=db_key.publisher_id,
        name=db_key.name,
        label=db_key.name,
        key_prefix=db_key.key_prefix,
        status=db_key.status,
        created_at=db_key.created_at,
        raw_key=raw_key,
    )


@router.get(
    "/publisher/{publisher_id}/keys",
    response_model=list[ApiKeyResponse],
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="List publisher API keys",
)
async def list_publisher_api_keys(
    publisher_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyResponse]:
    _assert_publisher_access(user, publisher_id)
    if await session.get(Publisher, publisher_id) is None:
        raise _not_found("Publisher", publisher_id)

    keys = await ApiKeyService.list_api_keys(session, publisher_id)
    return [
        ApiKeyResponse(
            id=k.id,
            publisher_id=k.publisher_id,
            name=k.name,
            key_prefix=k.key_prefix,
            revoked_at=k.revoked_at,
            created_at=k.created_at,
            updated_at=k.updated_at,
        )
        for k in keys
    ]


@router.delete(
    "/publisher/{publisher_id}/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Revoke a publisher API key by publisher and key id",
)
async def revoke_publisher_api_key_by_pub(
    publisher_id: int,
    key_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    _assert_publisher_access(user, publisher_id)
    success = await ApiKeyService.revoke_api_key(session, publisher_id, key_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"API key {key_id} not found or already revoked")


@router.delete(
    "/publisher/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles("admin", "publisher"))],
    summary="Revoke a publisher API key",
)
async def revoke_publisher_api_key(
    key_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    api_key = await session.get(PublisherApiKey, key_id)
    if api_key is None:
        raise HTTPException(status_code=404, detail=f"API key {key_id} not found")
    _assert_publisher_access(user, api_key.publisher_id)
    api_key.status = 0  # revoked
    await session.flush()
