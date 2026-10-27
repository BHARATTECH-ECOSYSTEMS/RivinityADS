"""Tests for Phase 6 Work Unit 6.1 (WU 6.1):
Durable Notification Schema and User Notification Preferences.

Covers:
1. Registration of Notification and NotificationPreference models in ORM metadata
2. Model creation, default values, and foreign-key relationships
3. Read/unread lifecycle transitions (mark_read, mark_unread) and timestamp tracking
4. Deduplication key uniqueness constraint scoped per user
5. Notification preferences uniqueness constraint on (user_id, channel, category)
6. Recipient-scoped retrieval, unread count queries, and tenant isolation
7. Cascade deletion behavior when parent user is removed
8. Single Alembic migration head with valid parent revision (h9c0d1e2f3a4)
9. Reversible upgrade and downgrade execution against a disposable database
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.auth import User
from liteads.models.base import Base
from liteads.models.notification import (
    Notification,
    NotificationCategory,
    NotificationChannel,
    NotificationPreference,
    NotificationSeverity,
    NotificationType,
)


@pytest.fixture
async def sample_users(test_db: AsyncSession):
    """Set up test users for notification ownership tests."""
    user1 = User(
        id=101,
        email="user1_notif@example.com",
        name="User One",
        password_hash="fakehash",
        role="advertiser",
        advertiser_id=1,
        is_active=True,
        email_verified=True,
    )
    user2 = User(
        id=102,
        email="user2_notif@example.com",
        name="User Two",
        password_hash="fakehash",
        role="publisher",
        publisher_id=1,
        is_active=True,
        email_verified=True,
    )
    test_db.add_all([user1, user2])
    await test_db.commit()
    return {"user1": user1, "user2": user2}


# ==============================================================================
# 1. ORM METADATA REGISTRATION
# ==============================================================================

def test_notification_models_registered_in_metadata():
    """Verify Notification and NotificationPreference are registered in Base.metadata."""
    table_names = Base.metadata.tables.keys()
    assert "notifications" in table_names, "notifications table not registered in Base.metadata"
    assert "notification_preferences" in table_names, "notification_preferences table not registered in Base.metadata"


# ==============================================================================
# 2. MODEL CREATION AND LIFECYCLE
# ==============================================================================

@pytest.mark.asyncio
async def test_notification_creation_and_defaults(test_db: AsyncSession, sample_users: dict):
    """Verify valid notification creation and default attributes."""
    user1 = sample_users["user1"]

    notif = Notification(
        user_id=user1.id,
        category=NotificationCategory.CAMPAIGN.value,
        type=NotificationType.CAMPAIGN_ACTIVATED.value,
        title="Campaign Launched",
        message="Your campaign 'Summer Sale' is now active and serving impressions.",
        link="/campaigns/42",
        resource_type="campaign",
        resource_id="42",
    )
    test_db.add(notif)
    await test_db.commit()
    await test_db.refresh(notif)

    assert notif.id is not None
    assert notif.user_id == user1.id
    assert notif.category == "campaign"
    assert notif.type == "campaign_activated"
    assert notif.channel == "in_app"
    assert notif.severity == "info"
    assert notif.is_read is False
    assert notif.read_at is None
    assert notif.link == "/campaigns/42"
    assert notif.resource_type == "campaign"
    assert notif.resource_id == "42"
    assert notif.created_at is not None
    assert notif.updated_at is not None


@pytest.mark.asyncio
async def test_notification_mark_read_and_unread_lifecycle(test_db: AsyncSession, sample_users: dict):
    """Verify mark_read and mark_unread helper methods update state and timestamps."""
    user1 = sample_users["user1"]

    notif = Notification(
        user_id=user1.id,
        category=NotificationCategory.SECURITY.value,
        type=NotificationType.SECURITY_ALERT.value,
        severity=NotificationSeverity.WARNING.value,
        title="New Login Detected",
        message="A new login was recorded from 192.168.1.1.",
    )
    test_db.add(notif)
    await test_db.commit()
    await test_db.refresh(notif)

    assert notif.is_read is False
    assert notif.read_at is None

    # Mark as read
    notif.mark_read()
    await test_db.commit()
    await test_db.refresh(notif)

    assert notif.is_read is True
    assert notif.read_at is not None

    # Mark back as unread
    notif.mark_unread()
    await test_db.commit()
    await test_db.refresh(notif)

    assert notif.is_read is False
    assert notif.read_at is None


# ==============================================================================
# 3. DEDUPLICATION AND UNIQUENESS CONSTRAINTS
# ==============================================================================

@pytest.mark.asyncio
async def test_notification_dedupe_key_prevents_duplicate_events(test_db: AsyncSession, sample_users: dict):
    """Verify dedupe_key prevents inserting identical event notifications for the same user."""
    user1 = sample_users["user1"]
    dedupe_key = "optimizer:camp_42:sugg_101"

    notif1 = Notification(
        user_id=user1.id,
        category=NotificationCategory.OPTIMIZATION.value,
        type=NotificationType.OPTIMIZATION_SUGGESTION_CREATED.value,
        title="Optimization Suggestion",
        message="Increase bid by 10% for better win rate.",
        dedupe_key=dedupe_key,
    )
    test_db.add(notif1)
    await test_db.commit()

    # Attempting to insert a duplicate notification for the same user and dedupe_key must fail
    notif2 = Notification(
        user_id=user1.id,
        category=NotificationCategory.OPTIMIZATION.value,
        type=NotificationType.OPTIMIZATION_SUGGESTION_CREATED.value,
        title="Duplicate Suggestion",
        message="Increase bid by 10% for better win rate.",
        dedupe_key=dedupe_key,
    )
    test_db.add(notif2)
    with pytest.raises(IntegrityError):
        await test_db.commit()
    await test_db.rollback()


@pytest.mark.asyncio
async def test_notification_dedupe_key_allowed_across_different_users(test_db: AsyncSession, sample_users: dict):
    """Verify the same dedupe_key can exist independently across distinct recipient users."""
    user1 = sample_users["user1"]
    user2 = sample_users["user2"]
    shared_key = "system:maintenance:2026-11-01"

    notif1 = Notification(
        user_id=user1.id,
        category=NotificationCategory.SYSTEM.value,
        type="maintenance_scheduled",
        title="Maintenance Notice",
        message="Scheduled maintenance on Nov 1.",
        dedupe_key=shared_key,
    )
    notif2 = Notification(
        user_id=user2.id,
        category=NotificationCategory.SYSTEM.value,
        type="maintenance_scheduled",
        title="Maintenance Notice",
        message="Scheduled maintenance on Nov 1.",
        dedupe_key=shared_key,
    )
    test_db.add_all([notif1, notif2])
    await test_db.commit()

    assert notif1.id is not None
    assert notif2.id is not None
    assert notif1.id != notif2.id


@pytest.mark.asyncio
async def test_null_dedupe_keys_do_not_conflict(test_db: AsyncSession, sample_users: dict):
    """Verify multiple notifications with null dedupe_key can coexist for the same user."""
    user1 = sample_users["user1"]

    notif1 = Notification(
        user_id=user1.id,
        category=NotificationCategory.BILLING.value,
        type=NotificationType.PAYMENT_CONFIRMED.value,
        title="Payment 1",
        message="Receipt 1.",
        dedupe_key=None,
    )
    notif2 = Notification(
        user_id=user1.id,
        category=NotificationCategory.BILLING.value,
        type=NotificationType.PAYMENT_CONFIRMED.value,
        title="Payment 2",
        message="Receipt 2.",
        dedupe_key=None,
    )
    test_db.add_all([notif1, notif2])
    await test_db.commit()

    assert notif1.id is not None
    assert notif2.id is not None


# ==============================================================================
# 4. NOTIFICATION PREFERENCES
# ==============================================================================

@pytest.mark.asyncio
async def test_notification_preference_creation_and_defaults(test_db: AsyncSession, sample_users: dict):
    """Verify creating a notification preference record with defaults."""
    user1 = sample_users["user1"]

    pref = NotificationPreference(
        user_id=user1.id,
        channel=NotificationChannel.EMAIL.value,
        category=NotificationCategory.OPTIMIZATION.value,
        is_enabled=False,
    )
    test_db.add(pref)
    await test_db.commit()
    await test_db.refresh(pref)

    assert pref.id is not None
    assert pref.user_id == user1.id
    assert pref.channel == "email"
    assert pref.category == "optimization"
    assert pref.is_enabled is False


@pytest.mark.asyncio
async def test_notification_preference_uniqueness_enforced(test_db: AsyncSession, sample_users: dict):
    """Verify a user cannot accumulate duplicate preference records for the same (user_id, channel, category)."""
    user1 = sample_users["user1"]

    pref1 = NotificationPreference(
        user_id=user1.id,
        channel=NotificationChannel.EMAIL.value,
        category=NotificationCategory.BILLING.value,
        is_enabled=True,
    )
    test_db.add(pref1)
    await test_db.commit()

    # Second entry for identical scope
    pref2 = NotificationPreference(
        user_id=user1.id,
        channel=NotificationChannel.EMAIL.value,
        category=NotificationCategory.BILLING.value,
        is_enabled=False,
    )
    test_db.add(pref2)
    with pytest.raises(IntegrityError):
        await test_db.commit()
    await test_db.rollback()


# ==============================================================================
# 5. RECIPIENT SCOPING, UNREAD QUERIES, AND TENANT ISOLATION
# ==============================================================================

@pytest.mark.asyncio
async def test_recipient_scoped_queries_and_unread_counts(test_db: AsyncSession, sample_users: dict):
    """Verify unread counts, pagination, and category filtering strictly isolate tenant notifications."""
    user1 = sample_users["user1"]
    user2 = sample_users["user2"]

    # User 1: 2 unread, 1 read
    n1 = Notification(user_id=user1.id, category="billing", type="pay", title="U1 N1", message="m", is_read=False)
    n2 = Notification(user_id=user1.id, category="campaign", type="camp", title="U1 N2", message="m", is_read=False)
    n3 = Notification(user_id=user1.id, category="billing", type="pay", title="U1 N3", message="m", is_read=True)

    # User 2: 3 unread
    n4 = Notification(user_id=user2.id, category="billing", type="pay", title="U2 N1", message="m", is_read=False)
    n5 = Notification(user_id=user2.id, category="publisher", type="pub", title="U2 N2", message="m", is_read=False)
    n6 = Notification(user_id=user2.id, category="publisher", type="pub", title="U2 N3", message="m", is_read=False)

    test_db.add_all([n1, n2, n3, n4, n5, n6])
    await test_db.commit()

    # Query User 1 unread count
    q_u1_unread = select(func.count(Notification.id)).where(
        Notification.user_id == user1.id,
        Notification.is_read.is_(False),
    )
    u1_unread_count = (await test_db.execute(q_u1_unread)).scalar()
    assert u1_unread_count == 2, f"Expected 2 unread notifications for User 1, got {u1_unread_count}"

    # Query User 2 unread count
    q_u2_unread = select(func.count(Notification.id)).where(
        Notification.user_id == user2.id,
        Notification.is_read.is_(False),
    )
    u2_unread_count = (await test_db.execute(q_u2_unread)).scalar()
    assert u2_unread_count == 3, f"Expected 3 unread notifications for User 2, got {u2_unread_count}"

    # Query User 1 billing notifications
    q_u1_billing = select(Notification).where(
        Notification.user_id == user1.id,
        Notification.category == "billing",
    )
    u1_billing_items = (await test_db.execute(q_u1_billing)).scalars().all()
    assert len(u1_billing_items) == 2
    assert all(item.user_id == user1.id for item in u1_billing_items)


# ==============================================================================
# 6. CASCADE DELETION ON USER
# ==============================================================================

@pytest.mark.asyncio
async def test_cascade_delete_on_user(test_db: AsyncSession, sample_users: dict):
    """Verify deleting a User removes their associated notifications and preferences."""
    try:
        await test_db.execute(text("PRAGMA foreign_keys = ON"))
        user1 = sample_users["user1"]

        notif = Notification(user_id=user1.id, category="billing", type="pay", title="Alert", message="msg")
        pref = NotificationPreference(user_id=user1.id, channel="in_app", category="billing", is_enabled=True)
        test_db.add_all([notif, pref])
        await test_db.commit()

        # Delete User 1
        await test_db.delete(user1)
        await test_db.commit()

        # Verify notification was deleted
        q_notif = select(Notification).where(Notification.user_id == user1.id)
        remaining_notifs = (await test_db.execute(q_notif)).scalars().all()
        assert len(remaining_notifs) == 0

        # Verify preference was deleted
        q_pref = select(NotificationPreference).where(NotificationPreference.user_id == user1.id)
        remaining_prefs = (await test_db.execute(q_pref)).scalars().all()
        assert len(remaining_prefs) == 0
    finally:
        await test_db.execute(text("PRAGMA foreign_keys = OFF"))


# ==============================================================================
# 7. ALEMBIC MIGRATION GRAPH VERIFICATION
# ==============================================================================

def test_alembic_migration_graph_single_head():
    """Verify Alembic migration tree has exactly one valid head and revision i1a2b3c4d5e6 parent is h9c0d1e2f3a4."""
    alembic_dir = Path(__file__).resolve().parent.parent.parent / "alembic"
    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly one Alembic head, found {heads}"

    rev_wu1 = script.get_revision("i1a2b3c4d5e6")
    assert rev_wu1 is not None
    assert rev_wu1.down_revision == "h9c0d1e2f3a4", f"Expected down_revision h9c0d1e2f3a4, got {rev_wu1.down_revision}"


# ==============================================================================
# 8. REVERSIBLE UPGRADE AND DOWNGRADE EXECUTION
# ==============================================================================

def test_migration_upgrade_and_downgrade_disposable_db():
    """Verify migration upgrade and downgrade execute successfully against a disposable SQLite database."""
    migration_file = (
        Path(__file__).resolve().parent.parent.parent
        / "alembic"
        / "versions"
        / "i1a2b3c4d5e6_phase6_wu1_durable_notification_schema.py"
    )
    spec = importlib.util.spec_from_file_location("phase6_migration", str(migration_file))
    mig_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig_module)

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        mig_module.op = op

        # Create prerequisite users table so foreign keys resolve cleanly
        conn.exec_driver_sql(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, name TEXT, password_hash TEXT, role TEXT, is_active BOOLEAN, email_verified BOOLEAN)"
        )

        # 1. Execute Upgrade
        mig_module.upgrade()
        inspector = inspect(conn)
        tables_after_upgrade = inspector.get_table_names()

        assert "notifications" in tables_after_upgrade
        assert "notification_preferences" in tables_after_upgrade

        notif_cols = {c["name"]: c for c in inspector.get_columns("notifications")}
        assert "user_id" in notif_cols
        assert "category" in notif_cols
        assert "type" in notif_cols
        assert "channel" in notif_cols
        assert "severity" in notif_cols
        assert "title" in notif_cols
        assert "message" in notif_cols
        assert "is_read" in notif_cols
        assert "dedupe_key" in notif_cols
        assert "expires_at" in notif_cols

        pref_cols = {c["name"]: c for c in inspector.get_columns("notification_preferences")}
        assert "user_id" in pref_cols
        assert "channel" in pref_cols
        assert "category" in pref_cols
        assert "is_enabled" in pref_cols

        # 2. Execute Downgrade
        mig_module.downgrade()
        inspector = inspect(conn)
        tables_after_downgrade = inspector.get_table_names()

        assert "notifications" not in tables_after_downgrade
        assert "notification_preferences" not in tables_after_downgrade
        assert "users" in tables_after_downgrade  # prerequisite table preserved
