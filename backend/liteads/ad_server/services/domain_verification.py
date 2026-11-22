import re
import secrets
from datetime import datetime, timezone
from typing import Optional

import dns.asyncresolver
from dns.exception import DNSException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from liteads.models.domain import AdvertiserDomain, VerificationStatus, VerificationMethod
from liteads.schemas.domain import DomainVerificationInstructions

def normalize_domain(domain: str) -> str:
    """Normalize a domain name."""
    if not domain:
        raise ValueError("Domain cannot be empty")
    
    # Remove protocol if present
    if "://" in domain:
        domain = domain.split("://", 1)[1]
    
    # Remove paths or query strings
    if "/" in domain:
        domain = domain.split("/", 1)[0]
    if "?" in domain:
        domain = domain.split("?", 1)[0]
    if "#" in domain:
        domain = domain.split("#", 1)[0]
        
    # Remove port if present
    if ":" in domain:
        domain = domain.split(":", 1)[0]
        
    # Lowercase and strip
    domain = domain.strip().lower()
    
    # Remove trailing dot if present
    if domain.endswith("."):
        domain = domain[:-1]
        
    # Reject local/private/IPs (simplified validation)
    if domain in ("localhost", ""):
        raise ValueError("Invalid domain")
        
    if re.match(r"^(\d{1,3}\.){3}\d{1,3}$", domain):
        raise ValueError("IP addresses are not allowed")
        
    # Basic domain format validation (very permissive but prevents obvious junk)
    if not re.match(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", domain):
        raise ValueError("Invalid domain format")
        
    return domain


def generate_verification_token() -> str:
    """Generate a secure, unpredictable challenge."""
    return f"rivinity-verify={secrets.token_urlsafe(32)}"


async def verify_dns_txt(domain: str, expected_token: str) -> tuple[bool, Optional[str]]:
    """
    Perform DNS TXT lookup to verify domain ownership.
    Returns (is_verified, error_message)
    """
    record_name = f"_rivinity-challenge.{domain}"
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = 2.5
    resolver.lifetime = 5.0
    
    try:
        try:
            answers = await resolver.resolve(record_name, "TXT")
        except (dns.resolver.Timeout, dns.resolver.LifetimeTimeout):
            # Fall back to public resolvers if local network resolvers time out (e.g. stale VPN / corporate DNS)
            fallback_resolver = dns.asyncresolver.Resolver(configure=False)
            fallback_resolver.nameservers = ["8.8.8.8", "1.1.1.1"]
            fallback_resolver.timeout = 3.0
            fallback_resolver.lifetime = 5.0
            answers = await fallback_resolver.resolve(record_name, "TXT")
        for rdata in answers:
            # rdata.strings is a tuple of bytes
            txt_strings = [s.decode('utf-8') for s in rdata.strings]
            txt_record = "".join(txt_strings)
            
            if txt_record == expected_token:
                return True, None
                
        return False, "Verification token not found in TXT records"
    except dns.resolver.NXDOMAIN:
        return False, f"TXT record not found for {record_name}"
    except dns.resolver.NoAnswer:
        return False, f"No TXT records found for {record_name}"
    except DNSException as e:
        return False, f"DNS lookup failed: {str(e)}"
    except Exception as e:
        return False, "An unexpected error occurred during verification"


class DomainVerificationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def register_domain(self, advertiser_id: int, domain_name: str) -> AdvertiserDomain:
        normalized_domain = normalize_domain(domain_name)
        
        # Check if already registered by anyone
        stmt = select(AdvertiserDomain).where(AdvertiserDomain.domain == normalized_domain)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            if existing.advertiser_id == advertiser_id:
                return existing  # Idempotent registration for same owner
            else:
                raise ValueError("Domain is already registered by another advertiser")
                
        # Create new domain record
        token = generate_verification_token()
        new_domain = AdvertiserDomain(
            advertiser_id=advertiser_id,
            domain=normalized_domain,
            status=VerificationStatus.PENDING,
            verification_method=VerificationMethod.DNS_TXT,
            verification_token=token,
        )
        self.session.add(new_domain)
        await self.session.commit()
        await self.session.refresh(new_domain)
        return new_domain
        
    def get_instructions(self, domain_record: AdvertiserDomain) -> DomainVerificationInstructions:
        record_name = f"_rivinity-challenge.{domain_record.domain}"
        return DomainVerificationInstructions(
            domain=domain_record.domain,
            record_type="TXT",
            record_name=record_name,
            record_value=domain_record.verification_token,
            instructions=f"Add a DNS TXT record for '{record_name}' with the value '{domain_record.verification_token}'. It may take some time for DNS changes to propagate."
        )

    async def check_verification(self, domain_record: AdvertiserDomain) -> AdvertiserDomain:
        """Check domain verification and update status."""
        if domain_record.status == VerificationStatus.VERIFIED:
            return domain_record
            
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        domain_record.last_checked_at = now_utc
        
        is_verified, error_msg = await verify_dns_txt(domain_record.domain, domain_record.verification_token)
        
        if is_verified:
            domain_record.status = VerificationStatus.VERIFIED
            domain_record.verified_at = now_utc
            domain_record.last_error = None
        else:
            domain_record.status = VerificationStatus.FAILED
            domain_record.last_error = error_msg
            
        await self.session.commit()
        await self.session.refresh(domain_record)
        return domain_record

    async def is_url_verified_for_advertiser(self, advertiser_id: int, url: str) -> bool:
        """
        Check if the domain for a given URL is verified by the advertiser.
        Policy: Exact match or verified parent domain.
        """
        try:
            domain = normalize_domain(url)
        except ValueError:
            return False
            
        # Check exact domain and all parent domains
        parts = domain.split(".")
        candidate_domains = []
        for i in range(len(parts) - 1): # At least tld and domain
            candidate_domains.append(".".join(parts[i:]))
            
        stmt = select(AdvertiserDomain).where(
            AdvertiserDomain.advertiser_id == advertiser_id,
            AdvertiserDomain.domain.in_(candidate_domains),
            AdvertiserDomain.status == VerificationStatus.VERIFIED
        )
        result = await self.session.execute(stmt)
        return result.first() is not None

    # Alias for convenience
    is_url_verified = is_url_verified_for_advertiser

