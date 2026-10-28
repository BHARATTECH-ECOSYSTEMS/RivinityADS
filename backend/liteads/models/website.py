"""Website builder database models.

Entities:
- WebsiteProject: Top-level website entity owned by a user (tenant-isolated).
- WebsitePage: Pages belonging to a project, storing schema-versioned blocks.
- WebsiteVersion: Immutable publication snapshots providing version history and safe rollback.
- WebsiteContactSubmission: Submissions from hosted contact forms.

Architectural Note on current_published_version_id:
To prevent circular foreign key dependencies between website_projects and website_versions,
current_published_version_id is defined as an indexed Integer on website_projects without
a database-level ForeignKey constraint. This eliminates table-creation ordering deadlocks
and circular drop-table warnings in migrations and test suites.
"""

from datetime import datetime, timezone
from enum import IntEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from liteads.models.base import Base, TimestampMixin


class WebsiteStatus(IntEnum):
    """Lifecycle status for website projects."""
    DRAFT = 0
    PUBLISHED = 1
    ARCHIVED = 2
    SUSPENDED = 3


class WebsiteMonetizationStatus(IntEnum):
    """Monetization status for website projects."""
    DISABLED = 0
    PENDING_APPROVAL = 1
    ACTIVE_MONETIZED = 2
    PAUSED = 3
    REJECTED = 4


class WebsiteProject(Base, TimestampMixin):
    """Website project owned by a tenant user."""

    __tablename__ = "website_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    publisher_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("publishers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    property_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("publisher_properties.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    custom_domain: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    template_id: Mapped[str] = mapped_column(
        String(64), default="business_services", nullable=False
    )
    theme_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[int] = mapped_column(
        Integer, default=WebsiteStatus.DRAFT, nullable=False, index=True
    )
    monetization_status: Mapped[int] = mapped_column(
        Integer, default=WebsiteMonetizationStatus.DISABLED, nullable=False, index=True
    )
    monetization_consent: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    current_published_version_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False, index=True
    )

    # Relationships
    owner: Mapped["User"] = relationship("User", lazy="selectin")
    publisher: Mapped["Publisher | None"] = relationship("Publisher", lazy="selectin")
    publisher_property: Mapped["PublisherProperty | None"] = relationship("PublisherProperty", lazy="selectin")

    pages: Mapped[list["WebsitePage"]] = relationship(
        "WebsitePage",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="WebsitePage.order_index",
    )
    versions: Mapped[list["WebsiteVersion"]] = relationship(
        "WebsiteVersion",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="desc(WebsiteVersion.version_number)",
    )
    submissions: Mapped[list["WebsiteContactSubmission"]] = relationship(
        "WebsiteContactSubmission",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    consent_logs: Mapped[list["WebsiteConsentLog"]] = relationship(
        "WebsiteConsentLog",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="desc(WebsiteConsentLog.recorded_at)",
    )

    @property
    def current_published_version(self) -> "WebsiteVersion | None":
        """Resolve current published version strictly within this project's version history."""
        if not self.current_published_version_id:
            return None
        return next((v for v in self.versions if v.id == self.current_published_version_id), None)


class WebsitePage(Base, TimestampMixin):
    """A page within a website project."""

    __tablename__ = "website_pages"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_website_pages_project_slug"),
        Index("ix_website_pages_project_order", "project_id", "order_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("website_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    is_home: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    order_index: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    seo_meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    blocks: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    project: Mapped["WebsiteProject"] = relationship("WebsiteProject", back_populates="pages")


class WebsiteVersion(Base, TimestampMixin):
    """An immutable frozen publication snapshot of a website project."""

    __tablename__ = "website_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version_number", name="uq_website_versions_project_version"),
        Index("ix_website_versions_project_version", "project_id", "version_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("website_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    published_by_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    snapshot_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    rollback_from_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    project: Mapped["WebsiteProject"] = relationship("WebsiteProject", back_populates="versions")


class WebsiteContactSubmission(Base, TimestampMixin):
    """A message submitted through a hosted website contact form."""

    __tablename__ = "website_contact_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("website_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_email: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    project: Mapped["WebsiteProject"] = relationship("WebsiteProject", back_populates="submissions")


class WebsiteConsentLog(Base, TimestampMixin):
    """Immutable audit trail for publisher monetization and visitor consent (Phase 10F)."""

    __tablename__ = "website_consent_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("website_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    consent_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "publisher_terms" or "visitor_consent"
    consent_scope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="v1.0", server_default="v1.0", nullable=False)
    is_granted: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    client_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )

    project: Mapped["WebsiteProject"] = relationship("WebsiteProject", back_populates="consent_logs")
