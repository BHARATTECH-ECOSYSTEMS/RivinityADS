"""
Targeting CRUD endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles, verified_user
from liteads.models import AdSet, Campaign, Status, TargetingRule, User
from liteads.schemas.targeting import AdSetTargetingResponse, GeoEntry, TargetingResponse, TargetingSet

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


async def _assert_ad_set_access(user: User, ad_set_id: int, session: AsyncSession) -> tuple[AdSet, Campaign]:
    """Verify that user has access to the ad set via its parent campaign."""
    ad_set = await session.get(AdSet, ad_set_id)
    if ad_set is None:
        raise HTTPException(status_code=404, detail="AdSet not found")
    campaign = await _assert_campaign_access(user, ad_set.campaign_id, session)
    return ad_set, campaign


# ── Phase 7B: AdSet-Scoped Targeting Endpoints ─────────────────────────────

@router.get("/ad-set/{ad_set_id}/targeting", response_model=AdSetTargetingResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_ad_set_targeting(
    ad_set_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AdSetTargetingResponse:
    """Get targeting settings scoped to a specific ad set."""
    ad_set, campaign = await _assert_ad_set_access(user, ad_set_id, session)

    query = select(TargetingRule).where(TargetingRule.ad_set_id == ad_set_id)
    result = await session.execute(query)
    rules = result.scalars().all()

    response = AdSetTargetingResponse(ad_set_id=ad_set_id, campaign_id=campaign.id)

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


@router.put("/ad-set/{ad_set_id}/targeting", response_model=AdSetTargetingResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def update_ad_set_targeting(
    ad_set_id: int,
    payload: TargetingSet,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AdSetTargetingResponse:
    """Update targeting settings scoped strictly to one ad set."""
    ad_set, campaign = await _assert_ad_set_access(user, ad_set_id, session)

    # Delete existing targeting rules strictly belonging to this ad set
    query = select(TargetingRule).where(
        TargetingRule.ad_set_id == ad_set_id,
        TargetingRule.rule_type.in_(["geo", "sector"])
    )
    result = await session.execute(query)
    existing_rules = result.scalars().all()
    for rule in existing_rules:
        await session.delete(rule)

    # Insert new rules with both ad_set_id and campaign_id
    if payload.geo_include:
        session.add(
            TargetingRule(
                campaign_id=campaign.id,
                ad_set_id=ad_set.id,
                rule_type="geo",
                is_include=True,
                rule_value={"entries": [e.model_dump(exclude_none=True) for e in payload.geo_include]}
            )
        )
    if payload.geo_exclude:
        session.add(
            TargetingRule(
                campaign_id=campaign.id,
                ad_set_id=ad_set.id,
                rule_type="geo",
                is_include=False,
                rule_value={"entries": [e.model_dump(exclude_none=True) for e in payload.geo_exclude]}
            )
        )

    if payload.sector_include:
        session.add(
            TargetingRule(
                campaign_id=campaign.id,
                ad_set_id=ad_set.id,
                rule_type="sector",
                is_include=True,
                rule_value={"sectors": payload.sector_include}
            )
        )
    if payload.sector_exclude:
        session.add(
            TargetingRule(
                campaign_id=campaign.id,
                ad_set_id=ad_set.id,
                rule_type="sector",
                is_include=False,
                rule_value={"sectors": payload.sector_exclude}
            )
        )

    await session.flush()

    return AdSetTargetingResponse(
        ad_set_id=ad_set.id,
        campaign_id=campaign.id,
        geo_include=payload.geo_include,
        geo_exclude=payload.geo_exclude,
        sector_include=payload.sector_include,
        sector_exclude=payload.sector_exclude,
    )


# ── Legacy Campaign Targeting Endpoints (Compatibility) ────────────────────

@router.get("/campaign/{campaign_id}/targeting", response_model=TargetingResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_targeting(
    campaign_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> TargetingResponse:
    """Get targeting settings for a campaign (operates on default ad set)."""
    await _assert_campaign_access(user, campaign_id, session)

    res = await session.execute(
        select(AdSet).where(AdSet.campaign_id == campaign_id).order_by(AdSet.id.asc())
    )
    default_ad_set = res.scalars().first()

    if default_ad_set:
        query = select(TargetingRule).where(TargetingRule.ad_set_id == default_ad_set.id)
    else:
        query = select(TargetingRule).where(
            TargetingRule.campaign_id == campaign_id,
            TargetingRule.ad_set_id.is_(None),
        )
    result = await session.execute(query)
    rules = result.scalars().all()

    response = TargetingResponse(campaign_id=campaign_id, ad_set_id=default_ad_set.id if default_ad_set else None)

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
    """Update targeting settings for a campaign (operates strictly on default ad set)."""
    campaign = await _assert_campaign_access(user, campaign_id, session)

    res = await session.execute(
        select(AdSet).where(AdSet.campaign_id == campaign_id).order_by(AdSet.id.asc())
    )
    default_ad_set = res.scalars().first()
    if default_ad_set is None:
        default_ad_set = AdSet(
            campaign_id=campaign_id,
            name="Default Ad Set",
            status=Status.ACTIVE,
            daily_budget=campaign.budget_daily,
            bid_type=campaign.bid_type,
            bid_amount=campaign.bid_amount,
        )
        session.add(default_ad_set)
        await session.flush()

    # Delete existing targeting rules ONLY for the default ad set or legacy unassigned rules
    # NEVER touch rules belonging to sibling ad sets
    query = select(TargetingRule).where(
        TargetingRule.campaign_id == campaign_id,
        (TargetingRule.ad_set_id == default_ad_set.id) | (TargetingRule.ad_set_id.is_(None)),
        TargetingRule.rule_type.in_(["geo", "sector"])
    )
    result = await session.execute(query)
    existing_rules = result.scalars().all()
    for rule in existing_rules:
        await session.delete(rule)

    # Insert new rules for the default ad set
    if payload.geo_include:
        session.add(
            TargetingRule(
                campaign_id=campaign_id,
                ad_set_id=default_ad_set.id,
                rule_type="geo",
                is_include=True,
                rule_value={"entries": [e.model_dump(exclude_none=True) for e in payload.geo_include]}
            )
        )
    if payload.geo_exclude:
        session.add(
            TargetingRule(
                campaign_id=campaign_id,
                ad_set_id=default_ad_set.id,
                rule_type="geo",
                is_include=False,
                rule_value={"entries": [e.model_dump(exclude_none=True) for e in payload.geo_exclude]}
            )
        )

    if payload.sector_include:
        session.add(
            TargetingRule(
                campaign_id=campaign_id,
                ad_set_id=default_ad_set.id,
                rule_type="sector",
                is_include=True,
                rule_value={"sectors": payload.sector_include}
            )
        )
    if payload.sector_exclude:
        session.add(
            TargetingRule(
                campaign_id=campaign_id,
                ad_set_id=default_ad_set.id,
                rule_type="sector",
                is_include=False,
                rule_value={"sectors": payload.sector_exclude}
            )
        )

    await session.flush()

    return TargetingResponse(
        campaign_id=campaign_id,
        ad_set_id=default_ad_set.id,
        geo_include=payload.geo_include,
        geo_exclude=payload.geo_exclude,
        sector_include=payload.sector_include,
        sector_exclude=payload.sector_exclude,
    )
