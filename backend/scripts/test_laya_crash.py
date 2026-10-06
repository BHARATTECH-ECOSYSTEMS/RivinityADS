import asyncio
import logging
from liteads.ad_server.services.optimization_scheduler import optimization_loop
from liteads.ad_server.services.laya_client import LayaDecisionClient
from liteads.ad_server.services.optimization_policy import OptimizationPolicyService
from liteads.ad_server.services.optimization_prep import OptimizationPreparationService, OptimizationPrepResult
from liteads.models.ad import Campaign
from unittest.mock import AsyncMock, patch, MagicMock

logging.basicConfig(level=logging.INFO)

async def main():
    print("=== Testing Laya Path ===")
    with patch("liteads.ad_server.services.optimization_scheduler.db.session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session
        
        async def mock_get(model, cid):
            return Campaign(id=cid, autopilot=True, status="active", impressions=1000)
        mock_session.get.side_effect = mock_get
        
        laya_client = LayaDecisionClient()
        policy_service = OptimizationPolicyService()
        
        # We must mock OptimizationPreparationService to avoid the `__init__` exception
        with patch("liteads.ad_server.services.optimization_scheduler.OptimizationPreparationService") as MockPrep:
            mock_prep_instance = MockPrep.return_value
            mock_prep_instance.prepare_context = AsyncMock()
            
            # Use `is_eligible` on the mock because optimization_scheduler code checks `if not context.is_eligible:`
            mock_context = MagicMock()
            mock_context.is_eligible = True
            mock_prep_instance.prepare_context.return_value = mock_context
            
            try:
                await optimization_loop._process_single_campaign(mock_session, "camp1", laya_client, policy_service)
            except Exception as e:
                print(f"Caught Exception: {type(e).__name__}: {e}")

if __name__ == '__main__':
    asyncio.run(main())
