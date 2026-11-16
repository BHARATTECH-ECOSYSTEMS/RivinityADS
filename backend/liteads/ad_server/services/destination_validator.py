"""
Destination Quality and SSRF-Protected Verification Service (Phase 7).

Authoritative verification for landing destinations and click-through URLs:
- HTTPS policy enforcement (rejects plaintext HTTP, javascript:, data:, file:)
- Strict SSRF protection on all server-side preflight fetches
- Multi-hop redirect chain following with SSRF re-validation at every hop
- HTTP reachability and status code validation (2xx required, 4xx/5xx blocked)
- Protection against loopback, private subnets, link-local, cloud metadata, and DNS rebinding
- Malicious and unusable destination detection
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse, urlunparse, urljoin

import httpx

from liteads.common.logger import get_logger
from liteads.common.ssrf import is_ip_allowed, SSRFSafeTransport, BLOCKED_DOMAIN_SUFFIXES, BLOCKED_HOSTNAMES

logger = get_logger(__name__)

MAX_REDIRECT_HOPS = 5
DEFAULT_TIMEOUT_SECONDS = 6.0
MAX_RESPONSE_BYTES = 65536  # 64 KB read limit

# Prohibited destination schemes
DISALLOWED_SCHEMES = {"http", "ftp", "file", "javascript", "data", "vbscript", "about"}

# Obvious malicious / phishing URL patterns
SUSPICIOUS_PATTERNS = [
    re.compile(r"^https?://.*(?:login|signin|verify|account|banking).*(?:\.xyz|\.top|\.work|\.click|\.zip)/", re.I),
    re.compile(r"^https?://.*(?:\.onion|\.invalid|\.test|\.example)/", re.I),
]


class DestinationValidationError(Exception):
    """Exception raised when a destination URL fails safety or quality validation."""

    def __init__(self, message: str, code: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


@dataclass
class DestinationValidationResult:
    """Outcome of destination quality and reachability inspection."""

    is_valid: bool
    normalized_url: str
    final_url: str | None = None
    http_status: int | None = None
    redirect_count: int = 0
    redirect_chain: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def reason(self) -> str | None:
        return self.error_message


def normalize_destination_url(url: str) -> str:
    """
    Normalizes a destination URL:
    - Strips leading/trailing whitespace
    - Lowercases scheme and host
    - Removes default port 443 for https
    - Ensures path is not empty ('/')
    """
    if not url or not isinstance(url, str):
        raise DestinationValidationError(
            "Destination URL cannot be empty", code="EMPTY_URL"
        )

    clean_url = url.strip()
    if len(clean_url) > 2048:
        raise DestinationValidationError(
            "Destination URL exceeds maximum length (2048 characters)",
            code="URL_TOO_LONG",
        )

    try:
        parsed = urlparse(clean_url)
    except Exception as exc:
        raise DestinationValidationError(
            f"Malformed destination URL: {exc}", code="MALFORMED_URL"
        ) from exc

    scheme = (parsed.scheme or "").lower()
    if not scheme:
        raise DestinationValidationError(
            "Destination URL is missing a scheme (HTTPS required)", code="MISSING_SCHEME"
        )

    if scheme != "https":
        raise DestinationValidationError(
            f"Destination URL must use HTTPS (received: {scheme})",
            code="HTTPS_REQUIRED",
        )

    if parsed.username or parsed.password:
        raise DestinationValidationError(
            "Destination URL must not contain embedded user credentials",
            code="CREDENTIALS_FORBIDDEN",
        )

    netloc = (parsed.netloc or "").strip()
    if not netloc:
        raise DestinationValidationError(
            "Destination URL must specify a valid host", code="MISSING_HOST"
        )

    # Separate host and port
    if "@" in netloc:
        raise DestinationValidationError(
            "Userinfo in host is forbidden", code="CREDENTIALS_FORBIDDEN"
        )

    if ":" in netloc:
        host, port_str = netloc.split(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            raise DestinationValidationError("Invalid port in URL", code="INVALID_PORT")
        if port == 443:
            netloc = host.lower()
        else:
            netloc = f"{host.lower()}:{port}"
    else:
        netloc = netloc.lower()

    path = parsed.path or "/"

    return urlunparse(
        (
            scheme,
            netloc,
            path,
            parsed.params,
            parsed.query,
            "",  # Strip fragment
        )
    )


def validate_hostname_ssrf(host: str) -> None:
    """
    Syntactically checks a hostname/IP against SSRF blocked hosts,
    suffixes, loopback, private ranges, and cloud metadata.
    """
    host_clean = host.strip().lower()
    if not host_clean:
        raise DestinationValidationError("Host cannot be empty", code="INVALID_HOST")

    # Check for direct IP address
    try:
        ip = ipaddress.ip_address(host_clean)
        if not is_ip_allowed(ip):
            raise DestinationValidationError(
                f"Destination IP '{host_clean}' is in a private or restricted network",
                code="SSRF_RESTRICTED_IP",
                details={"ip": str(ip)},
            )
        return
    except ValueError:
        pass  # It's a hostname, not a raw IP literal

    # Single-label hosts (e.g. 'localhost', 'internal', 'intranet')
    if "." not in host_clean:
        raise DestinationValidationError(
            f"Destination host '{host_clean}' is a single-label or internal hostname",
            code="SSRF_INTERNAL_HOST",
        )

    if host_clean in BLOCKED_HOSTNAMES:
        raise DestinationValidationError(
            f"Destination host '{host_clean}' is a prohibited internal hostname",
            code="SSRF_PROHIBITED_HOST",
        )

    for suffix in BLOCKED_DOMAIN_SUFFIXES:
        if host_clean.endswith(suffix):
            raise DestinationValidationError(
                f"Destination host '{host_clean}' uses a restricted domain suffix ({suffix})",
                code="SSRF_RESTRICTED_SUFFIX",
            )


async def resolve_and_verify_safe_ips(host: str, port: int = 443) -> list[str]:
    """
    Resolves DNS for a hostname and verifies every resolved IP address is a
    safe, publicly routable address.
    """
    loop = asyncio.get_running_loop()
    try:
        addr_info = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DestinationValidationError(
            f"DNS resolution failed for destination host '{host}'",
            code="DNS_RESOLUTION_FAILED",
            details={"host": host, "error": str(exc)},
        ) from exc

    if not addr_info:
        raise DestinationValidationError(
            f"DNS resolution returned no addresses for host '{host}'",
            code="DNS_RESOLUTION_EMPTY",
        )

    resolved_ips: list[str] = []
    for entry in addr_info:
        ip_str = entry[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise DestinationValidationError(
                f"Invalid IP address resolved for host '{host}': {ip_str}",
                code="INVALID_RESOLVED_IP",
            ) from exc

        if not is_ip_allowed(ip_obj):
            raise DestinationValidationError(
                f"Destination host '{host}' resolves to prohibited/private IP {ip_str}",
                code="SSRF_PROHIBITED_IP",
                details={"host": host, "resolved_ip": ip_str},
            )
        resolved_ips.append(ip_str)

    return resolved_ips


class DestinationValidator:
    """
    Performs comprehensive destination quality, security, and reachability verification.
    """

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self.timeout = timeout_seconds

    @classmethod
    def normalize_and_prevalidate(cls, url: str) -> DestinationValidationResult:
        """Synchronously check URL normalization, scheme, credentials, SSRF syntax, and suspicious patterns."""
        try:
            norm_url = normalize_destination_url(url)
        except DestinationValidationError as exc:
            return DestinationValidationResult(
                is_valid=False,
                normalized_url=url,
                error_code=exc.code,
                error_message=exc.message,
                details=exc.details,
            )

        parsed = urlparse(norm_url)
        host = parsed.netloc.split(":")[0]

        try:
            validate_hostname_ssrf(host)
        except DestinationValidationError as exc:
            return DestinationValidationResult(
                is_valid=False,
                normalized_url=norm_url,
                error_code=exc.code,
                error_message=exc.message,
                details=exc.details,
            )

        for pattern in SUSPICIOUS_PATTERNS:
            if pattern.search(norm_url):
                return DestinationValidationResult(
                    is_valid=False,
                    normalized_url=norm_url,
                    error_code="SUSPICIOUS_DESTINATION",
                    error_message="Destination matches high-risk phishing/malicious URL pattern",
                )

        return DestinationValidationResult(
            is_valid=True,
            normalized_url=norm_url,
            final_url=norm_url,
        )

    @classmethod
    async def check_destination(
        cls,
        url: str,
        *,
        perform_fetch: bool = True,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> DestinationValidationResult:
        """
        Validate destination URL against HTTPS policy, SSRF defenses,
        redirect chains, and HTTP 2xx success.
        """
        pre_res = cls.normalize_and_prevalidate(url)
        if not pre_res.is_valid:
            return pre_res

        norm_url = pre_res.normalized_url

        if not perform_fetch:
            return DestinationValidationResult(
                is_valid=True,
                normalized_url=norm_url,
                final_url=norm_url,
            )

        # 4. Safe Server-Side Fetch with SSRF-Safe Transport and Redirect Loop Inspection
        current_url = norm_url
        redirect_chain: list[str] = [norm_url]
        redirect_count = 0
        final_status = None

        headers = {
            "User-Agent": "RivinityADS-DestinationValidator/1.0 (+https://rivinityads.com/bot; quality-verification)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        transport = SSRFSafeTransport()
        async with httpx.AsyncClient(
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=False,  # We manually follow redirects to validate every hop
            headers=headers,
        ) as client:
            while redirect_count <= MAX_REDIRECT_HOPS:
                curr_parsed = urlparse(current_url)
                curr_host = curr_parsed.netloc.split(":")[0]

                # Pre-verify DNS resolution for each hop
                try:
                    await resolve_and_verify_safe_ips(curr_host)
                except DestinationValidationError as exc:
                    return DestinationValidationResult(
                        is_valid=False,
                        normalized_url=norm_url,
                        final_url=current_url,
                        redirect_count=redirect_count,
                        redirect_chain=redirect_chain,
                        error_code=exc.code,
                        error_message=exc.message,
                        details=exc.details,
                    )

                try:
                    # Attempt HEAD first for efficiency
                    response = await client.head(current_url)
                    if response.status_code in (405, 501):  # Method not allowed -> fallback to GET
                        response = await client.get(current_url)
                except httpx.TimeoutException:
                    return DestinationValidationResult(
                        is_valid=False,
                        normalized_url=norm_url,
                        final_url=current_url,
                        redirect_count=redirect_count,
                        redirect_chain=redirect_chain,
                        error_code="DESTINATION_TIMEOUT",
                        error_message=f"Destination check timed out after {timeout_seconds}s",
                    )
                except (httpx.ConnectError, httpx.RequestError) as exc:
                    err_str = str(exc)
                    if "prohibited" in err_str.lower() or "ssrf" in err_str.lower():
                        return DestinationValidationResult(
                            is_valid=False,
                            normalized_url=norm_url,
                            final_url=current_url,
                            redirect_count=redirect_count,
                            redirect_chain=redirect_chain,
                            error_code="SSRF_PROHIBITED_IP",
                            error_message="Connection aborted: destination resolves to prohibited IP address",
                        )
                    return DestinationValidationResult(
                        is_valid=False,
                        normalized_url=norm_url,
                        final_url=current_url,
                        redirect_count=redirect_count,
                        redirect_chain=redirect_chain,
                        error_code="CONNECTION_FAILED",
                        error_message=f"Failed to connect to destination: {err_str}",
                    )

                final_status = response.status_code

                # Handle redirect
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        return DestinationValidationResult(
                            is_valid=False,
                            normalized_url=norm_url,
                            final_url=current_url,
                            http_status=final_status,
                            redirect_count=redirect_count,
                            redirect_chain=redirect_chain,
                            error_code="MALFORMED_REDIRECT",
                            error_message="Redirect response missing Location header",
                        )

                    next_url = urljoin(current_url, location)
                    try:
                        norm_next_url = normalize_destination_url(next_url)
                    except DestinationValidationError as exc:
                        return DestinationValidationResult(
                            is_valid=False,
                            normalized_url=norm_url,
                            final_url=next_url,
                            http_status=final_status,
                            redirect_count=redirect_count,
                            redirect_chain=redirect_chain,
                            error_code=f"REDIRECT_{exc.code}",
                            error_message=f"Redirect hop violated destination policy: {exc.message}",
                        )

                    next_host = urlparse(norm_next_url).netloc.split(":")[0]
                    try:
                        validate_hostname_ssrf(next_host)
                    except DestinationValidationError as exc:
                        return DestinationValidationResult(
                            is_valid=False,
                            normalized_url=norm_url,
                            final_url=norm_next_url,
                            http_status=final_status,
                            redirect_count=redirect_count,
                            redirect_chain=redirect_chain,
                            error_code=f"REDIRECT_{exc.code}",
                            error_message=f"Redirect destination attempted SSRF: {exc.message}",
                        )

                    if norm_next_url in redirect_chain:
                        return DestinationValidationResult(
                            is_valid=False,
                            normalized_url=norm_url,
                            final_url=norm_next_url,
                            http_status=final_status,
                            redirect_count=redirect_count,
                            redirect_chain=redirect_chain,
                            error_code="REDIRECT_LOOP_DETECTED",
                            error_message="Circular redirect loop detected in destination chain",
                        )

                    redirect_count += 1
                    if redirect_count > MAX_REDIRECT_HOPS:
                        return DestinationValidationResult(
                            is_valid=False,
                            normalized_url=norm_url,
                            final_url=norm_next_url,
                            http_status=final_status,
                            redirect_count=redirect_count,
                            redirect_chain=redirect_chain,
                            error_code="TOO_MANY_REDIRECTS",
                            error_message=f"Destination exceeded maximum of {MAX_REDIRECT_HOPS} redirects",
                        )

                    current_url = norm_next_url
                    redirect_chain.append(current_url)
                    continue

                # Final non-redirect response
                break

        # 5. Evaluate final HTTP response code
        if final_status is None:
            return DestinationValidationResult(
                is_valid=False,
                normalized_url=norm_url,
                error_code="NO_RESPONSE",
                error_message="Destination produced no HTTP response",
            )

        if 200 <= final_status < 300:
            return DestinationValidationResult(
                is_valid=True,
                normalized_url=norm_url,
                final_url=current_url,
                http_status=final_status,
                redirect_count=redirect_count,
                redirect_chain=redirect_chain,
            )
        elif 400 <= final_status < 500:
            return DestinationValidationResult(
                is_valid=False,
                normalized_url=norm_url,
                final_url=current_url,
                http_status=final_status,
                redirect_count=redirect_count,
                redirect_chain=redirect_chain,
                error_code="HTTP_CLIENT_ERROR",
                error_message=f"Destination returned HTTP client error ({final_status})",
            )
        elif final_status >= 500:
            return DestinationValidationResult(
                is_valid=False,
                normalized_url=norm_url,
                final_url=current_url,
                http_status=final_status,
                redirect_count=redirect_count,
                redirect_chain=redirect_chain,
                error_code="HTTP_SERVER_ERROR",
                error_message=f"Destination returned HTTP server error ({final_status})",
            )
        else:
            return DestinationValidationResult(
                is_valid=False,
                normalized_url=norm_url,
                final_url=current_url,
                http_status=final_status,
                redirect_count=redirect_count,
                redirect_chain=redirect_chain,
                error_code=f"UNEXPECTED_HTTP_STATUS_{final_status}",
                error_message=f"Destination returned unexpected HTTP status {final_status}",
            )


# Global singleton instance
destination_validator = DestinationValidator()
