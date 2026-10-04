"""Authentication, session, verification, and password reset endpoints."""

from datetime import datetime, timedelta
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Cookie, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.security import (
    check_login_rate_limit,
    create_access_token,
    create_refresh_token,
    current_user,
    decode_token,
    hash_password,
    token_digest,
    verify_password,
)
from liteads.models import Advertiser, AuditLog, OneTimeToken, Publisher, RefreshToken, User
from liteads.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UserResponse,
)

router = APIRouter()


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        advertiser_id=user.advertiser_id,
        publisher_id=user.publisher_id,
    )


async def _tokens(user: User, session: AsyncSession, response: Response) -> AuthResponse:
    refresh = create_refresh_token(user)
    session.add(RefreshToken(
        user_id=user.id,
        token_hash=token_digest(refresh),
        expires_at=datetime.utcnow() + timedelta(days=get_settings().auth.refresh_token_days),
    ))
    response.set_cookie(
        key="refresh_token",
        value=refresh,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/v1/auth"
    )
    return AuthResponse(user=_user_response(user), access_token=create_access_token(user))


@router.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, response: Response, session: AsyncSession = Depends(get_session)) -> AuthResponse:
    email = str(payload.email).lower()
    if await session.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")
    if payload.role == "advertiser":
        account = Advertiser(name=payload.name, contact_email=email)
        session.add(account)
        await session.flush()
        user = User(name=payload.name, email=email, password_hash=hash_password(payload.password), role=payload.role, advertiser_id=account.id)
    else:
        account = Publisher(name=payload.name, email=email)
        session.add(account)
        await session.flush()
        user = User(name=payload.name, email=email, password_hash=hash_password(payload.password), role=payload.role, publisher_id=account.id)
    session.add(user)
    await session.flush()
    session.add(AuditLog(user_id=user.id, action="register"))
    return await _tokens(user, session, response)


@router.post("/auth/login", response_model=AuthResponse)
async def login(request: Request, payload: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)) -> AuthResponse:
    email = str(payload.email).lower()
    await check_login_rate_limit(request, email)
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash) or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    session.add(AuditLog(user_id=user.id, action="login", ip_address=request.client.host if request.client else None))
    return await _tokens(user, session, response)


@router.post("/auth/refresh", response_model=AuthResponse)
async def refresh(response: Response, refresh_token: str | None = Cookie(None), session: AsyncSession = Depends(get_session)) -> AuthResponse:
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")
    claims = decode_token(refresh_token, "refresh")
    stored = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_digest(refresh_token)))
    now = datetime.utcnow()
    if stored is None or stored.revoked_at is not None or stored.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh session is invalid")
    user = await session.get(User, int(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or missing")
    stored.revoked_at = now
    result = await _tokens(user, session, response)
    
    # After _tokens, find the new token hash
    new_token_hash = None
    for obj in session.new:
        if isinstance(obj, RefreshToken):
            new_token_hash = obj.token_hash
            break
            
    if new_token_hash:
        stored.replaced_by = new_token_hash
        
    return result


@router.post("/auth/logout", response_model=MessageResponse)
async def logout(response: Response, refresh_token: str | None = Cookie(None), session: AsyncSession = Depends(get_session)) -> MessageResponse:
    if refresh_token:
        stored = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_digest(refresh_token)))
        if stored is not None:
            stored.revoked_at = datetime.utcnow()
            
    response.delete_cookie("refresh_token", path="/api/v1/auth", httponly=True, secure=True, samesite="lax")
    return MessageResponse(message="Logged out")


@router.post("/auth/verify-email", response_model=MessageResponse)
async def verify_email(token: str, session: AsyncSession = Depends(get_session)) -> MessageResponse:
    record = await session.scalar(select(OneTimeToken).where(OneTimeToken.token_hash == token_digest(token), OneTimeToken.purpose == "email_verification"))
    if record is None or record.used_at is not None or record.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token is invalid")
    user = await session.get(User, record.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token is invalid")
    user.email_verified = True
    record.used_at = datetime.utcnow()
    return MessageResponse(message="Email verified")


@router.post("/auth/request-password-reset", response_model=MessageResponse)
async def request_password_reset(payload: LoginRequest, session: AsyncSession = Depends(get_session)) -> MessageResponse:
    user = await session.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is not None:
        token = secrets.token_urlsafe(32)
        session.add(OneTimeToken(user_id=user.id, token_hash=token_digest(token), purpose="password_reset", expires_at=datetime.utcnow() + timedelta(hours=get_settings().auth.password_reset_token_hours)))
    return MessageResponse(message="If the account exists, password reset instructions have been sent")


@router.post("/auth/reset-password", response_model=MessageResponse)
async def reset_password(payload: ResetPasswordRequest, session: AsyncSession = Depends(get_session)) -> MessageResponse:
    record = await session.scalar(select(OneTimeToken).where(OneTimeToken.token_hash == token_digest(payload.token), OneTimeToken.purpose == "password_reset"))
    if record is None or record.used_at is not None or record.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid")
    user = await session.get(User, record.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid")
    user.password_hash = hash_password(payload.password)
    record.used_at = datetime.utcnow()
    await session.execute(update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked_at=datetime.utcnow()))
    session.add(AuditLog(user_id=user.id, action="password_reset"))
    return MessageResponse(message="Password reset")


@router.post("/auth/me", response_model=UserResponse)
async def me(user: User = Depends(current_user)) -> UserResponse:
    return _user_response(user)
