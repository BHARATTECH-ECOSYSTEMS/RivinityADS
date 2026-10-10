"""
Domain ownership models.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from liteads.models.base import Base, TimestampMixin


class VerificationStatus(str, Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class VerificationMethod(str, Enum):
    DNS_TXT = "DNS_TXT"


class AdvertiserDomain(Base, TimestampMixin):
    """Advertiser verified domains."""

    __tablename__ = "advertiser_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advertiser_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("advertisers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[VerificationStatus] = mapped_column(
        String(50), default=VerificationStatus.PENDING, nullable=False
    )
    verification_method: Mapped[VerificationMethod] = mapped_column(
        String(50), default=VerificationMethod.DNS_TXT, nullable=False
    )
    verification_token: Mapped[str] = mapped_column(String(255), nullable=False)
    
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    advertiser = relationship("Advertiser", backref="domains")
