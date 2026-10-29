"""Unit and integration tests for Phase 6.1C: Marketing Consent, Subscriber Models & List Synchronization.

Verifies:
- No automatic marketing enrollment from account creation or transactional emails.
- Explicit affirmative opt-in and revocation (opt-out) recording with timestamps.
- Opted-out addresses are never silently re-subscribed by retry or reconciliation.
- Public double opt-in subscription, anti-enumeration generic responses, and confirmation token expiry.
- Minimal attribute sync allowlist (name, role only).
- Listmonk-disabled graceful local operation without network requests.
- Tenant isolation and role-based access for reconciliation.
- Unsubscribe reconciliation reflecting Listmonk-side blocklists into local consent records.
- API endpoints for preferences, public subscription, and confirmation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.marketing_consent_service import MarketingConsentService
from liteads.common.config import ListmonkSettings, Settings
from liteads.common.listmonk_client import ListmonkClient
from liteads.common.security import create_access_token
from liteads.models.auth import User
from liteads.models.consent import (
    ConsentCategory,
    ConsentSource,
    ConsentStatus,
    MarketingConsent,
)
from liteads.schemas.marketing import MarketingPreferenceUpdateRequest, PublicSubscribeRequest


# ── 1. Consent Decoupling: No Automatic Opt-In ────────────────────────────────

@pytest.mark.asyncio
async def test_no_implicit_marketing_consent_for_new_user(test_db: AsyncSession):
    """Ensure a user has NO implicit marketing consent upon account creation."""
    user = User(
        name="Alice Advertiser",
        email="alice@brand.com",
        password_hash="hashed_pw",
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)

    service = MarketingConsentService()
    prefs = await service.get_user_preferences(session=test_db, user=user)

    assert prefs.email == "alice@brand.com"
    assert len(prefs.items) == len(ConsentCategory)
    for item in prefs.items:
        # Crucial acceptance test: all categories default to opted_in=False
        assert item.opted_in is False
        assert item.status == ConsentStatus.OPTED_OUT.value
        assert item.consented_at is None


# ── 2. Explicit Opt-In and Revocation (Opt-Out) ───────────────────────────────

@pytest.mark.asyncio
async def test_explicit_affirmative_opt_in(test_db: AsyncSession):
    """User affirmatively opts in: records consented_at and source='preferences'."""
    user = User(
        name="Bob Publisher",
        email="bob@media.org",
        password_hash="hashed_pw",
        role="publisher",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)

    service = MarketingConsentService()
    record = await service.update_user_preference(
        session=test_db,
        user=user,
        category=ConsentCategory.PUBLISHER_EDUCATION.value,
        opted_in=True,
        ip_address="198.51.100.4",
        user_agent="Mozilla/5.0 Test",
    )

    assert record.status == ConsentStatus.OPTED_IN.value
    assert record.consented_at is not None
    assert record.revoked_at is None
    assert record.source == ConsentSource.PREFERENCES.value
    assert record.ip_address == "198.51.100.4"

    # Query back via preferences endpoint
    prefs = await service.get_user_preferences(session=test_db, user=user)
    pub_pref = next(p for p in prefs.items if p.category == ConsentCategory.PUBLISHER_EDUCATION.value)
    assert pub_pref.opted_in is True
    assert pub_pref.status == ConsentStatus.OPTED_IN.value


@pytest.mark.asyncio
async def test_explicit_revocation_records_revoked_at(test_db: AsyncSession):
    """User revokes consent: sets status='opted_out' and records revoked_at."""
    user = User(
        name="Carol Advertiser",
        email="carol@growth.io",
        password_hash="hashed_pw",
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)

    service = MarketingConsentService()
    # 1. Opt in first
    await service.update_user_preference(
        session=test_db,
        user=user,
        category=ConsentCategory.PRODUCT_NEWSLETTER.value,
        opted_in=True,
    )

    # 2. Revoke consent
    record = await service.update_user_preference(
        session=test_db,
        user=user,
        category=ConsentCategory.PRODUCT_NEWSLETTER.value,
        opted_in=False,
    )

    assert record.status == ConsentStatus.OPTED_OUT.value
    assert record.revoked_at is not None

    prefs = await service.get_user_preferences(session=test_db, user=user)
    news_pref = next(p for p in prefs.items if p.category == ConsentCategory.PRODUCT_NEWSLETTER.value)
    assert news_pref.opted_in is False
    assert news_pref.status == ConsentStatus.OPTED_OUT.value


# ── 3. Retries Never Re-Subscribe Opted-Out Addresses ─────────────────────────

@pytest.mark.asyncio
async def test_sync_never_resubscribes_opted_out_address(test_db: AsyncSession):
    """Ensure sync_subscriber_to_listmonk NEVER adds an opted-out address to a list on retry."""
    user = User(
        name="Dave",
        email="dave@optout.com",
        password_hash="hashed_pw",
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)

    mock_client = AsyncMock(spec=ListmonkClient)
    # Listmonk returns an existing subscriber in list 10
    mock_client.get_subscriber_by_email.return_value = {
        "id": 88,
        "email": "dave@optout.com",
        "lists": [{"id": 10}],
        "status": "enabled",
    }
    mock_client.update_subscriber.return_value = {"id": 88, "status": "blocklisted", "lists": []}

    settings = ListmonkSettings(
        enabled=True,
        url="http://localhost:9000",
        user="admin",
        token="token",
        list_newsletter_id=10,
    )

    service = MarketingConsentService(client=mock_client)

    consent = MarketingConsent(
        user_id=user.id,
        email=user.email,
        category=ConsentCategory.PRODUCT_NEWSLETTER.value,
        status=ConsentStatus.OPTED_OUT.value,
        revoked_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    test_db.add(consent)
    await test_db.commit()
    await test_db.refresh(consent)

    with patch("liteads.ad_server.services.marketing_consent_service.get_settings") as mock_get_settings:
        mock_cfg = MagicMock()
        mock_cfg.listmonk = settings
        mock_get_settings.return_value = mock_cfg

        # Run sync on an opted-out consent record
        success = await service.sync_subscriber_to_listmonk(session=test_db, consent=consent)

    assert success is True
    # Verify update_subscriber was called to REMOVE list 10, NOT add it!
    mock_client.update_subscriber.assert_awaited_once_with(
        subscriber_id=88,
        list_ids=[],
        status="blocklisted",
    )
    # Create subscriber must NOT be called
    mock_client.create_subscriber.assert_not_awaited()


# ── 4. Public Double Opt-In Subscription ──────────────────────────────────────

@pytest.mark.asyncio
async def test_public_subscribe_anti_enumeration_generic_response(test_db: AsyncSession):
    """Verify public subscribe returns generic response and stores pending_confirmation."""
    service = MarketingConsentService()

    success, message = await service.public_subscribe(
        session=test_db,
        email="public.reader@example.com",
        name="Public Reader",
        category="product_newsletter",
    )

    assert success is True
    assert "confirmation link has been sent" in message

    # Check database state: status must be PENDING_CONFIRMATION
    stmt = select(MarketingConsent).where(MarketingConsent.email == "public.reader@example.com")
    record = await test_db.scalar(stmt)
    assert record is not None
    assert record.status == ConsentStatus.PENDING_CONFIRMATION.value
    assert record.confirmation_token is not None
    assert record.confirmation_token_expires_at is not None
    assert record.consented_at is None  # Not consented until confirmed!


@pytest.mark.asyncio
async def test_public_confirm_activates_subscription(test_db: AsyncSession):
    """Clicking confirm link validates token and transitions status to opted_in."""
    service = MarketingConsentService()

    await service.public_subscribe(
        session=test_db,
        email="confirm.me@example.com",
        category="product_newsletter",
    )

    stmt = select(MarketingConsent).where(MarketingConsent.email == "confirm.me@example.com")
    record = await test_db.scalar(stmt)
    token = record.confirmation_token

    # Confirm using the token
    success, message = await service.public_confirm(session=test_db, token=token)
    assert success is True
    assert "confirmed successfully" in message

    await test_db.refresh(record)
    assert record.status == ConsentStatus.OPTED_IN.value
    assert record.confirmed_at is not None
    assert record.consented_at is not None
    assert record.confirmation_token is None  # Token consumed


@pytest.mark.asyncio
async def test_public_confirm_invalid_or_expired_token(test_db: AsyncSession):
    """Expired or invalid token fails safely."""
    service = MarketingConsentService()

    # Expired token record
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    record = MarketingConsent(
        email="expired@example.com",
        category="product_newsletter",
        status=ConsentStatus.PENDING_CONFIRMATION.value,
        confirmation_token="expired_token_12345678",
        confirmation_token_expires_at=past,
    )
    test_db.add(record)
    await test_db.commit()

    success, message = await service.public_confirm(session=test_db, token="expired_token_12345678")
    assert success is False
    assert "expired" in message.lower()


# ── 5. Data Minimization Allowlist ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_uses_minimal_allowlist_attributes_only(test_db: AsyncSession):
    """Ensure synchronization sends only allowlisted attributes (role) to Listmonk."""
    mock_client = AsyncMock(spec=ListmonkClient)
    mock_client.get_subscriber_by_email.return_value = None
    mock_client.create_subscriber.return_value = {"id": 101}

    settings = ListmonkSettings(
        enabled=True,
        url="http://localhost:9000",
        user="admin",
        token="token",
        list_newsletter_id=5,
    )

    service = MarketingConsentService(client=mock_client)

    consent = MarketingConsent(
        email="minimal@brand.com",
        category=ConsentCategory.PRODUCT_NEWSLETTER.value,
        status=ConsentStatus.OPTED_IN.value,
        consented_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    test_db.add(consent)
    await test_db.commit()
    await test_db.refresh(consent)

    with patch("liteads.ad_server.services.marketing_consent_service.get_settings") as mock_get_settings:
        mock_cfg = MagicMock()
        mock_cfg.listmonk = settings
        mock_get_settings.return_value = mock_cfg

        await service.sync_subscriber_to_listmonk(
            session=test_db,
            consent=consent,
            name="Minimal User",
            role="advertiser",
        )

    # Verify create_subscriber call parameters: only role attribute allowed
    mock_client.create_subscriber.assert_awaited_once_with(
        email="minimal@brand.com",
        name="Minimal User",
        list_ids=[5],
        attribs={"role": "advertiser"},
        preconfirm=True,
    )


# ── 6. Listmonk Disabled Path ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_listmonk_disabled_operates_cleanly_without_network_calls(test_db: AsyncSession):
    """When Listmonk is disabled, local consent tracking works with zero external HTTP calls."""
    user = User(
        name="Offline User",
        email="offline@test.local",
        password_hash="hashed_pw",
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)

    mock_client = AsyncMock(spec=ListmonkClient)
    service = MarketingConsentService(client=mock_client)

    # With default settings (listmonk.enabled=False)
    record = await service.update_user_preference(
        session=test_db,
        user=user,
        category=ConsentCategory.ADVERTISER_EDUCATION.value,
        opted_in=True,
    )

    assert record.status == ConsentStatus.OPTED_IN.value
    # No calls made to Listmonk client
    mock_client.get_subscriber_by_email.assert_not_awaited()
    mock_client.create_subscriber.assert_not_awaited()
    mock_client.update_subscriber.assert_not_awaited()


# ── 7. Unsubscribe Reconciliation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unsubscribe_reconciliation_reflects_blocklisted_subscribers(test_db: AsyncSession):
    """Reconciliation job reads Listmonk blocklisted status and revokes local consent."""
    mock_client = AsyncMock(spec=ListmonkClient)
    # Listmonk reports blocklisted user
    mock_client.get_subscribers.return_value = [
        {"id": 42, "email": "opted_out_via_email@domain.com", "status": "blocklisted"}
    ]

    settings = ListmonkSettings(
        enabled=True,
        url="http://localhost:9000",
        user="admin",
        token="token",
    )

    service = MarketingConsentService(client=mock_client)

    # Locally user was opted_in
    consent = MarketingConsent(
        email="opted_out_via_email@domain.com",
        category=ConsentCategory.PRODUCT_NEWSLETTER.value,
        status=ConsentStatus.OPTED_IN.value,
        consented_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5),
    )
    test_db.add(consent)
    await test_db.commit()
    await test_db.refresh(consent)

    with patch("liteads.ad_server.services.marketing_consent_service.get_settings") as mock_get_settings:
        mock_cfg = MagicMock()
        mock_cfg.listmonk = settings
        mock_get_settings.return_value = mock_cfg

        result = await service.reconcile_unsubscribes(session=test_db)

    assert result.checked_count == 1
    assert result.updated_opt_outs == 1

    await test_db.refresh(consent)
    assert consent.status == ConsentStatus.OPTED_OUT.value
    assert consent.revoked_at is not None
    assert consent.source == ConsentSource.LISTMONK_RECONCILIATION.value


# ── 8. API Endpoint Tests (Tenant Isolation & Public Endpoints) ───────────────

@pytest.mark.asyncio
async def test_api_user_preferences_lifecycle(client: AsyncClient, test_db: AsyncSession):
    """Verify GET and PUT /api/v1/marketing/preferences with authenticated JWT."""
    user = User(
        name="Eve User",
        email="eve@tenant.com",
        password_hash="pwd",
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)

    token = create_access_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET preferences
    get_res = await client.get("/api/v1/marketing/preferences", headers=headers)
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["email"] == "eve@tenant.com"
    assert all(item["opted_in"] is False for item in data["items"])

    # 2. PUT preference: opt in to advertiser_education
    put_res = await client.put(
        "/api/v1/marketing/preferences",
        headers=headers,
        json={"category": "advertiser_education", "opted_in": True},
    )
    assert put_res.status_code == 200
    updated_data = put_res.json()
    adv_item = next(i for i in updated_data["items"] if i["category"] == "advertiser_education")
    assert adv_item["opted_in"] is True
    assert adv_item["status"] == "opted_in"

    # 3. PUT preference: opt out of advertiser_education
    optout_res = await client.put(
        "/api/v1/marketing/preferences",
        headers=headers,
        json={"category": "advertiser_education", "opted_in": False},
    )
    assert optout_res.status_code == 200
    final_data = optout_res.json()
    adv_item_final = next(i for i in final_data["items"] if i["category"] == "advertiser_education")
    assert adv_item_final["opted_in"] is False
    assert adv_item_final["status"] == "opted_out"


@pytest.mark.asyncio
async def test_api_public_subscribe_and_confirm_endpoints(client: AsyncClient, test_db: AsyncSession):
    """Verify public subscribe and confirm endpoints without authentication."""
    # 1. Public subscribe
    sub_res = await client.post(
        "/api/v1/marketing/public/subscribe",
        json={"email": "reader@news.org", "category": "product_newsletter"},
    )
    assert sub_res.status_code == 200
    assert sub_res.json()["status"] == "received"

    # Fetch token from DB
    stmt = select(MarketingConsent).where(MarketingConsent.email == "reader@news.org")
    record = await test_db.scalar(stmt)
    assert record is not None
    token = record.confirmation_token

    # 2. Public confirm GET
    confirm_res = await client.get(f"/api/v1/marketing/public/confirm?token={token}")
    assert confirm_res.status_code == 200
    assert "confirmed" in confirm_res.json()["message"].lower()

    # 3. Invalid token returns 400
    bad_res = await client.get("/api/v1/marketing/public/confirm?token=invalid_token_1234567")
    assert bad_res.status_code == 400


@pytest.mark.asyncio
async def test_api_reconcile_requires_admin(client: AsyncClient, test_db: AsyncSession):
    """Verify /api/v1/marketing/reconcile rejects non-admin users."""
    user = User(
        name="Advertiser User",
        email="adv@tenant.com",
        password_hash="pwd",
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)

    token = create_access_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post("/api/v1/marketing/reconcile", headers=headers)
    assert res.status_code == 403  # Forbidden: require_roles('admin')
