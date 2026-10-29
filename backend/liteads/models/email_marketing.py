"""Tenant-scoped Email Marketing Models (Phase 6.1D).

Provides local authoritative ownership, mapping, and lifecycle tracking for:
- EmailMarketingList: Mailing lists scoped to an advertiser or platform tenant.
- EmailMarketingContact: Permission-based marketing contacts with explicit consent provenance.
- EmailMarketingCampaign: Marketing email campaigns with confirmed launch guardrails.

External Listmonk IDs are stored for synchronization only and are never exposed
directly to client requests or trusted across tenant boundaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

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


class EmailMarketingList(Base, TimestampMixin):
    """Mailing list owned by a specific tenant."""

    __tablename__ = "email_marketing_lists"
    __table_args__ = (
        Index("ix_em_lists_tenant", "tenant_type", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_type: Mapped[str] = mapped_column(String(32), nullable=False, default="advertiser")
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="private")
    optin: Mapped[str] = mapped_column(String(32), nullable=False, default="single")
    listmonk_list_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relationships
    contacts: Mapped[list["EmailMarketingContact"]] = relationship(
        "EmailMarketingContact", back_populates="list", cascade="all, delete-orphan"
    )
    campaigns: Mapped[list["EmailMarketingCampaign"]] = relationship(
        "EmailMarketingCampaign", back_populates="list"
    )


class EmailMarketingContact(Base, TimestampMixin):
    """Permission-based subscriber contact linked to an owned list."""

    __tablename__ = "email_marketing_contacts"
    __table_args__ = (
        UniqueConstraint("tenant_type", "tenant_id", "list_id", "email", name="uq_em_contacts_tenant_list_email"),
        Index("ix_em_contacts_tenant_list", "tenant_type", "tenant_id", "list_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    list_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_marketing_lists.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="subscribed")
    consent_source: Mapped[str] = mapped_column(String(64), nullable=False, default="import")
    consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    listmonk_subscriber_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relationships
    list: Mapped["EmailMarketingList"] = relationship("EmailMarketingList", back_populates="contacts")


class EmailMarketingCampaign(Base, TimestampMixin):
    """Marketing campaign entity with draft, preview, test, and confirmed launch workflow."""

    __tablename__ = "email_marketing_campaigns"
    __table_args__ = (
        Index("ix_em_campaigns_tenant", "tenant_type", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    list_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_marketing_lists.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="html")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    listmonk_campaign_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    send_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    list: Mapped["EmailMarketingList"] = relationship("EmailMarketingList", back_populates="campaigns")

