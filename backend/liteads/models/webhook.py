"""Webhook model for event delivery to external endpoints."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.orm import Mapped, mapped_column

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

    @property
    def event_list(self) -> list[str]:
        """Return subscribed events as a list."""
        return [e.strip() for e in self.events.split(",") if e.strip()]
