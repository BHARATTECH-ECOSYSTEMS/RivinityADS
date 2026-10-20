"""
Unit and integration tests for Operational Gatekeeping via verified_user (Step 4).

Covers:
- verified_user dependency behavior:
  - allows active, email_verified=True users.
  - rejects active, unverified users with HTTP 403 ("Email verification required").
  - rejects inactive users with HTTP 401 ("User is inactive or missing").
- Authentication error preservation:
  - missing credentials -> 401
  - invalid / expired tokens -> 401
- Access control across route categories:
  - Operational advertiser routes reject unverified users with 403
  - Operational publisher routes reject unverified users with 403
  - Finance routes reject unverified users with 403
  - Webhooks and Chat routes reject unverified users with 403
  - Moderation routes reject unverified admins with 403
- Verification unlock:
  - Registration creates unverified user who is blocked from operational routes
  - Calling verify-email unlocks operational routes immediately using existing session/token
- Session & Refresh token behavior:
  - Refreshing tokens does not bypass verified_user gate for unverified accounts
- Onboarding & Public routes:
  - /auth/me allows unverified users (returns email_verified=False)
  - Public ad serving, health, and monitoring endpoints remain open
  - Reference data endpoints remain accessible to authenticated users
- Tenant isolation & role checks:
  - Role restrictions (require_roles) and cross-tenant boundaries remain enforced for verified users
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from httpx import AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.security import create_access_token, verified_user
from liteads.models import Advertiser, Campaign, OneTimeToken, Publisher, User


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 1. Unit tests for verified_user dependency
# ---------------------------------------------------------------------------

async def test_verified_user_dependency_allows_active_verified_user():
    user = User(id=1, email="user@example.com", is_active=True, email_verified=True, role="advertiser")
    result = await verified_user(user=user)
    assert result is user


async def test_verified_user_dependency_rejects_unverified_user():
    user = User(id=2, email="unverified@example.com", is_active=True, email_verified=False, role="advertiser")
    with pytest.raises(HTTPException) as exc_info:
        await verified_user(user=user)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Email verification required"


async def test_verified_user_dependency_rejects_inactive_user():
    # Inactive even if email_verified is True
    user = User(id=3, email="inactive@example.com", is_active=False, email_verified=True, role="advertiser")
    with pytest.raises(HTTPException) as exc_info:
        await verified_user(user=user)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "User is inactive or missing"


# ---------------------------------------------------------------------------
# 2. Authentication error preservation
# ---------------------------------------------------------------------------

async def test_unauthenticated_requests_preserve_401(client: AsyncClient):
    # Missing credentials
    resp1 = await client.get("/api/v1/campaign")
    assert resp1.status_code == 401
    assert "Authentication required" in resp1.json()["detail"]

    # Invalid token
    resp2 = await client.get("/api/v1/campaign", headers={"Authorization": "Bearer invalid.jwt.token"})
    assert resp2.status_code == 401
    assert "Invalid or expired token" in resp2.json()["detail"]


async def test_inactive_user_token_preserves_401(client: AsyncClient, test_db: AsyncSession):
    inactive_user = User(
        name="Inactive User",
        email="inactive_user_test@example.com",
        password_hash="hash",
        role="advertiser",
        is_active=False,
        email_verified=True,
    )
    test_db.add(inactive_user)
    await test_db.commit()

    token = create_access_token(inactive_user)
    resp = await client.get("/api/v1/campaign", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "User is inactive or missing"


# ---------------------------------------------------------------------------
# 3. /auth/me allows unverified users (onboarding check)
# ---------------------------------------------------------------------------

async def test_unverified_user_can_access_auth_me(client: AsyncClient, test_db: AsyncSession):
    user = User(
        name="Unverified Explorer",
        email="unverified_explorer@example.com",
        password_hash="hash",
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add(user)
    await test_db.commit()

    token = create_access_token(user)
    resp = await client.post("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "unverified_explorer@example.com"
    assert data["email_verified"] is False


# ---------------------------------------------------------------------------
# 4. Operational routes reject unverified advertiser
# ---------------------------------------------------------------------------

async def test_operational_routes_reject_unverified_advertiser(client: AsyncClient, test_db: AsyncSession):
    adv_user = User(
        name="Unverified Adv",
        email="unverified_adv_op@example.com",
        password_hash="hash",
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add(adv_user)
    await test_db.flush()

    adv = Advertiser(name="Adv Op Corp", owner_user_id=adv_user.id, balance=Decimal("500.00"))
    test_db.add(adv)
    await test_db.flush()
    adv_user.advertiser_id = adv.id
    await test_db.commit()

    headers = {"Authorization": f"Bearer {create_access_token(adv_user)}"}

    # 1. Campaign listing
    r_camp_list = await client.get("/api/v1/campaign", headers=headers)
    assert r_camp_list.status_code == 403
    assert r_camp_list.json()["detail"] == "Email verification required"

    # 2. Campaign creation
    r_camp_create = await client.post(
        "/api/v1/campaign",
        json={
            "name": "New Campaign",
            "advertiser_id": adv.id,
            "budget_daily": 50.0,
            "budget_total": 500.0,
            "bid_amount": 1.0,
            "bid_type": 1,
            "status": 1,
        },
        headers=headers,
    )
    assert r_camp_create.status_code == 403
    assert r_camp_create.json()["detail"] == "Email verification required"

    # 3. Creative listing
    r_creative_list = await client.get("/api/v1/creative", headers=headers)
    assert r_creative_list.status_code == 403
    assert r_creative_list.json()["detail"] == "Email verification required"

    # 4. Creative creation
    r_creative_create = await client.post(
        "/api/v1/creative",
        json={"campaign_id": 1, "title": "Creative 1", "landing_url": "https://example.com"},
        headers=headers,
    )
    assert r_creative_create.status_code == 403
    assert r_creative_create.json()["detail"] == "Email verification required"

    # 5. Targeting endpoint
    r_targeting = await client.get("/api/v1/campaign/1/targeting", headers=headers)
    assert r_targeting.status_code == 403
    assert r_targeting.json()["detail"] == "Email verification required"

    # 6. Domain endpoint
    r_domain = await client.get("/api/v1/domain", headers=headers)
    assert r_domain.status_code == 403
    assert r_domain.json()["detail"] == "Email verification required"

    # 7. Finance ledger
    r_finance_ledger = await client.get(f"/api/v1/finance/advertiser/{adv.id}/ledger", headers=headers)
    assert r_finance_ledger.status_code == 403
    assert r_finance_ledger.json()["detail"] == "Email verification required"

    # 8. Finance payment request
    r_finance_pay = await client.post(
        f"/api/v1/finance/advertiser/{adv.id}/payment-request",
        json={"amount": 100.0},
        headers=headers,
    )
    assert r_finance_pay.status_code == 403
    assert r_finance_pay.json()["detail"] == "Email verification required"

    # 9. Webhooks
    r_webhooks = await client.get("/api/v1/webhooks", headers=headers)
    assert r_webhooks.status_code == 403
    assert r_webhooks.json()["detail"] == "Email verification required"

    # 10. Chat session
    r_chat = await client.post("/api/v1/chat/session", json={}, headers=headers)
    assert r_chat.status_code == 403
    assert r_chat.json()["detail"] == "Email verification required"


# ---------------------------------------------------------------------------
# 5. Operational routes reject unverified publisher
# ---------------------------------------------------------------------------

async def test_operational_routes_reject_unverified_publisher(client: AsyncClient, test_db: AsyncSession):
    pub_user = User(
        name="Unverified Pub",
        email="unverified_pub_op@example.com",
        password_hash="hash",
        role="publisher",
        is_active=True,
        email_verified=False,
    )
    test_db.add(pub_user)
    await test_db.flush()

    pub = Publisher(name="Pub Op Media", email=pub_user.email, owner_user_id=pub_user.id, balance=Decimal("150.00"))
    test_db.add(pub)
    await test_db.flush()
    pub_user.publisher_id = pub.id
    await test_db.commit()

    headers = {"Authorization": f"Bearer {create_access_token(pub_user)}"}

    # 1. Slot listing
    r_slot = await client.get("/api/v1/slot", headers=headers)
    assert r_slot.status_code == 403
    assert r_slot.json()["detail"] == "Email verification required"

    # 2. Publisher API keys
    r_keys = await client.get(f"/api/v1/publisher/{pub.id}/keys", headers=headers)
    assert r_keys.status_code == 403
    assert r_keys.json()["detail"] == "Email verification required"

    # 3. Publisher reporting
    r_report = await client.get(f"/api/v1/publisher/{pub.id}/reporting", headers=headers)
    assert r_report.status_code == 403
    assert r_report.json()["detail"] == "Email verification required"

    # 4. Publisher placement health
    r_health = await client.get(f"/api/v1/publisher/{pub.id}/placements/health", headers=headers)
    assert r_health.status_code == 403
    assert r_health.json()["detail"] == "Email verification required"

    # 5. Publisher finance ledger
    r_ledger = await client.get(f"/api/v1/finance/publisher/{pub.id}/ledger", headers=headers)
    assert r_ledger.status_code == 403
    assert r_ledger.json()["detail"] == "Email verification required"

    # 6. Publisher payout request
    r_payout = await client.post(
        f"/api/v1/finance/publisher/{pub.id}/payout",
        json={"amount": 50.0, "reference_id": "payout_req_1"},
        headers=headers,
    )
    assert r_payout.status_code == 403
    assert r_payout.json()["detail"] == "Email verification required"


# ---------------------------------------------------------------------------
# 6. Operational routes reject unverified admin
# ---------------------------------------------------------------------------

async def test_operational_routes_reject_unverified_admin(client: AsyncClient, test_db: AsyncSession):
    admin_user = User(
        name="Unverified Admin",
        email="unverified_admin_op@example.com",
        password_hash="hash",
        role="admin",
        is_active=True,
        email_verified=False,
    )
    test_db.add(admin_user)
    await test_db.commit()

    headers = {"Authorization": f"Bearer {create_access_token(admin_user)}"}

    # Advertiser management
    r_adv = await client.get("/api/v1/advertiser", headers=headers)
    assert r_adv.status_code == 403
    assert r_adv.json()["detail"] == "Email verification required"

    # Moderation pending queue
    r_mod = await client.get("/api/v1/moderation/creatives/pending", headers=headers)
    assert r_mod.status_code == 403
    assert r_mod.json()["detail"] == "Email verification required"


# ---------------------------------------------------------------------------
# 7. Verified users can access operational routes
# ---------------------------------------------------------------------------

async def test_verified_users_can_access_operational_routes(client: AsyncClient, test_db: AsyncSession):
    adv_user = User(
        name="Verified Adv",
        email="verified_adv_ok@example.com",
        password_hash="hash",
        role="advertiser",
        is_active=True,
        email_verified=True,
    )
    test_db.add(adv_user)
    await test_db.flush()

    adv = Advertiser(name="Verified Adv Corp", owner_user_id=adv_user.id, balance=Decimal("100.00"))
    test_db.add(adv)
    await test_db.flush()
    adv_user.advertiser_id = adv.id
    await test_db.commit()

    headers = {"Authorization": f"Bearer {create_access_token(adv_user)}"}

    # Campaign list succeeds
    r_camp = await client.get("/api/v1/campaign", headers=headers)
    assert r_camp.status_code == 200
    assert isinstance(r_camp.json(), list)

    # Finance ledger succeeds
    r_ledger = await client.get(f"/api/v1/finance/advertiser/{adv.id}/ledger", headers=headers)
    assert r_ledger.status_code == 200
    assert "balance" in r_ledger.json()


# ---------------------------------------------------------------------------
# 8. Full registration -> verification workflow unlocks routes immediately
# ---------------------------------------------------------------------------

async def test_full_onboarding_and_verification_flow(client: AsyncClient, test_db: AsyncSession):
    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock) as mock_send:
        # Step 1: Register
        reg_resp = await client.post(
            "/api/v1/auth/register",
            json={
                "name": "Flow Advertiser",
                "email": "unverified_flow_user@example.com",
                "password": "Password123!",
                "role": "advertiser",
            },
        )
        assert reg_resp.status_code == 201
        data = reg_resp.json()
        token = data["access_token"]
        assert data["user"]["email_verified"] is False
        headers = {"Authorization": f"Bearer {token}"}

        # Step 2: Operational route blocked before verification
        blocked_resp = await client.get("/api/v1/campaign", headers=headers)
        assert blocked_resp.status_code == 403
        assert blocked_resp.json()["detail"] == "Email verification required"

        # Step 3: Retrieve generated token from mock email call
        assert mock_send.call_count == 1
        body_text = mock_send.call_args[1]["text_content"]
        # Extract token from the verification url
        raw_token = body_text.split("token=")[1].split()[0]

        # Step 4: Verify email
        verify_resp = await client.post("/api/v1/auth/verify-email", json={"token": raw_token})
        assert verify_resp.status_code == 200
        assert verify_resp.json()["message"] == "Email verified"

        # Step 5: Operational route immediately accessible with SAME initial token
        success_resp = await client.get("/api/v1/campaign", headers=headers)
        assert success_resp.status_code == 200
        assert isinstance(success_resp.json(), list)


# ---------------------------------------------------------------------------
# 9. Token refresh does not bypass verification gate for unverified accounts
# ---------------------------------------------------------------------------

async def test_token_refresh_does_not_bypass_verification(client: AsyncClient, test_db: AsyncSession):
    with patch("liteads.ad_server.routers.auth.send_email", new_callable=AsyncMock):
        # Register
        reg_resp = await client.post(
            "/api/v1/auth/register",
            json={
                "name": "Refresh Advertiser",
                "email": "unverified_refresh_user@example.com",
                "password": "Password123!",
                "role": "advertiser",
            },
        )
        assert reg_resp.status_code == 201

        # Use cookie to refresh access token
        cookies = {"refresh_token": reg_resp.cookies.get("refresh_token")}
        refresh_resp = await client.post("/api/v1/auth/refresh", cookies=cookies)
        assert refresh_resp.status_code == 200
        refreshed_access_token = refresh_resp.json()["access_token"]

        # Refreshed token is still rejected by operational routes
        refreshed_headers = {"Authorization": f"Bearer {refreshed_access_token}"}
        op_resp = await client.get("/api/v1/campaign", headers=refreshed_headers)
        assert op_resp.status_code == 403
        assert op_resp.json()["detail"] == "Email verification required"


# ---------------------------------------------------------------------------
# 10. Public and reference routes remain accessible
# ---------------------------------------------------------------------------

async def test_public_and_reference_routes_accessibility(client: AsyncClient, test_db: AsyncSession):
    # Public health check
    health_resp = await client.get("/health")
    assert health_resp.status_code == 200

    ping_resp = await client.get("/ping")
    assert ping_resp.status_code == 200

    # Unverified user can access reference metadata
    unverified_user = User(
        name="Ref User",
        email="unverified_ref@example.com",
        password_hash="hash",
        role="advertiser",
        is_active=True,
        email_verified=False,
    )
    test_db.add(unverified_user)
    await test_db.commit()

    headers = {"Authorization": f"Bearer {create_access_token(unverified_user)}"}
    ref_resp = await client.get("/api/v1/reference/sectors", headers=headers)
    assert ref_resp.status_code == 200
    assert isinstance(ref_resp.json(), list)


# ---------------------------------------------------------------------------
# 11. Tenant isolation and role restrictions remain enforced
# ---------------------------------------------------------------------------

async def test_tenant_isolation_and_role_checks_remain_enforced(client: AsyncClient, test_db: AsyncSession):
    # Setup Advertiser A (verified)
    user_a = User(name="Adv A", email="verified_a@test.com", password_hash="hash", role="advertiser", email_verified=True)
    test_db.add(user_a)
    await test_db.flush()
    adv_a = Advertiser(name="Adv A Corp", owner_user_id=user_a.id, balance=Decimal("100.00"))
    test_db.add(adv_a)
    await test_db.flush()
    user_a.advertiser_id = adv_a.id

    # Setup Advertiser B (verified)
    user_b = User(name="Adv B", email="verified_b@test.com", password_hash="hash", role="advertiser", email_verified=True)
    test_db.add(user_b)
    await test_db.flush()
    adv_b = Advertiser(name="Adv B Corp", owner_user_id=user_b.id, balance=Decimal("100.00"))
    test_db.add(adv_b)
    await test_db.flush()
    user_b.advertiser_id = adv_b.id

    # Setup Campaign for A
    camp_a = Campaign(
        advertiser_id=adv_a.id,
        name="Campaign A",
        budget_daily=Decimal("10.00"),
        budget_total=Decimal("100.00"),
        bid_amount=Decimal("1.00"),
        status=1,
    )
    test_db.add(camp_a)
    await test_db.commit()

    headers_b = {"Authorization": f"Bearer {create_access_token(user_b)}"}

    # Advertiser B attempts to access Advertiser A's campaign
    cross_resp = await client.get(f"/api/v1/campaign/{camp_a.id}", headers=headers_b)
    assert cross_resp.status_code in (403, 404)

    # Advertiser B attempts to access Admin route -> 403 Insufficient permissions
    admin_resp = await client.get("/api/v1/advertiser", headers=headers_b)
    assert admin_resp.status_code == 403
    assert admin_resp.json()["detail"] == "Insufficient permissions"
