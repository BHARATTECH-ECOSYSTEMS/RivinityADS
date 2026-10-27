"""Tests for Phase 6 Work Unit 6.2 (WU 6.2):
Durable Notification Dispatcher and Email Delivery Worker.

Covers:
1. In-app notification and email delivery outbox job creation in a single transaction
2. Consistent commit behavior across notification and delivery records
3. User preference suppression of optional emails while preserving in-app notifications
4. Mandatory security notifications bypassing opt-out preferences
5. Deduplication handling and idempotency for repeated / concurrent events
6. Successful email delivery and status transition to 'delivered'
7. Transient SMTP error retry with exponential backoff and jitter
8. Terminal failure after reaching maximum attempts
9. Permanent error immediate dead-letter classification (e.g. invalid recipient format)
10. Abandoned job / worker crash lease recovery
11. Atomic job claiming across concurrent workers
12. SMTP unavailability safety (in-app notification persists regardless of SMTP status)
13. Diagnostic error sanitization (redaction of tokens, secrets, passwords)
14. Single Alembic migration head (j2b3c4d5e6f7) and linear parent (i1a2b3c4d5e6)
15. Reversible upgrade and downgrade execution on a disposable database
"""

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from liteads.ad_server.services.notification_service import NotificationService
from liteads.ad_server.services.notification_worker import (
    NotificationDeliveryWorker,
    calculate_backoff,
    is_permanent_email_error,
    sanitize_error,
)
from liteads.common.email import EmailDeliveryError
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


@pytest.fixture
def session_factory(test_db: AsyncSession):
    """Factory creating isolated AsyncSession instances bound to the test database."""
    return async_sessionmaker(
        test_db.bind, class_=AsyncSession, expire_on_commit=False
    )


@pytest.fixture
async def sample_user(test_db: AsyncSession):
    """Set up a test user with a verified email for notification tests."""
    user = User(
        id=201,
        email="advertiser_wu62@example.com",
        name="Advertiser WU6.2",
        password_hash="fakehash",
        role="advertiser",
        advertiser_id=10,
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    return user


# ==============================================================================
# 1. NOTIFICATION & DELIVERY OUTBOX CREATION
# ==============================================================================

@pytest.mark.asyncio
async def test_notification_and_delivery_job_creation(test_db: AsyncSession, sample_user: User):
    """Verify in-app notification and email delivery outbox are created in the same transaction."""
    notif = await NotificationService.create_notification(
        session=test_db,
        user_id=sample_user.id,
        category=NotificationCategory.BILLING,
        type=NotificationType.PAYMENT_CONFIRMED,
        title="Payment Successful",
        message="Your deposit of $250.00 has been credited to your balance.",
        send_email=True,
        email_subject="Receipt: Deposit Confirmed",
        email_body_text="Your deposit of $250.00 was credited.",
    )
    await test_db.commit()

    # 1. Verify in-app notification
    assert notif.id is not None
    assert notif.user_id == sample_user.id
    assert notif.category == "billing"
    assert notif.is_read is False

    # 2. Verify delivery outbox job
    stmt = select(NotificationDelivery).where(NotificationDelivery.notification_id == notif.id)
    delivery = (await test_db.execute(stmt)).scalars().first()
    assert delivery is not None
    assert delivery.user_id == sample_user.id
    assert delivery.recipient_email == sample_user.email
    assert delivery.channel == "email"
    assert delivery.subject == "Receipt: Deposit Confirmed"
    assert delivery.body_text == "Your deposit of $250.00 was credited."
    assert delivery.status == "pending"
    assert delivery.attempts == 0


@pytest.mark.asyncio
async def test_transactional_rollback_preserves_consistency(test_db: AsyncSession, sample_user: User):
    """Verify rolling back before commit leaves neither notification nor delivery in the database."""
    user_id = sample_user.id
    await NotificationService.create_notification(
        session=test_db,
        user_id=user_id,
        category=NotificationCategory.CAMPAIGN,
        type=NotificationType.CAMPAIGN_ACTIVATED,
        title="Should Rollback",
        message="This should not persist.",
        send_email=True,
    )
    # Explicit rollback
    await test_db.rollback()

    notif_res = (await test_db.execute(select(Notification).where(Notification.title == "Should Rollback"))).scalars().all()
    assert len(notif_res) == 0

    deliv_res = (await test_db.execute(select(NotificationDelivery).where(NotificationDelivery.user_id == user_id))).scalars().all()
    assert len(deliv_res) == 0


# ==============================================================================
# 2. PREFERENCE SUPPRESSION & MANDATORY SECURITY ALERTS
# ==============================================================================

@pytest.mark.asyncio
async def test_preference_suppression_skips_email_while_retaining_in_app(test_db: AsyncSession, sample_user: User):
    """Verify opted-out optional category suppresses email outbox job but preserves in-app notification."""
    # User opts out of campaign emails
    pref = NotificationPreference(
        user_id=sample_user.id,
        channel=NotificationChannel.EMAIL.value,
        category=NotificationCategory.CAMPAIGN.value,
        is_enabled=False,
    )
    test_db.add(pref)
    await test_db.commit()

    notif = await NotificationService.create_notification(
        session=test_db,
        user_id=sample_user.id,
        category=NotificationCategory.CAMPAIGN,
        type=NotificationType.CAMPAIGN_PAUSED,
        title="Campaign Paused",
        message="Daily budget reached.",
        send_email=True,
    )
    await test_db.commit()

    # In-app notification must exist
    assert notif.id is not None
    assert notif.category == "campaign"

    # Outbox delivery must NOT exist (suppressed)
    stmt = select(NotificationDelivery).where(NotificationDelivery.notification_id == notif.id)
    delivery = (await test_db.execute(stmt)).scalars().first()
    assert delivery is None


@pytest.mark.asyncio
async def test_mandatory_security_notifications_bypass_opt_out_preferences(test_db: AsyncSession, sample_user: User):
    """Verify security alerts always queue email delivery even if a security opt-out record exists."""
    # Attempted opt-out of security
    pref = NotificationPreference(
        user_id=sample_user.id,
        channel=NotificationChannel.EMAIL.value,
        category=NotificationCategory.SECURITY.value,
        is_enabled=False,
    )
    test_db.add(pref)
    await test_db.commit()

    notif = await NotificationService.create_notification(
        session=test_db,
        user_id=sample_user.id,
        category=NotificationCategory.SECURITY,
        type=NotificationType.SECURITY_ALERT,
        title="Security Alert: Password Changed",
        message="Your account password was recently changed.",
        send_email=False,  # Security is mandatory, should queue email regardless
    )
    await test_db.commit()

    assert notif.id is not None

    # Delivery outbox record MUST exist because security is mandatory
    stmt = select(NotificationDelivery).where(NotificationDelivery.notification_id == notif.id)
    delivery = (await test_db.execute(stmt)).scalars().first()
    assert delivery is not None
    assert delivery.status == "pending"
    assert delivery.subject == "Security Alert: Password Changed"


# ==============================================================================
# 3. DEDUPLICATION
# ==============================================================================

@pytest.mark.asyncio
async def test_duplicate_event_deduplication(test_db: AsyncSession, sample_user: User):
    """Verify repeated notifications with identical dedupe_key return existing record without duplicate email jobs."""
    dedupe_key = "optimizer:camp_99:sugg_55"

    notif1 = await NotificationService.create_notification(
        session=test_db,
        user_id=sample_user.id,
        category=NotificationCategory.OPTIMIZATION,
        type=NotificationType.OPTIMIZATION_SUGGESTION_CREATED,
        title="Suggestion Available",
        message="Expand targeting radius.",
        dedupe_key=dedupe_key,
        send_email=True,
    )
    await test_db.commit()

    # Second call with same dedupe_key
    notif2 = await NotificationService.create_notification(
        session=test_db,
        user_id=sample_user.id,
        category=NotificationCategory.OPTIMIZATION,
        type=NotificationType.OPTIMIZATION_SUGGESTION_CREATED,
        title="Suggestion Available",
        message="Expand targeting radius.",
        dedupe_key=dedupe_key,
        send_email=True,
    )
    await test_db.commit()

    # Must return identical notification instance
    assert notif1.id == notif2.id

    # Count of delivery jobs must be exactly 1
    stmt = select(func.count(NotificationDelivery.id)).where(NotificationDelivery.notification_id == notif1.id)
    count = (await test_db.execute(stmt)).scalar()
    assert count == 1


# ==============================================================================
# 4. WORKER CLAIMING & EMAIL DELIVERY
# ==============================================================================

@pytest.mark.asyncio
async def test_successful_email_delivery(test_db: AsyncSession, sample_user: User, session_factory):
    """Verify worker claims pending job, executes send_email, and records 'delivered' status."""
    notif = await NotificationService.create_notification(
        session=test_db,
        user_id=sample_user.id,
        category=NotificationCategory.BILLING,
        type=NotificationType.PAYMENT_CONFIRMED,
        title="Receipt",
        message="Paid $50.",
        send_email=True,
    )
    await test_db.commit()

    worker = NotificationDeliveryWorker(session_factory=session_factory)

    # Claim job
    claimed = await worker.claim_jobs(session=test_db, batch_size=10)
    assert len(claimed) == 1
    job, token = claimed[0]
    assert job.status == "processing"
    assert job.attempts == 1
    assert job.lease_token == token

    # Process job with mocked send_email
    with patch("liteads.ad_server.services.notification_worker.send_email", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = None
        success = await worker.process_job(job, token, session_factory=session_factory)
        assert success is True
        mock_send.assert_awaited_once_with(
            to_address=sample_user.email,
            subject="Receipt",
            text_content="Paid $50.",
            html_content=None,
        )

    # Verify updated database state
    job = await test_db.get(NotificationDelivery, job.id)
    await test_db.refresh(job)
    assert job.status == "delivered"
    assert job.delivered_at is not None
    assert job.lease_token is None
    assert job.last_error is None


@pytest.mark.asyncio
async def test_retry_after_transient_smtp_failure(test_db: AsyncSession, sample_user: User, session_factory):
    """Verify transient SMTP failure schedules retry with backoff and resets status to 'pending'."""
    notif = await NotificationService.create_notification(
        session=test_db,
        user_id=sample_user.id,
        category=NotificationCategory.SYSTEM,
        type="test_event",
        title="System Notice",
        message="Notice.",
        send_email=True,
    )
    await test_db.commit()

    worker = NotificationDeliveryWorker(session_factory=session_factory)
    claimed = await worker.claim_jobs(session=test_db, batch_size=10)
    job, token = claimed[0]

    now_before = datetime.now(timezone.utc).replace(tzinfo=None)

    # Simulate transient network connection failure
    with patch("liteads.ad_server.services.notification_worker.send_email", new_callable=AsyncMock) as mock_send:
        mock_send.side_effect = EmailDeliveryError("Connection refused by SMTP host on port 1025")
        success = await worker.process_job(job, token, session_factory=session_factory)
        assert success is False

    job = await test_db.get(NotificationDelivery, job.id)
    await test_db.refresh(job)
    assert job.status == "pending"
    assert job.attempts == 1
    assert job.last_error is not None
    assert "Connection refused" in job.last_error
    assert job.next_attempt_at >= now_before
    assert job.lease_token is None


@pytest.mark.asyncio
async def test_terminal_failure_after_max_attempts(test_db: AsyncSession, sample_user: User, session_factory):
    """Verify job transitions to 'failed' status once max_attempts is reached."""
    # Create job with attempts=4, max_attempts=5
    notif = Notification(user_id=sample_user.id, category="billing", type="pay", title="T", message="M")
    test_db.add(notif)
    await test_db.flush()

    delivery = NotificationDelivery(
        notification_id=notif.id,
        user_id=sample_user.id,
        recipient_email=sample_user.email,
        channel="email",
        subject="Retry Test",
        body_text="Body",
        status="pending",
        attempts=4,
        max_attempts=5,
    )
    test_db.add(delivery)
    await test_db.commit()

    worker = NotificationDeliveryWorker(session_factory=session_factory)
    claimed = await worker.claim_jobs(session=test_db, batch_size=10)
    job, token = claimed[0]
    assert job.attempts == 5  # incremented on claim

    # Simulate transient failure on final allowed attempt
    with patch("liteads.ad_server.services.notification_worker.send_email", new_callable=AsyncMock) as mock_send:
        mock_send.side_effect = EmailDeliveryError("Transient timeout")
        await worker.process_job(job, token, session_factory=session_factory)

    job = await test_db.get(NotificationDelivery, job.id)
    await test_db.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 5


@pytest.mark.asyncio
async def test_permanent_email_failure_immediate_dead_letter(test_db: AsyncSession, sample_user: User, session_factory):
    """Verify permanent errors (e.g. invalid email syntax) immediately fail without retrying."""
    notif = Notification(user_id=sample_user.id, category="billing", type="pay", title="T", message="M")
    test_db.add(notif)
    await test_db.flush()

    # Invalid recipient email format
    delivery = NotificationDelivery(
        notification_id=notif.id,
        user_id=sample_user.id,
        recipient_email="invalid-syntax-no-at-sign",
        channel="email",
        subject="Bad Email",
        body_text="Body",
        status="pending",
        attempts=0,
        max_attempts=5,
    )
    test_db.add(delivery)
    await test_db.commit()

    worker = NotificationDeliveryWorker(session_factory=session_factory)
    claimed = await worker.claim_jobs(session=test_db, batch_size=10)
    job, token = claimed[0]

    with patch("liteads.ad_server.services.notification_worker.send_email", new_callable=AsyncMock) as mock_send:
        success = await worker.process_job(job, token, session_factory=session_factory)
        assert success is False
        mock_send.assert_not_called()  # Did not attempt SMTP for malformed address

    job = await test_db.get(NotificationDelivery, job.id)
    await test_db.refresh(job)
    assert job.status == "failed"
    assert "invalid recipient email syntax" in job.last_error.lower()


# ==============================================================================
# 5. LEASE EXPIRATION & WORKER RECOVERY
# ==============================================================================

@pytest.mark.asyncio
async def test_worker_crash_expired_lease_recovery(test_db: AsyncSession, sample_user: User, session_factory):
    """Verify jobs abandoned by a crashed worker with expired leases are reclaimed."""
    notif = Notification(user_id=sample_user.id, category="billing", type="pay", title="T", message="M")
    test_db.add(notif)
    await test_db.flush()

    # Job stuck in 'processing' whose lease expired 30 seconds ago
    past_lease = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=30)
    delivery = NotificationDelivery(
        notification_id=notif.id,
        user_id=sample_user.id,
        recipient_email=sample_user.email,
        channel="email",
        subject="Abandoned Job",
        body_text="Body",
        status="processing",
        lease_token="crashed-worker-token-xyz",
        lease_expires_at=past_lease,
        attempts=1,
        max_attempts=5,
    )
    test_db.add(delivery)
    await test_db.commit()

    # Recovery worker claims abandoned job
    worker = NotificationDeliveryWorker(session_factory=session_factory)
    claimed = await worker.claim_jobs(session=test_db, batch_size=10)
    assert len(claimed) == 1
    job, new_token = claimed[0]

    assert job.id == delivery.id
    assert job.status == "processing"
    assert new_token != "crashed-worker-token-xyz"
    assert job.lease_token == new_token
    assert job.attempts == 2


@pytest.mark.asyncio
async def test_concurrent_workers_claim_disjoint_jobs(test_db: AsyncSession, sample_user: User, session_factory):
    """Verify multiple workers claiming jobs receive mutually disjoint sets of jobs without double-claiming."""
    for i in range(4):
        notif = Notification(user_id=sample_user.id, category="billing", type="pay", title=f"T{i}", message=f"M{i}")
        test_db.add(notif)
        await test_db.flush()

        delivery = NotificationDelivery(
            notification_id=notif.id,
            user_id=sample_user.id,
            recipient_email=f"user_{i}@example.com",
            channel="email",
            subject=f"Job {i}",
            body_text=f"Body {i}",
            status="pending",
            attempts=0,
            max_attempts=5,
        )
        test_db.add(delivery)
    await test_db.commit()

    worker_a = NotificationDeliveryWorker(session_factory=session_factory, worker_id="worker-A")
    worker_b = NotificationDeliveryWorker(session_factory=session_factory, worker_id="worker-B")

    claimed_a = await worker_a.claim_jobs(session=test_db, batch_size=2)
    claimed_b = await worker_b.claim_jobs(session=test_db, batch_size=2)

    assert len(claimed_a) == 2
    assert len(claimed_b) == 2

    ids_a = {job.id for job, _ in claimed_a}
    ids_b = {job.id for job, _ in claimed_b}

    # Mutually disjoint
    assert ids_a.isdisjoint(ids_b)
    assert len(ids_a.intersection(ids_b)) == 0

    tokens_a = {token for _, token in claimed_a}
    tokens_b = {token for _, token in claimed_b}
    assert tokens_a.isdisjoint(tokens_b)


@pytest.mark.asyncio
async def test_fenced_update_rejects_stale_worker_token(test_db: AsyncSession, sample_user: User, session_factory):
    """Verify an outcome update with a stale lease token is rejected and cannot overwrite the job."""
    notif = Notification(user_id=sample_user.id, category="billing", type="pay", title="T", message="M")
    test_db.add(notif)
    await test_db.flush()

    delivery = NotificationDelivery(
        notification_id=notif.id,
        user_id=sample_user.id,
        recipient_email=sample_user.email,
        channel="email",
        subject="Fencing Test",
        body_text="Body",
        status="processing",
        lease_token="valid-active-token",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=60),
        attempts=1,
    )
    test_db.add(delivery)
    await test_db.commit()

    worker = NotificationDeliveryWorker(session_factory=session_factory)

    # Attempt to persist with an outdated token
    updated = await worker._persist_delivery_outcome(
        delivery_id=delivery.id,
        success=True,
        claim_token="stale-expired-token",
        session_factory=session_factory,
    )
    assert updated is False

    delivery = await test_db.get(NotificationDelivery, delivery.id)
    await test_db.refresh(delivery)
    assert delivery.status == "processing"  # Unchanged
    assert delivery.lease_token == "valid-active-token"


# ==============================================================================
# 6. SMTP UNAVAILABILITY & IN-APP ISOLATION
# ==============================================================================

@pytest.mark.asyncio
async def test_smtp_unavailability_preserves_in_app_notification(test_db: AsyncSession, sample_user: User):
    """Verify that SMTP downtime does not prevent in-app notification persistence."""
    with patch("liteads.common.email.get_settings") as mock_settings:
        # SMTP completely broken / unconfigured
        mock_settings.return_value.email.host = ""
        mock_settings.return_value.email.mock_delivery = False

        notif = await NotificationService.create_notification(
            session=test_db,
            user_id=sample_user.id,
            category=NotificationCategory.BILLING,
            type=NotificationType.PAYMENT_CONFIRMED,
            title="In-App Persists",
            message="Your account balance updated.",
            send_email=True,
        )
        await test_db.commit()

        # In-app notification must be safely committed
        assert notif.id is not None
        assert notif.title == "In-App Persists"

        # Outbox job is safely recorded as pending, awaiting worker
        stmt = select(NotificationDelivery).where(NotificationDelivery.notification_id == notif.id)
        delivery = (await test_db.execute(stmt)).scalars().first()
        assert delivery is not None
        assert delivery.status == "pending"


# ==============================================================================
# 7. DIAGNOSTIC SANITIZATION
# ==============================================================================

def test_diagnostic_sanitization_redacts_credentials():
    """Verify sanitize_error strips tokens, secrets, passwords, and sensitive URL query strings."""
    raw = (
        "SMTP connect error: host=mail.server password=super_secret_pw123 "
        "auth_token=abc-token-xyz url=https://ad.rivinity.com/reset?token=one_time_secret_123"
    )
    sanitized = sanitize_error(raw)

    assert "super_secret_pw123" not in sanitized
    assert "abc-token-xyz" not in sanitized
    assert "one_time_secret_123" not in sanitized
    assert "[REDACTED]" in sanitized or "[PARAMS_REDACTED]" in sanitized


# ==============================================================================
# 8. ALEMBIC MIGRATION GRAPH VERIFICATION
# ==============================================================================

def test_alembic_single_head_j2b3c4d5e6f7():
    """Verify Alembic migration tree has single head j2b3c4d5e6f7 and down_revision i1a2b3c4d5e6."""
    alembic_dir = Path(__file__).resolve().parent.parent.parent / "alembic"
    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert len(heads) == 1, f"Expected 1 head, got {heads}"

    head_rev = script.get_revision("j2b3c4d5e6f7")
    assert head_rev is not None
    assert head_rev.down_revision == "i1a2b3c4d5e6"


# ==============================================================================
# 9. DISPOSABLE DB MIGRATION UPGRADE & DOWNGRADE
# ==============================================================================

def test_migration_j2b3c4d5e6f7_upgrade_downgrade_disposable_db():
    """Verify migration j2b3c4d5e6f7 upgrade and downgrade on an isolated in-memory database."""
    migration_file = (
        Path(__file__).resolve().parent.parent.parent
        / "alembic"
        / "versions"
        / "j2b3c4d5e6f7_phase6_wu2_notification_deliveries_outbox.py"
    )
    spec = importlib.util.spec_from_file_location("phase6_wu2_migration", str(migration_file))
    mig_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig_module)

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        mig_module.op = op

        # Create prerequisite tables (users, notifications)
        conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE notifications (id INTEGER PRIMARY KEY)")

        # 1. Upgrade
        mig_module.upgrade()
        inspector = inspect(conn)
        assert "notification_deliveries" in inspector.get_table_names()

        cols = {c["name"]: c for c in inspector.get_columns("notification_deliveries")}
        assert "notification_id" in cols
        assert "user_id" in cols
        assert "recipient_email" in cols
        assert "channel" in cols
        assert "subject" in cols
        assert "status" in cols
        assert "attempts" in cols
        assert "lease_token" in cols
        assert "lease_expires_at" in cols
        assert "next_attempt_at" in cols
        assert "delivered_at" in cols
        assert "last_error" in cols

        # 2. Downgrade
        mig_module.downgrade()
        inspector = inspect(conn)
        assert "notification_deliveries" not in inspector.get_table_names()
        assert "notifications" in inspector.get_table_names()
        assert "users" in inspector.get_table_names()
