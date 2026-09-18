"""Password hashing, JWT handling, and authenticated-user dependencies."""

import base64
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.cache import redis_client
from liteads.models import User

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, n, r, p, salt_text, digest_text = encoded.split("$")
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
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


async def check_login_rate_limit(request: Request, email: str) -> None:
    key = f"rate_limit:login:{request.client.host if request.client else 'unknown'}:{email.lower()}"
    window = get_settings().auth.login_window_seconds
    attempts = await redis_client.incr(key)
    if attempts == 1:
        await redis_client.expire(key, window)
    if attempts > get_settings().auth.max_login_attempts:
        raise HTTPException(status_code=429, detail="Too many login attempts")


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


def require_roles(*roles: str):
    async def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return dependency


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

from liteads.models.publisher import Publisher
from liteads.management.api_key_service import ApiKeyService

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