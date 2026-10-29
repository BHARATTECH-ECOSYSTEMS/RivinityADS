"""Marketing consent, subscription state, and audit provenance models (Phase 6.1C).

Maintains authoritative record of explicit marketing consent for both registered
RivinityADS users and lawful external subscribers. Explicitly decouples marketing
consent from transactional emails, account creation, and billing events.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from liteads.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from liteads.models.auth import User


class ConsentCategory(str, Enum):
    """Specific marketing and communication categories."""
    PRODUCT_NEWSLETTER = "product_newsletter"
    ADVERTISER_EDUCATION = "advertiser_education"
    PUBLISHER_EDUCATION = "publisher_education"


class ConsentStatus(str, Enum):
    """Marketing consent lifecycle state."""
    OPTED_IN = "opted_in"
    OPTED_OUT = "opted_out"
    PENDING_CONFIRMATION = "pending_confirmation"


class ConsentSource(str, Enum):
    """Provenance and audit source for consent actions."""
    SIGNUP = "signup"
    PREFERENCES = "preferences"
    PUBLIC_FORM = "public_form"
    IMPORT = "import"
    LISTMONK_RECONCILIATION = "listmonk_reconciliation"


class MarketingConsent(Base, TimestampMixin):
    """Authoritative local record of marketing consent and Listmonk synchronization.

    Guarantees:
    - No implicit opt-in from transactional emails or account registration.
    - Idempotent tracking per (email, category) pair.
    - Explicit revocation timestamp preventing accidental re-subscription on retries.
    """

    __tablename__ = "marketing_consents"
    __table_args__ = (
        UniqueConstraint(
            "email", "category", name="uq_marketing_consents_email_category"
        ),
        Index("ix_marketing_consents_status_category", "status", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, default=ConsentCategory.PRODUCT_NEWSLETTER.value
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ConsentStatus.PENDING_CONFIRMATION.value
    )
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default=ConsentSource.PREFERENCES.value
    )
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="2026.1")

    # Double opt-in confirmation tracking
    confirmation_token: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    confirmation_token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    # Timestamps & provenance
    consented_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # External Listmonk mapping & synchronization state
    listmonk_subscriber_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[user_id])

