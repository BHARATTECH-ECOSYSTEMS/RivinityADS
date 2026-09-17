"""
Reference data CRUD endpoints (sectors, locations).
"""

from typing import Annotated
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, String, null, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import collate

from liteads.common.database import get_session
from liteads.common.security import current_user
from liteads.models import User, GeoLocation

router = APIRouter(dependencies=[Depends(current_user)])

# The canonical 24-sector taxonomy for the platform
SECTORS = [
    "developers", "designers", "data/AI", "fintech/banking", "healthcare",
    "education", "ecommerce", "gaming", "travel", "real estate",
    "automotive", "food/beverage", "fashion", "sports", "entertainment",
    "business/SMB", "marketing", "legal", "agriculture", "government",
    "crypto/web3", "SaaS/B2B", "jobs/careers", "parenting"
]

@router.get("/sectors")
async def get_sectors(
    user: User = Depends(current_user),
) -> list[str]:
    """Get the list of valid sectors."""
    return sorted(SECTORS)


@router.get("/locations/countries")
async def get_countries(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    """Get distinct countries available in targeting data."""
    query = select(GeoLocation.country).distinct().order_by(GeoLocation.country)
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/locations/states")
async def get_states(
    country: Annotated[str, Query(min_length=2, max_length=2)],
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, str]]:
    """Get distinct states available for a given country."""
    query = (
        select(GeoLocation.state_code, func.max(GeoLocation.state_name).label("name"))
        .where(func.lower(GeoLocation.country) == country.lower())
        .where(GeoLocation.state_code != None)
        .group_by(GeoLocation.state_code)
        .order_by(GeoLocation.state_code)
    )
    result = await session.execute(query)
    return [{"code": row[0], "name": row[1] or row[0]} for row in result.all()]


@router.get("/locations/cities")
async def get_cities(
    country: Annotated[str, Query(min_length=2, max_length=2)],
    state: Annotated[str, Query(min_length=1)],
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    """Get distinct cities available for a given state."""
    query = (
        select(GeoLocation.city).distinct()
        .where(func.lower(GeoLocation.country) == country.lower())
        .where(func.lower(GeoLocation.state_code) == state.lower())
        .where(GeoLocation.city != None)
        .order_by(GeoLocation.city)
    )
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/locations/pincodes")
async def get_pincodes(
    country: Annotated[str, Query(min_length=2, max_length=2)],
    state: Annotated[str, Query(min_length=1)],
    city: Annotated[str, Query(min_length=1)],
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    """Get distinct pincodes available for a given city."""
    query = (
        select(GeoLocation.pincode).distinct()
        .where(func.lower(GeoLocation.country) == country.lower())
        .where(func.lower(GeoLocation.state_code) == state.lower())
        .where(func.lower(GeoLocation.city) == city.lower())
        .order_by(GeoLocation.pincode)
    )
    result = await session.execute(query)
    return result.scalars().all()
