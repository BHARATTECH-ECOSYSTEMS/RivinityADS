"""Phase 6 — Laya Decision Model Hardening Test Suite.

Validates:
1. Critical fail-closed safety: An empty allowlist under limited_pilot fails closed (zero campaigns eligible).
2. Pilot allowlist gating: Only explicitly allowlisted campaigns execute under limited_pilot.
3. Budget delta cap: Changes exceeding configured pilot_max_budget_delta_amount fall back to suggestion.
4. Data volume gates: Fewer than 500 impressions returns "insufficient evidence".
5. Objective-aware optimization:
   - CPA campaigns require recorded conversions before budget increases.
   - CPA campaigns reject budget increases when effective CPA exceeds target bid by >20%.
   - CPC campaigns require recorded clicks before budget increases.
6. Creative integrity: improve_creative is strictly suggestion-only, never applied autonomously.
7. Atomic revert workflow:
   - Reverting budget decision restores budget_before and creates immutable audit record.
   - Reverting targeting decision removes added targeting rule.
   - Reverting already reverted decision fails safely.
8. REST API authorization and scoping:
   - Admin-only status endpoint.
   - Advertiser-scoped decision listings.
   - Tenant-scoped decision revert endpoint.
   - Campaign optimization state endpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.laya_client import LayaDecisionResult
from liteads.ad_server.services.optimization_executor import (
    OptimizationExecutor,
    OptimizationExecutionError,
)
from liteads.ad_server.services.optimization_policy import (
    OptimizationPolicyService,
    OptimizationPolicyResult,
)
from liteads.ad_server.services.optimization_prep import (
    OptimizationPreparationService,
    OptimizationPrepResult,
)
from liteads.common.config import get_settings
from liteads.common.security import create_access_token
from liteads.models.ad import (
    Advertiser,
    Campaign,
    CampaignChangeLog,
    OptimizationDecision,
    Suggestion,
    TargetingRule,
)
from liteads.models.auth import User
from liteads.models.base import BidType, Status

pytestmark = pytest.mark.asyncio


# ── Helpers & Fixtures ───────────────────────────────────────────────────────

async def _create_test_advertiser(session: AsyncSession, name: str = "Test Advertiser", balance: Decimal = Decimal("1000.00")) -> tuple[User, Advertiser, str]:
    adv = Advertiser(name=name, contact_email=f"{name.lower().replace(' ', '')}@example.com", balance=balance)
    session.add(adv)
    await session.flush()

    user = User(
        name=f"User {name}",
        email=f"user_{name.lower().replace(' ', '')}@example.com",
        password_hash="test_pw_hash",
        role="advertiser",
        advertiser_id=adv.id,
        is_active=True,
        email_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(adv)
    await session.refresh(user)

    token = create_access_token(user)
    return user, adv, token


async def _create_test_admin(session: AsyncSession) -> tuple[User, str]:
    admin = User(
        name="Platform Admin",
        email="operator_admin@example.com",
        password_hash="test_admin_hash",
        role="admin",
        is_active=True,
        email_verified=True,
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)

    token = create_access_token(admin)
    return admin, token


async def _create_test_campaign(
    session: AsyncSession,
    advertiser_id: int,
    name: str = "Test Campaign",
    budget_daily: Decimal = Decimal("100.00"),
    autopilot: bool = True,
    bid_type: int = BidType.CPM,
    bid_amount: Decimal = Decimal("2.50"),
    impressions: int = 1000,
    clicks: int = 50,
    conversions: int = 5,
) -> Campaign:
    camp = Campaign(
        advertiser_id=advertiser_id,
        name=name,
        budget_daily=budget_daily,
        budget_total=Decimal("10000.00"),
        spent_today=Decimal("10.00"),
        spent_total=Decimal("50.00"),
        bid_type=bid_type,
        bid_amount=bid_amount,
        impressions=impressions,
        clicks=clicks,
        conversions=conversions,
        status=Status.ACTIVE,
        autopilot=autopilot,
    )
    session.add(camp)
    await session.commit()
    await session.refresh(camp)
    return camp


# ── Core Hardening Test Cases ────────────────────────────────────────────────

async def test_empty_allowlist_fails_closed_in_limited_pilot(test_db: AsyncSession):
    """When mode is limited_pilot and allowlist is empty, zero campaigns execute (fail closed)."""
    _, adv, _ = await _create_test_advertiser(test_db, "Allowlist Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Autopilot Camp", budget_daily=Decimal("100.00"), autopilot=True)

    executor = OptimizationExecutor(test_db)
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Performance looks good",
        action="increase_budget_20",
        confidence=0.88,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    settings = get_settings()
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", []):

        await executor.execute(campaign, policy_res)

    await test_db.refresh(campaign)
    # 1. Budget must NOT be changed
    assert campaign.budget_daily == Decimal("100.00")

    # 2. A suggestion must be recorded instead of automatic execution
    sugg = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == campaign.id))).scalar_one_or_none()
    assert sugg is not None
    assert sugg.action.get("action") == "increase_budget_20"

    # 3. Decision audit must record the fail-closed reason
    decision = (await test_db.execute(
        select(OptimizationDecision)
        .where(OptimizationDecision.campaign_id == campaign.id)
        .order_by(OptimizationDecision.id.desc())
    )).scalar_one_or_none()
    assert decision is not None
    assert decision.outcome == "suggested"
    assert "Pilot allowlist is empty: zero campaigns eligible under limited_pilot (failed closed)" in (decision.reason or "")


async def test_non_allowlisted_campaign_fails_closed_in_limited_pilot(test_db: AsyncSession):
    """Campaign not in non-empty allowlist must not execute automatically."""
    _, adv, _ = await _create_test_advertiser(test_db, "Non Allowlisted Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Camp 101", budget_daily=Decimal("100.00"), autopilot=True)

    executor = OptimizationExecutor(test_db)
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="High ROAS",
        action="increase_budget_20",
        confidence=0.85,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    settings = get_settings()
    # Allowlist only campaign 99999 (not this campaign)
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", [99999]):

        await executor.execute(campaign, policy_res)

    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00")

    decision = (await test_db.execute(
        select(OptimizationDecision)
        .where(OptimizationDecision.campaign_id == campaign.id)
        .order_by(OptimizationDecision.id.desc())
    )).scalar_one_or_none()
    assert decision is not None
    assert decision.outcome == "suggested"
    assert "not in pilot allowlist" in (decision.reason or "")


async def test_allowlisted_campaign_executes_in_limited_pilot(test_db: AsyncSession):
    """Campaign explicitly in allowlist executes within budget delta cap."""
    _, adv, _ = await _create_test_advertiser(test_db, "Allowlisted Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Pilot Camp", budget_daily=Decimal("100.00"), autopilot=True)

    executor = OptimizationExecutor(test_db)
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Pilot qualified",
        action="increase_budget_20",
        confidence=0.92,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    settings = get_settings()
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", [campaign.id]), \
         patch.object(settings.optimization, "pilot_max_budget_delta_amount", Decimal("50.00")):

        await executor.execute(campaign, policy_res)

    await test_db.refresh(campaign)
    # 100 * 1.2 = 120 (delta 20 <= 50 cap)
    assert campaign.budget_daily == Decimal("120.00")

    decision = (await test_db.execute(
        select(OptimizationDecision)
        .where(OptimizationDecision.campaign_id == campaign.id)
        .order_by(OptimizationDecision.id.desc())
    )).scalar_one_or_none()
    assert decision is not None
    assert decision.outcome == "applied"
    assert decision.change_applied is not None
    assert float(decision.change_applied.get("budget_before")) == 100.0
    assert float(decision.change_applied.get("budget_after")) == 120.0


async def test_pilot_budget_delta_cap_safety(test_db: AsyncSession):
    """Budget delta exceeding pilot_max_budget_delta_amount falls back to suggestion."""
    _, adv, _ = await _create_test_advertiser(test_db, "Delta Cap Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Cap Camp", budget_daily=Decimal("100.00"), autopilot=True)

    executor = OptimizationExecutor(test_db)
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Grow budget",
        action="increase_budget_20",  # delta is 20
        confidence=0.90,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    settings = get_settings()
    # Set cap lower than 20: cap = 15.00
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", [campaign.id]), \
         patch.object(settings.optimization, "pilot_max_budget_delta_amount", Decimal("15.00")):

        await executor.execute(campaign, policy_res)

    await test_db.refresh(campaign)
    # Must NOT apply delta 20
    assert campaign.budget_daily == Decimal("100.00")

    decision = (await test_db.execute(
        select(OptimizationDecision)
        .where(OptimizationDecision.campaign_id == campaign.id)
        .order_by(OptimizationDecision.id.desc())
    )).scalar_one_or_none()
    assert decision is not None
    assert decision.outcome == "suggested"
    assert "exceeded cap" in (decision.reason or "")


# ── Policy & Objective-Aware Tests ───────────────────────────────────────────

async def test_evidence_gate_low_impressions():
    """Policy must return insufficient evidence when impressions < 500."""
    policy = OptimizationPolicyService()
    campaign = Campaign(
        id=1,
        impressions=499,
        status=Status.ACTIVE,
        autopilot=True,
    )
    prep_res = OptimizationPrepResult(
        eligible=False,
        reason="Campaign has fewer than 500 impressions",
    )
    decision = LayaDecisionResult(
        choice="increase_budget_20",
        confidence=0.85,
        raw_response={},
    )

    res = policy.evaluate(campaign, prep_res, decision)
    assert not res.accepted
    assert "insufficient evidence: Campaign has fewer than 500 impressions" in res.reason


async def test_cpa_objective_aware_conversions_gate():
    """CPA campaigns require recorded conversions before increasing budget."""
    policy = OptimizationPolicyService()
    campaign = Campaign(
        id=1,
        bid_type=BidType.CPA,
        bid_amount=Decimal("20.00"),
        budget_daily=Decimal("100.00"),
        spent_total=Decimal("80.00"),
        impressions=1200,
        clicks=50,
        conversions=0,  # 0 conversions
        status=Status.ACTIVE,
        autopilot=True,
    )
    prep_res = OptimizationPrepResult(eligible=True, state="{}")
    decision = LayaDecisionResult(
        choice="increase_budget_20",
        confidence=0.85,
        raw_response={},
    )

    res = policy.evaluate(campaign, prep_res, decision)
    assert not res.accepted
    assert "insufficient evidence: CPA campaigns require recorded conversions before increasing budget" in res.reason


async def test_cpa_objective_aware_target_bid_violation():
    """CPA campaigns reject budget increases when effective CPA exceeds target bid by >20%."""
    policy = OptimizationPolicyService()
    campaign = Campaign(
        id=1,
        bid_type=BidType.CPA,
        bid_amount=Decimal("20.00"),  # target CPA $20; 20 * 1.2 = $24 max
        budget_daily=Decimal("100.00"),
        spent_total=Decimal("60.00"),
        impressions=2000,
        clicks=100,
        conversions=2,  # effective CPA = 60 / 2 = $30 > $24
        status=Status.ACTIVE,
        autopilot=True,
    )
    prep_res = OptimizationPrepResult(eligible=True, state="{}")
    decision = LayaDecisionResult(
        choice="increase_budget_20",
        confidence=0.85,
        raw_response={},
    )

    res = policy.evaluate(campaign, prep_res, decision)
    assert not res.accepted
    assert "CPA objective violation" in res.reason
    assert "exceeds target bid" in res.reason


async def test_cpa_objective_aware_accepted():
    """CPA campaigns accept budget increases when effective CPA is within allowable range."""
    policy = OptimizationPolicyService()
    campaign = Campaign(
        id=1,
        bid_type=BidType.CPA,
        bid_amount=Decimal("25.00"),  # target CPA $25
        budget_daily=Decimal("100.00"),
        spent_total=Decimal("90.00"),
        impressions=2000,
        clicks=100,
        conversions=5,  # effective CPA = 90 / 5 = $18 <= $25
        status=Status.ACTIVE,
        autopilot=True,
    )
    prep_res = OptimizationPrepResult(eligible=True, state="{}")
    decision = LayaDecisionResult(
        choice="increase_budget_20",
        confidence=0.85,
        raw_response={},
    )

    res = policy.evaluate(campaign, prep_res, decision)
    assert res.accepted


async def test_cpc_objective_aware_clicks_gate():
    """CPC campaigns require recorded clicks before increasing budget."""
    policy = OptimizationPolicyService()
    campaign = Campaign(
        id=1,
        bid_type=BidType.CPC,
        bid_amount=Decimal("1.50"),
        budget_daily=Decimal("100.00"),
        spent_total=Decimal("0.00"),
        impressions=800,
        clicks=0,  # 0 clicks
        status=Status.ACTIVE,
        autopilot=True,
    )
    prep_res = OptimizationPrepResult(eligible=True, state="{}")
    decision = LayaDecisionResult(
        choice="increase_budget_20",
        confidence=0.80,
        raw_response={},
    )

    res = policy.evaluate(campaign, prep_res, decision)
    assert not res.accepted
    assert "insufficient evidence: CPC campaigns require recorded clicks before increasing budget" in res.reason


async def test_improve_creative_never_applied_autonomously(test_db: AsyncSession):
    """improve_creative must remain a reviewable suggestion even if autopilot is on and campaign is in pilot."""
    _, adv, _ = await _create_test_advertiser(test_db, "Creative Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Creative Camp", autopilot=True)

    executor = OptimizationExecutor(test_db)
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Creative fatigue detected",
        action="improve_creative",
        confidence=0.89,
        autopilot=True,
    )

    settings = get_settings()
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", [campaign.id]):

        await executor.execute(campaign, policy_res)

    sugg = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == campaign.id))).scalar_one_or_none()
    assert sugg is not None
    assert sugg.action.get("action") == "improve_creative"

    decision = (await test_db.execute(
        select(OptimizationDecision)
        .where(OptimizationDecision.campaign_id == campaign.id)
        .order_by(OptimizationDecision.id.desc())
    )).scalar_one_or_none()
    assert decision is not None
    assert decision.outcome == "suggested"
    assert "requires human review" in (decision.reason or "")


# ── Revert Workflow Tests ───────────────────────────────────────────────────

async def test_atomic_revert_budget_decision(test_db: AsyncSession):
    """Reverting a budget decision restores previous budget and creates revert records."""
    _, adv, _ = await _create_test_advertiser(test_db, "Revert Budget Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Revert Budget Camp", budget_daily=Decimal("100.00"), autopilot=True)

    executor = OptimizationExecutor(test_db)
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Apply test change",
        action="increase_budget_20",
        confidence=0.95,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    settings = get_settings()
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", [campaign.id]), \
         patch.object(settings.optimization, "pilot_max_budget_delta_amount", Decimal("50.00")):

        await executor.execute(campaign, policy_res)

    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("120.00")

    decision = (await test_db.execute(
        select(OptimizationDecision)
        .where(OptimizationDecision.campaign_id == campaign.id, OptimizationDecision.outcome == "applied")
        .order_by(OptimizationDecision.id.desc())
    )).scalars().first()
    assert decision is not None

    # Revert the decision
    reverted_res = await executor.revert_decision(decision.decision_id, actor="operator_tester", auto_commit=True)
    assert reverted_res["action"] == "revert_budget"
    assert float(reverted_res["budget_to"]) == 100.0

    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00")

    # Verify changelog entry
    log = (await test_db.execute(
        select(CampaignChangeLog)
        .where(CampaignChangeLog.campaign_id == campaign.id)
        .order_by(CampaignChangeLog.id.desc())
    )).scalars().first()
    assert log is not None
    assert "Revert" in str(log.action) or "revert" in str(log.action).lower()

    # Verify revert audit record
    revert_dec = (await test_db.execute(
        select(OptimizationDecision)
        .where(
            OptimizationDecision.campaign_id == campaign.id,
            OptimizationDecision.outcome == "reverted",
            OptimizationDecision.action == "revert_increase_budget_20",
        )
    )).scalars().first()
    assert revert_dec is not None
    assert revert_dec.actor == "operator_tester"

    # Verify original decision updated
    await test_db.refresh(decision)
    assert decision.outcome == "reverted"

    # Attempting to revert again must fail
    with pytest.raises(OptimizationExecutionError) as exc_info:
        await executor.revert_decision(decision.decision_id, actor="operator_tester")
    assert "Cannot revert decision" in str(exc_info.value)


async def test_atomic_revert_targeting_decision(test_db: AsyncSession):
    """Reverting a targeting decision removes the added targeting rule."""
    _, adv, _ = await _create_test_advertiser(test_db, "Revert Targeting Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Revert Geo Camp", autopilot=True)

    executor = OptimizationExecutor(test_db)
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Expand Karnataka geo",
        action="shift_to_new_geo",
        confidence=0.91,
        autopilot=True,
        operation_type="ADD",
    )

    settings = get_settings()
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", [campaign.id]):

        await executor.execute(campaign, policy_res, target_payload={"geo": "IN-KA"})

    rules = (await test_db.execute(select(TargetingRule).where(TargetingRule.campaign_id == campaign.id))).scalars().all()
    assert any(r.rule_value == "IN-KA" for r in rules)

    decision = (await test_db.execute(
        select(OptimizationDecision)
        .where(OptimizationDecision.campaign_id == campaign.id, OptimizationDecision.outcome == "applied")
    )).scalar_one()

    # Revert targeting
    await executor.revert_decision(decision.decision_id, actor="admin_tester", auto_commit=True)

    rules_after = (await test_db.execute(select(TargetingRule).where(TargetingRule.campaign_id == campaign.id))).scalars().all()
    assert not any(r.rule_value == "IN-KA" for r in rules_after)


# ── REST API Scoping & Authorization Tests ───────────────────────────────────

async def test_api_optimization_status_role_scoping(client: AsyncClient, test_db: AsyncSession):
    """GET /api/v1/optimization/status must be admin-only."""
    _, adv, adv_token = await _create_test_advertiser(test_db, "Status Adv")
    _, admin_token = await _create_test_admin(test_db)

    # 1. Advertiser must get 403
    adv_res = await client.get("/api/v1/optimization/status", headers={"Authorization": f"Bearer {adv_token}"})
    assert adv_res.status_code == 403

    # 2. Admin must get 200 with full governance fields
    admin_res = await client.get("/api/v1/optimization/status", headers={"Authorization": f"Bearer {admin_token}"})
    assert admin_res.status_code == 200
    data = admin_res.json()
    assert "global_mode" in data
    assert "kill_switch" in data
    assert "pilot_campaign_ids" in data
    assert "max_daily_changes" in data
    assert "pilot_max_budget_delta_amount" in data


async def test_api_optimization_decisions_tenant_isolation(client: AsyncClient, test_db: AsyncSession):
    """Advertiser only sees their own campaign decisions; Admin sees all."""
    _, adv_a, tok_a = await _create_test_advertiser(test_db, "Adv A")
    _, adv_b, tok_b = await _create_test_advertiser(test_db, "Adv B")
    _, admin_tok = await _create_test_admin(test_db)

    camp_a = await _create_test_campaign(test_db, adv_a.id, "Camp A")
    camp_b = await _create_test_campaign(test_db, adv_b.id, "Camp B")

    # Insert a decision for Camp A and Camp B
    dec_a = OptimizationDecision(
        decision_id="dec_a_12345",
        campaign_id=camp_a.id,
        mode="limited_pilot",
        action="increase_budget_20",
        confidence=0.88,
        policy_version="1.0",
        outcome="applied",
        actor="laya",
    )
    dec_b = OptimizationDecision(
        decision_id="dec_b_67890",
        campaign_id=camp_b.id,
        mode="limited_pilot",
        action="increase_budget_20",
        confidence=0.82,
        policy_version="1.0",
        outcome="applied",
        actor="laya",
    )
    test_db.add_all([dec_a, dec_b])
    await test_db.commit()

    # Adv A queries decisions
    res_a = await client.get("/api/v1/optimization/decisions", headers={"Authorization": f"Bearer {tok_a}"})
    assert res_a.status_code == 200
    items_a = res_a.json()
    assert any(d["decision_id"] == "dec_a_12345" for d in items_a)
    assert not any(d["decision_id"] == "dec_b_67890" for d in items_a)

    # Admin queries decisions -> sees both
    res_admin = await client.get("/api/v1/optimization/decisions", headers={"Authorization": f"Bearer {admin_tok}"})
    assert res_admin.status_code == 200
    items_admin = res_admin.json()
    assert any(d["decision_id"] == "dec_a_12345" for d in items_admin)
    assert any(d["decision_id"] == "dec_b_67890" for d in items_admin)


async def test_api_campaign_optimization_state(client: AsyncClient, test_db: AsyncSession):
    """GET /api/v1/campaign/{id}/optimization-state returns current campaign autopilot status."""
    _, adv, tok = await _create_test_advertiser(test_db, "Opt State Adv")
    camp = await _create_test_campaign(test_db, adv.id, "State Camp", budget_daily=Decimal("150.00"), autopilot=True)

    settings = get_settings()
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", [camp.id]):

        res = await client.get(f"/api/v1/campaign/{camp.id}/optimization-state", headers={"Authorization": f"Bearer {tok}"})
        assert res.status_code == 200
        data = res.json()
        assert data["campaign_id"] == camp.id
        assert data["autopilot"] is True
        assert data["is_pilot_eligible"] is True
        assert data["effective_mode"] == "limited_pilot"
        assert float(data["daily_budget"]) == 150.0


async def test_api_revert_decision_endpoint(client: AsyncClient, test_db: AsyncSession):
    """POST /api/v1/optimization/decisions/{decision_id}/revert enforces tenant scoping and reverts."""
    _, adv_a, tok_a = await _create_test_advertiser(test_db, "Revert Adv A")
    _, adv_b, tok_b = await _create_test_advertiser(test_db, "Revert Adv B")

    camp_a = await _create_test_campaign(test_db, adv_a.id, "Camp A", budget_daily=Decimal("120.00"), autopilot=True)

    dec = OptimizationDecision(
        decision_id="dec_revert_target",
        campaign_id=camp_a.id,
        mode="limited_pilot",
        action="increase_budget_20",
        confidence=0.90,
        policy_version="1.0",
        outcome="applied",
        change_applied={"budget_before": "100.00", "budget_after": "120.00"},
        actor="laya",
    )
    test_db.add(dec)
    await test_db.commit()

    # 1. Adv B attempts to revert Adv A's decision -> 403 Forbidden (cross-tenant protection)
    res_b = await client.post(f"/api/v1/optimization/decisions/{dec.decision_id}/revert", headers={"Authorization": f"Bearer {tok_b}"})
    assert res_b.status_code == 403

    # 2. Adv A reverts own decision -> 200
    res_a = await client.post(f"/api/v1/optimization/decisions/{dec.decision_id}/revert", headers={"Authorization": f"Bearer {tok_a}"})
    assert res_a.status_code == 200
    assert res_a.json()["status"] == "reverted"

    # Verify budget restored
    await test_db.refresh(camp_a)
    assert camp_a.budget_daily == Decimal("100.00")

    # 3. Adv A reverts again -> 400
    res_a2 = await client.post(f"/api/v1/optimization/decisions/{dec.decision_id}/revert", headers={"Authorization": f"Bearer {tok_a}"})
    assert res_a2.status_code == 400
    assert "revert" in res_a2.json()["detail"].lower()
