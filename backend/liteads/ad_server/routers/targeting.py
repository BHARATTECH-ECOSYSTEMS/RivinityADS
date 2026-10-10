"""
Targeting CRUD endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles, verified_user
from liteads.models import Campaign, TargetingRule, User
from liteads.schemas.targeting import GeoEntry, TargetingResponse, TargetingSet

router = APIRouter(dependencies=[Depends(verified_user)])


async def _assert_campaign_access(user: User, campaign_id: int, session: AsyncSession) -> Campaign:
    """Admin: any campaign. Advertiser: only own campaigns."""
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if user.role == "advertiser" and campaign.advertiser_id != user.advertiser_id:
        raise HTTPException(status_code=403, detail="Campaign belongs to another advertiser")
    if user.role == "publisher":
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return campaign


@router.get("/campaign/{campaign_id}/targeting", response_model=TargetingResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_targeting(
    campaign_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> TargetingResponse:
    """Get targeting settings for a campaign."""
    await _assert_campaign_access(user, campaign_id, session)

    query = select(TargetingRule).where(TargetingRule.campaign_id == campaign_id)
    result = await session.execute(query)
    rules = result.scalars().all()

    response = TargetingResponse(campaign_id=campaign_id)

    for rule in rules:
        if rule.rule_type == "geo":
            entries = rule.rule_value.get("entries", [])
            parsed_entries = [GeoEntry(**entry) for entry in entries]
            if rule.is_include:
                response.geo_include = parsed_entries
            else:
                response.geo_exclude = parsed_entries
        elif rule.rule_type == "sector":
            sectors = rule.rule_value.get("sectors", [])
            if rule.is_include:
                response.sector_include = sectors
            else:
                response.sector_exclude = sectors

    return response


@router.put("/campaign/{campaign_id}/targeting", response_model=TargetingResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def update_targeting(
    campaign_id: int,
    payload: TargetingSet,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> TargetingResponse:
    """Update targeting settings for a campaign."""
    await _assert_campaign_access(user, campaign_id, session)

    # Delete existing targeting rules for geo and sector
    query = select(TargetingRule).where(
        TargetingRule.campaign_id == campaign_id,
        TargetingRule.rule_type.in_(["geo", "sector"])
    )
    result = await session.execute(query)
    existing_rules = result.scalars().all()
    for rule in existing_rules:
        await session.delete(rule)

    # Insert new rules
    if payload.geo_include:
        session.add(
            TargetingRule(
                campaign_id=campaign_id,
                rule_type="geo",
                is_include=True,
                rule_value={"entries": [e.model_dump(exclude_none=True) for e in payload.geo_include]}
            )
        )
    if payload.geo_exclude:
        session.add(
            TargetingRule(
                campaign_id=campaign_id,
                rule_type="geo",
                is_include=False,
                rule_value={"entries": [e.model_dump(exclude_none=True) for e in payload.geo_exclude]}
            )
        )

    if payload.sector_include:
        session.add(
            TargetingRule(
                campaign_id=campaign_id,
                rule_type="sector",
                is_include=True,
                rule_value={"sectors": payload.sector_include}
            )
        )
    if payload.sector_exclude:
        session.add(
            TargetingRule(
                campaign_id=campaign_id,
                rule_type="sector",
                is_include=False,
                rule_value={"sectors": payload.sector_exclude}
            )
        )

    await session.flush()

    return TargetingResponse(
        campaign_id=campaign_id,
        geo_include=payload.geo_include,
        geo_exclude=payload.geo_exclude,
        sector_include=payload.sector_include,
        sector_exclude=payload.sector_exclude,
    )
