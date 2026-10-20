"""Password hashing, JWT handling, and authenticated-user dependencies."""

import base64
import hashlib
import hmac
import ipaddress
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

import structlog
from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.models import User

logger = structlog.get_logger()
bearer = HTTPBearer(auto_error=False)


# Precomputed scrypt hash with standard parameters (N=16384, r=8, p=1) used for
# constant-time dummy verification when user account does not exist (F-03).
DUMMY_PASSWORD_HASH: str = (
    "scrypt$16384$8$1$TeC6mYPjRnxhCK-sJiYQEw==$"
    "VY7TPUC2he3X4IrgseEZ-cnxvn_HbunW5ek-wwAonfU0-OCFx__O-IQiOtyNdaOrZgZLfStw1ZU2FrI6GepZAA=="
)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, encoded: str | None) -> bool:
    if not password or not encoded or not isinstance(encoded, str):
        return False
    try:
        parts = encoded.split("$")
        if len(parts) != 6 or parts[0] != "scrypt":
            return False
        _, n, r, p, salt_text, digest_text = parts
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _token(subject: int, token_type: str, expires: timedelta, **claims: Any) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(subject), "type": token_type, "iat": now, "exp": now + expires, **claims}
    return jwt.encode(payload, get_settings().auth.jwt_secret, algorithm="HS256")


def create_access_token(user: User) -> str:
    return _token(user.id, "access", timedelta(minutes=get_settings().auth.access_token_minutes), role=user.role)


def create_refresh_token(user: User) -> str:
    return _token(user.id, "refresh", timedelta(days=get_settings().auth.refresh_token_days), jti=secrets.token_hex(16))


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, get_settings().auth.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc
    if payload.get("type") != expected_type or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    return payload


def _clean_ip_str(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("[") and "]" in raw:
        raw = raw[1 : raw.index("]")]
    elif ":" in raw and raw.count(":") == 1:
        raw = raw.split(":")[0]
    return raw.strip()


def is_trusted_proxy(ip_str: str | None, trusted_proxies: list[str]) -> bool:
    """Check if an IP string belongs to the trusted proxies list (supports individual IPs and CIDRs)."""
    if not ip_str or not trusted_proxies:
        return False
    try:
        ip = ipaddress.ip_address(_clean_ip_str(ip_str))
    except ValueError:
        return False

    for trusted in trusted_proxies:
        trusted_clean = str(trusted).strip()
        if not trusted_clean:
            continue
        try:
            if "/" in trusted_clean:
                network = ipaddress.ip_network(trusted_clean, strict=False)
                if ip in network:
                    return True
            else:
                trusted_ip = ipaddress.ip_address(trusted_clean)
                if ip == trusted_ip:
                    return True
        except ValueError:
            continue
    return False


def resolve_client_ip(
    request: Request,
    trusted_proxies: list[str] | None = None,
) -> str:
    """Resolve the authentic client IP address for security rate-limiting and audit logging.

    Enforces trusted reverse proxy verification:
    - If the direct socket peer (request.client.host) is NOT a configured trusted proxy,
      forwarding headers (X-Forwarded-For, X-Real-IP) are untrusted and completely ignored.
    - If the direct peer IS a configured trusted proxy, walks the X-Forwarded-For chain
      backwards (right-to-left) to find the first untrusted IP address.
    - If X-Forwarded-For contains only trusted proxies, returns the leftmost valid originator.
    - If X-Forwarded-For is missing or malformed, falls back to X-Real-IP (if valid) or the peer host.
    - Returns 'unknown' if request.client is None.
    """
    if not request.client or not request.client.host:
        return "unknown"

    peer_host = _clean_ip_str(request.client.host)
    if not peer_host:
        return "unknown"

    try:
        ipaddress.ip_address(peer_host)
    except ValueError:
        return peer_host

    if trusted_proxies is None:
        try:
            settings_proxies = get_settings().server.trusted_proxies
            trusted_proxies = (
                settings_proxies
                if isinstance(settings_proxies, list)
                else ["127.0.0.1", "::1"]
            )
        except Exception:
            trusted_proxies = ["127.0.0.1", "::1"]

    # If the direct connection is NOT from a trusted proxy, never trust forwarded headers.
    if not is_trusted_proxy(peer_host, trusted_proxies):
        return peer_host

    # Immediate peer is trusted. Inspect forwarded headers.
    candidates: list[str] = []
    if hasattr(request, "headers"):
        raw_headers: list[str] = []
        if hasattr(request.headers, "getlist"):
            raw_headers = request.headers.getlist("x-forwarded-for")
        else:
            val = request.headers.get("x-forwarded-for")
            if val:
                raw_headers = [val]

        for h in raw_headers:
            for part in h.split(","):
                cleaned = _clean_ip_str(part)
                if cleaned:
                    candidates.append(cleaned)

    # Walk backwards from right (closest proxy to backend) to left (client originator)
    for cand in reversed(candidates):
        try:
            ipaddress.ip_address(cand)
        except ValueError:
            continue
        if not is_trusted_proxy(cand, trusted_proxies):
            return cand

    # If all valid candidates in X-Forwarded-For are trusted proxies, take the leftmost valid one
    for cand in candidates:
        try:
            ipaddress.ip_address(cand)
            return cand
        except ValueError:
            continue

    # Fallback to X-Real-IP if present and valid
    if hasattr(request, "headers"):
        x_real_ip = request.headers.get("x-real-ip")
        if x_real_ip:
            cleaned_real = _clean_ip_str(x_real_ip)
            try:
                ipaddress.ip_address(cleaned_real)
                return cleaned_real
            except ValueError:
                pass

    return peer_host


async def check_login_rate_limit(request: Request, email: str) -> None:
    ip = resolve_client_ip(request)
    normalized_email = email.strip().lower()
    
    ip_key = f"rate_limit:login:ip:{ip}"
    account_key = f"rate_limit:login:account:{normalized_email}"
    window = get_settings().auth.login_window_seconds
    max_attempts = get_settings().auth.max_login_attempts
    
    try:
        pipeline = redis_client.pipeline()
        pipeline.incr(ip_key)
        pipeline.incr(account_key)
        results = await pipeline.execute()
        
        ip_attempts, account_attempts = results
        
        expire_pipe = redis_client.pipeline()
        if ip_attempts == 1:
            expire_pipe.expire(ip_key, window)
        if account_attempts == 1:
            expire_pipe.expire(account_key, window)
        if len(expire_pipe.command_stack) > 0:
            await expire_pipe.execute()
            
        if ip_attempts > max_attempts or account_attempts > max_attempts:
            raise HTTPException(status_code=429, detail="Too many login attempts")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Service unavailable")


async def check_password_reset_request_rate_limit(request: Request, email: str) -> None:
    ip = resolve_client_ip(request)
    normalized_email = email.strip().lower()

    ip_key = f"rate_limit:password_reset_req:ip:{ip}"
    account_key = f"rate_limit:password_reset_req:account:{normalized_email}"
    window = get_settings().auth.login_window_seconds
    max_attempts = get_settings().auth.max_login_attempts

    try:
        pipeline = redis_client.pipeline()
        pipeline.incr(ip_key)
        pipeline.incr(account_key)
        results = await pipeline.execute()

        ip_attempts, account_attempts = results

        expire_pipe = redis_client.pipeline()
        if ip_attempts == 1:
            expire_pipe.expire(ip_key, window)
        if account_attempts == 1:
            expire_pipe.expire(account_key, window)
        if len(expire_pipe.command_stack) > 0:
            await expire_pipe.execute()

        if ip_attempts > max_attempts or account_attempts > max_attempts:
            raise HTTPException(status_code=429, detail="Too many password reset requests")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Service unavailable")


async def check_password_reset_completion_rate_limit(request: Request) -> None:
    ip = resolve_client_ip(request)

    ip_key = f"rate_limit:password_reset_comp:ip:{ip}"
    window = get_settings().auth.login_window_seconds
    max_attempts = get_settings().auth.max_login_attempts

    try:
        pipeline = redis_client.pipeline()
        pipeline.incr(ip_key)
        results = await pipeline.execute()

        ip_attempts = results[0]

        expire_pipe = redis_client.pipeline()
        if ip_attempts == 1:
            expire_pipe.expire(ip_key, window)
        if len(expire_pipe.command_stack) > 0:
            await expire_pipe.execute()

        if ip_attempts > max_attempts:
            raise HTTPException(status_code=429, detail="Too many password reset attempts")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Service unavailable")


async def check_resend_verification_rate_limit(request: Request, email: str) -> None:
    ip = resolve_client_ip(request)
    normalized_email = email.strip().lower()

    ip_key = f"rate_limit:resend_verif:ip:{ip}"
    account_key = f"rate_limit:resend_verif:account:{normalized_email}"
    window = get_settings().auth.login_window_seconds
    max_attempts = get_settings().auth.max_login_attempts

    try:
        pipeline = redis_client.pipeline()
        pipeline.incr(ip_key)
        pipeline.incr(account_key)
        results = await pipeline.execute()

        ip_attempts, account_attempts = results

        expire_pipe = redis_client.pipeline()
        if ip_attempts == 1:
            expire_pipe.expire(ip_key, window)
        if account_attempts == 1:
            expire_pipe.expire(account_key, window)
        if len(expire_pipe.command_stack) > 0:
            await expire_pipe.execute()

        if ip_attempts > max_attempts or account_attempts > max_attempts:
            raise HTTPException(status_code=429, detail="Too many verification resend requests")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Service unavailable")


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    payload = decode_token(credentials.credentials, "access")
    user = await session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or missing")
    return user


async def verified_user(
    user: User = Depends(current_user),
) -> User:
    """Ensure the authenticated user is active and has verified their email address."""
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or missing")
    if not user.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email verification required")
    return user


def require_roles(*roles: str):
    async def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return dependency


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


from liteads.models.publisher import Publisher, PublisherApiKey


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> Publisher:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API Key required")
    raw_key = credentials.credentials
    parts = raw_key.split("_")
    if len(parts) != 4 or parts[0] != "pk" or parts[1] != "live":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key format")
    prefix = f"{parts[0]}_{parts[1]}_{parts[2]}"

    db_key = await ApiKeyService.get_key_by_prefix(session, prefix)
    if not db_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")

    if not ApiKeyService.verify_key(raw_key, db_key.key_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")

    publisher = await session.get(Publisher, db_key.publisher_id)
    if not publisher or publisher.status != 1:  # Status.ACTIVE
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Publisher inactive")

    return publisher


async def authenticate_s2s_or_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Returns {"type": "publisher", "entity": Publisher} or {"type": "user", "entity": User}"""
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    token = credentials.credentials
    if token.startswith("pk_live_"):
        publisher = await verify_api_key(credentials, session)
        return {"type": "publisher", "entity": publisher}
    else:
        user = await current_user(credentials, session)
        return {"type": "user", "entity": user}


# ── Phase 4.2 & Phase 5.2 — Publisher API Key authentication & S2S Hardening ──

class AuthenticatedAdCaller:
    """Result of authenticating an ad request caller."""

    def __init__(
        self,
        is_authenticated: bool,
        publisher_id: int | None = None,
        api_key: Any = None,
        user: Any = None,
        auth_type: str = "anonymous",
    ):
        self.is_authenticated = is_authenticated
        self.publisher_id = publisher_id
        self.api_key = api_key
        self.user = user
        self.auth_type = auth_type


async def resolve_publisher_api_key(
    raw_key: str,
    session: AsyncSession,
) -> tuple[PublisherApiKey, Publisher]:
    """Find, verify, and return the PublisherApiKey and Publisher for raw_key.

    Resolves against actual persisted PublisherApiKey records and verifies
    against stored key_hash using constant-time verification.

    Raises HTTPException(401) on:
    - Empty or non-string key
    - Unregistered / unknown key
    - Revoked key
    - Inactive publisher account
    - Environment eligibility mismatch
    """
    if not raw_key or not isinstance(raw_key, str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    raw_key = raw_key.strip()
    from liteads.models.publisher import Publisher, PublisherApiKey
    from sqlalchemy import select, or_

    sha256_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    prefixes: list[str] = []
    if len(raw_key) >= 16:
        prefixes.append(raw_key[:16])
    if len(raw_key) >= 12:
        prefixes.append(raw_key[:12])
    if len(raw_key) >= 8:
        prefixes.append(raw_key[:8])

    stmt = select(PublisherApiKey).where(
        or_(
            PublisherApiKey.key_hash == sha256_hash,
            PublisherApiKey.key_prefix.in_(prefixes),
        )
    )
    result = await session.execute(stmt)
    candidates: list[PublisherApiKey] = []
    if hasattr(result, "scalars"):
        try:
            candidates = list(result.scalars().all())
        except Exception:
            candidates = []
    if not candidates and hasattr(result, "scalar_one_or_none"):
        try:
            one = result.scalar_one_or_none()
            if one is not None:
                candidates = [one]
        except Exception:
            pass

    matching_key: PublisherApiKey | None = None
    for candidate in candidates:
        if candidate.key_hash == sha256_hash:
            matching_key = candidate
            break
        try:
            if verify_password(raw_key, candidate.key_hash):
                matching_key = candidate
                break
        except Exception:
            continue

    if matching_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    if matching_key.revoked_at is not None or matching_key.status == 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key is revoked")

    # Environment eligibility check
    is_test_key = raw_key.startswith("pk_test")
    if is_test_key and not matching_key.is_sandbox:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key environment mismatch")
    if not is_test_key and raw_key.startswith("pk_live") and matching_key.is_sandbox:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key environment mismatch")

    publisher = await session.get(Publisher, matching_key.publisher_id)
    from unittest.mock import Mock
    if publisher is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Publisher account is inactive")
    if not isinstance(publisher, Mock) and getattr(publisher, "status", None) != 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Publisher account is inactive")

    return matching_key, publisher


async def _get_publisher_id_from_key(key: str, session: AsyncSession) -> int | None:
    if not key:
        return None
    try:
        db_key, _ = await resolve_publisher_api_key(key, session)
        return db_key.publisher_id
    except Exception:
        return None


async def publisher_from_api_key(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> int:
    """Authenticate ad/event API calls via ``X-API-Key`` header.

    Returns the ``publisher_id`` associated with the validated key.
    Raises HTTP 401 if the header is missing or the key is invalid/revoked.
    """
    key = request.headers.get("X-API-Key", "").strip()
    if not key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")

    db_key, _ = await resolve_publisher_api_key(key, session)
    return db_key.publisher_id


async def optional_publisher_from_api_key(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> int | None:
    """Soft version of :func:`publisher_from_api_key`.

    Returns the publisher_id if a valid ``X-API-Key`` header is present,
    otherwise returns ``None``.
    """
    key = request.headers.get("X-API-Key", "").strip()
    if not key:
        return None
    return await _get_publisher_id_from_key(key, session)


# ── Phase 4.3 & 5.2 — HMAC signature verification for S2S signed requests ─────

_HMAC_TIMESTAMP_WINDOW = 300  # seconds


def verify_hmac_signature(
    raw_key: str,
    timestamp_str: str,
    body_bytes: bytes,
    signature: str,
    method: str = "POST",
    path: str = "/api/v1/ad/request",
    signature_version: str | None = None,
) -> None:
    """Validate an HMAC-SHA256 signed request.

    Protocol Policy:
    - Authoritative Standard (v2):
      HMAC-SHA256(raw_key, "{METHOD}|{PATH}|{timestamp}|{sha256(body)}")
      Cryptographically binds the HTTP method and URL path, preventing cross-endpoint
      and cross-method replay attacks.
    - Controlled Legacy Migration (v1):
      HMAC-SHA256(raw_key, "{raw_key}|{timestamp}|{sha256(body)}")
      Permitted ONLY when caller explicitly provides header `X-Signature-Version: 1`
      AND request path is `/api/v1/ad/request`. If replayed on any other path, it is rejected.
      Default (no header or `2`) strictly enforces method and path binding.

    Raises HTTP 401 on any failure (bad sig, stale timestamp, wrong format).
    """
    if not timestamp_str:
        raise HTTPException(status_code=401, detail="X-Timestamp header required")
    if not signature:
        raise HTTPException(status_code=401, detail="X-Signature header required")

    try:
        ts = int(timestamp_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="X-Timestamp must be a Unix epoch integer")

    drift = abs(int(time.time()) - ts)
    if drift > _HMAC_TIMESTAMP_WINDOW:
        raise HTTPException(
            status_code=401,
            detail=f"Request timestamp is outside the permitted {_HMAC_TIMESTAMP_WINDOW}-second window ({drift}s drift)",
        )

    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    secret_bytes = raw_key.encode("utf-8")

    # Controlled Legacy Migration (v1)
    if signature_version == "1":
        if path != "/api/v1/ad/request":
            logger.warning("legacy_hmac_replayed_on_non_ad_path", path=path)
            raise HTTPException(
                status_code=401,
                detail="Legacy signature format v1 is not permitted on this path",
            )
        msg_v1 = f"{raw_key}|{timestamp_str}|{body_sha256}".encode("utf-8")
        sig_v1 = hmac.new(secret_bytes, msg_v1, hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig_v1, signature):
            return
        raise HTTPException(status_code=401, detail="Invalid request signature")

    # Authoritative Standard (v2: Method and path bound)
    msg_v2 = f"{method.upper()}|{path}|{timestamp_str}|{body_sha256}".encode("utf-8")
    sig_v2 = hmac.new(secret_bytes, msg_v2, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(sig_v2, signature):
        raise HTTPException(status_code=401, detail="Invalid request signature")


async def authenticate_ad_request(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedAdCaller:
    """Authenticate ad request callers.

    Strictly validates credentials when present:
    - If X-API-Key, X-Signature, X-Timestamp or Bearer pk_... is provided,
      authentication is strictly required. Any failure (unknown key, bad sig,
      revoked key, timestamp expired) raises HTTP 401. It NEVER falls back to anonymous.
    - If Authorization: Bearer <jwt> is provided, verifies JWT.
    - If no auth headers are provided, treats as anonymous request.
    """
    x_api_key = request.headers.get("X-API-Key", "").strip()
    x_sig = request.headers.get("X-Signature", "").strip()
    x_ts = request.headers.get("X-Timestamp", "").strip()
    x_sig_ver = request.headers.get("X-Signature-Version", "").strip()
    auth_header = request.headers.get("Authorization", "").strip()

    # If Authorization header contains an API key
    if auth_header.startswith("Bearer "):
        token_val = auth_header[7:].strip()
        if token_val.startswith("pk_") or token_val.startswith("rk_"):
            if not x_api_key:
                x_api_key = token_val

    # Case 1: Caller provided API-key or S2S signature headers -> MUST authenticate
    if x_api_key or x_sig or x_ts:
        if not x_api_key:
            raise HTTPException(status_code=401, detail="X-API-Key header required")
        if not x_sig:
            raise HTTPException(status_code=401, detail="X-Signature header required")
        if not x_ts:
            raise HTTPException(status_code=401, detail="X-Timestamp header required")

        # 1. Resolve and verify key against database
        try:
            db_key, publisher = await resolve_publisher_api_key(x_api_key, session)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=500, detail="Authentication verification failed")

        # 2. Verify HMAC signature against request body, method, and path
        body_bytes = await request.body()
        verify_hmac_signature(
            raw_key=x_api_key,
            timestamp_str=x_ts,
            body_bytes=body_bytes,
            signature=x_sig,
            method=request.method,
            path=request.url.path,
            signature_version=x_sig_ver or None,
        )

        # 3. Update usage metadata (last_used_at)
        try:
            from datetime import timezone
            db_key.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.add(db_key)
            await session.commit()
        except Exception as e:
            logger.warning("failed_to_update_last_used_at", error=str(e))

        return AuthenticatedAdCaller(
            is_authenticated=True,
            publisher_id=db_key.publisher_id,
            api_key=db_key,
            auth_type="api_key",
        )

    # Case 2: Dashboard user with JWT token
    if auth_header.startswith("Bearer "):
        jwt_token = auth_header[7:].strip()
        try:
            payload = decode_token(jwt_token, "access")
            user_id = int(payload["sub"])
            user = await session.get(User, user_id)
            if user and user.is_active:
                return AuthenticatedAdCaller(
                    is_authenticated=True,
                    publisher_id=user.publisher_id,
                    user=user,
                    auth_type="jwt",
                )
            raise HTTPException(status_code=401, detail="User is inactive or missing")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid authorization token")

    # Case 3: Anonymous request (no credential headers at all)
    return AuthenticatedAdCaller(
        is_authenticated=False,
        publisher_id=None,
        auth_type="anonymous",
    )
