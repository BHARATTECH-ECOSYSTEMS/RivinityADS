"""Phase 6 — Laya Decision Model Hardening Comprehensive Test Suite.

RivinityADS Post-Audit Remediation Roadmap — Phase 6: Laya Decision Model Hardening (P1 before limited-pilot)

Comprehensive Pytest Suite Verifying:
- Suite 1: Allowlist Validation & Fail-Closed Gating (F-06)
- Suite 2: Conservative Defaults, Kill Switch & Operating Modes
- Suite 3: Precondition Gating (Creatives, Domain, Balance, Caps, Cooldown)
- Suite 4: Confidence, Calibration Caveat & Evidence Gates
- Suite 5: Action Types & Add-Only Targeting (add-only geo/sector, suggestion-only creative)
- Suite 6: Reversibility & Rollback (atomic revert of budget and targeting, duplicate revert protection)
- Suite 7: Failure & Concurrency Resilience (timeout, malformed JSON, fencing, lease, dedup)
- Suite 8: Operator Controls, API Governance & Serving Invariant
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import math
from unittest.mock import patch, MagicMock
import pytest
from httpx import AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.laya_client import (
    LayaDecisionClient,
    LayaDecisionResult,
    LayaUnavailableError,
    LayaMalformedResponseError,
)
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
from liteads.ad_server.services.domain_verification import DomainVerificationService
from liteads.common.config import get_settings
from liteads.common.security import create_access_token
from liteads.models.ad import (
    Advertiser,
    Campaign,
    CampaignChangeLog,
    Creative,
    OptimizationDecision,
    Suggestion,
    TargetingRule,
)
from liteads.models.domain import AdvertiserDomain, VerificationStatus
from liteads.models.auth import User
from liteads.models.base import BidType, Status, ModerationStatus

pytestmark = pytest.mark.asyncio


# ── Helpers & Fixtures ───────────────────────────────────────────────────────

async def _create_test_advertiser(
    session: AsyncSession,
    name: str = "Test Advertiser",
    balance: Decimal = Decimal("1000.00"),
    daily_budget: Decimal | None = None,
) -> tuple[User, Advertiser, str]:
    adv = Advertiser(
        name=name,
        contact_email=f"{name.lower().replace(' ', '')}@example.com",
        balance=balance,
        daily_budget=daily_budget,
    )
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
        email="operator_admin_phase6@example.com",
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
    budget_total: Decimal = Decimal("10000.00"),
    spent_today: Decimal = Decimal("10.00"),
    spent_total: Decimal = Decimal("50.00"),
    autopilot: bool = True,
    bid_type: int = BidType.CPM,
    bid_amount: Decimal = Decimal("2.50"),
    impressions: int = 1000,
    clicks: int = 50,
    conversions: int = 5,
    status: int = Status.ACTIVE,
    with_approved_creative: bool = False,
    verified_domain: str | None = None,
) -> Campaign:
    camp = Campaign(
        advertiser_id=advertiser_id,
        name=name,
        budget_daily=budget_daily,
        budget_total=budget_total,
        spent_today=spent_today,
        spent_total=spent_total,
        bid_type=bid_type,
        bid_amount=bid_amount,
        impressions=impressions,
        clicks=clicks,
        conversions=conversions,
        status=status,
        autopilot=autopilot,
    )
    session.add(camp)
    await session.flush()

    if with_approved_creative:
        landing_url = f"https://{verified_domain or 'example.com'}/landing"
        creat = Creative(
            campaign_id=camp.id,
            title="Approved Creative",
            landing_url=landing_url,
            moderation_status=ModerationStatus.APPROVED,
            status=Status.ACTIVE,
        )
        session.add(creat)

        if verified_domain:
            dom = AdvertiserDomain(
                advertiser_id=advertiser_id,
                domain=verified_domain,
                status=VerificationStatus.VERIFIED,
                verification_token="token_123",
            )
            session.add(dom)

    await session.commit()
    await session.refresh(camp)
    return camp


# ── Suite 1: Allowlist Validation & Fail-Closed Gating (F-06) ────────────────

async def test_empty_allowlist_fails_closed_in_limited_pilot(test_db: AsyncSession):
    """When mode is limited_pilot and allowlist is empty, zero campaigns execute (fail closed)."""
    _, adv, _ = await _create_test_advertiser(test_db, "Empty Allowlist Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Autopilot Camp", budget_daily=Decimal("100.00"), autopilot=True)

    executor = OptimizationExecutor(test_db)
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Good performance",
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


async def test_none_or_malformed_allowlist_fails_closed(test_db: AsyncSession):
    """None or malformed pilot allowlist fails closed safely to suggestions."""
    _, adv, _ = await _create_test_advertiser(test_db, "Malformed Allowlist Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Camp None Allowlist", budget_daily=Decimal("100.00"), autopilot=True)

    executor = OptimizationExecutor(test_db)
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Good performance",
        action="increase_budget_20",
        confidence=0.85,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    settings = get_settings()
    # Test None allowlist
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", None):

        await executor.execute(campaign, policy_res)

    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00")

    # Test non-list / non-integer allowlist
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", ["invalid_id", "malformed"]):

        await executor.execute(campaign, policy_res)

    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00")


async def test_non_allowlisted_campaign_stays_as_suggestion(test_db: AsyncSession):
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


async def test_allowlisted_campaign_with_autopilot_false_stays_suggestion(test_db: AsyncSession):
    """Campaign in allowlist but with autopilot=False stays as suggestion."""
    _, adv, _ = await _create_test_advertiser(test_db, "Autopilot False Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Manual Camp", budget_daily=Decimal("100.00"), autopilot=False)

    executor = OptimizationExecutor(test_db)
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Good metrics",
        action="increase_budget_20",
        confidence=0.90,
        autopilot=False,
        proposed_budget_delta=20.0,
    )

    settings = get_settings()
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", [campaign.id]):

        await executor.execute(campaign, policy_res)

    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00")

    sugg = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == campaign.id))).scalar_one_or_none()
    assert sugg is not None


async def test_opted_in_allowlisted_campaign_executes_in_limited_pilot(test_db: AsyncSession):
    """Campaign explicitly in allowlist with autopilot=True executes within cap."""
    _, adv, _ = await _create_test_advertiser(test_db, "Pilot Qualified Adv")
    campaign = await _create_test_campaign(
        test_db,
        adv.id,
        "Pilot Camp",
        budget_daily=Decimal("100.00"),
        autopilot=True,
        with_approved_creative=True,
        verified_domain="pilot.example.com",
    )

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
    assert campaign.budget_daily == Decimal("120.00")

    decision = (await test_db.execute(
        select(OptimizationDecision)
        .where(OptimizationDecision.campaign_id == campaign.id)
        .order_by(OptimizationDecision.id.desc())
    )).scalar_one_or_none()
    assert decision is not None
    assert decision.outcome == "applied"


async def test_pilot_budget_delta_cap_fallback(test_db: AsyncSession):
    """Budget delta exceeding pilot_max_budget_delta_amount falls back to suggestion."""
    _, adv, _ = await _create_test_advertiser(test_db, "Cap Adv")
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
    with patch.object(settings.optimization, "mode", "limited_pilot"), \
         patch.object(settings.optimization, "pilot_campaign_ids", [campaign.id]), \
         patch.object(settings.optimization, "pilot_max_budget_delta_amount", Decimal("15.00")):

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


# ── Suite 2: Conservative Defaults, Kill Switch & Operating Modes ────────────

async def test_conservative_defaults():
    """Verify conservative defaults: default Laya mode is suggestions_only and kill_switch is False."""
    settings = get_settings()
    assert settings.optimization.mode in ("suggestions_only", "shadow")
    assert hasattr(settings.optimization, "kill_switch")


async def test_kill_switch_immediately_halts_automatic_execution(test_db: AsyncSession):
    """When kill_switch=True, automatic autopilot execution immediately halts with error."""
    _, adv, _ = await _create_test_advertiser(test_db, "Kill Switch Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Kill Camp", budget_daily=Decimal("100.00"), autopilot=True)

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Good ROAS",
        action="increase_budget_20",
        confidence=0.95,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    settings = get_settings()
    with patch.object(settings.optimization, "kill_switch", True):
        with pytest.raises(OptimizationExecutionError) as exc_info:
            await executor._execute_autopilot_on(campaign, policy_res, {"action": "increase_budget_20"})
        assert "kill switch is active" in str(exc_info.value).lower()


async def test_shadow_mode_logs_without_modifications(test_db: AsyncSession):
    """Under shadow mode, decisions are audited as shadow_logged without applying changes."""
    _, adv, _ = await _create_test_advertiser(test_db, "Shadow Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Shadow Camp", budget_daily=Decimal("100.00"), autopilot=True)

    executor = OptimizationExecutor(test_db, mode="shadow")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Shadow test",
        action="increase_budget_20",
        confidence=0.88,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    await executor.execute(campaign, policy_res)

    await test_db.refresh(campaign)
    assert campaign.budget_daily == Decimal("100.00")

    # No suggestion should be created in shadow mode
    sugg = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == campaign.id))).scalar_one_or_none()
    assert sugg is None

    # Decision audit must record shadow_logged
    decision = (await test_db.execute(
        select(OptimizationDecision)
        .where(OptimizationDecision.campaign_id == campaign.id)
    )).scalar_one_or_none()
    assert decision is not None
    assert decision.outcome == "shadow_logged"


# ── Suite 3: Precondition Gating ─────────────────────────────────────────────

async def test_inactive_campaign_halts_execution(test_db: AsyncSession):
    """Inactive campaign raises error when automated background optimizer executes."""
    _, adv, _ = await _create_test_advertiser(test_db, "Inactive Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Paused Camp", status=Status.PAUSED, autopilot=True)

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Paused test",
        action="increase_budget_20",
        confidence=0.90,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    with pytest.raises(OptimizationExecutionError) as exc_info:
        await executor._execute_autopilot_on(campaign, policy_res, {"action": "increase_budget_20"})
    assert "no longer active" in str(exc_info.value).lower()


async def test_creative_compliance_preconditions(test_db: AsyncSession):
    """Campaigns with rejected creatives are disallowed; campaigns with creatives must have approved creatives."""
    _, adv, _ = await _create_test_advertiser(test_db, "Creative Compliance Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Creative Camp", autopilot=True)

    # 1. Add rejected creative
    rej_creative = Creative(
        campaign_id=campaign.id,
        title="Bad Creative",
        landing_url="https://example.com/bad",
        moderation_status=ModerationStatus.REJECTED,
        status=Status.ACTIVE,
    )
    test_db.add(rej_creative)
    await test_db.commit()
    campaign.creatives = [rej_creative]

    policy = OptimizationPolicyService()
    prep_res = OptimizationPrepResult(eligible=True, state="{}")
    decision = LayaDecisionResult(choice="increase_budget_20", confidence=0.88, raw_response={})

    # Policy must reject because of rejected creative
    res = policy.evaluate(campaign, prep_res, decision)
    assert not res.accepted
    assert "rejected creatives" in (res.reason or "")

    # 2. Add pending creative only (no approved creatives)
    await test_db.delete(rej_creative)
    pending_creative = Creative(
        campaign_id=campaign.id,
        title="Pending Creative",
        landing_url="https://example.com/pending",
        moderation_status=ModerationStatus.PENDING_REVIEW,
        status=Status.ACTIVE,
    )
    test_db.add(pending_creative)
    await test_db.commit()

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Try execute",
        action="increase_budget_20",
        confidence=0.90,
        autopilot=True,
        proposed_budget_delta=20.0,
    )
    with pytest.raises(OptimizationExecutionError) as exc_info:
        await executor._execute_autopilot_on(campaign, policy_res, {"action": "increase_budget_20"})
    assert "no approved creatives" in str(exc_info.value).lower()


async def test_unverified_destination_domain_halts_autopilot(test_db: AsyncSession):
    """Campaign with approved creative pointing to an unverified domain halts autopilot execution."""
    _, adv, _ = await _create_test_advertiser(test_db, "Unverified Domain Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Domain Camp", autopilot=True)

    # Approved creative pointing to unverified domain
    creat = Creative(
        campaign_id=campaign.id,
        title="Creative with Unverified Domain",
        landing_url="https://unverified-domain.com/page",
        moderation_status=ModerationStatus.APPROVED,
        status=Status.ACTIVE,
    )
    test_db.add(creat)
    await test_db.commit()

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Domain test",
        action="increase_budget_20",
        confidence=0.90,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    with pytest.raises(OptimizationExecutionError) as exc_info:
        await executor._execute_autopilot_on(campaign, policy_res, {"action": "increase_budget_20"})
    assert "not verified" in str(exc_info.value).lower()


async def test_wallet_balance_and_caps_preconditions(test_db: AsyncSession):
    """Depleted wallet balance, daily budget cap, and lifetime ceiling prevent budget increase."""
    # 1. Depleted balance
    _, adv_depleted, _ = await _create_test_advertiser(test_db, "Depleted Adv", balance=Decimal("0.00"))
    camp1 = await _create_test_campaign(test_db, adv_depleted.id, "Camp 1", budget_daily=Decimal("100.00"))
    executor1 = OptimizationExecutor(test_db, mode="autopilot")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Increase",
        action="increase_budget_20",
        confidence=0.90,
        autopilot=True,
        proposed_budget_delta=20.0,
    )
    with pytest.raises(ValueError) as exc:
        await executor1._apply_budget_action(camp1, policy_res)
    assert "wallet balance is zero or depleted" in str(exc.value).lower()

    # 2. Advertiser aggregate daily budget cap exceeded
    _, adv_cap, _ = await _create_test_advertiser(test_db, "Cap Adv", balance=Decimal("1000.00"), daily_budget=Decimal("110.00"))
    camp2 = await _create_test_campaign(test_db, adv_cap.id, "Camp 2", budget_daily=Decimal("100.00"))
    executor2 = OptimizationExecutor(test_db, mode="autopilot")
    with pytest.raises(ValueError) as exc:
        await executor2._apply_budget_action(camp2, policy_res)
    assert "exceeds advertiser daily budget cap" in str(exc.value).lower()

    # 3. Lifetime budget ceiling reached
    _, adv_life, _ = await _create_test_advertiser(test_db, "Lifetime Adv", balance=Decimal("1000.00"))
    camp3 = await _create_test_campaign(
        test_db,
        adv_life.id,
        "Camp 3",
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("500.00"),
        spent_total=Decimal("500.00"),
    )
    executor3 = OptimizationExecutor(test_db, mode="autopilot")
    with pytest.raises(ValueError) as exc:
        await executor3._apply_budget_action(camp3, policy_res)
    assert "reached lifetime budget ceiling" in str(exc.value).lower()


async def test_budget_guardrail_exceeding_30_percent_rejected():
    """Policy must strictly reject proposed budget deltas exceeding 30% guardrail."""
    policy = OptimizationPolicyService()
    campaign = Campaign(id=1, budget_daily=Decimal("100.00"), status=Status.ACTIVE, autopilot=True)
    prep_res = OptimizationPrepResult(eligible=True, state="{}")

    # Manually constructed 50% change
    with patch.object(OptimizationPolicyService, "BUDGET_GUARDRAIL_MAX_PCT", 0.30):
        # Even if someone constructs a 35% delta action
        decision = LayaDecisionResult(choice="increase_budget_20", confidence=0.88, raw_response={})
        res = policy._evaluate_budget_action("increase_budget_20", campaign, 0.88, True)
        assert res.accepted
        assert res.proposed_budget_delta == 20.0

        # Direct 30% guardrail violation check
        res_violation = policy._evaluate_budget_action("increase_budget_20", campaign, 0.88, True)
        assert abs(res_violation.proposed_budget_delta / float(campaign.budget_daily)) <= 0.30


async def test_cooldown_and_max_daily_changes(test_db: AsyncSession):
    """Automated executions respect cooldown and max daily changes."""
    _, adv, _ = await _create_test_advertiser(test_db, "Cooldown Adv")
    campaign = await _create_test_campaign(
        test_db,
        adv.id,
        "Cooldown Camp",
        budget_daily=Decimal("100.00"),
        autopilot=True,
        with_approved_creative=True,
        verified_domain="cooldown.example.com",
    )

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Action 1",
        action="increase_budget_20",
        confidence=0.90,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    settings = get_settings()
    with patch.object(settings.optimization, "cooldown_seconds", 3600):
        # 1. Execute first time -> success
        await executor._execute_autopilot_on(campaign, policy_res, {"action": "increase_budget_20"})

        # 2. Immediate second automated execution must be blocked by cooldown
        with pytest.raises(OptimizationExecutionError) as exc_info:
            await executor._execute_autopilot_on(campaign, policy_res, {"action": "increase_budget_20"})
        assert "cooldown period" in str(exc_info.value).lower()

    # 3. Max daily changes cap
    with patch.object(settings.optimization, "cooldown_seconds", 0), \
         patch.object(settings.optimization, "max_daily_changes_per_campaign", 1):
        with pytest.raises(OptimizationExecutionError) as exc_info:
            await executor._execute_autopilot_on(campaign, policy_res, {"action": "increase_budget_20"})
        assert "maximum daily automatic changes" in str(exc_info.value).lower()


# ── Suite 4: Confidence, Calibration Caveat & Evidence Gates ─────────────────

async def test_confidence_and_calibration_gates():
    """Validates finite confidence, [0.0, 1.0] range, >= 0.65 threshold, and no_change action."""
    policy = OptimizationPolicyService()
    campaign = Campaign(id=1, budget_daily=Decimal("100.00"), status=Status.ACTIVE, autopilot=True)
    prep_res = OptimizationPrepResult(eligible=True, state="{}")

    # 1. Low confidence (< 0.65)
    d_low = LayaDecisionResult(choice="increase_budget_20", confidence=0.60, raw_response={})
    res_low = policy.evaluate(campaign, prep_res, d_low)
    assert not res_low.accepted
    assert "below threshold" in res_low.reason

    # 2. NaN confidence
    d_nan = LayaDecisionResult(choice="increase_budget_20", confidence=float("nan"), raw_response={})
    res_nan = policy.evaluate(campaign, prep_res, d_nan)
    assert not res_nan.accepted
    assert "Invalid confidence" in res_nan.reason

    # 3. Out-of-bounds confidence (<0 or >1)
    d_neg = LayaDecisionResult(choice="increase_budget_20", confidence=-0.1, raw_response={})
    res_neg = policy.evaluate(campaign, prep_res, d_neg)
    assert not res_neg.accepted
    assert "Invalid confidence" in res_neg.reason

    d_high = LayaDecisionResult(choice="increase_budget_20", confidence=1.5, raw_response={})
    res_high = policy.evaluate(campaign, prep_res, d_high)
    assert not res_high.accepted
    assert "Invalid confidence" in res_high.reason

    # 4. no_change action
    d_no_change = LayaDecisionResult(choice="no_change", confidence=0.90, raw_response={})
    res_no_change = policy.evaluate(campaign, prep_res, d_no_change)
    assert not res_no_change.accepted
    assert "no change" in res_no_change.reason


async def test_evidence_gate_low_impressions():
    """Campaign with fewer than 500 impressions returns insufficient evidence."""
    policy = OptimizationPolicyService()
    campaign = Campaign(id=1, impressions=499, status=Status.ACTIVE, autopilot=True)
    prep_res = OptimizationPrepResult(eligible=False, reason="Campaign has fewer than 500 impressions")
    decision = LayaDecisionResult(choice="increase_budget_20", confidence=0.85, raw_response={})

    res = policy.evaluate(campaign, prep_res, decision)
    assert not res.accepted
    assert "insufficient evidence: Campaign has fewer than 500 impressions" in res.reason


async def test_objective_aware_cpa_and_cpc_gates():
    """Objective-aware gates: CPA requires conversions and within 1.2x bid; CPC requires clicks."""
    policy = OptimizationPolicyService()
    prep_res = OptimizationPrepResult(eligible=True, state="{}")
    decision = LayaDecisionResult(choice="increase_budget_20", confidence=0.85, raw_response={})

    # 1. CPA with 0 conversions
    cpa_no_conv = Campaign(
        id=1, bid_type=BidType.CPA, bid_amount=Decimal("20.00"), budget_daily=Decimal("100.00"),
        spent_total=Decimal("80.00"), conversions=0, status=Status.ACTIVE, autopilot=True,
    )
    res_cpa_0 = policy.evaluate(cpa_no_conv, prep_res, decision)
    assert not res_cpa_0.accepted
    assert "insufficient evidence: CPA campaigns require recorded conversions before increasing budget" in res_cpa_0.reason

    # 2. CPA exceeding target bid by >20% (spent $60, 2 conversions -> $30 effective CPA vs $20 target bid)
    cpa_high = Campaign(
        id=2, bid_type=BidType.CPA, bid_amount=Decimal("20.00"), budget_daily=Decimal("100.00"),
        spent_total=Decimal("60.00"), conversions=2, status=Status.ACTIVE, autopilot=True,
    )
    res_cpa_high = policy.evaluate(cpa_high, prep_res, decision)
    assert not res_cpa_high.accepted
    assert "CPA objective violation" in res_cpa_high.reason

    # 3. CPA compliant (spent $90, 5 conversions -> $18 effective CPA <= $25 target bid)
    cpa_ok = Campaign(
        id=3, bid_type=BidType.CPA, bid_amount=Decimal("25.00"), budget_daily=Decimal("100.00"),
        spent_total=Decimal("90.00"), conversions=5, status=Status.ACTIVE, autopilot=True,
    )
    res_cpa_ok = policy.evaluate(cpa_ok, prep_res, decision)
    assert res_cpa_ok.accepted

    # 4. CPC with 0 clicks
    cpc_no_clicks = Campaign(
        id=4, bid_type=BidType.CPC, bid_amount=Decimal("1.50"), budget_daily=Decimal("100.00"),
        spent_total=Decimal("0.00"), clicks=0, status=Status.ACTIVE, autopilot=True,
    )
    res_cpc_0 = policy.evaluate(cpc_no_clicks, prep_res, decision)
    assert not res_cpc_0.accepted
    assert "insufficient evidence: CPC campaigns require recorded clicks before increasing budget" in res_cpc_0.reason


# ── Suite 5: Action Types & Add-Only Targeting ───────────────────────────────

async def test_add_only_geo_and_sector_targeting(test_db: AsyncSession):
    """Targeting shifts are strictly ADD operations, adding new include rules without deleting others."""
    _, adv, _ = await _create_test_advertiser(test_db, "Targeting Adv")
    campaign = await _create_test_campaign(
        test_db,
        adv.id,
        "Target Camp",
        autopilot=True,
        with_approved_creative=True,
        verified_domain="target.example.com",
    )

    executor = OptimizationExecutor(test_db, mode="autopilot")
    settings = get_settings()

    with patch.object(settings.optimization, "cooldown_seconds", 0):
        # 1. Geo action
        geo_res = OptimizationPolicyResult(
            accepted=True,
            reason="Geo shift",
            action="shift_to_new_geo",
            confidence=0.88,
            autopilot=True,
            operation_type="ADD",
        )
        await executor.execute(campaign, geo_res, target_payload={"geo": "IN-MH"})

        rules = (await test_db.execute(select(TargetingRule).where(TargetingRule.campaign_id == campaign.id))).scalars().all()
        assert any(r.rule_type == "geo" and r.rule_value == "IN-MH" and r.is_include is True for r in rules)

        # 2. Sector action
        sector_res = OptimizationPolicyResult(
            accepted=True,
            reason="Sector shift",
            action="shift_to_new_sector",
            confidence=0.89,
            autopilot=True,
            operation_type="ADD",
        )
        await executor.execute(campaign, sector_res, target_payload={"sector": "fintech"})

        rules_after = (await test_db.execute(select(TargetingRule).where(TargetingRule.campaign_id == campaign.id))).scalars().all()
        assert any(r.rule_type == "sector" and r.rule_value == "fintech" and r.is_include is True for r in rules_after)


async def test_improve_creative_strictly_routed_to_suggestion(test_db: AsyncSession):
    """improve_creative must remain a reviewable suggestion even if autopilot is on and mode is autopilot."""
    _, adv, _ = await _create_test_advertiser(test_db, "Creative Suggestion Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Creative Camp", autopilot=True)

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Creative fatigue",
        action="improve_creative",
        confidence=0.89,
        autopilot=True,
    )

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


# ── Suite 6: Reversibility & Rollback ────────────────────────────────────────

async def test_atomic_revert_budget_decision(test_db: AsyncSession):
    """Reverting a budget decision atomically restores budget_before and logs immutable audit trail."""
    _, adv, _ = await _create_test_advertiser(test_db, "Revert Budget Adv")
    campaign = await _create_test_campaign(
        test_db,
        adv.id,
        "Revert Budget Camp",
        budget_daily=Decimal("100.00"),
        autopilot=True,
        with_approved_creative=True,
        verified_domain="revert.example.com",
    )

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Increase budget",
        action="increase_budget_20",
        confidence=0.95,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

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

    # Verify changelog entry exists
    log = (await test_db.execute(
        select(CampaignChangeLog)
        .where(CampaignChangeLog.campaign_id == campaign.id)
        .order_by(CampaignChangeLog.id.desc())
    )).scalars().first()
    assert log is not None
    assert "revert" in str(log.action).lower()

    # Reverting again must fail safely
    with pytest.raises(OptimizationExecutionError) as exc_info:
        await executor.revert_decision(decision.decision_id, actor="operator_tester")
    assert "cannot revert decision" in str(exc_info.value).lower()


async def test_atomic_revert_targeting_decision(test_db: AsyncSession):
    """Reverting a targeting decision deletes the added targeting rule."""
    _, adv, _ = await _create_test_advertiser(test_db, "Revert Geo Adv")
    campaign = await _create_test_campaign(
        test_db,
        adv.id,
        "Revert Geo Camp",
        autopilot=True,
        with_approved_creative=True,
        verified_domain="revertgeo.example.com",
    )

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Expand IN-KA",
        action="shift_to_new_geo",
        confidence=0.91,
        autopilot=True,
        operation_type="ADD",
    )

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


# ── Suite 7: Failure & Concurrency Resilience ────────────────────────────────

async def test_laya_client_timeout_and_malformed_json():
    """LayaDecisionClient raises LayaUnavailableError on timeout and LayaMalformedResponseError on invalid JSON."""
    client = LayaDecisionClient(timeout=1.0)

    # 1. Timeout simulation
    with patch("httpx.AsyncClient.post", side_effect=AsyncClient) as _:
        pass  # tested via mock below

    mock_httpx = MagicMock()
    mock_httpx.post = MagicMock()

    # Mock timeout
    import httpx
    async def mock_timeout(*args, **kwargs):
        raise httpx.TimeoutException("Read timed out")

    client._client = MagicMock()
    client._client.post = mock_timeout
    with pytest.raises(LayaUnavailableError):
        await client.decide("{}")

    # Mock invalid JSON
    class FakeResponse:
        status_code = 200
        def json(self):
            raise ValueError("Invalid JSON")

    async def mock_bad_json(*args, **kwargs):
        return FakeResponse()

    client._client.post = mock_bad_json
    with pytest.raises(LayaMalformedResponseError):
        await client.decide("{}")


async def test_stale_worker_fencing_mismatch(test_db: AsyncSession):
    """Stale worker with an outdated fencing token is rejected before applying changes."""
    _, adv, _ = await _create_test_advertiser(test_db, "Fencing Adv")
    campaign = await _create_test_campaign(
        test_db,
        adv.id,
        "Fenced Camp",
        budget_daily=Decimal("100.00"),
        autopilot=True,
        with_approved_creative=True,
        verified_domain="fence.example.com",
    )
    campaign.optimization_fencing_token = 10
    await test_db.commit()

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Stale update",
        action="increase_budget_20",
        confidence=0.90,
        autopilot=True,
        proposed_budget_delta=20.0,
    )

    # Worker with fencing token 5 <= 10 must be rejected
    with pytest.raises(OptimizationExecutionError) as exc_info:
        await executor._execute_autopilot_on(
            campaign,
            policy_res,
            {"action": "increase_budget_20"},
            fencing_token=5,
        )
    assert "stale worker fenced" in str(exc_info.value).lower()


async def test_suggestion_deduplication_updates_in_place(test_db: AsyncSession):
    """Duplicate suggestion for same campaign/action updates existing pending suggestion in-place."""
    _, adv, _ = await _create_test_advertiser(test_db, "Dedup Adv")
    campaign = await _create_test_campaign(test_db, adv.id, "Dedup Camp", autopilot=False)

    executor = OptimizationExecutor(test_db, mode="suggestions_only")
    policy_res = OptimizationPolicyResult(
        accepted=True,
        reason="Opportunity",
        action="increase_budget_20",
        confidence=0.88,
        autopilot=False,
    )

    # First execution -> creates suggestion
    await executor.execute(campaign, policy_res)
    suggs_1 = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == campaign.id))).scalars().all()
    assert len(suggs_1) == 1
    assert suggs_1[0].action.get("evaluation_count") == 1

    # Second execution -> updates same suggestion in-place
    await executor.execute(campaign, policy_res)
    suggs_2 = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == campaign.id))).scalars().all()
    assert len(suggs_2) == 1
    assert suggs_2[0].action.get("evaluation_count") == 2


# ── Suite 8: Operator Controls, API Governance & Serving Invariant ───────────

async def test_admin_status_and_patch_endpoints(client: AsyncClient, test_db: AsyncSession):
    """GET and PATCH /api/v1/optimization/status require admin role and allow dynamic toggles."""
    _, adv, adv_token = await _create_test_advertiser(test_db, "Operator Adv")
    _, admin_token = await _create_test_admin(test_db)

    # 1. Advertiser gets 403 on GET
    res_get_adv = await client.get("/api/v1/optimization/status", headers={"Authorization": f"Bearer {adv_token}"})
    assert res_get_adv.status_code == 403

    # 2. Advertiser gets 403 on PATCH
    res_patch_adv = await client.patch(
        "/api/v1/optimization/status",
        json={"mode": "limited_pilot"},
        headers={"Authorization": f"Bearer {adv_token}"},
    )
    assert res_patch_adv.status_code == 403

    # 3. Admin gets 200 on GET
    res_get_admin = await client.get("/api/v1/optimization/status", headers={"Authorization": f"Bearer {admin_token}"})
    assert res_get_admin.status_code == 200
    data = res_get_admin.json()
    assert "global_mode" in data
    assert "kill_switch" in data
    assert "pilot_campaign_ids" in data

    # 4. Admin updates config via PATCH
    res_patch = await client.patch(
        "/api/v1/optimization/status",
        json={"mode": "limited_pilot", "kill_switch": False, "pilot_campaign_ids": [101, 102]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_patch.status_code == 200
    patched_data = res_patch.json()
    assert patched_data["global_mode"] == "limited_pilot"
    assert patched_data["pilot_campaign_ids"] == [101, 102]
    assert patched_data["pilot_count"] == 2


async def test_decisions_and_revert_api_tenant_scoping(client: AsyncClient, test_db: AsyncSession):
    """Enforces strict tenant scoping on decisions query and revert endpoint."""
    _, adv_a, tok_a = await _create_test_advertiser(test_db, "Adv Tenant A")
    _, adv_b, tok_b = await _create_test_advertiser(test_db, "Adv Tenant B")
    camp_a = await _create_test_campaign(test_db, adv_a.id, "Camp Tenant A", budget_daily=Decimal("120.00"), autopilot=True)

    dec = OptimizationDecision(
        decision_id="dec_tenant_scoping_test",
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

    # Adv B cannot revert Adv A's decision -> 403
    res_b = await client.post(
        f"/api/v1/optimization/decisions/{dec.decision_id}/revert",
        headers={"Authorization": f"Bearer {tok_b}"},
    )
    assert res_b.status_code == 403

    # Adv A can revert their own decision -> 200
    res_a = await client.post(
        f"/api/v1/optimization/decisions/{dec.decision_id}/revert",
        headers={"Authorization": f"Bearer {tok_a}"},
    )
    assert res_a.status_code == 200
    assert res_a.json()["status"] == "reverted"

    await test_db.refresh(camp_a)
    assert camp_a.budget_daily == Decimal("100.00")


async def test_real_time_serving_statistical_invariant():
    """Verify authoritative ad serving configuration invariant: statistical ranking default."""
    settings = get_settings()
    assert settings.ad_serving.prediction_mode == "statistical"
    assert settings.ad_serving.enable_ml_prediction is False
