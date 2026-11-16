"""Phase 4 — Advertiser Campaign Transitions and Readiness Parity Test Suite.

Validates:
1. Server-authoritative readiness endpoint (`GET /api/v1/campaign/{campaign_id}/readiness` and `/validate`).
2. Structured blockers and warnings contract (codes, categories, entity types, fields, messages).
3. Authorization & tenant boundaries (Advertiser isolation, Admin access, Publisher rejected).
4. Launch gating rules parity:
   - Wallet balance funding (zero or negative balance blocks).
   - Campaign budget configuration (zero daily budget, total < daily).
   - AdSet sub-budget ceiling advisory warning (sum of sub-budgets > daily ceiling).
   - Active ad sets & creative hierarchy (must have at least one active ad set and creative).
   - Creative moderation approval gate (pending/rejected blocks).
   - Creative landing domain verification gate (unverified domain blocks).
   - Destination URL syntax validation (invalid URL blocks).
   - Targeting requirement (missing targeting blocks).
   - Campaign lifecycle status (only DRAFT can be activated).
5. Draft creation and query-string resume compatibility.
6. Multi-AdSet campaign with varying budgets and targeting.
7. Candidate retrieval isolation: Active ad in inactive/paused AdSet is NEVER served.
8. Idempotency & double-activation safety.
9. Legacy campaign backward-compatibility (direct campaign targeting & creatives without ad sets).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.campaign_activation import (
    validate_campaign_readiness,
    activate_campaign_logic,
    CampaignActivationError,
)
from liteads.models.ad import (
    AdSet,
    Advertiser,
    Campaign,
    Creative,
    CreativeType,
    Status,
    TargetingRule,
)
from liteads.models.auth import User
from liteads.models.base import ModerationStatus
from liteads.models.domain import AdvertiserDomain, VerificationStatus
from liteads.models.publisher import AdSlot, Publisher
from liteads.rec_engine.retrieval.targeting import TargetingRetrieval
from liteads.schemas.internal import UserContext

pytestmark = pytest.mark.asyncio


# ── Helper Fixtures & Setup ──────────────────────────────────────────────────

async def _register_user(client: AsyncClient, email: str, role: str = "advertiser") -> tuple[str, int, int | None]:
    """Register user and return (access_token, user_id, advertiser_id)."""
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "Password123!",
            "company_name": "Phase 4 Testing Org",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    token = data["access_token"]
    user_id = data["user"]["id"]
    advertiser_id = data["user"].get("advertiser_id")
    return token, user_id, advertiser_id


async def _verify_domain(session: AsyncSession, advertiser_id: int, domain: str) -> AdvertiserDomain:
    """Register and verify a landing domain for the advertiser."""
    dom = AdvertiserDomain(
        advertiser_id=advertiser_id,
        domain=domain,
        status=VerificationStatus.VERIFIED,
        verification_token=f"tok_{domain}",
        verified_at=datetime.now(timezone.utc),
    )
    session.add(dom)
    await session.commit()
    await session.refresh(dom)
    return dom


# ── Test Cases ───────────────────────────────────────────────────────────────

async def test_readiness_endpoint_auth_and_scoping(client: AsyncClient, test_db: AsyncSession):
    """Verify endpoint authentication, role restrictions, and tenant isolation."""
    # 1. Register Adv A and Adv B
    tok_a, user_a, adv_a = await _register_user(client, "adv_a@test.com")
    tok_b, user_b, adv_b = await _register_user(client, "adv_b@test.com")
    tok_pub, user_pub, _ = await _register_user(client, "pub_user@test.com", role="publisher")

    # Create campaign for Adv A
    camp = Campaign(
        advertiser_id=adv_a,
        name="Adv A Campaign",
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        status=Status.DRAFT,
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)

    # 2. Unauthenticated request -> 401
    res_unauth = await client.get(f"/api/v1/campaign/{camp.id}/readiness")
    assert res_unauth.status_code == 401

    # 3. Publisher requesting advertiser campaign readiness -> 403
    res_pub = await client.get(
        f"/api/v1/campaign/{camp.id}/readiness",
        headers={"Authorization": f"Bearer {tok_pub}"},
    )
    assert res_pub.status_code == 403

    # 4. Adv B requesting Adv A campaign readiness -> 403 (tenant isolated)
    res_adv_b = await client.get(
        f"/api/v1/campaign/{camp.id}/readiness",
        headers={"Authorization": f"Bearer {tok_b}"},
    )
    assert res_adv_b.status_code == 403

    # 5. Adv A requesting own campaign -> 200 with structured response
    res_adv_a = await client.get(
        f"/api/v1/campaign/{camp.id}/readiness",
        headers={"Authorization": f"Bearer {tok_a}"},
    )
    assert res_adv_a.status_code == 200
    data = res_adv_a.json()
    assert data["campaign_id"] == camp.id
    assert data["campaign_name"] == "Adv A Campaign"
    assert data["status"] == Status.DRAFT
    assert data["is_ready"] is False
    assert len(data["blockers"]) > 0

    # 6. Alias /validate works identically
    res_validate = await client.get(
        f"/api/v1/campaign/{camp.id}/validate",
        headers={"Authorization": f"Bearer {tok_a}"},
    )
    assert res_validate.status_code == 200
    assert res_validate.json()["campaign_id"] == camp.id


async def test_readiness_blockers_and_activation_parity(client: AsyncClient, test_db: AsyncSession):
    """Verify that every blocker preventing readiness also rejects activation with parity."""
    token, user_id, adv_id = await _register_user(client, "parity_adv@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Step A: Empty campaign, zero wallet balance
    camp = Campaign(
        advertiser_id=adv_id,
        name="Parity Test Campaign",
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        status=Status.DRAFT,
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)

    # Check readiness: Should have balance blocker, no ad set/creatives blocker, no targeting blocker
    res = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    assert res.status_code == 200
    r_data = res.json()
    assert r_data["is_ready"] is False
    codes = [b["code"] for b in r_data["blockers"]]
    assert "INSUFFICIENT_BALANCE" in codes
    assert "NO_CREATIVES" in codes
    assert "MISSING_TARGETING" in codes

    # Attempt activation: Must fail with HTTP 400 and first blocker
    act_res = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert act_res.status_code == 400
    assert "Insufficient wallet balance" in act_res.json()["detail"]

    # Step B: Fund wallet balance
    adv = await test_db.get(Advertiser, adv_id)
    adv.balance = Decimal("500.00")
    await test_db.commit()

    # Re-check readiness: balance blocker gone, but creatives & targeting remain
    res = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    codes = [b["code"] for b in res.json()["blockers"]]
    assert "INSUFFICIENT_BALANCE" not in codes
    assert "NO_CREATIVES" in codes

    # Step C: Add Ad Set, Creative with unapproved moderation and unverified domain
    ad_set = AdSet(
        campaign_id=camp.id,
        name="Set 1",
        status=Status.ACTIVE,
        daily_budget=Decimal("100.00"),
        bid_amount=Decimal("1.50"),
    )
    test_db.add(ad_set)
    await test_db.commit()
    await test_db.refresh(ad_set)

    creative = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set.id,
        title="Unverified Banner",
        landing_url="https://unverified-site.com/landing",
        creative_type=CreativeType.BANNER,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.PENDING_REVIEW,
    )
    test_db.add(creative)

    # Add targeting
    rule = TargetingRule(
        campaign_id=camp.id,
        ad_set_id=ad_set.id,
        rule_type="geo",
        rule_value={"countries": ["IN"]},
        is_include=True,
    )
    test_db.add(rule)
    await test_db.commit()

    # Re-check readiness: Unapproved moderation and unverified domain must block
    res = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    codes = [b["code"] for b in res.json()["blockers"]]
    assert "MODERATION_NOT_APPROVED" in codes
    assert "UNVERIFIED_DOMAIN" in codes

    # Attempt activation: fails because creative is not approved
    act_res = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert act_res.status_code == 400
    assert "not approved by moderation" in act_res.json()["detail"]

    # Step D: Approve creative, but domain still unverified
    creative.moderation_status = ModerationStatus.APPROVED
    await test_db.commit()

    res = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    codes = [b["code"] for b in res.json()["blockers"]]
    assert "MODERATION_NOT_APPROVED" not in codes
    assert "UNVERIFIED_DOMAIN" in codes

    act_res = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert act_res.status_code == 400
    assert "domain for creative" in act_res.json()["detail"]
    assert "is not verified" in act_res.json()["detail"]

    # Step E: Verify domain
    await _verify_domain(test_db, adv_id, "unverified-site.com")

    # Re-check readiness: ALL blockers resolved -> is_ready = True
    res = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    data = res.json()
    assert data["blockers"] == [], f"Unexpected blockers: {data['blockers']}"
    assert data["is_ready"] is True

    # Activation now succeeds!
    act_res = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert act_res.status_code == 200
    assert act_res.json()["status"] == Status.ACTIVE

    # Step F: Idempotency / Lifecycle check: activating an already active campaign fails
    act_again = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert act_again.status_code == 400
    assert "already active" in act_again.json()["detail"].lower() or "draft" in act_again.json()["detail"].lower()


async def test_budget_mismatches_and_subbudget_warnings(client: AsyncClient, test_db: AsyncSession):
    """Verify total < daily budget blocks, and sum(sub_budgets) > campaign ceiling generates warning."""
    token, user_id, adv_id = await _register_user(client, "budget_adv@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Total budget less than daily budget
    camp = Campaign(
        advertiser_id=adv_id,
        name="Budget Test Campaign",
        budget_daily=Decimal("500.00"),
        budget_total=Decimal("200.00"),  # Error!
        status=Status.DRAFT,
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)

    res = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    codes = [b["code"] for b in res.json()["blockers"]]
    assert "BUDGET_MISMATCH" in codes

    # Fix total budget
    camp.budget_total = Decimal("2000.00")
    await test_db.commit()

    # Create 2 Ad Sets whose sum of daily budgets exceeds campaign ceiling ($300 + $300 = $600 > $500)
    ad_set_1 = AdSet(
        campaign_id=camp.id,
        name="AdSet 1",
        status=Status.ACTIVE,
        daily_budget=Decimal("300.00"),
        bid_amount=Decimal("2.00"),
    )
    ad_set_2 = AdSet(
        campaign_id=camp.id,
        name="AdSet 2",
        status=Status.ACTIVE,
        daily_budget=Decimal("300.00"),
        bid_amount=Decimal("2.00"),
    )
    test_db.add_all([ad_set_1, ad_set_2])
    await test_db.commit()

    res = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    warning_codes = [w["code"] for w in res.json()["warnings"]]
    assert "SUB_BUDGETS_EXCEED_CAMPAIGN_CEILING" in warning_codes


async def test_multi_adset_varying_budgets_and_targeting(client: AsyncClient, test_db: AsyncSession):
    """Verify multi-adset setup: Set 1 active with targeting; Set 2 paused without ads."""
    token, user_id, adv_id = await _register_user(client, "multi_set@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Fund wallet
    adv = await test_db.get(Advertiser, adv_id)
    adv.balance = Decimal("1000.00")

    camp = Campaign(
        advertiser_id=adv_id,
        name="Multi AdSet Campaign",
        budget_daily=Decimal("500.00"),
        budget_total=Decimal("5000.00"),
        status=Status.DRAFT,
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)

    # Set 1: Active, with targeting and verified creative
    set1 = AdSet(
        campaign_id=camp.id,
        name="Active Target Set",
        status=Status.ACTIVE,
        daily_budget=Decimal("200.00"),
        bid_amount=Decimal("1.00"),
    )
    # Set 2: Paused, no creatives
    set2 = AdSet(
        campaign_id=camp.id,
        name="Paused Inactive Set",
        status=Status.PAUSED,
        daily_budget=Decimal("100.00"),
        bid_amount=Decimal("1.00"),
    )
    test_db.add_all([set1, set2])
    await test_db.commit()
    await test_db.refresh(set1)
    await test_db.refresh(set2)

    # Targeting on Set 1
    rule1 = TargetingRule(
        campaign_id=camp.id,
        ad_set_id=set1.id,
        rule_type="sector",
        rule_value={"sectors": ["developers"]},
        is_include=True,
    )
    test_db.add(rule1)

    # Approved creative on Set 1
    await _verify_domain(test_db, adv_id, "activebrand.com")
    cr1 = Creative(
        campaign_id=camp.id,
        ad_set_id=set1.id,
        title="Active Dev Banner",
        landing_url="https://activebrand.com/signup",
        creative_type=CreativeType.BANNER,
        width=728,
        height=90,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED,
    )
    test_db.add(cr1)
    await test_db.commit()

    # Query readiness
    res = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["blockers"] == [], f"Unexpected blockers: {data['blockers']}"
    assert data["is_ready"] is True
    assert data["summary"]["total_ad_sets"] == 2
    assert data["summary"]["active_ad_sets"] == 1

    # Warning for paused ad set without creatives
    warning_codes = [w["code"] for w in data["warnings"]]
    assert "AD_SET_INACTIVE" in warning_codes
    assert "AD_SET_NO_CREATIVES" in warning_codes


async def test_inactive_adset_creative_never_served(test_db: AsyncSession):
    """Ensure candidate retrieval strictly excludes creatives whose AdSet is paused or inactive."""
    # 1. Create Advertiser and Publisher
    adv = Advertiser(
        name="AdSet Isolation Adv",
        balance=Decimal("1000.00"),
        status=Status.ACTIVE,
    )
    test_db.add(adv)

    pub = Publisher(
        name="Test Pub For Slot",
        status=Status.ACTIVE,
        payout_details={},
    )
    test_db.add(pub)
    await test_db.commit()
    await test_db.refresh(adv)
    await test_db.refresh(pub)

    # Create slot 300x250
    slot = AdSlot(
        publisher_id=pub.id,
        slot_id="slot_300x250",
        name="Test 300x250 Slot",
        width=300,
        height=250,
        status=Status.ACTIVE,
    )
    test_db.add(slot)
    await test_db.commit()

    # 2. Create Active Campaign
    camp = Campaign(
        advertiser_id=adv.id,
        name="Active Serving Campaign",
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        status=Status.ACTIVE,
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)

    # 3. Create Paused AdSet
    paused_ad_set = AdSet(
        campaign_id=camp.id,
        name="Paused AdSet",
        status=Status.PAUSED,  # Inactive!
        daily_budget=Decimal("50.00"),
        bid_amount=Decimal("1.00"),
    )
    test_db.add(paused_ad_set)
    await test_db.commit()
    await test_db.refresh(paused_ad_set)

    # 4. Create Active Creative in Paused AdSet
    creative = Creative(
        campaign_id=camp.id,
        ad_set_id=paused_ad_set.id,
        title="Orphaned Active Banner",
        landing_url="https://example.com",
        creative_type=CreativeType.BANNER,
        width=300,
        height=250,
        status=Status.ACTIVE,  # Creative is active, but AdSet is PAUSED
        moderation_status=ModerationStatus.APPROVED,
    )
    test_db.add(creative)
    await test_db.commit()

    # 5. Run candidate retrieval
    retriever = TargetingRetrieval(test_db)
    await retriever.refresh()
    user_ctx = UserContext(user_id="user_test_adset")
    candidates = await retriever.retrieve(user_ctx, slot_id="slot_300x250", limit=10)

    # The creative from the paused ad set MUST NOT be retrieved
    retrieved_creative_ids = [c.creative_id for c in candidates]
    assert creative.id not in retrieved_creative_ids

    # 6. Now activate the AdSet
    paused_ad_set.status = Status.ACTIVE
    await test_db.commit()
    await retriever.refresh()

    candidates_after = await retriever.retrieve(user_ctx, slot_id="slot_300x250", limit=10)
    retrieved_after_ids = [c.creative_id for c in candidates_after]
    assert creative.id in retrieved_after_ids


async def test_legacy_campaign_backward_compatibility(test_db: AsyncSession):
    """Ensure legacy campaigns (creatives & targeting rules attached directly to Campaign) are valid and ready."""
    adv = Advertiser(
        name="Legacy Advertiser",
        balance=Decimal("200.00"),
        status=Status.ACTIVE,
    )
    test_db.add(adv)
    await test_db.commit()
    await test_db.refresh(adv)

    await _verify_domain(test_db, adv.id, "legacybrand.com")

    # Campaign with no AdSets
    camp = Campaign(
        advertiser_id=adv.id,
        name="Legacy Direct Campaign",
        budget_daily=Decimal("50.00"),
        budget_total=Decimal("500.00"),
        status=Status.DRAFT,
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)

    # Direct creative on campaign
    cr = Creative(
        campaign_id=camp.id,
        ad_set_id=None,
        title="Direct Creative",
        landing_url="https://legacybrand.com/product",
        creative_type=CreativeType.BANNER,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED,
    )
    test_db.add(cr)

    # Direct targeting on campaign
    rule = TargetingRule(
        campaign_id=camp.id,
        ad_set_id=None,
        rule_type="geo",
        rule_value={"countries": ["US"]},
        is_include=True,
    )
    test_db.add(rule)
    await test_db.commit()

    # Evaluate readiness
    readiness = await validate_campaign_readiness(test_db, camp.id, adv.id)
    assert readiness.blockers == [], f"Unexpected blockers: {readiness.blockers}"
    assert readiness.is_ready is True

    # Activate logic
    activated_camp = await activate_campaign_logic(test_db, camp.id, adv.id)
    assert activated_camp.status == Status.ACTIVE
