import asyncio
import logging
from liteads.ad_server.services.optimization_scheduler import optimization_loop
from liteads.ad_server.services.laya_client import LayaDecisionClient
from liteads.ad_server.services.optimization_policy import OptimizationPolicyService
from liteads.ad_server.services.optimization_executor import OptimizationExecutor
from liteads.ad_server.services.optimization_prep import OptimizationContext
from liteads.models.ad import Campaign
from unittest.mock import AsyncMock, patch, MagicMock
from liteads.common.logger import get_logger

logging.basicConfig(level=logging.INFO)
logger = get_logger(__name__)

async def main():
    print("=== Testing Laya Connectivity ===")
    laya = LayaDecisionClient()
    ctx = OptimizationContext(
        campaign_id="test1", advertiser_id="adv1", is_eligible=True,
        budget_daily=100.0, spend_7d=50.0, impressions_7d=1000,
        clicks_7d=10, conversions_7d=1, ctr_7d=0.01, cvr_7d=0.1, cpa_7d=50.0
    )
    decision = await laya.get_decision(ctx)
    print(f"Laya Decision: choice={decision.choice}, conf={decision.confidence}")

    print("\n=== Testing Policy ===")
    policy = OptimizationPolicyService()
    camp = Campaign(id="test1", autopilot=True)
    res = policy.evaluate(camp, decision)
    print(f"Policy Result: accepted={res.accepted}, reason={res.reason}")
    
    print("\n=== Testing Failure Isolation ===")
    with patch("liteads.ad_server.services.optimization_scheduler.db.session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session
        
        # We need a campaign id list
        camp_ids = ["camp1", "camp2"]
        
        # Mock get_decision to fail for camp1, succeed for camp2
        async def mock_get_decision(context):
            if context.campaign_id == "camp1":
                raise Exception("Laya Timeout")
            return MagicMock(choice="no_change", confidence=0.8, target_payload=None)
        
        with patch.object(LayaDecisionClient, 'get_decision', side_effect=mock_get_decision):
            # Mock DB to return campaign objects
            async def mock_get(model, cid):
                c = Campaign(id=cid, autopilot=True, status="active")
                return c
            mock_session.get.side_effect = mock_get
            
            # Mock prep to always return eligible
            with patch("liteads.ad_server.services.optimization_scheduler.OptimizationPreparationService.prepare_context") as mock_prep:
                mock_prep.return_value = OptimizationContext(
                    campaign_id="mock", advertiser_id="adv1", is_eligible=True,
                    budget_daily=100.0, spend_7d=50.0, impressions_7d=1000,
                    clicks_7d=10, conversions_7d=1, ctr_7d=0.01, cvr_7d=0.1, cpa_7d=50.0
                )
                
                # We also need to mock session.execute to return camp_ids so _process_cycle processes them
                mock_execute = AsyncMock()
                mock_execute.all.return_value = [[c] for c in camp_ids]
                mock_session.execute.return_value = mock_execute
                
                print("Running _process_cycle with mock DB...")
                await optimization_loop._process_cycle()
                print("Scheduler remained alive through cycle.")
                
    print("\n=== Testing <500 Impressions Protection ===")
    with patch("liteads.ad_server.services.optimization_scheduler.db.session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session
        
        async def mock_get_under_500(model, cid):
            return Campaign(id=cid, autopilot=True, status="active")
        mock_session.get.side_effect = mock_get_under_500
        
        with patch("liteads.ad_server.services.optimization_scheduler.OptimizationPreparationService.prepare_context") as mock_prep:
            mock_prep.return_value = OptimizationContext(
                campaign_id="mock2", advertiser_id="adv1", is_eligible=False,  # Ineligible
                budget_daily=100.0, spend_7d=50.0, impressions_7d=499,
                clicks_7d=10, conversions_7d=1, ctr_7d=0.01, cvr_7d=0.1, cpa_7d=50.0
            )
            
            mock_execute = AsyncMock()
            mock_execute.all.return_value = [["camp_ineligible"]]
            mock_session.execute.return_value = mock_execute
            
            with patch.object(LayaDecisionClient, 'get_decision') as mock_laya:
                await optimization_loop._process_cycle()
                print(f"Laya was called: {mock_laya.called}")

if __name__ == '__main__':
    asyncio.run(main())
