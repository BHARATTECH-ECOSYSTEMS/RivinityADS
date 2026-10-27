"""Comprehensive test suite for Phase 6 Work Unit 6.4 (WU 6.4):
Secure Notification Preferences API.

Covers:
1. Authenticated user can retrieve their own preferences.
2. Missing preference records follow the documented default behavior without persisting records during reads.
3. User can create a preference.
4. User can update an existing preference without creating a duplicate.
5. Repeated identical updates are idempotent.
6. Batch updates work correctly (both bare array and object envelope).
7. Duplicate entries in one request are rejected consistently with HTTP 422.
8. Invalid channels/categories and malformed payloads are rejected with HTTP 422.
9. Unauthenticated (401), inactive (401), and unverified (403) users follow existing authentication policy.
10. Tenant isolation: no user can read or modify another user's preferences.
11. A failed batch transaction rolls back cleanly without partial state mutation.
12. Mandatory security-notification behavior remains intact (cannot be opted out).
13. WU6.2 optional-email suppression still respects the persisted preferences.
14. Both array and object payloads accepted on PUT.
"""

from unittest.mock import patch

from httpx import AsyncClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.notification_service import NotificationService
from liteads.common.security import create_access_token
from liteads.models.auth import User
from liteads.models.notification import (
    NotificationCategory,
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
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
        id=601,
        email="user_a_wu64@example.com",
        name="User A WU6.4",
        password_hash="fakehash_a_wu64",
        role="advertiser",
        advertiser_id=61,
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
        id=602,
        email="user_b_wu64@example.com",
        name="User B WU6.4",
        password_hash="fakehash_b_wu64",
        role="publisher",
        publisher_id=62,
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
# 1. RETRIEVE OWN PREFERENCES & RESPONSE SCHEMA
# ==============================================================================

async def test_authenticated_user_can_retrieve_own_preferences(
    client: AsyncClient,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 1: Authenticated user retrieves explicit preference items without internal fields."""
    res = await client.get("/api/v1/notifications/preferences", headers=auth_headers_a)
    assert res.status_code == 200, res.text
    data = res.json()

    assert "preferences" in data
    prefs = data["preferences"]
    assert isinstance(prefs, list)
    # 2 channels * 9 categories = 18 total combinations
    assert len(prefs) == len(NotificationChannel) * len(NotificationCategory)

    for item in prefs:
        assert "channel" in item
        assert "category" in item
        assert "is_enabled" in item
        assert isinstance(item["is_enabled"], bool)
        # Verify internal fields are strictly omitted
        assert "id" not in item
        assert "user_id" not in item
        assert "created_at" not in item
        assert "updated_at" not in item


# ==============================================================================
# 2. DEFAULT BEHAVIOR ON MISSING RECORDS (NO READ-TIME PERSISTENCE)
# ==============================================================================

async def test_missing_preference_records_default_to_enabled_without_persisting(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 2: Missing rows default to is_enabled=True and GET never writes rows to DB."""
    user_id = user_a.id
    # Verify database has zero preference rows for user A
    initial_count = (
        await test_db.execute(
            select(func.count(NotificationPreference.id)).where(NotificationPreference.user_id == user_id)
        )
    ).scalar()
    assert initial_count == 0

    res = await client.get("/api/v1/notifications/preferences", headers=auth_headers_a)
    assert res.status_code == 200
    prefs = res.json()["preferences"]

    # All preferences must evaluate to True by default
    for item in prefs:
        assert item["is_enabled"] is True, f"Expected {item['channel']}:{item['category']} to be True by default"

    # Crucially verify database STILL has zero rows for user A
    after_count = (
        await test_db.execute(
            select(func.count(NotificationPreference.id)).where(NotificationPreference.user_id == user_id)
        )
    ).scalar()
    assert after_count == 0


# ==============================================================================
# 3. CREATE PREFERENCE RECORD
# ==============================================================================

async def test_user_can_create_preference(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 3: User can create a new preference setting (e.g. opt-out of campaign emails)."""
    user_id = user_a.id
    payload = [
        {"channel": "email", "category": "campaign", "is_enabled": False}
    ]
    res = await client.put("/api/v1/notifications/preferences", json=payload, headers=auth_headers_a)
    assert res.status_code == 200, res.text
    prefs = res.json()["preferences"]

    matching = [p for p in prefs if p["channel"] == "email" and p["category"] == "campaign"]
    assert len(matching) == 1
    assert matching[0]["is_enabled"] is False

    # Check persisted row in DB
    q = select(NotificationPreference).where(
        NotificationPreference.user_id == user_id,
        NotificationPreference.channel == "email",
        NotificationPreference.category == "campaign",
    )
    row = (await test_db.execute(q)).scalars().first()
    assert row is not None
    assert row.is_enabled is False


# ==============================================================================
# 4. UPDATE EXISTING PREFERENCE WITHOUT DUPLICATES
# ==============================================================================

async def test_user_can_update_existing_preference_without_duplicate(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 4: Updating an existing preference updates the row in-place without adding duplicate rows."""
    user_id = user_a.id
    # First set to False
    res1 = await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "email", "category": "billing", "is_enabled": False}],
        headers=auth_headers_a,
    )
    assert res1.status_code == 200

    # Next update to True
    res2 = await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "email", "category": "billing", "is_enabled": True}],
        headers=auth_headers_a,
    )
    assert res2.status_code == 200
    matching = [p for p in res2.json()["preferences"] if p["channel"] == "email" and p["category"] == "billing"]
    assert matching[0]["is_enabled"] is True

    # Verify only 1 row exists in DB
    rows = (
        await test_db.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.channel == "email",
                NotificationPreference.category == "billing",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].is_enabled is True


# ==============================================================================
# 5. REPEATED IDENTICAL UPDATES ARE IDEMPOTENT
# ==============================================================================

async def test_repeated_identical_updates_are_idempotent(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 5: Repeated identical updates succeed identically and produce no duplicate rows."""
    user_id = user_a.id
    payload = [{"channel": "email", "category": "creative", "is_enabled": False}]

    r1 = await client.put("/api/v1/notifications/preferences", json=payload, headers=auth_headers_a)
    assert r1.status_code == 200

    r2 = await client.put("/api/v1/notifications/preferences", json=payload, headers=auth_headers_a)
    assert r2.status_code == 200

    count = (
        await test_db.execute(
            select(func.count(NotificationPreference.id)).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.channel == "email",
                NotificationPreference.category == "creative",
            )
        )
    ).scalar()
    assert count == 1


# ==============================================================================
# 6. BATCH UPDATES WORK CORRECTLY (ARRAY & ENVELOPE FORMATS)
# ==============================================================================

async def test_batch_updates_work_correctly_both_formats(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 6: Batch updating multiple preferences succeeds in a single transaction."""
    # 1. Bare array format
    batch_array = [
        {"channel": "email", "category": "billing", "is_enabled": False},
        {"channel": "email", "category": "domain", "is_enabled": False},
        {"channel": "in_app", "category": "optimization", "is_enabled": False},
    ]
    res1 = await client.put("/api/v1/notifications/preferences", json=batch_array, headers=auth_headers_a)
    assert res1.status_code == 200
    pref_map1 = {(p["channel"], p["category"]): p["is_enabled"] for p in res1.json()["preferences"]}
    assert pref_map1[("email", "billing")] is False
    assert pref_map1[("email", "domain")] is False
    assert pref_map1[("in_app", "optimization")] is False

    # 2. Envelope object format: {"preferences": [...]}
    batch_obj = {
        "preferences": [
            {"channel": "email", "category": "billing", "is_enabled": True},
            {"channel": "email", "category": "webhook", "is_enabled": False},
        ]
    }
    res2 = await client.put("/api/v1/notifications/preferences", json=batch_obj, headers=auth_headers_a)
    assert res2.status_code == 200
    pref_map2 = {(p["channel"], p["category"]): p["is_enabled"] for p in res2.json()["preferences"]}
    assert pref_map2[("email", "billing")] is True
    assert pref_map2[("email", "webhook")] is False
    assert pref_map2[("email", "domain")] is False  # untouched from previous batch


# ==============================================================================
# 7. DUPLICATE ENTRIES IN ONE REQUEST REJECTED WITH 422
# ==============================================================================

async def test_duplicate_entries_in_one_request_rejected_with_422(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 7: Conflicting or duplicate preference entries in a single request payload return HTTP 422."""
    user_id = user_a.id
    payload = [
        {"channel": "email", "category": "billing", "is_enabled": False},
        {"channel": "email", "category": "billing", "is_enabled": True},
    ]
    res = await client.put("/api/v1/notifications/preferences", json=payload, headers=auth_headers_a)
    assert res.status_code == 422
    assert "duplicate" in res.json()["detail"].lower()

    # Verify no database state was created
    count = (
        await test_db.execute(
            select(func.count(NotificationPreference.id)).where(NotificationPreference.user_id == user_id)
        )
    ).scalar()
    assert count == 0


# ==============================================================================
# 8. INVALID CHANNELS, CATEGORIES, AND MALFORMED PAYLOADS REJECTED
# ==============================================================================

async def test_invalid_channels_and_categories_rejected(
    client: AsyncClient,
    auth_headers_a: dict[str, str],
):
    """Req 8: Invalid channel/category values and malformed JSON payloads rejected with 422."""
    # Unsupported channel (e.g. sms)
    r1 = await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "sms", "category": "billing", "is_enabled": True}],
        headers=auth_headers_a,
    )
    assert r1.status_code == 422

    # Unsupported category
    r2 = await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "email", "category": "non_existent_category", "is_enabled": True}],
        headers=auth_headers_a,
    )
    assert r2.status_code == 422

    # Malformed is_enabled (not a boolean)
    r3 = await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "email", "category": "billing", "is_enabled": "not_a_bool"}],
        headers=auth_headers_a,
    )
    assert r3.status_code == 422

    # Malformed body structure (string instead of list/object)
    r4 = await client.put(
        "/api/v1/notifications/preferences",
        json="invalid_structure",
        headers=auth_headers_a,
    )
    assert r4.status_code == 422


# ==============================================================================
# 9. AUTHENTICATION & VERIFICATION GUARDS
# ==============================================================================

async def test_unauthenticated_inactive_and_unverified_policy(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Req 9: Unauthenticated (401), inactive (401), and unverified (403) requests rejected."""
    # 1. No Authorization header
    r_no_auth = await client.get("/api/v1/notifications/preferences")
    assert r_no_auth.status_code == 401

    r_no_auth_put = await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "email", "category": "billing", "is_enabled": False}],
    )
    assert r_no_auth_put.status_code == 401

    # 2. Inactive user
    inactive_user = User(
        id=690,
        email="inactive_wu64@example.com",
        name="Inactive WU6.4",
        password_hash="fake",
        role="advertiser",
        is_active=False,
        email_verified=True,
    )
    test_db.add(inactive_user)
    await test_db.commit()
    t_inactive = create_access_token(inactive_user)
    r_inactive = await client.get(
        "/api/v1/notifications/preferences",
        headers={"Authorization": f"Bearer {t_inactive}"},
    )
    assert r_inactive.status_code == 401

    # 3. Unverified email user
    unverified_user = User(
        id=691,
        email="unverified_wu64@example.com",
        name="Unverified WU6.4",
        password_hash="fake",
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add(unverified_user)
    await test_db.commit()
    t_unverified = create_access_token(unverified_user)
    r_unverified = await client.get(
        "/api/v1/notifications/preferences",
        headers={"Authorization": f"Bearer {t_unverified}"},
    )
    assert r_unverified.status_code == 403
    assert "verification" in r_unverified.json()["detail"].lower()


# ==============================================================================
# 10. TENANT ISOLATION
# ==============================================================================

async def test_cross_tenant_isolation_cannot_view_or_mutate_other_user_preferences(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    user_b: User,
    auth_headers_a: dict[str, str],
    auth_headers_b: dict[str, str],
):
    """Req 10: User A's preferences are completely isolated from User B."""
    # User A disables campaign emails
    await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "email", "category": "campaign", "is_enabled": False}],
        headers=auth_headers_a,
    )

    # User B queries their preferences; campaign email MUST still be True
    res_b = await client.get("/api/v1/notifications/preferences", headers=auth_headers_b)
    assert res_b.status_code == 200
    pref_b_map = {(p["channel"], p["category"]): p["is_enabled"] for p in res_b.json()["preferences"]}
    assert pref_b_map[("email", "campaign")] is True

    # User B disables billing email
    await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "email", "category": "billing", "is_enabled": False}],
        headers=auth_headers_b,
    )

    # User A queries their preferences; billing email remains True for User A
    res_a = await client.get("/api/v1/notifications/preferences", headers=auth_headers_a)
    assert res_a.status_code == 200
    pref_a_map = {(p["channel"], p["category"]): p["is_enabled"] for p in res_a.json()["preferences"]}
    assert pref_a_map[("email", "billing")] is True
    assert pref_a_map[("email", "campaign")] is False


# ==============================================================================
# 11. TRANSACTION ROLLBACK ON DATABASE FAILURE
# ==============================================================================

async def test_failed_batch_transaction_rolls_back_cleanly(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 11: A database error during batch commit leaves zero partial state."""
    user_id = user_a.id
    # Start with known state
    initial_count = (
        await test_db.execute(
            select(func.count(NotificationPreference.id)).where(NotificationPreference.user_id == user_id)
        )
    ).scalar()
    assert initial_count == 0

    payload = [
        {"channel": "email", "category": "billing", "is_enabled": False},
        {"channel": "email", "category": "campaign", "is_enabled": False},
    ]

    with patch.object(AsyncSession, "commit", side_effect=RuntimeError("Simulated DB commit error")):
        with pytest.raises(RuntimeError):
            await client.put("/api/v1/notifications/preferences", json=payload, headers=auth_headers_a)

    await test_db.rollback()
    # Confirm zero rows committed
    post_count = (
        await test_db.execute(
            select(func.count(NotificationPreference.id)).where(NotificationPreference.user_id == user_id)
        )
    ).scalar()
    assert post_count == 0


# ==============================================================================
# 12. MANDATORY SECURITY NOTIFICATIONS CANNOT BE DISABLED
# ==============================================================================

async def test_mandatory_security_notifications_cannot_be_disabled(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 12: Attempting to disable security notifications returns 422; GET always reports True."""
    user_id = user_a.id
    user_email = user_a.email

    # 1. Attempting to disable security category is rejected with HTTP 422
    res = await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "email", "category": "security", "is_enabled": False}],
        headers=auth_headers_a,
    )
    assert res.status_code == 422
    assert "security" in res.json()["detail"].lower()

    # 2. In GET preferences, security category is always True
    get_res = await client.get("/api/v1/notifications/preferences", headers=auth_headers_a)
    sec_prefs = [p for p in get_res.json()["preferences"] if p["category"] == "security"]
    for sp in sec_prefs:
        assert sp["is_enabled"] is True

    # 3. Direct dispatch test: even if a forced row existed, dispatcher NEVER suppresses security
    forced_pref = NotificationPreference(
        user_id=user_id,
        channel="email",
        category="security",
        is_enabled=False,
    )
    test_db.add(forced_pref)
    await test_db.commit()

    # Dispatch a security notification
    notif = await NotificationService.create_notification(
        session=test_db,
        user_id=user_id,
        category=NotificationCategory.SECURITY,
        type=NotificationType.SECURITY_ALERT,
        title="Security Alert",
        message="Important security alert",
        send_email=True,
    )
    await test_db.commit()

    # Outbox delivery job MUST be created despite the forced preference
    delivery = (
        await test_db.execute(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == notif.id)
        )
    ).scalars().first()
    assert delivery is not None
    assert delivery.recipient_email == user_email


# ==============================================================================
# 13. WU 6.2 OPTIONAL-EMAIL SUPPRESSION RESPECTS PERSISTED PREFERENCES
# ==============================================================================

async def test_optional_email_suppression_respects_persisted_preferences(
    client: AsyncClient,
    test_db: AsyncSession,
    user_a: User,
    auth_headers_a: dict[str, str],
):
    """Req 13: Optional category email is suppressed when user opts out, and delivered when enabled."""
    user_id = user_a.id
    user_email = user_a.email

    # User A opts out of campaign emails via the API
    put_res = await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "email", "category": "campaign", "is_enabled": False}],
        headers=auth_headers_a,
    )
    assert put_res.status_code == 200

    # 1. Dispatch campaign notification with send_email=True
    notif1 = await NotificationService.create_notification(
        session=test_db,
        user_id=user_id,
        category=NotificationCategory.CAMPAIGN,
        type=NotificationType.CAMPAIGN_ACTIVATED,
        title="Campaign Active",
        message="Your campaign is running",
        send_email=True,
    )
    await test_db.commit()

    # In-app notification created
    assert notif1.id is not None
    # Email delivery job MUST be suppressed
    delivery1 = (
        await test_db.execute(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == notif1.id)
        )
    ).scalars().first()
    assert delivery1 is None, "Email delivery should have been suppressed by user preference"

    # 2. User re-enables campaign emails
    put_res2 = await client.put(
        "/api/v1/notifications/preferences",
        json=[{"channel": "email", "category": "campaign", "is_enabled": True}],
        headers=auth_headers_a,
    )
    assert put_res2.status_code == 200

    # Dispatch another campaign notification
    notif2 = await NotificationService.create_notification(
        session=test_db,
        user_id=user_id,
        category=NotificationCategory.CAMPAIGN,
        type=NotificationType.CAMPAIGN_PAUSED,
        title="Campaign Paused",
        message="Your campaign is paused",
        send_email=True,
    )
    await test_db.commit()

    # Email delivery job MUST now be created
    delivery2 = (
        await test_db.execute(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == notif2.id)
        )
    ).scalars().first()
    assert delivery2 is not None
    assert delivery2.recipient_email == user_email
