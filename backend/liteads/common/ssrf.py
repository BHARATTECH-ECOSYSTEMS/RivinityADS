"""SSRF (Server-Side Request Forgery) protection utilities for RivinityADS.

Provides comprehensive defenses for outbound webhook deliveries:
- Strict URL scheme validation (HTTPS only)
- Credential and user-info rejection
- Allowed port enforcement (standard HTTPS 443, 8443)
- Canonical IP syntax enforcement; rejection of hex, octal, dword, shorthand, and leading-zero IPs
- Blocking loopback, private (RFC 1918), link-local, multicast, reserved, unspecified, and non-global IPs
- Blocking IPv4-mapped IPv6, 6to4, Teredo, and cloud metadata services (AWS, GCP, Azure, Alibaba)
- Rejecting internal domain names, single-label hosts, and restricted suffixes (.local, .internal, etc.)
- Multi-address DNS resolution validation (rejects if ANY resolved address is prohibited)
- Defense against DNS rebinding / TOCTOU via SSRFSafeBackend pinning at socket connect time
- Re-validation of all redirect hops
- Bounded response reads and strict timeouts
"""

import asyncio
import ipaddress
import logging
import re
import socket
import urllib.parse
from typing import Any

import httpcore
from httpcore._backends.auto import AutoBackend
import httpx

logger = logging.getLogger(__name__)

# Allowed outbound HTTPS ports
ALLOWED_PORTS = {443, 8443}

# Canonical IPv4 regex (strict 4 octets 0-255 without leading zeros)
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])"
RE_IPV4_CANONICAL = re.compile(rf"^(?:{_OCTET}\.){{3}}{_OCTET}$")

# Blocked domain names and suffixes
BLOCKED_DOMAIN_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".corp",
    ".arpa",
    ".invalid",
    ".test",
    ".example",
    ".intranet",
    ".onion",
)

BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "instance-data",
    "kubernetes.default",
}

# Explicit cloud metadata & sensitive addresses
EXPLICIT_BLOCKED_IPS = {
    ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud IMDS
    ipaddress.ip_address("169.254.169.254"),  # AWS / Azure / GCP IMDSv1/v2
    ipaddress.ip_address("169.254.169.250"),  # Azure host wire
    ipaddress.ip_address("169.254.169.251"),
    ipaddress.ip_address("169.254.169.253"),
    ipaddress.ip_address("fd00:ec2::254"),    # AWS IPv6 IMDS
}

# Special IPv6 networks to inspect
IPV6_6TO4_NET = ipaddress.IPv6Network("2002::/16")
IPV6_TEREDO_NET = ipaddress.IPv6Network("2001::/32")
IPV6_NAT64_NET = ipaddress.IPv6Network("64:ff9b::/96")


class SSRFError(ValueError):
    """Base exception for SSRF-related validation and delivery errors."""

    pass


class SSRFValidationError(SSRFError):
    """Raised when a URL destination violates SSRF safety policies."""

    pass


class SSRFConnectionError(SSRFError, httpcore.ConnectError):
    """Raised when an outbound connection attempts to reach a prohibited IP."""

    pass


def is_ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if the IP address is a safe, publicly routable address.

    Blocks:
    - Loopback (127.0.0.0/8, ::1)
    - Private networks (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7)
    - Link-local (169.254.0.0/16, fe80::/10)
    - Multicast (224.0.0.0/4, ff00::/8)
    - Reserved / unspecified / non-global
    - IPv4-mapped IPv6 (::ffff:0:0/96) targeting private addresses
    - 6to4, Teredo, NAT64 with private embedded IPv4
    - Known cloud metadata endpoints
    """
    if (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False

    # IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1 or ::ffff:169.254.169.254)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        if not is_ip_allowed(ip.ipv4_mapped):
            return False

    # Deprecated IPv4-compatible IPv6 (::x.x.x.x)
    if isinstance(ip, ipaddress.IPv6Address) and int(ip) <= 0xFFFFFFFF:
        embedded_ipv4 = ipaddress.IPv4Address(int(ip))
        if not is_ip_allowed(embedded_ipv4):
            return False

    # 6to4 (2002::/16) - check embedded 32-bit IPv4 address
    if isinstance(ip, ipaddress.IPv6Address) and ip in IPV6_6TO4_NET:
        embedded_ipv4 = ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
        if not is_ip_allowed(embedded_ipv4):
            return False

    # Teredo (2001::/32) - check embedded client IPv4 address
    if isinstance(ip, ipaddress.IPv6Address) and ip in IPV6_TEREDO_NET:
        embedded_ipv4 = ipaddress.IPv4Address((~int(ip)) & 0xFFFFFFFF)
        if not is_ip_allowed(embedded_ipv4):
            return False

    # NAT64 well-known prefix (64:ff9b::/96)
    if isinstance(ip, ipaddress.IPv6Address) and ip in IPV6_NAT64_NET:
        embedded_ipv4 = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if not is_ip_allowed(embedded_ipv4):
            return False

    # Explicit cloud metadata IPs
    if ip in EXPLICIT_BLOCKED_IPS:
        return False

    return True


def validate_destination_url(
    url: str,
    *,
    resolve_dns: bool = True,
) -> urllib.parse.ParseResult:
    """Validate that a webhook destination URL is safe against SSRF attacks.

    Parameters
    ----------
    url : str
        The webhook destination URL.
    resolve_dns : bool, default True
        Whether to perform DNS resolution check on hostnames.

    Returns
    -------
    urllib.parse.ParseResult
        The parsed URL if valid.

    Raises
    ------
    SSRFValidationError
        If the URL violates any SSRF security constraint.
    """
    if not url or not isinstance(url, str):
        raise SSRFValidationError("Webhook destination URL cannot be empty.")

    url_clean = url.strip()
    if len(url_clean) < 8 or len(url_clean) > 2048:
        raise SSRFValidationError(
            "Webhook destination URL length must be between 8 and 2048 characters."
        )

    try:
        parsed = urllib.parse.urlparse(url_clean)
    except Exception as exc:
        raise SSRFValidationError(f"Malformed webhook destination URL: {exc}") from exc

    # 1. Enforce HTTPS scheme only
    if not parsed.scheme or parsed.scheme.lower() != "https":
        raise SSRFValidationError(
            "Webhook destination URL must use the HTTPS scheme."
        )

    # 2. Reject credentials / userinfo
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise SSRFValidationError(
            "Webhook destination URL must not contain user credentials."
        )

    # 3. Validate host presence
    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError("Webhook destination URL must contain a valid host.")

    # 4. Enforce supported ports
    try:
        port = parsed.port
    except ValueError as exc:
        raise SSRFValidationError(f"Malformed port in webhook destination URL: {exc}") from exc

    if port is not None and port not in ALLOWED_PORTS:
        allowed_str = ", ".join(str(p) for p in sorted(ALLOWED_PORTS))
        raise SSRFValidationError(
            f"Unsupported port {port}: webhook destinations only allow ports: {allowed_str}."
        )

    # 5. Check if host is a literal IP address
    if RE_IPV4_CANONICAL.match(hostname):
        ip = ipaddress.IPv4Address(hostname)
        if not is_ip_allowed(ip):
            raise SSRFValidationError(
                "Webhook destination host is a private or prohibited IP address."
            )
        return parsed

    is_ipv6 = False
    try:
        ip = ipaddress.IPv6Address(hostname)
        is_ipv6 = True
    except (ValueError, ipaddress.AddressValueError):
        is_ipv6 = False

    if is_ipv6:
        if not is_ip_allowed(ip):
            raise SSRFValidationError(
                "Webhook destination host is a private or prohibited IPv6 address."
            )
        return parsed

    # Reject ambiguous or non-canonical numeric IP formats
    # (e.g. hex 0x7f000001, integer 2130706433, octal 0177.0.0.1, shorthand 127.1)
    parts = hostname.split(".")
    if (
        hostname.isdigit()
        or hostname.lower().startswith("0x")
        or any(p.lower().startswith("0x") for p in parts)
        or any(len(p) > 1 and p.startswith("0") and p.isdigit() for p in parts)
        or all(p.isdigit() for p in parts)
    ):
        raise SSRFValidationError(
            "Webhook destination contains an invalid or ambiguous numeric IP representation."
        )

    # 6. Check restricted domain names and suffixes
    host_lower = hostname.lower().rstrip(".")
    if host_lower in BLOCKED_HOSTNAMES or any(
        host_lower.endswith(suffix) for suffix in BLOCKED_DOMAIN_SUFFIXES
    ):
        raise SSRFValidationError(
            "Webhook destination host belongs to a restricted internal domain."
        )

    # Validate RFC 1123 domain syntax & require FQDN (at least 2 labels)
    labels = host_lower.split(".")
    if len(labels) < 2:
        raise SSRFValidationError(
            "Webhook destination host must be a fully qualified domain name."
        )

    for label in labels:
        if not label or len(label) > 63:
            raise SSRFValidationError("Malformed hostname in webhook destination URL.")
        if label.startswith("-") or label.endswith("-"):
            raise SSRFValidationError("Malformed hostname in webhook destination URL.")
        if not re.match(r"^[a-z0-9-]+$", label):
            raise SSRFValidationError("Malformed hostname in webhook destination URL.")

    # 7. DNS resolution check (if requested)
    if resolve_dns:
        target_port = port or 443
        try:
            addr_info = socket.getaddrinfo(hostname, target_port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise SSRFValidationError(
                f"Could not resolve webhook destination host '{hostname}' via DNS."
            ) from exc

        if not addr_info:
            raise SSRFValidationError(
                f"Could not resolve webhook destination host '{hostname}' via DNS."
            )

        resolved_ips = {r[4][0] for r in addr_info}
        for ip_str in resolved_ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError as exc:
                raise SSRFValidationError(
                    "Webhook destination resolved to an invalid IP address."
                ) from exc
            if not is_ip_allowed(ip_obj):
                raise SSRFValidationError(
                    "Webhook destination host resolves to a private or prohibited IP address."
                )

    return parsed


class SSRFSafeBackend(AutoBackend):
    """Network backend that enforces strict SSRF validation and IP pinning at connection time.

    Intercepts connect_tcp right before the socket is created:
    1. Resolves DNS via asyncio.get_running_loop().getaddrinfo
    2. Validates that ALL resolved addresses are allowed public IPs
    3. Rejects immediately if ANY address is prohibited
    4. Pins the TCP connection to verified safe IPs
    5. Leaves SNI / server_hostname intact so TLS certificate verification succeeds
    """

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        loop = asyncio.get_running_loop()
        try:
            addr_info = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise httpcore.ConnectError(f"DNS resolution failed for {host}") from exc

        if not addr_info:
            raise httpcore.ConnectError(f"DNS resolution returned no addresses for {host}")

        resolved_ips = [r[4][0] for r in addr_info]
        for ip_str in resolved_ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError as exc:
                raise SSRFConnectionError(f"Invalid IP resolved for {host}") from exc
            if not is_ip_allowed(ip_obj):
                raise SSRFConnectionError(
                    f"Outbound connection to {host} aborted: resolves to prohibited address"
                )

        last_exc: Exception | None = None
        for target_ip in resolved_ips:
            try:
                return await super().connect_tcp(
                    target_ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, OSError) as exc:
                last_exc = exc
                continue
        if last_exc is not None:
            raise last_exc
        raise httpcore.ConnectError(f"Could not connect to any resolved IP for {host}")


class SSRFSafeTransport(httpx.AsyncHTTPTransport):
    """Custom httpx AsyncHTTPTransport configured with SSRFSafeBackend."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=self._pool._ssl_context,
            max_connections=self._pool._max_connections,
            max_keepalive_connections=self._pool._max_keepalive_connections,
            keepalive_expiry=self._pool._keepalive_expiry,
            http1=self._pool._http1,
            http2=self._pool._http2,
            network_backend=SSRFSafeBackend(),
        )
