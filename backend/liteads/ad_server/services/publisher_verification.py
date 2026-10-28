"""Publisher property domain verification service (Phase 10E).

Supports:
1. DNS TXT record challenge (_rivinity-challenge.<domain>).
2. HTML Meta Tag challenge (<meta name="rivinity-site-verification" content="<token>">).
3. Platform subdomain automated verification for projects hosted on RivinityADS.

Security Guards:
- Cryptographically secure unpredictable challenges with 48h expiry.
- Strict domain normalization preventing host injection, user-info, IP addresses, and localhost.
- SSRF defenses for HTML meta-tag verification (blocking RFC 1918, loopback, link-local, multicast IPs).
- Rate limiting on verification checks to prevent denial-of-service or DNS flood attacks.
- Controlled test adapters for deterministic, un-mocked local and staging tests.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse

import dns.asyncresolver
from dns.exception import DNSException
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.common.logger import get_logger
from liteads.models.publisher import PublisherProperty, Status

from liteads.common.ssrf import is_ip_allowed

logger = get_logger(__name__)

# Test adapters (allow testing DNS/HTML verification in controlled environments without external network)
_test_dns_resolver: Callable[[str], list[str]] | None = None
_test_html_fetcher: Callable[[str], str] | None = None

# In-memory rate limiting fallback when Redis is offline (property_id -> list of attempt timestamps)
_IN_MEMORY_VERIFY_RATE_LIMITS: dict[int, list[float]] = {}


def set_test_dns_resolver(resolver: Callable[[str], list[str]] | None) -> None:
    """Set custom test resolver for DNS TXT verification."""
    global _test_dns_resolver
    _test_dns_resolver = resolver


def set_test_html_fetcher(fetcher: Callable[[str], str] | None) -> None:
    """Set custom test fetcher for HTML meta tag verification."""
    global _test_html_fetcher
    _test_html_fetcher = fetcher


def normalize_property_domain(raw_domain: str) -> str:
    """
    Normalize domain name and enforce strict format constraints.

    Rejects:
    - Empty or whitespace-only domain.
    - Userinfo (@).
    - IP addresses (IPv4 and IPv6).
    - Localhost or single-label domains.
    - Malformed or control characters.
    """
    if not raw_domain or not isinstance(raw_domain, str):
        raise ValueError("Domain cannot be empty.")

    clean = raw_domain.strip().lower()

    # Reject userinfo
    if "@" in clean:
        raise ValueError("Invalid domain: user-info syntax not allowed.")

    # Remove protocol prefix if present
    if "://" in clean:
        clean = clean.split("://", 1)[1]

    # Strip path, query, fragment
    for sep in ("/", "?", "#"):
        if sep in clean:
            clean = clean.split(sep, 1)[0]

    # Strip port if present (handle bracketed IPv6 [::1]:8080 or host:port)
    if clean.startswith("[") and "]" in clean:
        bracket_end = clean.find("]")
        clean = clean[1:bracket_end]
    elif clean.count(":") == 1:
        clean = clean.split(":", 1)[0]

    clean = clean.strip().rstrip(".")

    if not clean:
        raise ValueError("Domain cannot be empty.")
    if clean == "localhost":
        raise ValueError("Invalid domain: 'localhost' is not an eligible production domain.")

    # Reject IP addresses
    try:
        ipaddress.ip_address(clean)
        raise ValueError("IP addresses are not allowed as property domains. Please provide a valid FQDN.")
    except ValueError as e:
        if "IP addresses are not allowed" in str(e):
            raise

    # Standard FQDN regex
    pattern = re.compile(
        r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
    )
    if not pattern.match(clean) or "--" in clean:
        raise ValueError(f"Invalid domain format: '{clean}'.")

    return clean


def generate_property_verification_token() -> str:
    """Generate an unpredictable high-entropy verification token."""
    entropy = secrets.token_urlsafe(32)
    return f"rivinity-site-verification={entropy}"


async def check_property_verification_rate_limit(property_id: int, max_attempts: int = 5, window_seconds: int = 900) -> bool:
    """
    Enforce rate limits on verification attempts per property.
    Allows up to 5 attempts per 15 minutes.
    """
    key = f"rate_limit:property_verify:{property_id}"
    if redis_client.connected:
        try:
            pipe = redis_client.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds)
            res = await pipe.execute()
            count = res[0]
            return count <= max_attempts
        except Exception as e:
            logger.debug("Redis rate limit error, using in-memory fallback", error=str(e))

    now = time.time()
    cutoff = now - window_seconds
    timestamps = _IN_MEMORY_VERIFY_RATE_LIMITS.setdefault(property_id, [])
    _IN_MEMORY_VERIFY_RATE_LIMITS[property_id] = [t for t in timestamps if t > cutoff]
    if len(_IN_MEMORY_VERIFY_RATE_LIMITS[property_id]) >= max_attempts:
        return False
    _IN_MEMORY_VERIFY_RATE_LIMITS[property_id].append(now)
    return True


async def verify_dns_txt(domain: str, expected_token: str) -> tuple[bool, str | None]:
    """
    Check DNS TXT records for _rivinity-challenge.<domain> and <domain>.
    Returns (is_verified, error_detail).
    """
    # Controlled test adapter
    if _test_dns_resolver is not None:
        try:
            records = _test_dns_resolver(domain)
            for r in records:
                if r.strip() == expected_token.strip():
                    return True, None
            return False, f"Verification token not found in DNS TXT records for {domain}"
        except Exception as e:
            return False, f"DNS resolution failed: {str(e)}"

    record_candidates = [f"_rivinity-challenge.{domain}", domain]
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = 5.0
    resolver.lifetime = 10.0

    last_error = None
    for rec_name in record_candidates:
        try:
            answers = await resolver.resolve(rec_name, "TXT")
            for rdata in answers:
                txt_strings = [s.decode("utf-8") for s in rdata.strings]
                full_txt = "".join(txt_strings).strip()
                if full_txt == expected_token.strip():
                    return True, None
        except dns.resolver.NXDOMAIN:
            last_error = f"DNS record {rec_name} does not exist (NXDOMAIN)."
        except dns.resolver.NoAnswer:
            last_error = f"No TXT records found for {rec_name}."
        except DNSException as e:
            last_error = f"DNS query failed: {str(e)}"
        except Exception as e:
            last_error = f"Unexpected DNS error: {str(e)}"

    return False, last_error or "Verification challenge TXT record not found."


def _is_ip_private_or_restricted(ip_str: str) -> bool:
    """Strict SSRF check: returns True if IP is private, loopback, link-local, reserved, or prohibited."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return not is_ip_allowed(ip)
    except ValueError:
        return True


async def verify_html_meta(domain: str, expected_token: str) -> tuple[bool, str | None]:
    """
    Fetch root HTML of domain and inspect for meta tag verification challenge.
    Enforces strict SSRF controls against private and loopback network targets.
    """
    # Controlled test adapter
    if _test_html_fetcher is not None:
        try:
            html_text = _test_html_fetcher(domain)
            if not html_text:
                return False, "Empty response from server"
            return _extract_and_check_meta_tag(html_text, expected_token)
        except Exception as e:
            return False, f"HTTP fetch failed: {str(e)}"

    # 1. Resolve host IP to prevent SSRF against loopback / internal private IPs
    try:
        addr_info = socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
        resolved_ips = [addr[4][0] for addr in addr_info]
    except Exception as e:
        return False, f"Could not resolve domain IP: {str(e)}"

    if not resolved_ips:
        return False, "Domain does not resolve to an IP address."

    for ip_addr in resolved_ips:
        if _is_ip_private_or_restricted(ip_addr):
            logger.warning("SSRF blocked: Domain resolves to private or restricted IP", domain=domain, ip=ip_addr)
            return False, "SSRF protection: Domain resolves to private, loopback, or reserved IP address."

    # 2. Fetch HTML with safe timeout and bounded size
    schemes = ["https", "http"]
    last_err = None

    for scheme in schemes:
        url = f"{scheme}://{domain}/"
        try:
            async with httpx.AsyncClient(
                timeout=5.0,
                follow_redirects=False,
                headers={"User-Agent": "RivinityADS-VerificationBot/1.0 (+https://rivinity.com/bot)"},
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    text_content = resp.text[:100000]  # Read at most 100KB of HTML header
                    is_valid, msg = _extract_and_check_meta_tag(text_content, expected_token)
                    if is_valid:
                        return True, None
                    last_err = msg
                else:
                    last_err = f"HTTP request returned status {resp.status_code}"
        except httpx.RequestError as e:
            last_err = f"HTTP connection failed: {str(e)}"

    return False, last_err or "HTML meta tag not found."


def _extract_and_check_meta_tag(html_content: str, expected_token: str) -> tuple[bool, str | None]:
    """Inspect HTML string for <meta name="rivinity-site-verification" content="...">."""
    # Case-insensitive search for meta tag
    meta_pattern = re.compile(
        r'<meta\s+[^>]*name=["\']rivinity-site-verification["\'][^>]*content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    match = meta_pattern.search(html_content)
    if not match:
        # Check inverted attribute order: content="..." name="..."
        meta_pattern_rev = re.compile(
            r'<meta\s+[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']rivinity-site-verification["\']',
            re.IGNORECASE,
        )
        match = meta_pattern_rev.search(html_content)

    if not match:
        return False, "Meta tag '<meta name=\"rivinity-site-verification\" content=\"...\">' not found in page head."

    found_token = match.group(1).strip()
    if found_token == expected_token.strip():
        return True, None

    return False, f"Meta tag token '{found_token}' does not match expected challenge '{expected_token}'."


class PublisherVerificationService:
    """Service orchestrating publisher domain verification and property lifecycle."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_challenge(
        self,
        property_obj: PublisherProperty,
        method: str = "DNS_TXT",
    ) -> PublisherProperty:
        """Issue or refresh an unpredictable verification challenge on a property."""
        clean_method = method.upper()
        if clean_method not in ("DNS_TXT", "HTML_META"):
            clean_method = "DNS_TXT"

        token = generate_property_verification_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

        property_obj.verification_method = clean_method
        property_obj.verification_token = token
        property_obj.challenge_expires_at = expires_at
        property_obj.verification_status = "PENDING"
        property_obj.last_error = None

        await self.session.commit()
        await self.session.refresh(property_obj)

        logger.info(
            "Issued property verification challenge",
            property_id=property_obj.id,
            domain=property_obj.domain,
            method=clean_method,
        )
        return property_obj

    async def verify_property(self, property_obj: PublisherProperty) -> PublisherProperty:
        """
        Execute server-side domain verification for a publisher property.
        Validates token freshness, rate limits, and DNS/HTML challenges.
        """
        # 1. Rate limiting
        rate_ok = await check_property_verification_rate_limit(property_obj.id)
        if not rate_ok:
            property_obj.last_error = "Too many verification attempts. Please wait 15 minutes before retrying."
            await self.session.commit()
            await self.session.refresh(property_obj)
            return property_obj

        # 2. If already verified, no-op
        if property_obj.verification_status == "VERIFIED":
            return property_obj

        # 3. Check challenge expiration
        now_utc = datetime.now(timezone.utc)
        if property_obj.challenge_expires_at:
            exp = property_obj.challenge_expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now_utc > exp:
                property_obj.verification_status = "FAILED"
                property_obj.last_error = "Verification challenge has expired. Please generate a new challenge."
                property_obj.last_checked_at = now_utc.replace(tzinfo=None)
                await self.session.commit()
                await self.session.refresh(property_obj)
                return property_obj

        token = property_obj.verification_token
        if not token:
            property_obj.verification_status = "FAILED"
            property_obj.last_error = "No active verification challenge found. Please generate a challenge first."
            property_obj.last_checked_at = now_utc.replace(tzinfo=None)
            await self.session.commit()
            await self.session.refresh(property_obj)
            return property_obj

        # 4. Check for cross-publisher domain collisions
        # Another publisher cannot claim a domain that is actively verified by a different publisher
        other_verified_stmt = select(PublisherProperty).where(
            PublisherProperty.domain == property_obj.domain,
            PublisherProperty.verification_status == "VERIFIED",
            PublisherProperty.publisher_id != property_obj.publisher_id,
        )
        other_res = await self.session.execute(other_verified_stmt)
        if other_res.scalar_one_or_none() is not None:
            property_obj.verification_status = "FAILED"
            property_obj.last_error = "This domain is currently verified by another publisher account. Contact support to dispute ownership."
            property_obj.last_checked_at = now_utc.replace(tzinfo=None)
            await self.session.commit()
            await self.session.refresh(property_obj)
            return property_obj

        # 5. Execute verification based on configured method
        method = (property_obj.verification_method or "DNS_TXT").upper()
        is_verified = False
        error_msg: str | None = None

        if method == "DNS_TXT":
            is_verified, error_msg = await verify_dns_txt(property_obj.domain, token)
        elif method == "HTML_META":
            is_verified, error_msg = await verify_html_meta(property_obj.domain, token)
        elif method == "PLATFORM_SUBDOMAIN":
            is_verified = property_obj.is_platform_subdomain
            if not is_verified:
                error_msg = "Platform subdomain ownership check failed."
        else:
            error_msg = f"Unknown verification method: {method}"

        property_obj.last_checked_at = now_utc.replace(tzinfo=None)
        if is_verified:
            property_obj.verification_status = "VERIFIED"
            property_obj.verified_at = now_utc.replace(tzinfo=None)
            property_obj.last_error = None
            property_obj.status = Status.ACTIVE
            logger.info("Publisher property successfully verified", property_id=property_obj.id, domain=property_obj.domain)
        else:
            property_obj.verification_status = "FAILED"
            property_obj.last_error = error_msg
            logger.warning(
                "Publisher property verification failed",
                property_id=property_obj.id,
                domain=property_obj.domain,
                method=method,
                reason=error_msg,
            )

        await self.session.commit()
        await self.session.refresh(property_obj)
        return property_obj
