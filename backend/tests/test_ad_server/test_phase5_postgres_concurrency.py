"""
Phase 5 PostgreSQL-Backed Integration Concurrency Test Suite.

Provides real PostgreSQL multi-connection integration tests demonstrating:
1. True competing transactions with database row locking (FOR UPDATE)
2. Aggregate advertiser-level financial constraints across multiple concurrent campaigns
3. Database-enforced stale-worker fencing tokens rejecting zombie workers
4. Partial unique index concurrency races on suggestions (uq_suggestions_pending_dedup)
5. Fully atomic manual suggestion approvals preventing double-execution races
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import os
import uuid
from unittest.mock import AsyncMock, patch, PropertyMock
import pytest
from liteads.common.cache import redis_client
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from liteads.models.ad import (
    Campaign,
    Advertiser,
    Suggestion,
    CampaignChangeLog,
    TargetingRule,
    OptimizationDecision,
)
from liteads.models.publisher import Publisher
from liteads.models.finance import Transaction
from liteads.ad_server.services.billing import BillingService
from liteads.models.base import Status
from liteads.ad_server.services.optimization_policy import OptimizationPolicyResult
from liteads.ad_server.services.optimization_executor import (
    OptimizationExecutor,
    OptimizationExecutionError,
    compute_suggestion_dedup_key,
)
from liteads.common.config import get_settings

pytestmark = pytest.mark.asyncio


from sqlalchemy.pool import NullPool


@pytest.fixture
async def pg_engine():
    """Create a real PostgreSQL async engine or skip if PostgreSQL is unavailable."""
    settings = get_settings()
    url = settings.database.async_url
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL async_url not configured")

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as e:
        await engine.dispose()
        pytest.skip(f"PostgreSQL server not accessible: {e}")

    yield engine
    await engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    """Session factory for creating independent, separate PostgreSQL connections."""
    return sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def pg_clean_tenant(pg_session_factory):
    """Creates an isolated Advertiser for each test and guarantees clean teardown."""
    unique_tag = uuid.uuid4().hex[:8]
    adv_id = None
    async with pg_session_factory() as session:
        adv = Advertiser(
            name=f"PG Concurrency Tenant {unique_tag}",
            contact_email=f"pg_test_{unique_tag}@example.com",
            balance=Decimal("1000.00"),
            daily_budget=Decimal("1000.00"),
        )
        session.add(adv)
        await session.commit()
        await session.refresh(adv)
        adv_id = adv.id

    yield adv_id

    # Teardown
    async with pg_session_factory() as session:
        # Delete related transactions, decisions, suggestions, logs, campaigns, and advertiser
        camps = (await session.execute(select(Campaign.id).where(Campaign.advertiser_id == adv_id))).scalars().all()
        for cid in camps:
            await session.execute(delete(Transaction).where(Transaction.campaign_id == cid))
            await session.execute(delete(OptimizationDecision).where(OptimizationDecision.campaign_id == cid))
            await session.execute(delete(Suggestion).where(Suggestion.campaign_id == cid))
            await session.execute(delete(CampaignChangeLog).where(CampaignChangeLog.campaign_id == cid))
            await session.execute(delete(TargetingRule).where(TargetingRule.campaign_id == cid))
        await session.execute(delete(Transaction).where(Transaction.advertiser_id == adv_id))
        await session.execute(delete(Campaign).where(Campaign.advertiser_id == adv_id))
        await session.execute(delete(Advertiser).where(Advertiser.id == adv_id))
        await session.commit()


# ---------------------------------------------------------------------------
# 1. PostgreSQL True Competing Transactions: Stale-Worker Fencing
# ---------------------------------------------------------------------------
async def test_postgres_stale_worker_fencing_competing_sessions(pg_session_factory, pg_clean_tenant):
    """
    Demonstrates true PostgreSQL fencing behavior:
    - Worker 1 prepares an action with fencing token 100.
    - Worker 2 takes over and successfully commits an action with fencing token 101.
    - Worker 1 resumes and attempts to commit with its stale token 100.
    - Worker 1's commit is REJECTED by PostgreSQL-enforced token check.
    """
    adv_id = pg_clean_tenant
    camp_id = None

    async with pg_session_factory() as session:
        camp = Campaign(
            name="PG Fencing Test Campaign",
            advertiser_id=adv_id,
            status=Status.ACTIVE,
            autopilot=True,
            budget_daily=Decimal("100.00"),
            impressions=2000,
            optimization_fencing_token=100,
        )
        session.add(camp)
        await session.commit()
        await session.refresh(camp)
        camp_id = camp.id

    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    # Worker 2 commits with fresh token 101 on session 2
    async with pg_session_factory() as session_worker2:
        camp_w2 = await session_worker2.get(Campaign, camp_id, with_for_update=True)
        executor_w2 = OptimizationExecutor(session_worker2, mode="autopilot")
        await executor_w2.execute(
            camp_w2,
            policy_result,
            fencing_token=101,
            enforce_cooldown=False,
            auto_commit=True,
        )

    # Verify Worker 2 updated the database
    async with pg_session_factory() as verify_session:
        camp_check = await verify_session.get(Campaign, camp_id)
        assert camp_check.budget_daily == Decimal("120.00")
        assert camp_check.optimization_fencing_token == 101

    # Worker 1 (which lagged behind) attempts to commit with stale token 100 on session 1
    async with pg_session_factory() as session_worker1:
        camp_w1 = await session_worker1.get(Campaign, camp_id, with_for_update=True)
        executor_w1 = OptimizationExecutor(session_worker1, mode="autopilot")
        with pytest.raises(OptimizationExecutionError, match=r"Stale worker fenced.*101.*100"):
            await executor_w1.execute(
                camp_w1,
                policy_result,
                fencing_token=100,
                enforce_cooldown=False,
                auto_commit=True,
            )

    # Confirm database state was NOT corrupted by stale Worker 1
    async with pg_session_factory() as final_session:
        camp_final = await final_session.get(Campaign, camp_id)
        assert camp_final.budget_daily == Decimal("120.00")
        assert camp_final.optimization_fencing_token == 101


# ---------------------------------------------------------------------------
# 2. PostgreSQL True Row Locking: Aggregate Multi-Campaign Financial Guardrails
# ---------------------------------------------------------------------------
async def test_postgres_aggregate_financial_guardrails_competing_sessions(pg_session_factory, pg_clean_tenant):
    """
    Demonstrates real PostgreSQL row locking (FOR UPDATE on Advertiser):
    - Advertiser has balance = 220.00 (just enough for one +20 increase, not two).
    - Campaign A has daily budget = 100.00.
    - Campaign B has daily budget = 100.00.
    - Two concurrent tasks attempt to increase Campaign A and B simultaneously.
    - Row-level lock on Advertiser serializes them:
      One task succeeds (total = 100 + 120 = 220.00 <= balance).
      The other task unblocks, re-evaluates, and fails (total = 120 + 120 = 240.00 > balance).
    """
    adv_id = pg_clean_tenant
    camp_a_id = None
    camp_b_id = None

    async with pg_session_factory() as session:
        adv = await session.get(Advertiser, adv_id)
        adv.balance = Decimal("220.00")
        adv.daily_budget = Decimal("500.00")

        camp_a = Campaign(
            name="PG Campaign A",
            advertiser_id=adv_id,
            status=Status.ACTIVE,
            autopilot=True,
            budget_daily=Decimal("100.00"),
            impressions=2000,
        )
        camp_b = Campaign(
            name="PG Campaign B",
            advertiser_id=adv_id,
            status=Status.ACTIVE,
            autopilot=True,
            budget_daily=Decimal("100.00"),
            impressions=2000,
        )
        session.add_all([camp_a, camp_b])
        await session.commit()
        await session.refresh(camp_a)
        await session.refresh(camp_b)
        camp_a_id = camp_a.id
        camp_b_id = camp_b.id

    policy_result = OptimizationPolicyResult(
        accepted=True, reason="OK", action="increase_budget_20",
        confidence=0.9, autopilot=True, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    results = []

    async def _run_worker(cid: int):
        async with pg_session_factory() as session:
            camp = await session.get(Campaign, cid, with_for_update=True)
            executor = OptimizationExecutor(session, mode="autopilot")
            try:
                await executor.execute(camp, policy_result, enforce_cooldown=False, auto_commit=True)
                results.append((cid, "SUCCESS"))
            except Exception as e:
                results.append((cid, "FAILED", str(e)))

    # Run both competing database operations concurrently
    await asyncio.gather(_run_worker(camp_a_id), _run_worker(camp_b_id))

    # Exactly one must have succeeded and one must have failed with insufficient balance
    successes = [r for r in results if r[1] == "SUCCESS"]
    failures = [r for r in results if r[1] == "FAILED"]

    assert len(successes) == 1, f"Expected exactly 1 success, got: {results}"
    assert len(failures) == 1, f"Expected exactly 1 failure, got: {results}"
    assert "insufficient for total committed daily budget" in failures[0][2]

    # Verify final database state: total committed budget is exactly 220.00, not 240.00
    async with pg_session_factory() as session:
        ca = await session.get(Campaign, camp_a_id)
        cb = await session.get(Campaign, camp_b_id)
        total_daily = ca.budget_daily + cb.budget_daily
        assert total_daily == Decimal("220.00")


# ---------------------------------------------------------------------------
# 3. PostgreSQL Unique Partial Index: Suggestion Dedup Race
# ---------------------------------------------------------------------------
async def test_postgres_unique_index_dedup_race(pg_session_factory, pg_clean_tenant):
    """
    Demonstrates PostgreSQL uq_suggestions_pending_dedup partial unique index:
    - Two concurrent workers attempt to insert the same pending suggestion.
    - PostgreSQL unique index blocks the second insert with IntegrityError.
    - The executor catches the uniqueness violation, rolls back the failed subtransaction,
      and updates the existing pending suggestion in-place.
    - Final state: exactly 1 suggestion in the database, with evaluation_count == 2.
    """
    adv_id = pg_clean_tenant
    camp_id = None

    async with pg_session_factory() as session:
        camp = Campaign(
            name="PG Dedup Race Campaign",
            advertiser_id=adv_id,
            status=Status.ACTIVE,
            autopilot=False,
            budget_daily=Decimal("100.00"),
            impressions=2000,
        )
        session.add(camp)
        await session.commit()
        await session.refresh(camp)
        camp_id = camp.id

    action_payload = {
        "action": "increase_budget_20",
        "confidence": 0.88,
        "proposed_budget_delta": 20.0,
        "operation_type": "MODIFY",
    }

    async def _insert_suggestion():
        async with pg_session_factory() as session:
            camp = await session.get(Campaign, camp_id)
            executor = OptimizationExecutor(session, mode="suggestions_only")
            return await executor._execute_autopilot_off(camp, action_payload, auto_commit=True)

    # Run competing insertions
    sug1, sug2 = await asyncio.gather(_insert_suggestion(), _insert_suggestion())

    # Both returns should point to the same suggestion ID
    assert sug1.id == sug2.id

    # Verify directly against PostgreSQL
    async with pg_session_factory() as session:
        all_suggs = (
            await session.execute(
                select(Suggestion).where(Suggestion.campaign_id == camp_id, Suggestion.status == "PENDING")
            )
        ).scalars().all()

        assert len(all_suggs) == 1
        assert all_suggs[0].action.get("evaluation_count") == 2


# ---------------------------------------------------------------------------
# 4. PostgreSQL Atomic Manual Suggestion Approval
# ---------------------------------------------------------------------------
async def test_postgres_atomic_manual_suggestion_approval(pg_session_factory, pg_clean_tenant):
    """
    Demonstrates single-transaction atomicity during manual suggestion approval:
    - Campaign mutation, changelog, decision audit, and suggestion status change
      are all committed within one single PostgreSQL transaction.
    - Two competing approval requests for the same suggestion:
      First approval succeeds and transitions status to APPLIED.
      Second approval finds status != PENDING and is cleanly rejected.
      Campaign budget is incremented once (+20), NOT twice.
    """
    adv_id = pg_clean_tenant
    camp_id = None
    sugg_id = None

    async with pg_session_factory() as session:
        camp = Campaign(
            name="PG Atomic Approval Campaign",
            advertiser_id=adv_id,
            status=Status.ACTIVE,
            autopilot=False,
            budget_daily=Decimal("100.00"),
            impressions=2000,
        )
        session.add(camp)
        await session.commit()
        await session.refresh(camp)
        camp_id = camp.id

        action_data = {
            "action": "increase_budget_20",
            "proposed_budget_delta": 20.0,
            "operation_type": "MODIFY",
            "confidence": 0.95,
        }
        dedup_key = compute_suggestion_dedup_key(camp_id, "increase_budget_20", None)
        sugg = Suggestion(
            campaign_id=camp_id,
            action=action_data,
            status="PENDING",
            dedup_key=dedup_key,
        )
        session.add(sugg)
        await session.commit()
        await session.refresh(sugg)
        sugg_id = sugg.id

    policy_result = OptimizationPolicyResult(
        accepted=True, reason="Manual approval", action="increase_budget_20",
        confidence=0.95, autopilot=False, proposed_budget_delta=20.0, operation_type="MODIFY"
    )

    results = []

    async def _approve_suggestion(worker_label: str):
        async with pg_session_factory() as session:
            # Replicates management.py route logic: canonical lock order (Advertiser -> Campaign -> Suggestion)
            sugg_pre = await session.get(Suggestion, sugg_id)
            if not sugg_pre or sugg_pre.status != "PENDING":
                results.append((worker_label, "REJECTED_NOT_PENDING"))
                return

            # Lock 1: Advertiser
            adv_stmt = select(Advertiser).where(Advertiser.id == adv_id).with_for_update().execution_options(populate_existing=True)
            adv_row = (await session.execute(adv_stmt)).scalar_one_or_none()

            # Lock 2: Campaign
            camp_stmt = select(Campaign).where(Campaign.id == camp_id).with_for_update().execution_options(populate_existing=True)
            camp_row = (await session.execute(camp_stmt)).scalar_one_or_none()

            # Lock 3: Suggestion
            sugg_stmt = select(Suggestion).where(Suggestion.id == sugg_id).with_for_update().execution_options(populate_existing=True)
            sugg_row = (await session.execute(sugg_stmt)).scalar_one_or_none()
            if not sugg_row or sugg_row.status != "PENDING":
                results.append((worker_label, "REJECTED_NOT_PENDING"))
                return

            executor = OptimizationExecutor(session)
            try:
                await executor._execute_autopilot_on(
                    campaign=camp_row,
                    policy_result=policy_result,
                    action_payload=sugg_row.action,
                    actor=f"user:{worker_label}",
                    enforce_cooldown=False,
                    auto_commit=False,
                    suggestion=sugg_row,
                )
                await session.commit()
                results.append((worker_label, "COMMITTED"))
            except Exception as e:
                await session.rollback()
                results.append((worker_label, "ERROR", str(e)))

    # Two competing approval requests
    await asyncio.gather(_approve_suggestion("worker_A"), _approve_suggestion("worker_B"))

    committed = [r for r in results if r[1] == "COMMITTED"]
    rejected = [r for r in results if r[1] == "REJECTED_NOT_PENDING"]

    assert len(committed) == 1, f"Expected exactly 1 committed approval, got: {results}"
    assert len(rejected) == 1, f"Expected exactly 1 rejected approval, got: {results}"

    # Verify PostgreSQL final state
    async with pg_session_factory() as session:
        camp_final = await session.get(Campaign, camp_id)
        sugg_final = await session.get(Suggestion, sugg_id)
        logs = (await session.execute(select(CampaignChangeLog).where(CampaignChangeLog.campaign_id == camp_id))).scalars().all()

        assert camp_final.budget_daily == Decimal("120.00")
        assert sugg_final.status == "APPLIED"
        assert len(logs) == 1


# ---------------------------------------------------------------------------
# 5. PostgreSQL Concurrent Billing and Optimization (No Deadlock Verification)
# ---------------------------------------------------------------------------
async def test_postgres_concurrent_billing_and_optimization_no_deadlock(pg_session_factory, pg_clean_tenant):
    """
    Demonstrates deadlock-free concurrency between ad-event impression billing
    and autopilot optimization loop when both operate simultaneously on the
    same Advertiser and Campaign rows.

    Because both paths follow the canonical lock order (Advertiser -> Campaign),
    they serialize safely without PostgreSQL detecting a deadlock (DeadlockDetectedError).
    """
    adv_id = pg_clean_tenant
    camp_id = None
    pub_id = None

    async with pg_session_factory() as session:
        # Create Publisher
        pub = Publisher(
            name=f"PG Deadlock Test Pub {uuid.uuid4().hex[:6]}",
            email="pub_deadlock@example.com",
            balance=Decimal("0.00"),
            status=Status.ACTIVE,
        )
        session.add(pub)
        await session.flush()
        pub_id = pub.id

        # Update Advertiser balance to 500.00
        adv = await session.get(Advertiser, adv_id)
        adv.balance = Decimal("500.00")
        adv.daily_budget = Decimal("500.00")

        # Create Campaign
        camp = Campaign(
            name="PG Deadlock Test Campaign",
            advertiser_id=adv_id,
            status=Status.ACTIVE,
            autopilot=True,
            budget_daily=Decimal("100.00"),
            impressions=1000,
            spent_today=Decimal("10.00"),
            spent_total=Decimal("10.00"),
            optimization_fencing_token=10,
        )
        session.add(camp)
        await session.commit()
        await session.refresh(camp)
        camp_id = camp.id

    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="Test Deadlock Free",
        action="increase_budget_20",
        confidence=0.9,
        autopilot=True,
        proposed_budget_delta=20.0,
        operation_type="MODIFY",
    )

    action_payload = {
        "action": "increase_budget_20",
        "proposed_budget_delta": 20.0,
        "operation_type": "MODIFY",
        "confidence": 0.9,
    }

    results = {}

    async def _billing_task():
        # Emulate ad event impression charge (BillingService)
        async with pg_session_factory() as session:
            try:
                # Simulates BillingService.process_impression_charge with canonical lock order
                success = await BillingService.process_impression_charge(
                    session=session,
                    advertiser_id=adv_id,
                    campaign_id=camp_id,
                    publisher_id=pub_id,
                    cost=Decimal("5.00"),
                    reference_id=f"ref_{uuid.uuid4().hex[:8]}",
                )
                await session.commit()
                results["billing"] = "SUCCESS" if success else "FAILED"
            except Exception as e:
                await session.rollback()
                results["billing"] = f"ERROR: {e}"

    async def _optimization_task():
        # Emulate optimization executor with canonical lock order
        async with pg_session_factory() as session:
            try:
                # Canonical lock order: Advertiser -> Campaign
                adv_row = await session.get(Advertiser, adv_id, with_for_update=True)
                camp_row = await session.get(Campaign, camp_id, with_for_update=True)
                executor = OptimizationExecutor(session, mode="autopilot")
                await executor._execute_autopilot_on(
                    campaign=camp_row,
                    policy_result=policy_result,
                    action_payload=action_payload,
                    fencing_token=11,
                    enforce_cooldown=False,
                    auto_commit=True,
                )
                results["optimization"] = "SUCCESS"
            except Exception as e:
                await session.rollback()
                results["optimization"] = f"ERROR: {e}"

    # Execute both transactions concurrently with a 10s timeout to prove no hang/deadlock
    try:
        await asyncio.wait_for(
            asyncio.gather(_billing_task(), _optimization_task()),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        pytest.fail("Deadlock detected: billing and optimization tasks timed out waiting for locks!")

    assert results.get("billing") == "SUCCESS", f"Billing task did not succeed: {results}"
    assert results.get("optimization") == "SUCCESS", f"Optimization task did not succeed: {results}"

    # Verify final consistent database state
    async with pg_session_factory() as session:
        adv_final = await session.get(Advertiser, adv_id)
        camp_final = await session.get(Campaign, camp_id)
        pub_final = await session.get(Publisher, pub_id)

        # Advertiser balance: 500.00 - 5.00 = 495.00
        assert adv_final.balance == Decimal("495.00")
        # Campaign budget daily: 100.00 + 20.00 = 120.00
        assert camp_final.budget_daily == Decimal("120.00")
        # Campaign spent: 10.00 + 5.00 = 15.00
        assert camp_final.spent_today == Decimal("15.00")
        # Publisher earned: 5.00 * 0.70 = 3.50
        assert pub_final.balance == Decimal("3.50")
        # Fencing token advanced
        assert camp_final.optimization_fencing_token == 11

    # Cleanup publisher
    async with pg_session_factory() as session:
        await session.execute(delete(Transaction).where(Transaction.publisher_id == pub_id))
        await session.execute(delete(Publisher).where(Publisher.id == pub_id))
        await session.commit()


# ---------------------------------------------------------------------------
# 6. PostgreSQL Worker Lease-Expiry Pause and Takeover (Finding 4 Regression)
# ---------------------------------------------------------------------------
async def test_postgres_worker_lease_expiry_pause_and_takeover(pg_session_factory, pg_clean_tenant):
    """
    Demonstrates defense against the lease-expiry commit race:
    - Worker 1 evaluates Campaign with fencing token 100.
    - Worker 1 experiences a long pause (simulating lease expiry where lock is lost).
    - Worker 2 takes over leadership, advances fencing token to 101, and commits changes.
    - Worker 1 resumes and attempts to commit with its stale generation (token 100, or invalid lease).
    - Worker 1 is transactionally prevented from committing:
      1) Pre-commit lease check detects lost leadership, OR
      2) PostgreSQL-enforced fencing check detects token 100 <= 101.
    - Worker 1 raises OptimizationExecutionError.
    - Campaign retains Worker 2's clean state without stale overwrites.
    """
    adv_id = pg_clean_tenant
    camp_id = None

    async with pg_session_factory() as session:
        camp = Campaign(
            name="PG Lease Expiry Race Campaign",
            advertiser_id=adv_id,
            status=Status.ACTIVE,
            autopilot=True,
            budget_daily=Decimal("100.00"),
            impressions=2000,
            optimization_fencing_token=100,
        )
        session.add(camp)
        await session.commit()
        await session.refresh(camp)
        camp_id = camp.id

    policy_result = OptimizationPolicyResult(
        accepted=True,
        reason="OK",
        action="increase_budget_20",
        confidence=0.9,
        autopilot=True,
        proposed_budget_delta=20.0,
        operation_type="MODIFY",
    )

    action_payload = {
        "action": "increase_budget_20",
        "proposed_budget_delta": 20.0,
        "operation_type": "MODIFY",
        "confidence": 0.9,
    }

    # Worker 2 takes over and successfully advances fencing token to 101
    async with pg_session_factory() as session_worker2:
        adv_w2 = await session_worker2.get(Advertiser, adv_id, with_for_update=True)
        camp_w2 = await session_worker2.get(Campaign, camp_id, with_for_update=True)
        executor_w2 = OptimizationExecutor(session_worker2, mode="autopilot")
        await executor_w2._execute_autopilot_on(
            campaign=camp_w2,
            policy_result=policy_result,
            action_payload=action_payload,
            fencing_token=101,
            enforce_cooldown=False,
            auto_commit=True,
        )

    # Verify Worker 2 committed
    async with pg_session_factory() as verify_session:
        camp_check = await verify_session.get(Campaign, camp_id)
        assert camp_check.budget_daily == Decimal("120.00")
        assert camp_check.optimization_fencing_token == 101

    # Worker 1 was paused and now wakes up with stale lease and stale token 100
    with patch("liteads.ad_server.services.optimization_executor.redis_client.get", new=AsyncMock(return_value="worker_2_owner_token")):
        with patch.object(type(redis_client), "connected", new_callable=PropertyMock, return_value=True):
            async with pg_session_factory() as session_worker1:
                adv_w1 = await session_worker1.get(Advertiser, adv_id, with_for_update=True)
                camp_w1 = await session_worker1.get(Campaign, camp_id, with_for_update=True)
                executor_w1 = OptimizationExecutor(session_worker1, mode="autopilot")
                # Attempting to execute with stale token 100 and lost lease must raise OptimizationExecutionError
                with pytest.raises(OptimizationExecutionError, match=r"(Stale worker fenced|Pre-commit leader lease expired)"):
                    await executor_w1._execute_autopilot_on(
                        campaign=camp_w1,
                        policy_result=policy_result,
                        action_payload=action_payload,
                        fencing_token=100,
                        owner_token="worker_1_owner_token",
                        enforce_cooldown=False,
                        auto_commit=True,
                    )

    # Final database state must reflect Worker 2's commit untouched
    async with pg_session_factory() as session_final:
        camp_final = await session_final.get(Campaign, camp_id)
        assert camp_final.budget_daily == Decimal("120.00")
        assert camp_final.optimization_fencing_token == 101
