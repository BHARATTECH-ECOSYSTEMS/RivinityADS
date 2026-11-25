"""
Campaign Optimization Scheduler (Phase 5).

Provides distributed, lease-coordinated background scheduling for campaign optimization:
- Redis distributed lock with unique owner tokens and atomic Lua release
- Background renewal heartbeat to maintain lease during active batches
- Stale worker fencing preventing rogue writes after lease loss
- Per-campaign atomic locking preventing concurrent duplicate executions
- Fail-closed multi-worker safety when Redis is unavailable
- Exception isolation per campaign
"""

import asyncio
from datetime import datetime, timezone
import os
from typing import Any, Optional
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.common.database import db
from liteads.common.logger import get_logger
from liteads.models.ad import Campaign, Advertiser
from liteads.models.base import Status
from liteads.ad_server.middleware.metrics import publish_worker_heartbeat
from liteads.ad_server.services.optimization_prep import OptimizationPreparationService
from liteads.ad_server.services.laya_client import LayaDecisionClient
from liteads.ad_server.services.optimization_policy import OptimizationPolicyService
from liteads.ad_server.services.optimization_executor import OptimizationExecutor

logger = get_logger(__name__)

DISTRIBUTED_LOCK_KEY = "lock:optimization_cycle"
PER_CAMPAIGN_LOCK_PREFIX = "lock:optimization:campaign:"


class OptimizationLoopTask:
    """
    Distributed background scheduler task for campaign optimization.
    """

    def __init__(self, interval_seconds: Optional[int] = None):
        settings = get_settings()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.optimization.interval_seconds
        )
        self.lock_ttl = settings.optimization.lock_ttl_seconds
        self.renewal_interval = settings.optimization.lock_renewal_interval_seconds
        self.campaign_lock_ttl = settings.optimization.campaign_lock_ttl_seconds

        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        """Returns True if the loop is currently active."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Starts the optimization loop background task. Prevents duplicate tasks."""
        if self.is_running:
            logger.warning("Optimization loop already running, ignoring start request.")
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Optimization loop started", interval_seconds=self.interval_seconds)

    async def stop(self) -> None:
        """Cleanly stops the background task."""
        if not self.is_running:
            return

        logger.info("Stopping optimization loop")
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Optimization loop stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            await publish_worker_heartbeat("optimization")
            try:
                await self._process_cycle()
                await publish_worker_heartbeat("optimization")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Unexpected error in optimization loop cycle", error=str(e))

            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    async def _process_cycle(self) -> dict[str, Any]:
        """
        Executes a single optimization cycle across active campaigns.
        
        Guarantees:
        - Exactly one cycle active per cluster / lease.
        - Lease renewal heartbeat for long cycles.
        - Stale worker fencing aborts processing if lease is lost.
        - Per-campaign locking prevents concurrent duplicate execution.
        """
        logger.info("Starting optimization cycle")
        settings = get_settings()

        # Check global kill switch
        if settings.optimization.kill_switch:
            logger.warning("Optimization kill switch is active; skipping cycle")
            return {"status": "skipped_kill_switch"}

        # 1. Multi-worker Redis coordination guard: fail closed if Redis is unavailable
        if not redis_client.connected:
            logger.warning("Optimization cycle skipped: Redis not connected; cannot obtain distributed lock or safe fencing token")
            return {"status": "skipped_redis_fencing_unavailable"}

        # 2. Acquire ownership-safe distributed lease
        owner_token = str(uuid.uuid4())
        lock_acquired = await redis_client.set(
            DISTRIBUTED_LOCK_KEY,
            owner_token,
            ttl=self.lock_ttl,
            nx=True,
        )
        if not lock_acquired:
            logger.info(
                "Optimization cycle skipped: lock held by another worker",
                lock_key=DISTRIBUTED_LOCK_KEY,
            )
            return {"status": "skipped_lock_held"}

        # 3. Start renewal heartbeat to prevent lease expiry during long processing
        stop_heartbeat = asyncio.Event()
        lost_lease = False
        current_campaign_lock_key: Optional[str] = None

        try:
            # If Redis counter is missing (e.g. after Redis restart/flush), initialize it from database max token
            # to prevent stale worker lockouts of previously optimized campaigns.
            counter_exists = await redis_client.get("fencing_token:optimization")
            if counter_exists is None:
                async with db.session() as init_session:
                    max_token_res = await init_session.execute(
                        select(func.coalesce(func.max(Campaign.optimization_fencing_token), 0))
                    )
                    db_max_token = max_token_res.scalar() or 0
                await redis_client.set("fencing_token:optimization", str(db_max_token), nx=True)

            fencing_token = await redis_client.incr("fencing_token:optimization")
        except Exception as e:
            logger.error("Optimization cycle skipped: failed to increment Redis fencing token", error=str(e))
            if lock_acquired:
                await redis_client.release_lock(DISTRIBUTED_LOCK_KEY, owner_token)
            return {"status": "skipped_redis_fencing_error", "error": str(e)}

        async def _heartbeat_loop():
            nonlocal lost_lease
            while not stop_heartbeat.is_set():
                try:
                    await asyncio.sleep(self.renewal_interval)
                except asyncio.CancelledError:
                    break
                if stop_heartbeat.is_set():
                    break
                renewed = await redis_client.renew_lock(
                    DISTRIBUTED_LOCK_KEY, owner_token, ttl=self.lock_ttl
                )
                if not renewed:
                    logger.warning("Failed to renew optimization lock lease; lease expired or reassigned!")
                    lost_lease = True
                    break
                # Safely renew active per-campaign lock as well
                if current_campaign_lock_key:
                    await redis_client.renew_lock(
                        current_campaign_lock_key, owner_token, ttl=self.campaign_lock_ttl
                    )

        heartbeat_task = None
        if redis_client.connected:
            heartbeat_task = asyncio.create_task(_heartbeat_loop())

        try:
            active_campaign_ids = []
            try:
                async with db.session() as session:
                    result = await session.execute(
                        select(Campaign.id).where(Campaign.status == Status.ACTIVE)
                    )
                    active_campaign_ids = [row[0] for row in result.all()]
            except Exception as e:
                logger.error("Failed to query active campaigns", error=str(e))
                return {"status": "failed_db", "error": str(e)}

            logger.info("Found active campaigns for optimization", count=len(active_campaign_ids))

            laya_client = LayaDecisionClient()
            policy_service = OptimizationPolicyService()
            processed_count = 0

            for camp_id in active_campaign_ids:
                if self._stop_event.is_set():
                    break

                # Stale worker protection: fence execution if lease lost
                if lost_lease:
                    logger.warning(
                        "Optimizer lost lease during cycle; aborting remaining campaign evaluations to prevent stale writes",
                        campaign_id=camp_id,
                    )
                    break

                # Per-campaign distributed lock
                camp_lock_acquired = True
                camp_lock_key = f"{PER_CAMPAIGN_LOCK_PREFIX}{camp_id}"
                if redis_client.connected:
                    camp_lock_acquired = await redis_client.set(
                        camp_lock_key,
                        owner_token,
                        ttl=self.campaign_lock_ttl,
                        nx=True,
                    )
                    if not camp_lock_acquired:
                        logger.info(
                            "Campaign optimization locked by another worker/cycle; skipping",
                            campaign_id=camp_id,
                        )
                        continue

                current_campaign_lock_key = camp_lock_key

                try:
                    async with db.session() as session:
                        await self._process_single_campaign(
                            session,
                            camp_id,
                            laya_client,
                            policy_service,
                            owner_token=owner_token,
                            fencing_token=fencing_token,
                        )
                    processed_count += 1
                except Exception as e:
                    logger.error("Campaign processing failed completely", campaign_id=camp_id, error=str(e))
                finally:
                    current_campaign_lock_key = None
                    if redis_client.connected and camp_lock_acquired:
                        await redis_client.release_lock(camp_lock_key, owner_token)

            logger.info("Completed optimization cycle", processed=processed_count, total=len(active_campaign_ids))
            return {"status": "success", "processed": processed_count, "total": len(active_campaign_ids)}

        finally:
            if heartbeat_task is not None:
                stop_heartbeat.set()
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

            if redis_client.connected and lock_acquired:
                await redis_client.release_lock(DISTRIBUTED_LOCK_KEY, owner_token)

    async def _process_single_campaign(
        self,
        session: AsyncSession,
        campaign_id: str | int,
        laya_client: LayaDecisionClient,
        policy_service: OptimizationPolicyService,
        owner_token: Optional[str] = None,
        fencing_token: Optional[int] = None,
    ) -> None:
        """Process a single campaign with decoupled Laya network calls, row locking, and fencing."""
        # 1. READ-ONLY EVALUATION (outside database row lock)
        # Avoid holding row lock during slow Laya HTTP network calls
        campaign = await session.get(Campaign, int(campaign_id))
        if not campaign or campaign.status != Status.ACTIVE:
            return

        initial_fencing_token = getattr(campaign, "optimization_fencing_token", 0) or 0
        advertiser_id = campaign.advertiser_id

        prep_service = OptimizationPreparationService()
        try:
            placements = await prep_service.get_campaign_eligible_placements(session, campaign)
        except Exception as e:
            logger.warning("Failed to retrieve placement health for campaign", campaign_id=campaign.id, error=str(e))
            placements = None

        context = prep_service.prepare_context(campaign, placements=placements)

        if not context.eligible:
            logger.info("Campaign ineligible for optimization (<500 impressions)", campaign_id=campaign.id)
            return

        settings = get_settings()
        try:
            decision = await laya_client.decide(context.state)
        except Exception as e:
            logger.warning("Laya decision failed", campaign_id=campaign.id, error=str(e))
            try:
                executor = OptimizationExecutor(session, mode=settings.optimization.mode)
                dummy_policy = OptimizationPolicyResult(
                    accepted=False,
                    reason=f"Laya sidecar error: {e}",
                    action=None,
                    confidence=0.0,
                    autopilot=bool(campaign.autopilot),
                )
                await executor._record_decision_audit(
                    campaign=campaign,
                    policy_result=dummy_policy,
                    mode=settings.optimization.mode,
                    outcome="failed",
                    reason=f"Laya sidecar error: {e}",
                    actor="system_optimizer",
                    execution_status="FAILED",
                    fencing_token=fencing_token,
                    auto_commit=True,
                )
            except Exception as audit_err:
                logger.warning("Failed to record Laya failure audit", error=str(audit_err))
            return

        policy_result = policy_service.evaluate(campaign, context, decision)
        if not policy_result.accepted:
            logger.info("Laya decision rejected by policy", campaign_id=campaign.id, reason=policy_result.reason)
            try:
                executor = OptimizationExecutor(session, mode=settings.optimization.mode)
                await executor._record_decision_audit(
                    campaign=campaign,
                    policy_result=policy_result,
                    mode=settings.optimization.mode,
                    outcome="rejected",
                    reason=policy_result.reason,
                    target_payload=getattr(decision, "target_payload", None),
                    actor="system_optimizer",
                    execution_status="REJECTED",
                    fencing_token=fencing_token,
                    auto_commit=True,
                )
            except Exception as audit_err:
                logger.warning("Failed to record policy rejection audit", error=str(audit_err))
            return

        # 2. FENCED MUTATION WITH CANONICAL ROW-LEVEL LOCKING (Advertiser -> Campaign)
        # Strictly acquire Advertiser row lock before Campaign row lock to prevent deadlock with billing
        locked_advertiser = await session.get(Advertiser, advertiser_id, with_for_update=True)
        if not locked_advertiser:
            logger.info("Advertiser not found or inactive; skipping", advertiser_id=advertiser_id)
            return

        locked_campaign = await session.get(Campaign, int(campaign_id), with_for_update=True)
        if not locked_campaign or locked_campaign.status != Status.ACTIVE or locked_campaign.advertiser_id != locked_advertiser.id:
            logger.info("Campaign changed state before optimization commit; skipping", campaign_id=campaign.id)
            return

        # Database fencing guard: check if another worker updated the campaign fencing token while waiting for row lock
        current_fencing_token = getattr(locked_campaign, "optimization_fencing_token", 0) or 0
        if current_fencing_token != initial_fencing_token:
            logger.info(
                "Campaign fencing token mutated while waiting for row lock; skipping stale evaluation",
                campaign_id=campaign.id,
                initial_token=initial_fencing_token,
                current_token=current_fencing_token,
            )
            return

        # Revalidate policy under row lock with fresh campaign and advertiser state
        fresh_context = prep_service.prepare_context(locked_campaign, placements=placements)
        if not fresh_context.eligible:
            logger.info("Campaign became ineligible for optimization upon revalidation", campaign_id=locked_campaign.id)
            return

        fresh_policy_result = policy_service.evaluate(locked_campaign, fresh_context, decision)
        if not fresh_policy_result.accepted:
            logger.info("Laya decision rejected by policy upon revalidation", campaign_id=locked_campaign.id, reason=fresh_policy_result.reason)
            return

        logger.info("Executing approved Laya decision", campaign_id=campaign.id, action=fresh_policy_result.action)
        settings = get_settings()
        executor = OptimizationExecutor(session, mode=settings.optimization.mode)
        target_payload = getattr(decision, "target_payload", None)
        try:
            await executor.execute(
                locked_campaign,
                fresh_policy_result,
                target_payload=target_payload,
                fencing_token=fencing_token,
                owner_token=owner_token,
            )
            if locked_campaign.autopilot:
                logger.info("Autopilot action processed", campaign_id=campaign.id, action=fresh_policy_result.action)
            else:
                logger.info("Suggestion processed", campaign_id=campaign.id, action=fresh_policy_result.action)
        except Exception as e:
            logger.error("Executor failed", campaign_id=campaign.id, error=str(e))
            raise


# Singleton instance
optimization_loop = OptimizationLoopTask()
