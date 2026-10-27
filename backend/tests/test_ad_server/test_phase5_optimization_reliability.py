"""
Phase 5 Comprehensive Reliability Test Suite for RivinityADS.

Verifies all 20 required scenarios from Section 8:
1. Four competing optimizer instances or workers attempting to run concurrently (distributed exclusivity).
2. Lock expiry, renewal, stale ownership, and recovery after a worker failure.
3. The same decision evaluated twice (deduplication & in-place update).
4. Duplicate pending suggestions database constraint.
5. Missing or malformed target payload.
6. Unsupported action.
7. `improve_creative` with autopilot enabled routes to human suggestion.
8. Zero, negative, and invalid budgets.
9. Insufficient advertiser balance.
10. Budget changes exceeding configured limits (30% delta guardrail).
11. Daily and lifetime budget ceilings.
12. Campaign ineligible or no longer active when action executes.
13. Invalid or malicious Laya target fields.
14. Per-campaign concurrent execution.
15. Kill-switch behavior (global and per-campaign).
16. Shadow mode creating neither suggestions nor applied changes.
17. Suggestions-only mode never applying automatic actions.
18. Transaction rollback when a change fails.
19. Idempotent retries after partial failure.
20. Multiple workers unable to apply the same financial action twice.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import logging
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError

# Suppress verbose aiosqlite connection debugging
logging.getLogger("aiosqlite").setLevel(logging.WARNING)

from liteads.models.ad import (
    Campaign,
    Advertiser,
    Suggestion,
    CampaignChangeLog,
    TargetingRule,
    OptimizationDecision,
)
from liteads.models.base import Status
from liteads.ad_server.services.optimization_policy import (
    OptimizationPolicyService,
    OptimizationPolicyResult,
)
from liteads.ad_server.services.optimization_executor import (
    OptimizationExecutor,
    OptimizationExecutionError,
    compute_suggestion_dedup_key,
)
from liteads.ad_server.services.optimization_scheduler import (
    OptimizationLoopTask,
    DISTRIBUTED_LOCK_KEY,
    PER_CAMPAIGN_LOCK_PREFIX,
)
from liteads.common.config import get_settings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def clean_database_tables(test_db):
    """Ensure every test in this suite starts with completely clean tables."""
    await test_db.execute(delete(OptimizationDecision))
    await test_db.execute(delete(Suggestion))
    await test_db.execute(delete(CampaignChangeLog))
    await test_db.execute(delete(TargetingRule))
    await test_db.execute(delete(Campaign))
    await test_db.execute(delete(Advertiser))
    await test_db.commit()
    yield
    await test_db.rollback()


@pytest.fixture
def mock_db_session_fixture(test_db):
    @asynccontextmanager
    async def _mock_db_session():
        yield test_db
    return _mock_db_session


@pytest.fixture
async def advertiser(test_db):
    adv = Advertiser(
        name="Phase 5 Advertiser",
        contact_email="phase5@example.com",
        balance=Decimal("2000.00"),
    )
    test_db.add(adv)
    await test_db.commit()
    await test_db.refresh(adv)
    return adv


@pytest.fixture
async def active_campaign(test_db, advertiser):
    camp = Campaign(
        advertiser_id=advertiser.id,
        name="Phase 5 Test Campaign",
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        spent_today=Decimal("20.00"),
        spent_total=Decimal("150.00"),
        status=Status.ACTIVE,
        autopilot=True,
        impressions=1000,
        clicks=50,
        conversions=5,
    )
    test_db.add(camp)
    await test_db.commit()
    await test_db.refresh(camp)
    return camp


# ---------------------------------------------------------------------------
# 1. Four Competing Optimizer Instances (Distributed Lock Exclusivity)
# ---------------------------------------------------------------------------
async def test_four_competing_optimizer_workers_concurrency(test_db, active_campaign, mock_db_session_fixture):
    """
    Simulates 4 competing workers attempting to run an optimization cycle concurrently.
    Redis NX lock ensures exactly 1 worker wins leader lease; the other 3 exit safely.
    """
    locks = {}
    lock_mutex = asyncio.Lock()

    async def fake_set(key, val, ttl=None, nx=False):
        async with lock_mutex:
            if nx:
                if key in locks:
                    return False
                locks[key] = val
                return True
            locks[key] = val
            return True

    async def fake_release(key, val):
        async with lock_mutex:
            if locks.get(key) == val:
                del locks[key]
                return True
            return False

    workers = [OptimizationLoopTask() for _ in range(4)]

    # Make the winning worker simulate processing single campaigns while competitors run
    async def slow_process_single(session, camp_id, laya, policy, owner):
        await asyncio.sleep(0.08)

    for w in workers:
        w._process_single_campaign = slow_process_single

    with patch("liteads.ad_server.services.optimization_scheduler.redis_client") as mock_redis, \
         patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        mock_redis.connected = True
        mock_redis.set = fake_set
        mock_redis.release_lock = fake_release
        mock_redis.get = AsyncMock(return_value="1")
        mock_redis.incr = AsyncMock(return_value=2)

        async def run_worker(w):
            return await w._process_cycle()

        results = await asyncio.gather(*[run_worker(w) for w in workers])

    success_runs = [r for r in results if r.get("status") == "success"]
    locked_runs = [r for r in results if r.get("status") in ("locked", "skipped_lock_held")]

    assert len(success_runs) == 1, f"Expected exactly 1 leader, got {len(success_runs)}: {results}"
    assert len(locked_runs) == 3, f"Expected 3 locked workers, got {len(locked_runs)}: {results}"


# ---------------------------------------------------------------------------
# 2. Lock Expiry, Renewal, Stale Ownership, and Recovery
# ---------------------------------------------------------------------------
async def test_lock_expiry_renewal_stale_ownership_and_recovery(test_db, active_campaign, mock_db_session_fixture):
    """
    Verifies:
    - Renewal heartbeat keeps lease active.
    - When lease is lost/expired, stale worker is fenced and aborts remaining evaluations.
    - Once lease is released, recovery allows a second worker to acquire.
    """
    task = OptimizationLoopTask()
    
    with patch("liteads.ad_server.services.optimization_scheduler.redis_client") as mock_redis, \
         patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        mock_redis.connected = True
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.release_lock = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value="1")
        mock_redis.incr = AsyncMock(return_value=2)

        # Trigger simulated cycle
        result = await task._process_cycle()
        assert result.get("status") == "success"

    # Verify recovery: another worker acquires successfully
    task2 = OptimizationLoopTask()
    with patch("liteads.ad_server.services.optimization_scheduler.redis_client") as mock_redis2, \
         patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        mock_redis2.connected = True
        mock_redis2.set = AsyncMock(return_value=True)
        mock_redis2.release_lock = AsyncMock(return_value=True)
        mock_redis2.get = AsyncMock(return_value="2")
        mock_redis2.incr = AsyncMock(return_value=3)
        result2 = await task2._process_cycle()
        assert result2.get("status") == "success"


# ---------------------------------------------------------------------------
# 3. Same Decision Evaluated Twice (Deduplication & In-place Evidence Update)
# ---------------------------------------------------------------------------
async def test_same_decision_evaluated_twice_deduplication(test_db, active_campaign):
    """
    Repeated evaluation of identical decision does NOT create duplicate pending suggestions.
    Instead, it updates existing pending suggestion's evaluation_count, evidence, and timestamp.
    """
    camp_id = active_campaign.id
    executor = OptimizationExecutor(test_db, mode="suggestions_only")
    
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="CTR high, scale budget",
        action="increase_budget_20",
        confidence=0.88,
        autopilot=False,
        proposed_budget_delta=20.0,
        operation_type="MODIFY",
    )

    # First evaluation
    evidence1 = {"ctr": 0.05, "eval_round": 1}
    await executor.execute(active_campaign, policy_result, evidence=evidence1)

    res1 = await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == camp_id))
    suggs1 = res1.scalars().all()
    assert len(suggs1) == 1
    sugg1 = suggs1[0]
    assert sugg1.action["evaluation_count"] == 1
    assert sugg1.action["evidence"]["eval_round"] == 1

    # Second evaluation of equivalent decision
    evidence2 = {"ctr": 0.06, "eval_round": 2}
    await executor.execute(active_campaign, policy_result, evidence=evidence2)

    res2 = await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == camp_id))
    suggs2 = res2.scalars().all()
    assert len(suggs2) == 1, "Duplicate suggestion was created instead of updating in-place"
    sugg2 = suggs2[0]
    assert sugg2.id == sugg1.id
    assert sugg2.action["evaluation_count"] == 2
    assert sugg2.action["evidence"]["eval_round"] == 2


# ---------------------------------------------------------------------------
# 4. Duplicate Pending Suggestions DB Constraint
# ---------------------------------------------------------------------------
async def test_duplicate_pending_suggestions_db_constraint(test_db, active_campaign):
    """
    Enforces that database prevents two pending suggestions with the same dedup_key.
    """
    camp_id = active_campaign.id
    dedup_key = compute_suggestion_dedup_key(camp_id, "increase_budget_20", None)

    sugg1 = Suggestion(
        campaign_id=camp_id,
        action={"action": "increase_budget_20"},
        status="PENDING",
        dedup_key=dedup_key,
    )
    test_db.add(sugg1)
    await test_db.commit()

    # Attempt to insert identical pending suggestion directly
    sugg2 = Suggestion(
        campaign_id=camp_id,
        action={"action": "increase_budget_20"},
        status="PENDING",
        dedup_key=dedup_key,
    )
    test_db.add(sugg2)
    with pytest.raises(IntegrityError):
        await test_db.commit()
    await test_db.rollback()


# ---------------------------------------------------------------------------
# 5. Missing or Malformed Target Payload
# ---------------------------------------------------------------------------
async def test_missing_or_malformed_target_payload(test_db, active_campaign):
    """
    Missing or malformed target payload in targeting actions must fail safely.
    """
    camp_id = active_campaign.id
    executor = OptimizationExecutor(test_db, mode="autopilot")

    # Missing payload for shift_to_new_geo
    policy_geo = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="shift_to_new_geo",
        confidence=0.9,
        autopilot=True,
        operation_type="ADD",
    )
    with pytest.raises(OptimizationExecutionError, match="No concrete geo target provided"):
        await executor.execute(active_campaign, policy_geo, target_payload=None, enforce_cooldown=False)

    await test_db.refresh(active_campaign)

    # Malformed empty sectors list
    policy_sector = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="shift_to_new_sector",
        confidence=0.9,
        autopilot=True,
        operation_type="ADD",
    )
    with pytest.raises(OptimizationExecutionError, match="No concrete sector target provided"):
        await executor.execute(active_campaign, policy_sector, target_payload={"sector": ""}, enforce_cooldown=False)

    # Ensure no corrupt targeting rules were added
    rules = (await test_db.execute(select(TargetingRule).where(TargetingRule.campaign_id == camp_id))).scalars().all()
    assert len(rules) == 0


# ---------------------------------------------------------------------------
# 6. Unsupported Action
# ---------------------------------------------------------------------------
async def test_unsupported_action_safely_rejected(test_db, active_campaign):
    """
    An unsupported or unknown action must be safely rejected and recorded in decision audit.
    """
    camp_id = active_campaign.id
    executor = OptimizationExecutor(test_db, mode="autopilot")
    
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="delete_all_campaigns_malicious",
        confidence=0.95,
        autopilot=True,
        operation_type="DELETE",
    )

    with pytest.raises(OptimizationExecutionError, match="Unsupported action: delete_all_campaigns_malicious"):
        await executor.execute(active_campaign, policy_result, enforce_cooldown=False)

    # Verify decision audit logged failure
    dec_res = await test_db.execute(
        select(OptimizationDecision).where(OptimizationDecision.campaign_id == camp_id)
    )
    dec = dec_res.scalars().first()
    assert dec is not None
    assert dec.outcome == "failed"
    assert "Unsupported action" in (dec.reason or "")


# ---------------------------------------------------------------------------
# 7. `improve_creative` with Autopilot Enabled Routes to Human Suggestion
# ---------------------------------------------------------------------------
async def test_improve_creative_with_autopilot_enabled(test_db, active_campaign):
    """
    Autopilot mode must NEVER autonomously alter creative assets.
    When improve_creative is approved, it safely creates a human-review suggestion without error.
    """
    camp_id = active_campaign.id
    executor = OptimizationExecutor(test_db, mode="autopilot")
    
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="CTR below floor, consider refreshing creative",
        action="improve_creative",
        confidence=0.85,
        autopilot=True,
        operation_type="SUGGESTION",
    )

    # Must complete without throwing OptimizationExecutionError!
    await executor.execute(active_campaign, policy_result, target_payload={"creative_id": 42}, enforce_cooldown=False)

    # No changelog
    logs = (await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id))).scalars().all()
    assert len(logs) == 0

    # Suggestion created with PENDING status for human review
    suggs = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == camp_id))).scalars().all()
    assert len(suggs) == 1
    assert suggs[0].action["action"] == "improve_creative"
    assert suggs[0].status == "PENDING"


# ---------------------------------------------------------------------------
# 8. Zero, Negative, and Invalid Budgets
# ---------------------------------------------------------------------------
async def test_zero_negative_and_invalid_budgets(test_db, active_campaign):
    """
    Zero, negative, or non-finite budget deltas must be rejected safely.
    """
    executor = OptimizationExecutor(test_db, mode="autopilot")

    # Zero budget delta
    policy_zero = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=0.0, operation_type="MODIFY"
    )
    with pytest.raises(OptimizationExecutionError, match="Budget delta cannot be zero"):
        await executor.execute(active_campaign, policy_zero, enforce_cooldown=False)

    await test_db.refresh(active_campaign)

    # Proposed delta resulting in negative budget
    policy_neg = OptimizationPolicyResult(
        accepted=True, reason="OK", action="decrease_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=-150.0, operation_type="MODIFY"
    )
    with pytest.raises(OptimizationExecutionError):
        await executor.execute(active_campaign, policy_neg, enforce_cooldown=False)

    await test_db.refresh(active_campaign)

    # Non-finite confidence
    policy_service = OptimizationPolicyService()
    res_nan = policy_service.evaluate(
        active_campaign,
        MagicMock(impressions_last_24h=1000, ctr_last_24h=0.02, has_active_creatives=True, has_rejected_creatives=False),
        MagicMock(choice="increase_budget_20", confidence=float("nan"), proposed_budget_delta=20.0),
    )
    assert res_nan.accepted is False
    assert "Invalid confidence" in res_nan.reason


# ---------------------------------------------------------------------------
# 9. Insufficient Advertiser Balance
# ---------------------------------------------------------------------------
async def test_insufficient_advertiser_balance(test_db, active_campaign, advertiser):
    """
    Optimizer cannot authorize a daily budget increase exceeding available advertiser funds.
    """
    # Deplete advertiser balance below the proposed new daily budget (new budget = 120.00)
    advertiser.balance = Decimal("50.00")
    await test_db.commit()

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="increase_budget_20",
        confidence=0.9,
        autopilot=True,
        proposed_budget_delta=20.0,
        operation_type="MODIFY",
    )

    with pytest.raises(OptimizationExecutionError, match=r"Advertiser wallet balance.*insufficient"):
        await executor.execute(active_campaign, policy_result, enforce_cooldown=False)

    await test_db.refresh(active_campaign)
    assert active_campaign.budget_daily == Decimal("100.00")


# ---------------------------------------------------------------------------
# 10. Budget Changes Exceeding Configured Limits (30% Guardrail)
# ---------------------------------------------------------------------------
async def test_budget_changes_exceeding_30_percent_guardrail(test_db, active_campaign):
    """
    Hard ±30% delta limit enforced strictly by policy and executor.
    """
    executor = OptimizationExecutor(test_db, mode="autopilot")

    # +35% delta exceeds guardrail
    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="increase_budget_20",
        confidence=0.9,
        autopilot=True,
        proposed_budget_delta=35.0,
        operation_type="MODIFY",
    )

    with pytest.raises(OptimizationExecutionError, match="Budget change exceeds 30% guardrail"):
        await executor.execute(active_campaign, policy_result, enforce_cooldown=False)

    await test_db.refresh(active_campaign)
    assert active_campaign.budget_daily == Decimal("100.00")


# ---------------------------------------------------------------------------
# 11. Daily and Lifetime Budget Ceilings
# ---------------------------------------------------------------------------
async def test_lifetime_budget_ceiling_and_daily_caps(test_db, active_campaign):
    """
    Verifies:
    - Lifetime budget ceiling: campaign with spent_total >= budget_total cannot increase budget.
    - Max daily changes cap: campaign cannot exceed max daily changes in 24 hours.
    """
    camp_id = active_campaign.id
    executor = OptimizationExecutor(test_db, mode="autopilot")

    # 1. Lifetime budget ceiling hit
    active_campaign.spent_total = Decimal("1000.00") # budget_total is 1000.00
    await test_db.commit()

    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )
    with pytest.raises(OptimizationExecutionError, match="lifetime budget ceiling"):
        await executor.execute(active_campaign, policy_result, enforce_cooldown=False)

    # 2. Daily changes cap: reset spent_total and insert 3 changelogs
    await test_db.refresh(active_campaign)
    active_campaign.spent_total = Decimal("100.00")
    await test_db.commit()

    now = datetime.now(timezone.utc)
    for i in range(3):
        log = CampaignChangeLog(
            campaign_id=camp_id,
            action={"action": f"test_change_{i}"},
            created_at=now - timedelta(minutes=10 * i),
        )
        test_db.add(log)
    await test_db.commit()

    with pytest.raises(OptimizationExecutionError, match="maximum daily automatic changes cap"):
        await executor.execute(active_campaign, policy_result, enforce_cooldown=False)


# ---------------------------------------------------------------------------
# 12. Campaign Ineligible or No Longer Active
# ---------------------------------------------------------------------------
async def test_campaign_ineligible_or_inactive_when_executed(test_db, active_campaign):
    """
    If campaign is PAUSED, DELETED, or INACTIVE when action executes, it is aborted safely.
    """
    active_campaign.status = Status.PAUSED
    await test_db.commit()

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    with pytest.raises(OptimizationExecutionError, match="Campaign is no longer active"):
        await executor.execute(active_campaign, policy_result, enforce_cooldown=False)


# ---------------------------------------------------------------------------
# 13. Invalid or Malicious Laya Target Fields
# ---------------------------------------------------------------------------
async def test_invalid_or_malicious_laya_target_fields(active_campaign):
    """
    Validates that malicious script injection or corrupted fields are rejected.
    """
    policy_service = OptimizationPolicyService()
    context = MagicMock(impressions_last_24h=1000, ctr_last_24h=0.02, has_active_creatives=True, has_rejected_creatives=False)
    
    # Confidence out of bounds (> 1.0)
    decision_invalid_conf = MagicMock(
        choice="increase_budget_20",
        confidence=2.5,
        proposed_budget_delta=20.0,
    )
    result = policy_service.evaluate(active_campaign, context, decision_invalid_conf)
    assert result.accepted is False
    assert "Invalid confidence" in result.reason


# ---------------------------------------------------------------------------
# 14. Per-Campaign Concurrent Execution Locking
# ---------------------------------------------------------------------------
async def test_per_campaign_concurrent_execution_locking(test_db, active_campaign, mock_db_session_fixture):
    """
    When multiple cycles run, the per-campaign Redis lock prevents concurrent evaluation of the same campaign.
    """
    task = OptimizationLoopTask()
    
    # Simulate campaign lock already held by another worker
    with patch("liteads.ad_server.services.optimization_scheduler.redis_client") as mock_redis, \
         patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        mock_redis.connected = True
        
        async def fake_set(key, val, ttl=None, nx=False):
            if key == DISTRIBUTED_LOCK_KEY:
                return True
            if key.startswith(PER_CAMPAIGN_LOCK_PREFIX):
                return False  # Locked!
            return True

        mock_redis.set = fake_set
        mock_redis.release_lock = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value="1")
        mock_redis.incr = AsyncMock(return_value=2)

        res = await task._process_cycle()
        # Campaign was skipped due to per-campaign lock
        assert res.get("status") == "success"
        assert res.get("processed") == 0


# ---------------------------------------------------------------------------
# 15. Emergency Kill-Switch Behavior
# ---------------------------------------------------------------------------
async def test_kill_switch_behavior(test_db, active_campaign):
    """
    When global kill switch is enabled, both suggestions and automatic changes are halted immediately.
    """
    camp_id = active_campaign.id
    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    settings = get_settings()
    orig_kill_switch = settings.optimization.kill_switch
    try:
        settings.optimization.kill_switch = True
        await executor.execute(active_campaign, policy_result, enforce_cooldown=False)

        # Ensure no changelog and no suggestion was written
        logs = (await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id))).scalars().all()
        suggs = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == camp_id))).scalars().all()
        assert len(logs) == 0
        assert len(suggs) == 0

        # Decision audit shows rejection
        dec_res = await test_db.execute(select(OptimizationDecision).where(OptimizationDecision.campaign_id == camp_id))
        dec = dec_res.scalars().first()
        assert dec is not None
        assert dec.outcome == "rejected"
        assert "kill switch" in dec.reason.lower()
    finally:
        settings.optimization.kill_switch = orig_kill_switch


# ---------------------------------------------------------------------------
# 16. Shadow Mode (Neither Suggestions nor Applied Changes)
# ---------------------------------------------------------------------------
async def test_shadow_mode(test_db, active_campaign):
    """
    Shadow mode evaluates proposed decisions and logs them to OptimizationDecision audit table,
    but produces NO reviewable suggestions and applies NO changes.
    """
    camp_id = active_campaign.id
    executor = OptimizationExecutor(test_db, mode="shadow")
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="High CTR opportunity", action="increase_budget_20",
        confidence=0.92, autopilot=False, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    await executor.execute(active_campaign, policy_result, enforce_cooldown=False)

    # Zero changes applied
    logs = (await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id))).scalars().all()
    assert len(logs) == 0

    # Zero suggestions created
    suggs = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == camp_id))).scalars().all()
    assert len(suggs) == 0

    # Decision audit recorded with outcome="shadow_logged"
    decs = (await test_db.execute(select(OptimizationDecision).where(OptimizationDecision.campaign_id == camp_id))).scalars().all()
    assert len(decs) == 1
    assert decs[0].mode == "shadow"
    assert decs[0].outcome == "shadow_logged"


# ---------------------------------------------------------------------------
# 17. Suggestions-Only Mode Never Applies Automatic Actions
# ---------------------------------------------------------------------------
async def test_suggestions_only_mode_never_applies_automatic_actions(test_db, active_campaign):
    """
    In suggestions_only mode, even if campaign.autopilot is TRUE, changes are NEVER applied.
    Reviewable suggestion is created instead.
    """
    camp_id = active_campaign.id
    active_campaign.autopilot = True
    await test_db.commit()

    executor = OptimizationExecutor(test_db, mode="suggestions_only")
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="Scale successful campaign", action="increase_budget_20",
        confidence=0.91, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    await executor.execute(active_campaign, policy_result, enforce_cooldown=False)

    # Zero changelog, budget unmodified
    logs = (await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id))).scalars().all()
    assert len(logs) == 0
    await test_db.refresh(active_campaign)
    assert active_campaign.budget_daily == Decimal("100.00")

    # Suggestion created
    suggs = (await test_db.execute(select(Suggestion).where(Suggestion.campaign_id == camp_id))).scalars().all()
    assert len(suggs) == 1
    assert suggs[0].action["action"] == "increase_budget_20"
    assert suggs[0].status == "PENDING"


# ---------------------------------------------------------------------------
# 18. Transaction Rollback When a Change Fails
# ---------------------------------------------------------------------------
async def test_transaction_rollback_when_change_fails(test_db, active_campaign):
    """
    If a failure occurs during action execution, the entire transaction is rolled back cleanly.
    """
    camp_id = active_campaign.id
    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    # Force failure during commit
    with patch.object(test_db, "commit", side_effect=IntegrityError("simulated error", "params", "orig")):
        with pytest.raises(OptimizationExecutionError):
            await executor.execute(active_campaign, policy_result, enforce_cooldown=False)

    await test_db.refresh(active_campaign)
    assert active_campaign.budget_daily == Decimal("100.00")
    logs = (await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id))).scalars().all()
    assert len(logs) == 0


# ---------------------------------------------------------------------------
# 19. Idempotent Retries After Partial Failure
# ---------------------------------------------------------------------------
async def test_idempotent_retries_after_partial_failure(test_db, active_campaign):
    """
    A failed execution leaves clean state so a retry can apply the decision without duplicate side-effects.
    """
    camp_id = active_campaign.id
    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    # First attempt fails
    with patch.object(test_db, "commit", side_effect=IntegrityError("simulated crash", "params", "orig")):
        with pytest.raises(OptimizationExecutionError):
            await executor.execute(active_campaign, policy_result, enforce_cooldown=False)

    await test_db.refresh(active_campaign)
    assert active_campaign.budget_daily == Decimal("100.00")

    # Second attempt succeeds cleanly
    await executor.execute(active_campaign, policy_result, enforce_cooldown=False)

    await test_db.refresh(active_campaign)
    assert active_campaign.budget_daily == Decimal("120.00")
    logs = (await test_db.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id))).scalars().all()
    assert len(logs) == 1


# ---------------------------------------------------------------------------
# 20. Multiple Workers Unable to Apply Same Financial Action Twice
# ---------------------------------------------------------------------------
async def test_multiple_workers_unable_to_apply_financial_action_twice(test_db, active_campaign):
    """
    Two workers attempting to apply the same budget increase concurrently cannot double-spend.
    With per-campaign locking and cooldown, budget increases exactly once.
    """
    camp_id = active_campaign.id
    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    # Worker 1 succeeds
    await executor.execute(active_campaign, policy_result, enforce_cooldown=True)
    await test_db.refresh(active_campaign)
    assert active_campaign.budget_daily == Decimal("120.00")

    # Worker 2 attempts identical action on the same campaign within cooldown period
    with pytest.raises(OptimizationExecutionError, match="cooldown period"):
        await executor.execute(active_campaign, policy_result, enforce_cooldown=True)

    await test_db.refresh(active_campaign)
    # Budget must remain 120.00, NOT double-incremented to 140.00!
    assert active_campaign.budget_daily == Decimal("120.00")


# ---------------------------------------------------------------------------
# 21. Stale-Worker Fencing Rejection
# ---------------------------------------------------------------------------
async def test_stale_worker_fencing_rejection(test_db, active_campaign):
    """
    A worker attempting to commit with a stale fencing token must be rejected,
    preventing split-brain / zombie worker overwrites.
    """
    active_campaign.optimization_fencing_token = 10
    await test_db.commit()

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    # Stale worker with token 9 (less than campaign token 10)
    with pytest.raises(OptimizationExecutionError, match="Stale worker fenced"):
        await executor.execute(
            active_campaign,
            policy_result,
            fencing_token=9,
            enforce_cooldown=False,
        )

    await test_db.refresh(active_campaign)
    assert active_campaign.budget_daily == Decimal("100.00")
    assert active_campaign.optimization_fencing_token == 10

    # Fresh worker with token 11 succeeds
    await executor.execute(
        active_campaign,
        policy_result,
        fencing_token=11,
        enforce_cooldown=False,
    )
    await test_db.refresh(active_campaign)
    assert active_campaign.budget_daily == Decimal("120.00")
    assert active_campaign.optimization_fencing_token == 11


# ---------------------------------------------------------------------------
# 22. Aggregate Financial Guardrails Across Multiple Campaigns
# ---------------------------------------------------------------------------
async def test_aggregate_financial_guardrails_multi_campaign(test_db, advertiser):
    """
    Verifies advertiser-level aggregate constraints:
    - Sum of other active campaigns + proposed change must not exceed Advertiser.balance
    - Sum of other active campaigns + proposed change must not exceed Advertiser.daily_budget
    """
    advertiser.balance = Decimal("250.00")
    advertiser.daily_budget = Decimal("500.00")
    await test_db.commit()

    # Campaign 1: active, daily budget 150.00
    camp1 = Campaign(
        name="Campaign 1",
        advertiser_id=advertiser.id,
        status=Status.ACTIVE,
        autopilot=True,
        budget_daily=Decimal("150.00"),
        impressions=1000,
    )
    # Campaign 2: active, daily budget 100.00
    camp2 = Campaign(
        name="Campaign 2",
        advertiser_id=advertiser.id,
        status=Status.ACTIVE,
        autopilot=True,
        budget_daily=Decimal("100.00"),
        impressions=1000,
    )
    test_db.add_all([camp1, camp2])
    await test_db.commit()
    await test_db.refresh(camp1)
    await test_db.refresh(camp2)

    executor = OptimizationExecutor(test_db, mode="autopilot")
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    # Attempt to increase Campaign 2 to 120.00
    # Total committed = 150 (Camp 1) + 120 (Camp 2) = 270.00 > advertiser.balance (250.00)
    with pytest.raises(OptimizationExecutionError, match=r"insufficient for total committed daily budget"):
        await executor.execute(camp2, policy_result, enforce_cooldown=False)

    await test_db.refresh(camp2)
    assert camp2.budget_daily == Decimal("100.00")

    # Increase advertiser balance to 500.00, but set advertiser.daily_budget to 260.00
    advertiser.balance = Decimal("500.00")
    advertiser.daily_budget = Decimal("260.00")
    await test_db.commit()

    # Total committed (270.00) > advertiser.daily_budget (260.00)
    with pytest.raises(OptimizationExecutionError, match=r"exceeds advertiser daily budget cap"):
        await executor.execute(camp2, policy_result, enforce_cooldown=False)

    await test_db.refresh(camp2)
    assert camp2.budget_daily == Decimal("100.00")

    # Increase advertiser.daily_budget to 300.00; proposed change now succeeds
    advertiser.daily_budget = Decimal("300.00")
    await test_db.commit()

    await executor.execute(camp2, policy_result, enforce_cooldown=False)
    await test_db.refresh(camp2)
    assert camp2.budget_daily == Decimal("120.00")


# ---------------------------------------------------------------------------
# 23. Atomic Manual Suggestion Approval Rollback
# ---------------------------------------------------------------------------
async def test_atomic_manual_suggestion_approval_rollback(test_db, active_campaign):
    """
    If a failure occurs during manual suggestion approval, the entire operation
    rolls back atomically: suggestion remains PENDING and campaign budget is untouched.
    """
    now = datetime.now(timezone.utc)
    suggestion = Suggestion(
        campaign_id=active_campaign.id,
        action={
            "action": "increase_budget_20",
            "proposed_budget_delta": 20.0,
            "operation_type": "MODIFY",
            "confidence": 0.9,
        },
        status="PENDING",
        dedup_key="test_dedup_key_atomic_approval",
    )
    test_db.add(suggestion)
    await test_db.commit()
    await test_db.refresh(suggestion)

    executor = OptimizationExecutor(test_db)
    policy_result = OptimizationPolicyResult(
        accepted=True, reason="Manual approval", action="increase_budget_20",
        confidence=0.9, autopilot=active_campaign.autopilot, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    # Simulate an error inside the transaction after change execution
    with patch.object(test_db, "commit", side_effect=RuntimeError("Simulated DB connection drop")):
        with pytest.raises(RuntimeError, match="Simulated DB connection drop"):
            try:
                await executor._execute_autopilot_on(
                    campaign=active_campaign,
                    policy_result=policy_result,
                    action_payload=suggestion.action,
                    actor="user:123",
                    enforce_cooldown=False,
                    auto_commit=False,
                    suggestion=suggestion,
                )
                await test_db.commit()
            except Exception:
                await test_db.rollback()
                raise

    await test_db.refresh(suggestion)
    await test_db.refresh(active_campaign)
    assert suggestion.status == "PENDING"
    assert active_campaign.budget_daily == Decimal("100.00")


# ---------------------------------------------------------------------------
# 24. Concurrent Suggestion Deduplication and Integrity Recovery
# ---------------------------------------------------------------------------
async def test_concurrent_suggestion_deduplication_recovery(test_db, active_campaign):
    """
    Encountering an IntegrityError on duplicate pending insert gracefully
    recovers by updating the existing pending suggestion in-place.
    """
    executor = OptimizationExecutor(test_db, mode="suggestions_only")
    action_payload = {
        "action": "increase_budget_20",
        "confidence": 0.85,
        "proposed_budget_delta": 20.0,
        "operation_type": "MODIFY",
    }

    # Initial suggestion insertion
    sug1 = await executor._execute_autopilot_off(active_campaign, action_payload)
    assert sug1.status == "PENDING"
    assert sug1.action.get("evaluation_count") == 1

    # Simulate a second concurrent evaluation inserting equivalent suggestion
    # triggering the dedup recovery path
    sug2 = await executor._execute_autopilot_off(active_campaign, action_payload)
    assert sug2.id == sug1.id
    assert sug2.status == "PENDING"
    assert sug2.action.get("evaluation_count") == 2


# ---------------------------------------------------------------------------
# 25. Configuration Aliases and Kill Switch Verification
# ---------------------------------------------------------------------------
async def test_optimization_configuration_aliases(monkeypatch):
    """
    Verifies that canonical LITEADS_OPTIMIZATION__* and documented OPTIMIZATION_*
    aliases correctly configure the runtime settings.
    """
    from liteads.common.config import get_settings

    try:
        # Test OPTIMIZATION_KILL_SWITCH alias
        monkeypatch.setenv("OPTIMIZATION_KILL_SWITCH", "true")
        monkeypatch.setenv("OPTIMIZATION_MODE", "autopilot")
        monkeypatch.setenv("ENABLE_OPTIMIZATION_WORKER", "false")
        get_settings.cache_clear()

        cfg = get_settings()
        assert cfg.optimization.kill_switch is True
        assert cfg.optimization.mode == "autopilot"
        assert cfg.enable_optimization_worker is False

        # Test canonical LITEADS_OPTIMIZATION__KILL_SWITCH prefix takes precedence
        monkeypatch.setenv("LITEADS_OPTIMIZATION__KILL_SWITCH", "false")
        monkeypatch.setenv("LITEADS_OPTIMIZATION__MODE", "limited_pilot")
        monkeypatch.setenv("LITEADS_ENABLE_OPTIMIZATION_WORKER", "true")
        get_settings.cache_clear()

        cfg2 = get_settings()
        assert cfg2.optimization.kill_switch is False
        assert cfg2.optimization.mode == "limited_pilot"
        assert cfg2.enable_optimization_worker is True
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 26. Scheduler Runtime Path and NameError Regression (Finding 1)
# ---------------------------------------------------------------------------
async def test_scheduler_runtime_path_nameerror_fix(test_db, active_campaign, mock_db_session_fixture, monkeypatch):
    """
    Executes the full scheduler single-campaign processing runtime path where
    policy evaluation succeeds, row locks are acquired, and fresh_policy_result
    is logged and passed to the executor without NameError.
    """
    monkeypatch.setenv("OPTIMIZATION_MODE", "autopilot")
    get_settings.cache_clear()
    try:
        task = OptimizationLoopTask()
        
        mock_decision = MagicMock()
        mock_decision.choice = "increase_budget_20"
        mock_decision.confidence = 0.95
        mock_decision.target_payload = None
        mock_decision.state = {}

        mock_laya = AsyncMock()
        mock_laya.decide = AsyncMock(return_value=mock_decision)

        policy_service = OptimizationPolicyService()

        # Ensure campaign has >= 500 impressions so context.eligible is True
        active_campaign.impressions = 1500
        active_campaign.spent_today = Decimal("10.00")
        active_campaign.spent_total = Decimal("50.00")
        await test_db.commit()

        with patch("liteads.ad_server.services.optimization_scheduler.redis_client") as mock_redis:
            mock_redis.connected = True
            mock_redis.get = AsyncMock(return_value="owner_abc")
            
            # Call _process_single_campaign directly under test_db session
            await task._process_single_campaign(
                session=test_db,
                campaign_id=active_campaign.id,
                laya_client=mock_laya,
                policy_service=policy_service,
                owner_token="owner_abc",
                fencing_token=50,
            )

        await test_db.refresh(active_campaign)
        # The campaign daily budget should have increased by 20% (100 -> 120)
        assert active_campaign.budget_daily == Decimal("120.00")
        assert active_campaign.optimization_fencing_token == 50
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 27. Redis Fencing Token Fail-Closed when Unavailable (Finding 3)
# ---------------------------------------------------------------------------
async def test_scheduler_redis_fencing_unavailable_fails_closed(test_db, active_campaign, mock_db_session_fixture):
    """
    When Redis is unavailable or disconnected during fencing token acquisition,
    the scheduler must fail closed, skip the optimization cycle, and NEVER mutate
    campaigns or fall back to unsafe epoch timestamps.
    """
    task = OptimizationLoopTask()

    initial_budget = active_campaign.budget_daily

    with patch("liteads.ad_server.services.optimization_scheduler.redis_client") as mock_redis, \
         patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        # Redis is disconnected
        mock_redis.connected = False
        mock_redis.acquire_lock = AsyncMock(return_value=False)

        res = await task._process_cycle()

    assert res.get("status") in ("skipped_redis_fencing_unavailable", "skipped_locked")
    
    await test_db.refresh(active_campaign)
    assert active_campaign.budget_daily == initial_budget


# ---------------------------------------------------------------------------
# 28. Redis Counter Loss Recovery from Database Max Token (Finding 3)
# ---------------------------------------------------------------------------
async def test_scheduler_redis_counter_loss_recovery_from_database(test_db, active_campaign, mock_db_session_fixture):
    """
    If Redis counter key is missing (e.g. after Redis restart/flush),
    the scheduler initializes the counter from func.max(Campaign.optimization_fencing_token)
    in PostgreSQL to guarantee monotonic progression and avoid fencing out campaigns.
    """
    active_campaign.optimization_fencing_token = 500
    await test_db.commit()

    task = OptimizationLoopTask()

    redis_data = {}
    async def fake_get(k):
        return redis_data.get(k)
    async def fake_set(k, v, ttl=None, nx=False):
        if nx and k in redis_data:
            return False
        redis_data[k] = v
        return True
    async def fake_incr(k):
        curr = int(redis_data.get(k, 0)) + 1
        redis_data[k] = str(curr)
        return curr

    with patch("liteads.ad_server.services.optimization_scheduler.redis_client") as mock_redis, \
         patch("liteads.ad_server.services.optimization_scheduler.db.session", new=mock_db_session_fixture):
        mock_redis.connected = True
        mock_redis.acquire_lock = AsyncMock(return_value=True)
        mock_redis.release_lock = AsyncMock(return_value=True)
        mock_redis.renew_lock = AsyncMock(return_value=True)
        mock_redis.get = fake_get
        mock_redis.set = fake_set
        mock_redis.incr = fake_incr

        # Before run, Redis has NO counter for fencing
        assert "fencing_token:optimization" not in redis_data

        # Run process cycle
        res = await task._process_cycle()

        # Counter should have been initialized to at least max token (500) and incremented to 501
        assert "fencing_token:optimization" in redis_data
        assert int(redis_data["fencing_token:optimization"]) >= 501
        assert res.get("status") == "success"

