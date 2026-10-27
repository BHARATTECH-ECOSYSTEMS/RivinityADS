"""Persistent in-app notification and notification preferences models."""

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from liteads.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from liteads.models.auth import User


class NotificationChannel(str, Enum):
    """Supported delivery channels for notifications."""
    IN_APP = "in_app"
    EMAIL = "email"


class NotificationSeverity(str, Enum):
    """Severity levels for notifications."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


class NotificationCategory(str, Enum):
    """High-level category grouping for notifications and user preferences."""
    BILLING = "billing"
    CAMPAIGN = "campaign"
    CREATIVE = "creative"
    OPTIMIZATION = "optimization"
    DOMAIN = "domain"
    SECURITY = "security"
    PUBLISHER = "publisher"
    SYSTEM = "system"
    WEBHOOK = "webhook"


class NotificationType(str, Enum):
    """Specific event types emitting notifications."""
    PAYMENT_CREATED = "payment_created"
    PAYMENT_CONFIRMED = "payment_confirmed"
    PAYMENT_FAILED = "payment_failed"
    CAMPAIGN_SUBMITTED = "campaign_submitted"
    CAMPAIGN_ACTIVATED = "campaign_activated"
    CAMPAIGN_PAUSED = "campaign_paused"
    CAMPAIGN_LIMITED = "campaign_limited"
    CREATIVE_APPROVED = "creative_approved"
    CREATIVE_REJECTED = "creative_rejected"
    OPTIMIZATION_SUGGESTION_CREATED = "optimization_suggestion_created"
    OPTIMIZATION_SUGGESTION_APPLIED = "optimization_suggestion_applied"
    OPTIMIZATION_SUGGESTION_FAILED = "optimization_suggestion_failed"
    DOMAIN_VERIFICATION_RESULT = "domain_verification_result"
    WEBHOOK_DELIVERY_FAILURE = "webhook_delivery_failure"
    PUBLISHER_WEEKLY_DIGEST = "publisher_weekly_digest"
    SECURITY_ALERT = "security_alert"
    PASSWORD_RESET_CONFIRMATION = "password_reset_confirmation"


class Notification(Base, TimestampMixin):
    """Durable notification entity representing in-app alerts and delivery history.

    Scoped strictly to recipient User to enforce role and tenant isolation.
    Supports read/unread lifecycle, bounded deduplication keys for periodic
    automation (e.g. optimizer loops), and retention expiry timestamps.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created_at", "user_id", "created_at"),
        Index("ix_notifications_user_is_read", "user_id", "is_read"),
        Index("ix_notifications_user_category", "user_id", "category"),
        Index(
            "uq_notifications_user_dedupe",
            "user_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
            sqlite_where=text("dedupe_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NotificationChannel.IN_APP.value,
        server_default="in_app",
        index=True,
    )
    severity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NotificationSeverity.INFO.value,
        server_default="info",
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_read: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False, index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    # Ownership relationship
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    deliveries: Mapped[list["NotificationDelivery"]] = relationship(
        "NotificationDelivery", back_populates="notification", cascade="all, delete-orphan"
    )

    def mark_read(self, at: datetime | None = None) -> None:
        """Mark notification as read with timestamp."""
        self.is_read = True
        self.read_at = at or datetime.now(timezone.utc).replace(tzinfo=None)

    def mark_unread(self) -> None:
        """Mark notification as unread."""
        self.is_read = False
        self.read_at = None


class NotificationPreference(Base, TimestampMixin):
    """Persistent user notification preferences per channel and category.

    Enforces unique constraint on (user_id, channel, category) so a user cannot
    accumulate duplicate preference rows for the same preference scope.
    """

    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "channel", "category", name="uq_notification_preference_user_channel_category"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NotificationChannel.IN_APP.value,
        server_default="in_app",
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )

    # Ownership relationship
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])


class NotificationDelivery(Base, TimestampMixin):
    """Durable delivery job/outbox record for outbound notification channels (e.g. email).

    Survives worker crashes and application restarts. Uses atomic lease tokens,
    next-attempt backoff scheduling, and max-attempts thresholds to guarantee
    resilient delivery without duplicate sends.
    """

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "notification_id", "channel", name="uq_notification_delivery_notif_channel"
        ),
        Index("ix_notification_deliveries_claim", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notification_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NotificationChannel.EMAIL.value,
        server_default="email",
        index=True,
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Delivery lifecycle status: "pending", "processing", "delivered", "failed"
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=5, server_default="5", nullable=False
    )

    # Worker concurrency lease & crash recovery
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    # Scheduling timestamps
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), index=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Diagnostics (sanitized, no secrets)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Relationships
    notification: Mapped["Notification"] = relationship(
        "Notification", back_populates="deliveries", foreign_keys=[notification_id]
    )
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])

