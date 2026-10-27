"""Webhook and WebhookDelivery models for event delivery to external endpoints."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from liteads.models.base import Base, TimestampMixin


class Webhook(Base, TimestampMixin):
    """Webhook registration for event delivery.

    Owners can register one or more URLs to receive real-time HTTP POST
    notifications when certain ad events occur (impression, click, conversion).

    Each payload is signed with ``HMAC-SHA256(secret, body)`` so the
    receiver can verify authenticity.
    """

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Owner: "advertiser" or "publisher"
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Delivery target
    url: Mapped[str] = mapped_column(Text, nullable=False)

    # Subscribed event types stored as comma-separated string
    # (e.g. "impression,click,conversion")
    events: Mapped[str] = mapped_column(
        String(255), nullable=False, default="impression,click,conversion"
    )

    # HMAC signing secret (generated server-side, returned once on creation)
    secret: Mapped[str] = mapped_column(String(128), nullable=False)

    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Delivery stats
    deliveries_ok: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deliveries_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationship to durable delivery jobs
    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        "WebhookDelivery", back_populates="webhook", cascade="all, delete-orphan"
    )

    @property
    def event_list(self) -> list[str]:
        """Return subscribed events as a list."""
        return [e.strip() for e in self.events.split(",") if e.strip()]


class WebhookDelivery(Base, TimestampMixin):
    """Durable delivery job/outbox entry for an outbound webhook event.

    Survives application and worker restarts. Tracks attempt count,
    exponential backoff schedule, worker lease tokens, and delivery status.
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    webhook_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True
    )

    owner_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Canonical JSON snapshot of payload
    payload: Mapped[str] = mapped_column(Text, nullable=False)

    # Delivery status: "pending", "processing", "delivered", "failed"
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    # Worker lease claiming
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Scheduling & execution tracking
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    webhook: Mapped["Webhook"] = relationship("Webhook", back_populates="deliveries")

    __table_args__ = (
        UniqueConstraint("webhook_id", "event_id", "event_type", name="uq_webhook_deliveries_idempotency"),
        Index("ix_webhook_deliveries_claim", "status", "next_attempt_at"),
        Index("ix_webhook_deliveries_history", "webhook_id", "created_at"),
    )
