"""Authentication, session, and password reset endpoints."""

from datetime import datetime, timedelta, timezone
import secrets

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.email import EmailDeliveryError, send_email
from liteads.common.email_templates import (
    build_password_changed_email,
    build_password_reset_email,
    build_verification_email,
)
from liteads.common.logger import get_logger
from liteads.ad_server.services.notification_service import NotificationService
from liteads.models.notification import (
    NotificationCategory,
    NotificationSeverity,
    NotificationType,
)
from liteads.common.security import (
    DUMMY_PASSWORD_HASH,
    check_login_rate_limit,
    check_password_reset_completion_rate_limit,
    check_password_reset_request_rate_limit,
    check_resend_verification_rate_limit,
    create_access_token,
    create_refresh_token,
    current_user,
    decode_token,
    hash_password,
    resolve_client_ip,
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
    RequestPasswordResetRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    UserResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
)

logger = get_logger(__name__)
router = APIRouter()


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        advertiser_id=user.advertiser_id,
        publisher_id=user.publisher_id,
        email_verified=user.email_verified,
    )


async def _tokens(user: User, session: AsyncSession, response: Response) -> AuthResponse:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    refresh = create_refresh_token(user)
    session.add(RefreshToken(
        user_id=user.id,
        token_hash=token_digest(refresh),
        expires_at=now + timedelta(days=get_settings().auth.refresh_token_days),
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
        user = User(name=payload.name, email=email, password_hash=hash_password(payload.password), role=payload.role, advertiser_id=account.id, email_verified=False)
    else:
        account = Publisher(name=payload.name, email=email)
        session.add(account)
        await session.flush()
        user = User(name=payload.name, email=email, password_hash=hash_password(payload.password), role=payload.role, publisher_id=account.id, email_verified=False)
    session.add(user)
    await session.flush()
    account.owner_user_id = user.id
    session.add(AuditLog(user_id=user.id, action="register"))

    # Generate verification token and commit user + token atomically
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(
        OneTimeToken(
            user_id=user.id,
            token_hash=token_digest(token),
            purpose="email_verification",
            expires_at=now + timedelta(hours=get_settings().auth.verification_token_hours),
        )
    )
    await session.commit()
    await session.refresh(user)

    # Deliver verification email asynchronously
    email_content = build_verification_email(recipient_email=user.email, token=token)
    try:
        await send_email(
            to_address=user.email,
            subject=email_content.subject,
            text_content=email_content.text_content,
            html_content=email_content.html_content,
            template_purpose="verification",
            template_data={"token": token, "email": user.email, "name": user.name},
        )
    except EmailDeliveryError as exc:
        logger.error("Failed to send verification email on registration", user_id=user.id, error=str(exc))
    except Exception as exc:
        logger.error("Unexpected error sending verification email on registration", user_id=user.id, error=str(exc))

    return await _tokens(user, session, response)


@router.post("/auth/verify-email", response_model=VerifyEmailResponse)
async def verify_email(
    payload: VerifyEmailRequest | None = None,
    token: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> VerifyEmailResponse:
    raw_token = payload.token if (payload and payload.token) else token
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token is invalid")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hashed_token = token_digest(raw_token)

    # Atomically consume the token: only an unused, unexpired token of purpose "email_verification" will match
    result = await session.execute(
        update(OneTimeToken)
        .where(
            OneTimeToken.token_hash == hashed_token,
            OneTimeToken.purpose == "email_verification",
            OneTimeToken.used_at.is_(None),
            OneTimeToken.expires_at > now,
        )
        .values(used_at=now)
    )

    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token is invalid")

    record = await session.scalar(select(OneTimeToken).where(OneTimeToken.token_hash == hashed_token))
    if record is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token is invalid")

    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token is invalid")

    user.email_verified = True

    # Invalidate any other outstanding email_verification tokens for this user
    await session.execute(
        update(OneTimeToken)
        .where(
            OneTimeToken.user_id == user.id,
            OneTimeToken.purpose == "email_verification",
            OneTimeToken.used_at.is_(None),
        )
        .values(used_at=now)
    )

    session.add(
        AuditLog(
            user_id=user.id,
            action="verify_email",
        )
    )

    await session.commit()
    return VerifyEmailResponse(message="Email verified", email=user.email)


@router.post("/auth/resend-verification", response_model=MessageResponse)
async def resend_verification(
    request: Request,
    payload: ResendVerificationRequest,
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    email = str(payload.email).lower()
    await check_resend_verification_rate_limit(request, email)

    user = await session.scalar(select(User).where(User.email == email))
    if user is not None and user.is_active and not user.email_verified:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Invalidate earlier unused verification tokens for the user
        await session.execute(
            update(OneTimeToken)
            .where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == "email_verification",
                OneTimeToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
        token = secrets.token_urlsafe(32)
        session.add(
            OneTimeToken(
                user_id=user.id,
                token_hash=token_digest(token),
                purpose="email_verification",
                expires_at=now + timedelta(hours=get_settings().auth.verification_token_hours),
            )
        )
        await session.commit()

        email_content = build_verification_email(recipient_email=user.email, token=token)
        try:
            await send_email(
                to_address=user.email,
                subject=email_content.subject,
                text_content=email_content.text_content,
                html_content=email_content.html_content,
                template_purpose="verification",
                template_data={"token": token, "email": user.email, "name": user.name},
            )
        except EmailDeliveryError as exc:
            logger.error("Failed to send verification email on resend", user_id=user.id, error=str(exc))
        except Exception as exc:
            logger.error("Unexpected error sending verification email on resend", user_id=user.id, error=str(exc))

    return MessageResponse(message="If an unverified account exists with this email, a verification link has been sent")



@router.post("/auth/login", response_model=AuthResponse)
async def login(request: Request, payload: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)) -> AuthResponse:
    email = str(payload.email).lower()
    await check_login_rate_limit(request, email)
    user = await session.scalar(select(User).where(User.email == email))

    # F-03: Mitigate login timing-based account enumeration.
    # Always perform password verification using the user's stored hash if present,
    # or the precomputed dummy hash if the account does not exist or has no stored hash.
    target_hash = user.password_hash if (user and user.password_hash) else DUMMY_PASSWORD_HASH
    valid_password = verify_password(payload.password, target_hash)

    if user is None or not valid_password or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    client_ip = resolve_client_ip(request)
    session.add(AuditLog(user_id=user.id, action="login", ip_address=client_ip if client_ip != "unknown" else None))
    return await _tokens(user, session, response)


@router.post("/auth/refresh", response_model=AuthResponse)
async def refresh(response: Response, refresh_token: str | None = Cookie(None), session: AsyncSession = Depends(get_session)) -> AuthResponse:
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")
    claims = decode_token(refresh_token, "refresh")
    stored = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_digest(refresh_token)))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires = stored.expires_at.replace(tzinfo=None) if (stored and stored.expires_at and stored.expires_at.tzinfo is not None) else (stored.expires_at if stored else None)
    if stored is None or stored.revoked_at is not None or (expires is not None and expires <= now):
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
            stored.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
            
    response.delete_cookie("refresh_token", path="/api/v1/auth", httponly=True, secure=True, samesite="lax")
    return MessageResponse(message="Logged out")


@router.post("/auth/request-password-reset", response_model=MessageResponse)
async def request_password_reset(
    request: Request,
    payload: RequestPasswordResetRequest,
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    email = str(payload.email).lower()
    await check_password_reset_request_rate_limit(request, email)

    user = await session.scalar(select(User).where(User.email == email))
    if user is not None and user.is_active:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Invalidate earlier unused password-reset tokens for the same user
        await session.execute(
            update(OneTimeToken)
            .where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == "password_reset",
                OneTimeToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
        token = secrets.token_urlsafe(32)
        session.add(
            OneTimeToken(
                user_id=user.id,
                token_hash=token_digest(token),
                purpose="password_reset",
                expires_at=now + timedelta(hours=get_settings().auth.password_reset_token_hours),
            )
        )
        await session.commit()

        email_content = build_password_reset_email(recipient_email=user.email, token=token)
        try:
            await send_email(
                to_address=user.email,
                subject=email_content.subject,
                text_content=email_content.text_content,
                html_content=email_content.html_content,
                template_purpose="password_reset",
                template_data={"token": token, "email": user.email, "name": user.name},
            )
        except EmailDeliveryError as exc:
            logger.error("Failed to send password reset email", user_id=user.id, error=str(exc))
        except Exception as exc:
            logger.error("Unexpected error sending password reset email", user_id=user.id, error=str(exc))

    return MessageResponse(message="If the account exists, password reset instructions have been sent")


@router.post("/auth/reset-password", response_model=MessageResponse)
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    await check_password_reset_completion_rate_limit(request)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hashed_token = token_digest(payload.token)

    # Atomically consume the token: only an unused, unexpired token of purpose "password_reset" will match
    result = await session.execute(
        update(OneTimeToken)
        .where(
            OneTimeToken.token_hash == hashed_token,
            OneTimeToken.purpose == "password_reset",
            OneTimeToken.used_at.is_(None),
            OneTimeToken.expires_at > now,
        )
        .values(used_at=now)
    )

    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid")

    record = await session.scalar(select(OneTimeToken).where(OneTimeToken.token_hash == hashed_token))
    if record is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid")

    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid")

    user.password_hash = hash_password(payload.password)
    user.password_changed_at = now

    # Revoke all active refresh tokens for the account
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )

    client_ip = resolve_client_ip(request)
    session.add(
        AuditLog(
            user_id=user.id,
            action="password_reset",
            ip_address=client_ip if client_ip != "unknown" else None,
        )
    )

    # Queue durable in-app security notification and transactional email outbox job
    user_id = user.id
    user_email = user.email
    token_record_id = record.id
    email_content = build_password_changed_email(recipient_email=user_email)
    try:
        await NotificationService.create_notification(
            session=session,
            user_id=user_id,
            category=NotificationCategory.SECURITY,
            type=NotificationType.PASSWORD_RESET_CONFIRMATION,
            severity=NotificationSeverity.WARNING,
            title="Password Changed",
            message="Your password was successfully changed. If you did not make this change, please sign in and secure your account immediately.",
            link="/login",
            resource_type="user",
            resource_id=str(user_id),
            dedupe_key=f"pwd_reset:{user_id}:{token_record_id}",
            send_email=True,
            recipient_email=user_email,
            email_subject=email_content.subject,
            email_body_text=email_content.text_content,
            email_body_html=email_content.html_content,
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.error(
            "Failed to finalize password reset notification; transaction rolled back",
            user_id=user_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while finalizing password reset. Please try again.",
        )

    return MessageResponse(message="Password reset")


@router.post("/auth/me", response_model=UserResponse)
async def me(user: User = Depends(current_user)) -> UserResponse:
    return _user_response(user)
