"""Comprehensive test suite for Phase 6 Work Unit 6.3 (WU 6.3):
Secure Notification APIs.

Covers:
1. Authenticated user can retrieve their own notifications.
2. Cross-tenant isolation: a user never receives another user's notifications.
3. Pagination returns correct items, total count, page, page_size, and total_pages.
4. Deterministic newest-first ordering with ID tie-breaker.
5. Unread-only filtering.
6. Category filtering and rejection of invalid category values.
7. Dedicated unread count endpoint calculated strictly from persisted unread rows.
8. Marking an individual notification as read updates is_read and read_at.
9. Idempotency when marking an already-read notification as read.
10. Cross-tenant protection: attempting to mark another user's notification returns 404 (no information leakage).
11. Mark-all-read updates only the calling user's unread records via an atomic update query.
12. Empty state handling: empty list, zero total, zero unread count, zero updated count.
13. Bounded pagination validation: page < 1, page_size < 1, and page_size > 100 rejected with HTTP 422.
14. Authentication and verification guards: unauthenticated (401), inactive (401), unverified email (403).
15. Transactional consistency: database failures rollback safely without partial state mutation.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from httpx import AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.security import create_access_token
from liteads.models.auth import User
from liteads.models.notification import (
    Notification,
    NotificationCategory,
    NotificationChannel,
    NotificationSeverity,
    NotificationType,
)

pytestmark = pytest.mark.asyncio


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
async def user_a(test_db: AsyncSession) -> User:
    """Create verified primary test user A."""
    user = User(
        id=501,
        email="user_a_wu63@example.com",
        name="User A WU6.3",
        password_hash="fakehash_a",
        role="advertiser",
        advertiser_id=51,
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


@pytest.fixture
async def user_b(test_db: AsyncSession) -> User:
    """Create verified separate tenant user B."""
    user = User(
        id=502,
        email="user_b_wu63@example.com",
        name="User B WU6.3",
        password_hash="fakehash_b",
        role="publisher",
        publisher_id=52,
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return user


@pytest.fixture
def auth_headers_a(user_a: User) -> dict[str, str]:
    """Authorization headers for user A."""
    token = create_access_token(user_a)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_headers_b(user_b: User) -> dict[str, str]:
    """Authorization headers for user B."""
    token = create_access_token(user_b)
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. RETRIEVE OWN NOTIFICATIONS & TENANT ISOLATION
# ==============================================================================

async def test_authenticated_user_can_retrieve_own_notifications(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    user_b: User,
    auth_headers_a: dict[str, str],
):
    """Req 1 & 2: User A retrieves only their notifications; User B's notifications are isolated."""
    # Add notifications for user A
    for i in range(3):
        notif_a = Notification(
            user_id=user_a.id,
            category=NotificationCategory.BILLING.value,
            type=NotificationType.PAYMENT_CONFIRMED.value,
            title=f"User A Notice {i}",
            message=f"Message A {i}",
        )
        test_db.add(notif_a)

    # Add notifications for user B
    for j in range(2):
        notif_b = Notification(
            user_id=user_b.id,
            category=NotificationCategory.CAMPAIGN.value,
            type=NotificationType.CAMPAIGN_ACTIVATED.value,
            title=f"User B Notice {j}",
            message=f"Message B {j}",
        )
        test_db.add(notif_b)

    await test_db.commit()

    res = await client.get("/api/v1/notifications", headers=auth_headers_a)
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["total"] == 3
    assert len(data["items"]) == 3
    titles = [item["title"] for item in data["items"]]
    assert all("User A Notice" in t for t in titles)
    assert not any("User B Notice" in t for t in titles)


async def test_user_never_receives_another_users_notifications(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    user_b: User,
    auth_headers_b: dict[str, str],
):
    """Req 2: User B only receives their own notifications even when User A has many."""
    notif_a = Notification(
        user_id=user_a.id,
        category="security",
        type="security_alert",
        title="Secret A Alert",
        message="Sensitive A details",
    )
    notif_b = Notification(
        user_id=user_b.id,
        category="publisher",
        type="publisher_weekly_digest",
        title="Publisher B Weekly",
        message="B Digest",
    )
    test_db.add_all([notif_a, notif_b])
    await test_db.commit()

    res = await client.get("/api/v1/notifications", headers=auth_headers_b)
    assert res.status_code == 200
    data = res.json()

    assert data["total"] == 1
    assert data["items"][0]["title"] == "Publisher B Weekly"
    assert data["items"][0]["id"] == notif_b.id


# ==============================================================================
# 2. BOUNDED PAGINATION
# ==============================================================================

async def test_pagination_records_and_metadata(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 3: Verify page, page_size, total, and total_pages across multiple pages."""
    for i in range(12):
        notif = Notification(
            user_id=user_a.id,
            category=NotificationCategory.SYSTEM.value,
            type=NotificationType.SECURITY_ALERT.value,
            title=f"Notification #{i:02d}",
            message=f"Message {i}",
        )
        test_db.add(notif)
    await test_db.commit()

    # Page 1, page_size 5 -> items 0..4
    res1 = await client.get("/api/v1/notifications?page=1&page_size=5", headers=auth_headers_a)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["page"] == 1
    assert data1["page_size"] == 5
    assert data1["total"] == 12
    assert data1["total_pages"] == 3
    assert len(data1["items"]) == 5

    # Page 2, page_size 5 -> items 5..9
    res2 = await client.get("/api/v1/notifications?page=2&page_size=5", headers=auth_headers_a)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["page"] == 2
    assert len(data2["items"]) == 5
    # Ensure no overlap between page 1 and page 2
    ids_page1 = {item["id"] for item in data1["items"]}
    ids_page2 = {item["id"] for item in data2["items"]}
    assert ids_page1.isdisjoint(ids_page2)

    # Page 3, page_size 5 -> items 10..11 (last 2 items)
    res3 = await client.get("/api/v1/notifications?page=3&page_size=5", headers=auth_headers_a)
    assert res3.status_code == 200
    data3 = res3.json()
    assert len(data3["items"]) == 2

    # Page 4 (out of range) -> empty items, total unchanged
    res4 = await client.get("/api/v1/notifications?page=4&page_size=5", headers=auth_headers_a)
    assert res4.status_code == 200
    data4 = res4.json()
    assert len(data4["items"]) == 0
    assert data4["total"] == 12


# ==============================================================================
# 3. DETERMINISTIC NEWEST-FIRST ORDERING
# ==============================================================================

async def test_deterministic_newest_first_ordering_with_id_tie_breaker(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 4: Notifications ordered newest-first by created_at desc with id desc as tie-breaker."""
    base_time = datetime(2026, 1, 1, 12, 0, 0)
    # Add older notification
    n1 = Notification(
        user_id=user_a.id,
        category="billing",
        type="payment",
        title="Oldest",
        message="M1",
        created_at=base_time,
    )
    # Add newer notification
    n2 = Notification(
        user_id=user_a.id,
        category="billing",
        type="payment",
        title="Newest",
        message="M2",
        created_at=base_time + timedelta(hours=2),
    )
    # Add notification with same timestamp as n2, but higher id
    n3 = Notification(
        user_id=user_a.id,
        category="billing",
        type="payment",
        title="Same Timestamp Higher ID",
        message="M3",
        created_at=base_time + timedelta(hours=2),
    )
    test_db.add_all([n1, n2, n3])
    await test_db.commit()

    res = await client.get("/api/v1/notifications", headers=auth_headers_a)
    assert res.status_code == 200
    items = res.json()["items"]

    assert len(items) == 3
    # n3 has same timestamp as n2 but higher id, so n3 must precede n2
    assert items[0]["id"] == n3.id
    assert items[1]["id"] == n2.id
    assert items[2]["id"] == n1.id


# ==============================================================================
# 4. UNREAD-ONLY AND CATEGORY FILTERING
# ==============================================================================

async def test_unread_only_filter(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 5: unread_only=true returns only unread notifications."""
    read_notif = Notification(
        user_id=user_a.id,
        category="campaign",
        type="campaign_activated",
        title="Read Campaign",
        message="Already viewed",
        is_read=True,
        read_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    unread_notif = Notification(
        user_id=user_a.id,
        category="campaign",
        type="campaign_paused",
        title="Unread Campaign",
        message="Not viewed",
        is_read=False,
    )
    test_db.add_all([read_notif, unread_notif])
    await test_db.commit()

    res = await client.get("/api/v1/notifications?unread_only=true", headers=auth_headers_a)
    assert res.status_code == 200
    data = res.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == unread_notif.id
    assert data["items"][0]["is_read"] is False


async def test_category_filtering_and_validation(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 6: category filter isolates matching category; invalid category rejected with 422."""
    billing = Notification(user_id=user_a.id, category="billing", type="pay", title="Bill", message="B")
    security = Notification(user_id=user_a.id, category="security", type="alert", title="Sec", message="S")
    test_db.add_all([billing, security])
    await test_db.commit()

    # Valid category
    res = await client.get("/api/v1/notifications?category=billing", headers=auth_headers_a)
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["category"] == "billing"

    # Invalid category rejected
    res_invalid = await client.get("/api/v1/notifications?category=not_a_real_category", headers=auth_headers_a)
    assert res_invalid.status_code == 422


# ==============================================================================
# 5. UNREAD COUNT ENDPOINT
# ==============================================================================

async def test_unread_count_calculated_from_persisted_data(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    user_b: User,
    auth_headers_a: dict[str, str],
):
    """Req 7: GET /notifications/unread-count reflects exact unread rows for authenticated user."""
    # 3 unread for User A
    for i in range(3):
        test_db.add(Notification(user_id=user_a.id, category="system", type="t", title=f"U{i}", message="M", is_read=False))
    # 2 read for User A
    for j in range(2):
        test_db.add(Notification(user_id=user_a.id, category="system", type="t", title=f"R{j}", message="M", is_read=True, read_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    # 4 unread for User B (must not affect user A)
    for k in range(4):
        test_db.add(Notification(user_id=user_b.id, category="system", type="t", title=f"UB{k}", message="M", is_read=False))

    await test_db.commit()

    res = await client.get("/api/v1/notifications/unread-count", headers=auth_headers_a)
    assert res.status_code == 200
    data = res.json()
    assert data["unread_count"] == 3


# ==============================================================================
# 6. MARK ONE AS READ & IDEMPOTENCY
# ==============================================================================

async def test_mark_notification_as_read_sets_is_read_and_timestamp(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 8: PATCH /notifications/{id}/read marks notification as read with timestamp."""
    notif = Notification(
        user_id=user_a.id,
        category="domain",
        type="domain_verification_result",
        title="Domain Verified",
        message="example.com approved",
        is_read=False,
    )
    test_db.add(notif)
    await test_db.commit()

    res = await client.patch(f"/api/v1/notifications/{notif.id}/read", headers=auth_headers_a)
    assert res.status_code == 200
    data = res.json()

    assert data["id"] == notif.id
    assert data["is_read"] is True
    assert data["read_at"] is not None

    # Verify persisted in database
    await test_db.refresh(notif)
    assert notif.is_read is True
    assert notif.read_at is not None


async def test_mark_already_read_notification_is_idempotent(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 9: Repeatedly marking a notification read does not error or change original read_at."""
    initial_read_time = datetime(2026, 2, 1, 10, 0, 0)
    notif = Notification(
        user_id=user_a.id,
        category="billing",
        type="payment",
        title="Receipt",
        message="Paid",
        is_read=True,
        read_at=initial_read_time,
    )
    test_db.add(notif)
    await test_db.commit()

    res = await client.patch(f"/api/v1/notifications/{notif.id}/read", headers=auth_headers_a)
    assert res.status_code == 200
    data = res.json()
    assert data["is_read"] is True

    # read_at timestamp remains preserved
    await test_db.refresh(notif)
    assert notif.read_at == initial_read_time


async def test_cannot_mark_another_users_notification_as_read(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    user_b: User,
    auth_headers_b: dict[str, str],
):
    """Req 10: User B attempting to mark User A's notification read receives 404 with no mutation."""
    notif_a = Notification(
        user_id=user_a.id,
        category="security",
        type="security_alert",
        title="User A Secret",
        message="Confidential",
        is_read=False,
    )
    test_db.add(notif_a)
    await test_db.commit()

    # User B tries to mark User A's notification
    res = await client.patch(f"/api/v1/notifications/{notif_a.id}/read", headers=auth_headers_b)
    assert res.status_code == 404
    assert res.json()["detail"] == "Notification not found"

    # User A's notification was NOT altered
    await test_db.refresh(notif_a)
    assert notif_a.is_read is False
    assert notif_a.read_at is None


# ==============================================================================
# 7. MARK ALL AS READ
# ==============================================================================

async def test_mark_all_read_affects_only_current_user(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    user_b: User,
    auth_headers_a: dict[str, str],
):
    """Req 11: PATCH /notifications/read-all marks all User A unread rows, leaving User B unaffected."""
    # User A has 3 unread, 1 already read
    for i in range(3):
        test_db.add(Notification(user_id=user_a.id, category="billing", type="t", title=f"A{i}", message="M", is_read=False))
    test_db.add(Notification(user_id=user_a.id, category="billing", type="t", title="A_read", message="M", is_read=True, read_at=datetime.now(timezone.utc).replace(tzinfo=None)))

    # User B has 2 unread
    b1 = Notification(user_id=user_b.id, category="billing", type="t", title="B0", message="M", is_read=False)
    b2 = Notification(user_id=user_b.id, category="billing", type="t", title="B1", message="M", is_read=False)
    test_db.add_all([b1, b2])
    await test_db.commit()

    res = await client.patch("/api/v1/notifications/read-all", headers=auth_headers_a)
    assert res.status_code == 200
    assert res.json()["updated_count"] == 3

    # User A's unread count is now 0
    unread_a_res = await client.get("/api/v1/notifications/unread-count", headers=auth_headers_a)
    assert unread_a_res.json()["unread_count"] == 0

    # User B's notifications remain untouched
    await test_db.refresh(b1)
    await test_db.refresh(b2)
    assert b1.is_read is False
    assert b2.is_read is False


# ==============================================================================
# 8. EMPTY LIST & ZERO UNREAD
# ==============================================================================

async def test_empty_notification_list_and_zero_counts(
    client: AsyncClient,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 12: Empty state returns zero counts and empty list cleanly."""
    res_list = await client.get("/api/v1/notifications", headers=auth_headers_a)
    assert res_list.status_code == 200
    data = res_list.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["total_pages"] == 0
    assert data["unread_count"] == 0

    res_unread = await client.get("/api/v1/notifications/unread-count", headers=auth_headers_a)
    assert res_unread.status_code == 200
    assert res_unread.json()["unread_count"] == 0

    res_mark_all = await client.patch("/api/v1/notifications/read-all", headers=auth_headers_a)
    assert res_mark_all.status_code == 200
    assert res_mark_all.json()["updated_count"] == 0


# ==============================================================================
# 9. BOUNDED PAGINATION VALIDATION
# ==============================================================================

async def test_invalid_pagination_parameters_rejected_with_422(
    client: AsyncClient,
    auth_headers_a: dict[str, str],
):
    """Req 13: Pagination boundaries strictly validated (page < 1, page_size < 1, page_size > 100)."""
    # page < 1
    r1 = await client.get("/api/v1/notifications?page=0", headers=auth_headers_a)
    assert r1.status_code == 422

    # page negative
    r2 = await client.get("/api/v1/notifications?page=-5", headers=auth_headers_a)
    assert r2.status_code == 422

    # page_size < 1
    r3 = await client.get("/api/v1/notifications?page_size=0", headers=auth_headers_a)
    assert r3.status_code == 422

    # page_size > 100
    r4 = await client.get("/api/v1/notifications?page_size=101", headers=auth_headers_a)
    assert r4.status_code == 422

    # non-integer
    r5 = await client.get("/api/v1/notifications?page=invalid", headers=auth_headers_a)
    assert r5.status_code == 422


# ==============================================================================
# 10. AUTHENTICATION & VERIFICATION GUARDS
# ==============================================================================

async def test_unauthenticated_and_unverified_user_policy(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Req 14: Unauthenticated (401), inactive (401), and unverified (403) requests rejected."""
    # 1. Missing Authorization header
    r_no_auth = await client.get("/api/v1/notifications")
    assert r_no_auth.status_code == 401

    # 2. Invalid JWT token
    r_bad_jwt = await client.get("/api/v1/notifications", headers={"Authorization": "Bearer bad.token.here"})
    assert r_bad_jwt.status_code == 401

    # 3. Inactive user
    inactive_user = User(
        id=590,
        email="inactive@example.com",
        name="Inactive",
        password_hash="fake",
        role="advertiser",
        is_active=False,
        email_verified=True,
    )
    test_db.add(inactive_user)
    await test_db.commit()
    t_inactive = create_access_token(inactive_user)
    r_inactive = await client.get("/api/v1/notifications", headers={"Authorization": f"Bearer {t_inactive}"})
    assert r_inactive.status_code == 401

    # 4. Unverified user
    unverified_user = User(
        id=591,
        email="unverified_step3@example.com",
        name="Unverified",
        password_hash="fake",
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add(unverified_user)
    await test_db.commit()
    t_unverified = create_access_token(unverified_user)
    r_unverified = await client.get("/api/v1/notifications", headers={"Authorization": f"Bearer {t_unverified}"})
    assert r_unverified.status_code == 403
    assert "verification" in r_unverified.json()["detail"].lower()


# ==============================================================================
# 11. TRANSACTIONAL CONSISTENCY ON ERROR
# ==============================================================================

async def test_database_failure_rolls_back_cleanly(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 15: An unhandled database failure during update rolls back without leaving partial state."""
    notif = Notification(
        user_id=user_a.id,
        category="billing",
        type="payment",
        title="Rollback Test",
        message="Should remain unread",
        is_read=False,
    )
    test_db.add(notif)
    await test_db.commit()

    notif_id = notif.id

    with patch.object(AsyncSession, "commit", side_effect=RuntimeError("Simulated DB connection drop")):
        with pytest.raises(RuntimeError):
            await client.patch(f"/api/v1/notifications/{notif_id}/read", headers=auth_headers_a)

    await test_db.rollback()
    refreshed = (await test_db.execute(select(Notification).where(Notification.id == notif_id))).scalars().first()
    assert refreshed.is_read is False
    assert refreshed.read_at is None
