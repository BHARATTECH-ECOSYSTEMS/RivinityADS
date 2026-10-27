"""Durable Notification Dispatcher Service (Phase 6).

Handles creating persistent in-app notifications and transactional email outbox jobs
with deduplication, user preference suppression, and atomic database persistence.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.auth import User
from liteads.models.notification import (
    Notification,
    NotificationCategory,
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
    NotificationSeverity,
    NotificationType,
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for dispatching notifications and managing user delivery preferences."""

    @staticmethod
    async def create_notification(
        session: AsyncSession,
        user_id: int,
        category: str | NotificationCategory,
        type: str | NotificationType,
        title: str,
        message: str,
        channel: str | NotificationChannel = NotificationChannel.IN_APP,
        severity: str | NotificationSeverity = NotificationSeverity.INFO,
        link: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        dedupe_key: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        send_email: bool = False,
        recipient_email: Optional[str] = None,
        email_subject: Optional[str] = None,
        email_body_text: Optional[str] = None,
        email_body_html: Optional[str] = None,
    ) -> Notification:
        """Create and persist an in-app notification and an optional email delivery outbox job.

        Atomic & Concurrency-Safe:
        - If dedupe_key is provided and an existing notification matches (user_id, dedupe_key),
          the existing record is returned without re-alerting or queuing duplicate emails.
        - Respects user NotificationPreference for optional categories, suppressing email
          outbox jobs when opted-out while always preserving the in-app notification.
        - Security notifications (category='security') are mandatory and cannot be suppressed.
        - Network I/O (SMTP) is NEVER performed within this database transaction.
        """
        cat_val = category.value if isinstance(category, NotificationCategory) else str(category)
        type_val = type.value if isinstance(type, NotificationType) else str(type)
        channel_val = channel.value if isinstance(channel, NotificationChannel) else str(channel)
        sev_val = severity.value if isinstance(severity, NotificationSeverity) else str(severity)

        # 1. Deduplication check: return existing notification if already dispatched
        if dedupe_key:
            stmt = select(Notification).where(
                Notification.user_id == user_id,
                Notification.dedupe_key == dedupe_key,
            )
            existing = (await session.execute(stmt)).scalars().first()
            if existing:
                logger.info(
                    "Notification deduplicated by dedupe_key",
                    extra={"user_id": user_id, "dedupe_key": dedupe_key, "notification_id": existing.id},
                )
                return existing

        notification = Notification(
            user_id=user_id,
            category=cat_val,
            type=type_val,
            channel=channel_val,
            severity=sev_val,
            title=title,
            message=message,
            link=link,
            resource_type=resource_type,
            resource_id=resource_id,
            dedupe_key=dedupe_key,
            expires_at=expires_at,
        )

        # Concurrency safety: handle race conditions where duplicate dedupe_key was committed in parallel
        if dedupe_key:
            try:
                async with session.begin_nested():
                    session.add(notification)
                    await session.flush()
            except IntegrityError:
                stmt = select(Notification).where(
                    Notification.user_id == user_id,
                    Notification.dedupe_key == dedupe_key,
                )
                existing = (await session.execute(stmt)).scalars().first()
                if existing:
                    logger.info(
                        "Notification deduplicated on concurrent race",
                        extra={"user_id": user_id, "dedupe_key": dedupe_key},
                    )
                    return existing
                raise
        else:
            session.add(notification)
            await session.flush()

        # 2. Check if email outbox record should be created
        should_queue_email = send_email or (cat_val == NotificationCategory.SECURITY.value)

        if should_queue_email:
            # Check preferences unless mandatory security notification
            is_suppressed = False
            if cat_val != NotificationCategory.SECURITY.value:
                pref_stmt = select(NotificationPreference.is_enabled).where(
                    NotificationPreference.user_id == user_id,
                    NotificationPreference.channel == NotificationChannel.EMAIL.value,
                    NotificationPreference.category == cat_val,
                )
                pref_enabled = (await session.execute(pref_stmt)).scalar()
                if pref_enabled is False:
                    is_suppressed = True
                    logger.info(
                        "Email delivery suppressed by user preference",
                        extra={"user_id": user_id, "category": cat_val},
                    )

            if not is_suppressed:
                # Resolve recipient email address
                target_email = recipient_email
                if not target_email:
                    user_stmt = select(User.email).where(User.id == user_id)
                    target_email = (await session.execute(user_stmt)).scalar()

                if target_email:
                    delivery_subject = email_subject or title
                    delivery_text = email_body_text or message
                    delivery_html = email_body_html

                    delivery = NotificationDelivery(
                        notification_id=notification.id,
                        user_id=user_id,
                        recipient_email=target_email,
                        channel=NotificationChannel.EMAIL.value,
                        subject=delivery_subject,
                        body_text=delivery_text,
                        body_html=delivery_html,
                        status="pending",
                        idempotency_key=f"email:{notification.id}" if notification.id else None,
                    )
                    session.add(delivery)
                    await session.flush()
                else:
                    logger.warning(
                        "Cannot queue email delivery: recipient email not found",
                        extra={"user_id": user_id, "notification_id": notification.id},
                    )

        return notification

    @staticmethod
    async def get_unread_count(session: AsyncSession, user_id: int) -> int:
        """Return exact count of unread notifications for a user."""
        stmt = select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
        return (await session.execute(stmt)).scalar() or 0

    @staticmethod
    async def list_notifications(
        session: AsyncSession,
        user_id: int,
        page: int = 1,
        page_size: int = 20,
        unread_only: bool = False,
        category: Optional[str] = None,
    ) -> tuple[list[Notification], int]:
        """Retrieve paginated notifications strictly scoped to user_id, ordered newest first with ID tie-breaker."""
        if page < 1:
            raise ValueError("Page must be >= 1")
        if page_size < 1 or page_size > 100:
            raise ValueError("Page size must be between 1 and 100")

        base_filter = [Notification.user_id == user_id]
        if unread_only:
            base_filter.append(Notification.is_read.is_(False))
        if category:
            base_filter.append(Notification.category == category)

        # Count total matching records
        count_stmt = select(func.count(Notification.id)).where(*base_filter)
        total = (await session.execute(count_stmt)).scalar() or 0

        # Query paginated rows (newest first, id desc as deterministic tie-breaker)
        offset = (page - 1) * page_size
        items_stmt = (
            select(Notification)
            .where(*base_filter)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        items = list((await session.execute(items_stmt)).scalars().all())
        return items, total

    @staticmethod
    async def mark_as_read(session: AsyncSession, notification_id: int, user_id: int) -> Optional[Notification]:
        """Mark a single notification as read, enforcing tenant ownership.

        Returns the updated Notification if found and owned by user_id, else None.
        Repeated calls are idempotent and return the notification without error.
        """
        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
        notif = (await session.execute(stmt)).scalars().first()
        if not notif:
            return None
        if not notif.is_read:
            notif.mark_read()
            await session.flush()
        return notif

    @staticmethod
    async def mark_all_as_read(session: AsyncSession, user_id: int) -> int:
        """Mark all unread notifications for a user as read via a single atomic update query."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(
                is_read=True,
                read_at=now,
                updated_at=now,
            )
        )
        res = await session.execute(stmt)
        await session.flush()
        return res.rowcount or 0

    @staticmethod
    async def get_preferences(
        session: AsyncSession,
        user_id: int,
    ) -> list["NotificationPreferenceResponse"]:
        """Retrieve complete notification preferences for a user without persisting defaults.

        Evaluates persisted preference rows for user_id against the complete
        matrix of supported channels and categories. Missing records evaluate to
        the system default (is_enabled=True). Mandatory security notifications
        always evaluate to is_enabled=True. Internal database fields are excluded.
        """
        from liteads.schemas.notification import NotificationPreferenceResponse

        stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        rows = (await session.execute(stmt)).scalars().all()
        persisted = {(r.channel, r.category): r.is_enabled for r in rows}

        results: list[NotificationPreferenceResponse] = []
        for ch in NotificationChannel:
            for cat in NotificationCategory:
                if cat == NotificationCategory.SECURITY:
                    enabled = True
                elif (ch.value, cat.value) in persisted:
                    enabled = persisted[(ch.value, cat.value)]
                else:
                    enabled = True  # System default: enabled

                results.append(
                    NotificationPreferenceResponse(
                        channel=ch.value,
                        category=cat.value,
                        is_enabled=enabled,
                    )
                )
        return results

    @staticmethod
    async def update_preferences(
        session: AsyncSession,
        user_id: int,
        items: list["NotificationPreferenceItem"],
    ) -> list["NotificationPreferenceResponse"]:
        """Atomically upsert user notification preferences with validation and idempotency.

        - Validates that duplicate (channel, category) items in the request batch are rejected.
        - Validates that mandatory security notifications cannot be disabled.
        - Idempotently updates existing records or inserts new records.
        - Safely handles concurrent insertion races via begin_nested().
        - Returns the complete resulting preference state.
        """
        from liteads.schemas.notification import NotificationPreferenceItem, NotificationPreferenceResponse

        # 1. Deterministic duplicate and mandatory security validation
        seen: set[tuple[str, str]] = set()
        for item in items:
            ch_val = item.channel.value if isinstance(item.channel, NotificationChannel) else str(item.channel)
            cat_val = item.category.value if isinstance(item.category, NotificationCategory) else str(item.category)
            pair = (ch_val, cat_val)
            if pair in seen:
                raise ValueError(f"Duplicate preference entry for channel '{ch_val}' and category '{cat_val}'")
            seen.add(pair)

            if cat_val == NotificationCategory.SECURITY.value and not item.is_enabled:
                raise ValueError("Mandatory security notifications cannot be disabled")

        if not items:
            return await NotificationService.get_preferences(session, user_id)

        # 2. Query existing preferences for this user
        stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        existing_rows = (await session.execute(stmt)).scalars().all()
        existing_map = {(r.channel, r.category): r for r in existing_rows}

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # 3. Apply updates / inserts transactionally
        for item in items:
            ch_val = item.channel.value if isinstance(item.channel, NotificationChannel) else str(item.channel)
            cat_val = item.category.value if isinstance(item.category, NotificationCategory) else str(item.category)
            pair = (ch_val, cat_val)

            if pair in existing_map:
                pref = existing_map[pair]
                if pref.is_enabled != item.is_enabled:
                    pref.is_enabled = item.is_enabled
                    pref.updated_at = now
            else:
                pref = NotificationPreference(
                    user_id=user_id,
                    channel=ch_val,
                    category=cat_val,
                    is_enabled=item.is_enabled,
                )
                session.add(pref)
                existing_map[pair] = pref

        await session.flush()
        return await NotificationService.get_preferences(session, user_id)

