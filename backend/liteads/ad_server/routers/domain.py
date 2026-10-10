from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from liteads.common.database import get_session
from liteads.common.security import current_user, require_roles, verified_user
from liteads.models.auth import User
from liteads.models.domain import AdvertiserDomain
from liteads.schemas.domain import (
    DomainCreate,
    DomainResponse,
    DomainVerificationInstructions,
)
from liteads.ad_server.services.domain_verification import DomainVerificationService, generate_verification_token

router = APIRouter(prefix="/domain", tags=["Domains"], dependencies=[Depends(verified_user)])


@router.post("", response_model=DomainResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def register_domain(
    payload: DomainCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AdvertiserDomain:
    """Register a new domain for verification."""
    # Enforce tenant authorization: user must have an advertiser_id
    if not user.advertiser_id:
        raise HTTPException(status_code=403, detail="Must be associated with an advertiser")
        
    service = DomainVerificationService(session)
    try:
        domain = await service.register_domain(user.advertiser_id, payload.domain)
        return domain
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=List[DomainResponse], dependencies=[Depends(require_roles("admin", "advertiser"))])
async def list_domains(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> List[AdvertiserDomain]:
    """List all domains for the authenticated advertiser."""
    if not user.advertiser_id:
        raise HTTPException(status_code=403, detail="Must be associated with an advertiser")
        
    stmt = select(AdvertiserDomain).where(AdvertiserDomain.advertiser_id == user.advertiser_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{domain_id}", response_model=DomainResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_domain(
    domain_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AdvertiserDomain:
    """Get domain details."""
    if not user.advertiser_id:
        raise HTTPException(status_code=403, detail="Must be associated with an advertiser")
        
    domain = await session.get(AdvertiserDomain, domain_id)
    if not domain or domain.advertiser_id != user.advertiser_id:
        raise HTTPException(status_code=404, detail="Domain not found")
        
    return domain


@router.get("/{domain_id}/instructions", response_model=DomainVerificationInstructions, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def get_domain_instructions(
    domain_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> DomainVerificationInstructions:
    """Get verification instructions for a domain."""
    if not user.advertiser_id:
        raise HTTPException(status_code=403, detail="Must be associated with an advertiser")
        
    domain = await session.get(AdvertiserDomain, domain_id)
    if not domain or domain.advertiser_id != user.advertiser_id:
        raise HTTPException(status_code=404, detail="Domain not found")
        
    service = DomainVerificationService(session)
    return service.get_instructions(domain)


@router.post("/{domain_id}/verify", response_model=DomainResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def check_domain_verification(
    domain_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AdvertiserDomain:
    """Trigger a server-side verification check for the domain."""
    if not user.advertiser_id:
        raise HTTPException(status_code=403, detail="Must be associated with an advertiser")
        
    domain = await session.get(AdvertiserDomain, domain_id)
    if not domain or domain.advertiser_id != user.advertiser_id:
        raise HTTPException(status_code=404, detail="Domain not found")
        
    service = DomainVerificationService(session)
    return await service.check_verification(domain)


@router.post("/{domain_id}/regenerate-challenge", response_model=DomainResponse, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def regenerate_challenge(
    domain_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AdvertiserDomain:
    """Regenerate the verification challenge token for an unverified domain."""
    if not user.advertiser_id:
        raise HTTPException(status_code=403, detail="Must be associated with an advertiser")
        
    domain = await session.get(AdvertiserDomain, domain_id)
    if not domain or domain.advertiser_id != user.advertiser_id:
        raise HTTPException(status_code=404, detail="Domain not found")
        
    if domain.status == "VERIFIED":
        raise HTTPException(status_code=400, detail="Domain is already verified")
        
    domain.verification_token = generate_verification_token()
    domain.status = "PENDING"
    domain.last_error = None
    
    await session.commit()
    await session.refresh(domain)
    return domain


@router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_roles("admin", "advertiser"))])
async def remove_domain(
    domain_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Remove a domain from the advertiser account."""
    if not user.advertiser_id:
        raise HTTPException(status_code=403, detail="Must be associated with an advertiser")
        
    domain = await session.get(AdvertiserDomain, domain_id)
    if not domain or domain.advertiser_id != user.advertiser_id:
        raise HTTPException(status_code=404, detail="Domain not found")
        
    await session.delete(domain)
    await session.commit()
