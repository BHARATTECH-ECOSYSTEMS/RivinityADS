import asyncio
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.logger import get_logger
from liteads.common.database import db
from liteads.models.ad import Campaign
from liteads.models.base import Status
from liteads.ad_server.services.optimization_prep import OptimizationPreparationService
from liteads.ad_server.services.laya_client import LayaDecisionClient
from liteads.ad_server.services.optimization_policy import OptimizationPolicyService
from liteads.ad_server.services.optimization_executor import OptimizationExecutor

logger = get_logger(__name__)

class OptimizationLoopTask:
    def __init__(self, interval_seconds: int = 900):
        self.interval_seconds = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def start(self):
        """Starts the optimization loop background task. Prevents duplicate tasks."""
        if self._task is not None and not self._task.done():
            logger.warning("Optimization loop already running, ignoring start request.")
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Optimization loop started", interval_seconds=self.interval_seconds)

    async def stop(self):
        """Cleanly stops the background task."""
        if self._task is None or self._task.done():
            return
            
        logger.info("Stopping optimization loop")
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        logger.info("Optimization loop stopped")

    async def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                await self._process_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Unexpected error in optimization loop cycle", error=str(e))
                
            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    async def _process_cycle(self):
        logger.info("Starting optimization cycle")
        
        active_campaign_ids = []
        try:
            async with db.session() as session:
                result = await session.execute(
                    select(Campaign.id).where(Campaign.status == Status.ACTIVE)
                )
                active_campaign_ids = [row[0] for row in result.all()]
        except Exception as e:
            logger.error("Failed to query active campaigns", error=str(e))
            return
            
        logger.info("Found active campaigns for optimization", count=len(active_campaign_ids))
        
        laya_client = LayaDecisionClient()
        policy_service = OptimizationPolicyService()
        
        for camp_id in active_campaign_ids:
            if self._stop_event.is_set():
                break
                
            try:
                async with db.session() as session:
                    await self._process_single_campaign(session, camp_id, laya_client, policy_service)
            except Exception as e:
                logger.error("Campaign processing failed completely", campaign_id=camp_id, error=str(e))
                
        logger.info("Completed optimization cycle")
                
    async def _process_single_campaign(
        self,
        session: AsyncSession,
        campaign_id: str,
        laya_client: LayaDecisionClient,
        policy_service: OptimizationPolicyService
    ):
        campaign = await session.get(Campaign, campaign_id)
        if not campaign or campaign.status != Status.ACTIVE:
            return
            
        prep_service = OptimizationPreparationService()
        context = prep_service.prepare_context(campaign)
        
        if not context.eligible:
            logger.info("Campaign ineligible for optimization (<500 impressions)", campaign_id=campaign.id)
            return
            
        try:
            decision = await laya_client.decide(context.state)
        except Exception as e:
            logger.warning("Laya decision failed", campaign_id=campaign.id, error=str(e))
            return
            
        policy_result = policy_service.evaluate(campaign, decision)
        if not policy_result.accepted:
            logger.info("Laya decision rejected by policy", campaign_id=campaign.id, reason=policy_result.reason)
            return
            
        logger.info("Executing approved Laya decision", campaign_id=campaign.id, action=policy_result.action)
        executor = OptimizationExecutor(session)
        try:
            await executor.execute(campaign, policy_result, decision.target_payload)
            if campaign.autopilot:
                logger.info("Autopilot action applied", campaign_id=campaign.id, action=policy_result.action)
            else:
                logger.info("Suggestion created", campaign_id=campaign.id, action=policy_result.action)
        except Exception as e:
            logger.error("Executor failed", campaign_id=campaign.id, error=str(e))
            raise

# Singleton instance
optimization_loop = OptimizationLoopTask(interval_seconds=900)
