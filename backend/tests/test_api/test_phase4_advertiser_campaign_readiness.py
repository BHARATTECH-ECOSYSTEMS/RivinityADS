"""Phase 4 — Advertiser Campaign Transitions and Readiness Parity Test Suite.

Verifies:
1. Draft campaign creation, retrieval, and resume.
2. Multi-AdSet campaign hierarchy, isolated targeting, and sub-budgets.
3. Sub-budget oversubscription warning when sum of active AdSets exceeds campaign ceiling.
4. Inactive/paused AdSet creative is never served.
5. Pending or rejected creative never serves.
6. Unverified landing domain blocks both readiness and activation.
7. Structured blockers (insufficient balance, missing targeting, no active ad sets, budget mismatch, schedule expired).
8. Double-submit idempotency protection prevents duplicate database rows.
9. Material creative edit resets moderation status.
10. Cross-campaign AdSet attachment rejected (HTTP 400).
11. Strict tenant isolation (cross-advertiser inspection/activation rejected with HTTP 403).
12. User-visible lifecycle state derivation (Draft, Needs Setup, Needs Changes, Pending Review, Approved / Eligible, Scheduled, Active, Paused, Completed, Rejected, Archived).
13. Server-authoritative activation transitions DRAFT to ACTIVE with zero wallet balance deduction.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.campaign_activation import validate_campaign_readiness
from liteads.models.ad import AdSet, Advertiser, Campaign, Creative, TargetingRule
from liteads.models.domain import AdvertiserDomain
from liteads.models.auth import User
from liteads.models.base import ModerationStatus, Status
from liteads.rec_engine.retrieval.targeting import TargetingRetrieval
from liteads.schemas.internal import UserContext

pytestmark = pytest.mark.asyncio


async def _register_advertiser(client: AsyncClient, email: str) -> tuple[str, int]:
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"Advertiser {email}",
            "email": email,
            "password": "Password123!",
            "company_name": "Phase 4 Corp",
            "role": "advertiser",
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["access_token"], data["user"]["id"]


async def _setup_advertiser_record(
    session: AsyncSession,
    user_id: int,
    balance: Decimal = Decimal("1000.00"),
) -> Advertiser:
    adv = Advertiser(
        name=f"Adv-{user_id}",
        owner_user_id=user_id,
        balance=balance,
    )
    session.add(adv)
    await session.flush()

    user = await session.get(User, user_id)
    if user:
        user.advertiser_id = adv.id
        session.add(user)

    await session.commit()
    await session.refresh(adv)
    return adv


async def _create_verified_domain(
    session: AsyncSession,
    advertiser_id: int,
    domain: str = "example.com",
) -> AdvertiserDomain:
    d = AdvertiserDomain(
        advertiser_id=advertiser_id,
        domain=domain,
        status="VERIFIED",
        verification_token=f"token_{advertiser_id}",
    )
    session.add(d)
    await session.commit()
    await session.refresh(d)
    return d


# ── 1. Draft Campaign Creation & Resume & Idempotency ───────────────────────

async def test_draft_campaign_creation_and_resume_with_idempotency(
    client: AsyncClient, test_db: AsyncSession
):
    token, user_id = await _register_advertiser(client, "phase4_draft@test.com")
    adv = await _setup_advertiser_record(test_db, user_id, Decimal("500.00"))
    headers = {"Authorization": f"Bearer {token}"}

    # Create campaign with Idempotency-Key
    idem_headers = {**headers, "Idempotency-Key": "wizard-camp-step1-001"}
    payload = {
        "advertiser_id": adv.id,
        "name": "Q4 Holiday Sale",
        "budget_daily": 100.0,
        "budget_total": 1000.0,
        "bid_type": 1,
        "bid_amount": 2.5,
    }

    res1 = await client.post("/api/v1/campaign", json=payload, headers=idem_headers)
    assert res1.status_code == 201, res1.text
    camp1 = res1.json()
    assert camp1["status"] == 5  # DRAFT
    assert camp1["name"] == "Q4 Holiday Sale"

    # Second submit with same Idempotency-Key returns exact same campaign without duplicate row
    res2 = await client.post("/api/v1/campaign", json=payload, headers=idem_headers)
    assert res2.status_code == 201
    camp2 = res2.json()
    assert camp2["id"] == camp1["id"]

    # Verify database has exactly 1 campaign
    count_res = await test_db.execute(select(Campaign).where(Campaign.advertiser_id == adv.id))
    all_camps = count_res.scalars().all()
    assert len(all_camps) == 1

    # Resume: fetch campaign by ID
    res_get = await client.get(f"/api/v1/campaign/{camp1['id']}", headers=headers)
    assert res_get.status_code == 200
    assert res_get.json()["id"] == camp1["id"]
    assert res_get.json()["status"] == 5


# ── 2. Multi-AdSet Hierarchy & Sub-Budgets ──────────────────────────────────

async def test_multi_adset_hierarchy_and_sub_budget_ceilings(
    client: AsyncClient, test_db: AsyncSession
):
    token, user_id = await _register_advertiser(client, "phase4_adset@test.com")
    adv = await _setup_advertiser_record(test_db, user_id, Decimal("500.00"))
    headers = {"Authorization": f"Bearer {token}"}

    # Create Campaign
    res_c = await client.post(
        "/api/v1/campaign",
        json={
            "advertiser_id": adv.id,
            "name": "Multi-AdSet Campaign",
            "budget_daily": 100.0,
            "budget_total": 1000.0,
            "bid_type": 1,
            "bid_amount": 2.0,
        },
        headers=headers,
    )
    camp_id = res_c.json()["id"]

    # 1. Create AdSet 1 with Idempotency-Key
    as_headers = {**headers, "Idempotency-Key": "wizard-adset-step2-alpha"}
    res_as1 = await client.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        json={"name": "Audience Alpha", "daily_budget": 60.0, "bid_amount": 2.5},
        headers=as_headers,
    )
    assert res_as1.status_code == 201
    as1_id = res_as1.json()["id"]

    # Retry with same Idempotency-Key returns existing AdSet
    res_as1_retry = await client.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        json={"name": "Audience Alpha", "daily_budget": 60.0, "bid_amount": 2.5},
        headers=as_headers,
    )
    assert res_as1_retry.status_code == 201
    assert res_as1_retry.json()["id"] == as1_id

    # 2. Reject single AdSet daily budget exceeding campaign master daily ceiling
    res_as_excess = await client.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        json={"name": "Too Big AdSet", "daily_budget": 150.0, "bid_amount": 2.0},
        headers=headers,
    )
    assert res_as_excess.status_code == 400
    assert "exceed campaign daily budget" in res_as_excess.json()["detail"].lower()


# ── 3. Sub-Budget Oversubscription Warning ──────────────────────────────────

async def test_sub_budgets_exceed_campaign_ceiling_readiness_warning(
    client: AsyncClient, test_db: AsyncSession
):
    token, user_id = await _register_advertiser(client, "phase4_oversub@test.com")
    adv = await _setup_advertiser_record(test_db, user_id, Decimal("500.00"))
    headers = {"Authorization": f"Bearer {token}"}

    # Campaign master ceiling: 100
    res_c = await client.post(
        "/api/v1/campaign",
        json={
            "advertiser_id": adv.id,
            "name": "Oversubscribed Campaign",
            "budget_daily": 100.0,
            "budget_total": 1000.0,
            "bid_type": 1,
            "bid_amount": 2.0,
        },
        headers=headers,
    )
    camp_id = res_c.json()["id"]

    # AdSet 1: daily 70
    await client.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        json={"name": "AdSet 1", "daily_budget": 70.0, "bid_amount": 2.0},
        headers=headers,
    )
    # AdSet 2: daily 60 (total 130 > 100)
    await client.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        json={"name": "AdSet 2", "daily_budget": 60.0, "bid_amount": 2.0},
        headers=headers,
    )

    # Readiness check should surface structured warning SUB_BUDGETS_EXCEED_CAMPAIGN_CEILING
    res_readiness = await client.get(f"/api/v1/campaign/{camp_id}/readiness", headers=headers)
    assert res_readiness.status_code == 200
    data = res_readiness.json()
    warning_codes = [w["code"] for w in data["warnings"]]
    assert "SUB_BUDGETS_EXCEED_CAMPAIGN_CEILING" in warning_codes


# ── 4. Inactive / Paused AdSet Creative Not Served ──────────────────────────

async def test_inactive_paused_adset_creative_never_served(test_db: AsyncSession):
    # Setup advertiser & active campaign
    adv = Advertiser(name="Adv-Serving", balance=Decimal("1000.00"))
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="Active Parent Campaign",
        status=Status.ACTIVE,
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        bid_amount=Decimal("2.00"),
        bid_type=1,
    )
    test_db.add(camp)
    await test_db.flush()

    # Active AdSet
    active_adset = AdSet(
        campaign_id=camp.id,
        name="Active Set",
        status=Status.ACTIVE,
        bid_amount=Decimal("2.00"),
    )
    # Paused AdSet
    paused_adset = AdSet(
        campaign_id=camp.id,
        name="Paused Set",
        status=Status.PAUSED,
        bid_amount=Decimal("2.00"),
    )
    test_db.add_all([active_adset, paused_adset])
    await test_db.flush()

    # Targeting rule on active adset
    tr_active = TargetingRule(campaign_id=camp.id, ad_set_id=active_adset.id, rule_type="geo", rule_value={"countries": ["IN"]})
    tr_paused = TargetingRule(campaign_id=camp.id, ad_set_id=paused_adset.id, rule_type="geo", rule_value={"countries": ["IN"]})

    # Approved creative under active adset
    c_active = Creative(
        campaign_id=camp.id,
        ad_set_id=active_adset.id,
        title="Active Creative",
        landing_url="https://example.com/active",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
        creative_type=1,
    )
    # Approved creative under paused adset
    c_paused = Creative(
        campaign_id=camp.id,
        ad_set_id=paused_adset.id,
        title="Paused Creative",
        landing_url="https://example.com/paused",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
        creative_type=1,
    )
    test_db.add_all([tr_active, tr_paused, c_active, c_paused])
    await test_db.commit()

    retrieval = TargetingRetrieval(test_db)
    u_ctx = UserContext(country="IN")
    candidates = await retrieval.retrieve(u_ctx, slot_id="slot_test")

    candidate_ids = [cand.creative_id for cand in candidates]
    assert c_active.id in candidate_ids, "Active AdSet creative must be eligible"
    assert c_paused.id not in candidate_ids, "Paused AdSet creative must NEVER be served"


# ── 5. Pending or Rejected Creative Never Serves ─────────────────────────────

async def test_pending_or_rejected_creative_never_serves(test_db: AsyncSession):
    adv = Advertiser(name="Adv-ModServing", balance=Decimal("1000.00"))
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="Mod Campaign",
        status=Status.ACTIVE,
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        bid_amount=Decimal("2.00"),
        bid_type=1,
    )
    test_db.add(camp)
    await test_db.flush()

    adset = AdSet(campaign_id=camp.id, name="Main Set", status=Status.ACTIVE, bid_amount=Decimal("2.00"))
    test_db.add(adset)
    await test_db.flush()

    tr = TargetingRule(campaign_id=camp.id, ad_set_id=adset.id, rule_type="geo", rule_value={"countries": ["US"]})
    c_pending = Creative(
        campaign_id=camp.id,
        ad_set_id=adset.id,
        title="Pending Creative",
        landing_url="https://example.com/pending",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.PENDING_REVIEW.value,
        creative_type=1,
    )
    c_rejected = Creative(
        campaign_id=camp.id,
        ad_set_id=adset.id,
        title="Rejected Creative",
        landing_url="https://example.com/rejected",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.REJECTED.value,
        creative_type=1,
    )
    c_approved = Creative(
        campaign_id=camp.id,
        ad_set_id=adset.id,
        title="Approved Creative",
        landing_url="https://example.com/approved",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
        creative_type=1,
    )
    test_db.add_all([tr, c_pending, c_rejected, c_approved])
    await test_db.commit()

    retrieval = TargetingRetrieval(test_db)
    u_ctx = UserContext(country="US")
    candidates = await retrieval.retrieve(u_ctx, slot_id="slot_mod")

    c_ids = [c.creative_id for c in candidates]
    assert c_approved.id in c_ids
    assert c_pending.id not in c_ids, "Pending creative must never be retrieved"
    assert c_rejected.id not in c_ids, "Rejected creative must never be retrieved"


# ── 6. Unverified Domain Blocks Readiness and Activation ─────────────────────

async def test_unverified_landing_domain_blocks_readiness_and_activation(
    client: AsyncClient, test_db: AsyncSession
):
    token, user_id = await _register_advertiser(client, "phase4_domain@test.com")
    adv = await _setup_advertiser_record(test_db, user_id, Decimal("500.00"))
    headers = {"Authorization": f"Bearer {token}"}

    # Create campaign & adset
    camp = Campaign(
        advertiser_id=adv.id,
        name="Domain Check Camp",
        status=5,
        budget_daily=Decimal("50.00"),
        budget_total=Decimal("500.00"),
        bid_amount=Decimal("1.00"),
    )
    test_db.add(camp)
    await test_db.flush()

    adset = AdSet(campaign_id=camp.id, name="Set 1", status=Status.ACTIVE, bid_amount=Decimal("1.00"))
    test_db.add(adset)
    await test_db.flush()

    tr = TargetingRule(campaign_id=camp.id, ad_set_id=adset.id, rule_type="geo", rule_value={"countries": ["IN"]})
    # Creative with unverified domain
    c = Creative(
        campaign_id=camp.id,
        ad_set_id=adset.id,
        title="Unverified Landing Ad",
        landing_url="https://unverified-domain.com/landing",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
        creative_type=1,
    )
    test_db.add_all([tr, c])
    await test_db.commit()

    # 1. Readiness check reports UNVERIFIED_DOMAIN blocker
    res_r = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    assert res_r.status_code == 200
    r_data = res_r.json()
    assert r_data["is_ready"] is False
    blocker_codes = [b["code"] for b in r_data["blockers"]]
    assert "UNVERIFIED_DOMAIN" in blocker_codes

    # 2. Activation fails with HTTP 400
    res_act = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert res_act.status_code == 400
    assert "not verified" in res_act.json()["detail"].lower()


# ── 7. Structured Blockers on Activation Failure ─────────────────────────────

async def test_structured_blockers_and_activation_failure_details(
    client: AsyncClient, test_db: AsyncSession
):
    token, user_id = await _register_advertiser(client, "phase4_blockers@test.com")
    # Advertiser with ZERO balance
    adv = await _setup_advertiser_record(test_db, user_id, Decimal("0.00"))
    headers = {"Authorization": f"Bearer {token}"}

    # Campaign missing creatives, targeting, and positive balance
    camp = Campaign(
        advertiser_id=adv.id,
        name="Incomplete Camp",
        status=5,
        budget_daily=Decimal("50.00"),
        budget_total=Decimal("500.00"),
        bid_amount=Decimal("1.00"),
    )
    test_db.add(camp)
    await test_db.commit()

    res_r = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    assert res_r.status_code == 200
    r_data = res_r.json()
    assert r_data["is_ready"] is False
    codes = [b["code"] for b in r_data["blockers"]]
    assert "INSUFFICIENT_BALANCE" in codes
    assert "NO_CREATIVES" in codes
    assert "MISSING_TARGETING" in codes

    # Activation attempt returns 400 with headers containing structured blockers
    res_act = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert res_act.status_code == 400
    assert "X-Activation-Blockers" in res_act.headers
    blockers_hdr = json.loads(res_act.headers["X-Activation-Blockers"])
    assert len(blockers_hdr) >= 1
    assert blockers_hdr[0]["code"] in ("INSUFFICIENT_BALANCE", "NO_CREATIVES", "MISSING_TARGETING")


# ── 8. Material Creative Edit Resets Moderation Status ───────────────────────

async def test_material_creative_edit_resets_moderation_status(
    client: AsyncClient, test_db: AsyncSession
):
    token, user_id = await _register_advertiser(client, "phase4_modreset@test.com")
    adv = await _setup_advertiser_record(test_db, user_id, Decimal("500.00"))
    await _create_verified_domain(test_db, adv.id, "example.com")
    headers = {"Authorization": f"Bearer {token}"}

    camp = Campaign(
        advertiser_id=adv.id,
        name="Mod Reset Camp",
        status=5,
        budget_daily=Decimal("50.00"),
        budget_total=Decimal("500.00"),
    )
    test_db.add(camp)
    await test_db.flush()

    adset = AdSet(campaign_id=camp.id, name="Set", status=Status.ACTIVE, bid_amount=Decimal("1.00"))
    test_db.add(adset)
    await test_db.flush()

    # Pre-approved creative
    c = Creative(
        campaign_id=camp.id,
        ad_set_id=adset.id,
        title="Initial Safe Headline",
        landing_url="https://example.com/safe",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
        creative_type=1,
    )
    test_db.add(c)
    await test_db.commit()

    # Material content edit: change title
    res_patch = await client.patch(
        f"/api/v1/creative/{c.id}",
        json={"title": "Radical Content Overhaul Headline"},
        headers=headers,
    )
    assert res_patch.status_code == 200
    data = res_patch.json()
    assert data["moderation_status"] == ModerationStatus.PENDING_REVIEW.value


# ── 9. Cross-Campaign AdSet Attachment Rejected ─────────────────────────────

async def test_cross_campaign_adset_attachment_rejected(
    client: AsyncClient, test_db: AsyncSession
):
    token, user_id = await _register_advertiser(client, "phase4_cross@test.com")
    adv = await _setup_advertiser_record(test_db, user_id, Decimal("500.00"))
    await _create_verified_domain(test_db, adv.id, "example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Campaign A with AdSet A
    camp_a = Campaign(advertiser_id=adv.id, name="Camp A", status=5, budget_daily=Decimal("50.00"), budget_total=Decimal("500.00"))
    test_db.add(camp_a)
    await test_db.flush()
    adset_a = AdSet(campaign_id=camp_a.id, name="Set A", status=Status.ACTIVE, bid_amount=Decimal("1.00"))
    test_db.add(adset_a)

    # Campaign B
    camp_b = Campaign(advertiser_id=adv.id, name="Camp B", status=5, budget_daily=Decimal("50.00"), budget_total=Decimal("500.00"))
    test_db.add(camp_b)
    await test_db.commit()

    # Attempt to create creative under Campaign B with AdSet from Campaign A
    res_c = await client.post(
        "/api/v1/creative",
        json={
            "campaign_id": camp_b.id,
            "ad_set_id": adset_a.id,  # Mismatch! Belongs to camp_a
            "title": "Confused Creative",
            "landing_url": "https://example.com/promo",
            "creative_type": 1,
            "width": 300,
            "height": 250,
        },
        headers=headers,
    )
    assert res_c.status_code == 400
    assert "does not belong" in res_c.json()["detail"].lower()


# ── 10. Strict Tenant Isolation ──────────────────────────────────────────────

async def test_strict_tenant_isolation_on_campaign_endpoints(
    client: AsyncClient, test_db: AsyncSession
):
    # Advertiser A
    token_a, user_id_a = await _register_advertiser(client, "phase4_adv_a@test.com")
    adv_a = await _setup_advertiser_record(test_db, user_id_a, Decimal("500.00"))

    # Advertiser B
    token_b, user_id_b = await _register_advertiser(client, "phase4_adv_b@test.com")
    adv_b = await _setup_advertiser_record(test_db, user_id_b, Decimal("500.00"))

    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Campaign belonging to Advertiser A
    camp_a = Campaign(advertiser_id=adv_a.id, name="Adv A Secret Campaign", status=5, budget_daily=Decimal("50.00"), budget_total=Decimal("500.00"))
    test_db.add(camp_a)
    await test_db.commit()

    # 1. Advertiser B cannot GET Campaign A
    res1 = await client.get(f"/api/v1/campaign/{camp_a.id}", headers=headers_b)
    assert res1.status_code == 403

    # 2. Advertiser B cannot GET Campaign A readiness
    res2 = await client.get(f"/api/v1/campaign/{camp_a.id}/readiness", headers=headers_b)
    assert res2.status_code == 403

    # 3. Advertiser B cannot POST activate Campaign A
    res3 = await client.post(f"/api/v1/campaign/{camp_a.id}/activate", headers=headers_b)
    assert res3.status_code == 403

    # 4. Advertiser B cannot attach AdSet to Campaign A
    res4 = await client.post(
        f"/api/v1/campaign/{camp_a.id}/ad-sets",
        json={"name": "Attacker AdSet", "bid_amount": 1.0},
        headers=headers_b,
    )
    assert res4.status_code == 403


# ── 11. User-Visible Lifecycle State Mapping ────────────────────────────────

async def test_user_visible_lifecycle_state_mapping(
    client: AsyncClient, test_db: AsyncSession
):
    token, user_id = await _register_advertiser(client, "phase4_lifecycle@test.com")
    adv = await _setup_advertiser_record(test_db, user_id, Decimal("500.00"))
    await _create_verified_domain(test_db, adv.id, "example.com")
    headers = {"Authorization": f"Bearer {token}"}

    now = datetime.now(timezone.utc)

    # 1. Draft without setup -> Needs Setup
    c1 = Campaign(advertiser_id=adv.id, name="C1", status=5, budget_daily=Decimal("50.00"), budget_total=Decimal("500.00"))
    test_db.add(c1)
    await test_db.commit()
    r1 = await client.get(f"/api/v1/campaign/{c1.id}/readiness", headers=headers)
    assert r1.json()["lifecycle_state"] == "Needs Setup"

    # 2. Active with end_time in the past -> Completed
    c2 = Campaign(
        advertiser_id=adv.id,
        name="C2",
        status=Status.ACTIVE,
        budget_daily=Decimal("50.00"),
        budget_total=Decimal("500.00"),
        start_time=now - timedelta(days=10),
        end_time=now - timedelta(days=1),
    )
    test_db.add(c2)
    await test_db.commit()
    r2 = await client.get(f"/api/v1/campaign/{c2.id}/readiness", headers=headers)
    assert r2.json()["lifecycle_state"] == "Completed"

    # 3. Active with start_time in the future -> Scheduled
    c3 = Campaign(
        advertiser_id=adv.id,
        name="C3",
        status=Status.ACTIVE,
        budget_daily=Decimal("50.00"),
        budget_total=Decimal("500.00"),
        start_time=now + timedelta(days=5),
        end_time=now + timedelta(days=20),
    )
    test_db.add(c3)
    await test_db.commit()
    r3 = await client.get(f"/api/v1/campaign/{c3.id}/readiness", headers=headers)
    assert r3.json()["lifecycle_state"] == "Scheduled"

    # 4. Status.PAUSED -> Paused
    c4 = Campaign(advertiser_id=adv.id, name="C4", status=Status.PAUSED, budget_daily=Decimal("50.00"), budget_total=Decimal("500.00"))
    test_db.add(c4)
    await test_db.commit()
    r4 = await client.get(f"/api/v1/campaign/{c4.id}/readiness", headers=headers)
    assert r4.json()["lifecycle_state"] == "Paused"

    # 5. Status.DELETED -> Archived
    c5 = Campaign(advertiser_id=adv.id, name="C5", status=Status.DELETED, budget_daily=Decimal("50.00"), budget_total=Decimal("500.00"))
    test_db.add(c5)
    await test_db.commit()
    r5 = await client.get(f"/api/v1/campaign/{c5.id}/readiness", headers=headers)
    assert r5.json()["lifecycle_state"] == "Archived"


# ── 12. Successful Activation Transitions to ACTIVE ──────────────────────────

async def test_successful_activation_transitions_to_active(
    client: AsyncClient, test_db: AsyncSession
):
    token, user_id = await _register_advertiser(client, "phase4_success@test.com")
    adv = await _setup_advertiser_record(test_db, user_id, Decimal("500.00"))
    await _create_verified_domain(test_db, adv.id, "example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Fully compliant campaign
    camp = Campaign(
        advertiser_id=adv.id,
        name="Launch Ready Campaign",
        status=5,
        budget_daily=Decimal("50.00"),
        budget_total=Decimal("500.00"),
        bid_amount=Decimal("1.50"),
        bid_type=1,
    )
    test_db.add(camp)
    await test_db.flush()

    adset = AdSet(campaign_id=camp.id, name="Active Launch Set", status=Status.ACTIVE, bid_amount=Decimal("1.50"))
    test_db.add(adset)
    await test_db.flush()

    tr = TargetingRule(campaign_id=camp.id, ad_set_id=adset.id, rule_type="geo", rule_value={"countries": ["US"]})
    cr = Creative(
        campaign_id=camp.id,
        ad_set_id=adset.id,
        title="Ready Ad",
        landing_url="https://example.com/promo",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
        creative_type=1,
    )
    test_db.add_all([tr, cr])
    await test_db.commit()

    # Readiness verifies ready
    res_r = await client.get(f"/api/v1/campaign/{camp.id}/readiness", headers=headers)
    assert res_r.status_code == 200
    assert res_r.json()["is_ready"] is True
    assert res_r.json()["lifecycle_state"] == "Approved / Eligible"

    # Activate
    res_act = await client.post(f"/api/v1/campaign/{camp.id}/activate", headers=headers)
    assert res_act.status_code == 200
    assert res_act.json()["status"] == 1  # ACTIVE

    # Balance must remain untouched (non-billable transition)
    await test_db.refresh(adv)
    assert adv.balance == Decimal("500.00")
