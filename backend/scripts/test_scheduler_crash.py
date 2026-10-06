import asyncio
import logging
from liteads.ad_server.services.optimization_scheduler import optimization_loop
from liteads.ad_server.services.laya_client import LayaDecisionClient
from liteads.ad_server.services.optimization_policy import OptimizationPolicyService
from liteads.ad_server.services.optimization_prep import OptimizationPreparationService, OptimizationPrepResult
from liteads.models.ad import Campaign
from unittest.mock import AsyncMock, patch, MagicMock
from liteads.common.logger import get_logger

logging.basicConfig(level=logging.INFO)
logger = get_logger(__name__)

async def main():
    print("=== Testing Failure Isolation and Laya Path ===")
    with patch("liteads.ad_server.services.optimization_scheduler.db.session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session
        
        camp_ids = ["camp1", "camp2"]
        mock_execute = AsyncMock()
        mock_execute.all.return_value = [[c] for c in camp_ids]
        mock_session.execute.return_value = mock_execute
        
        async def mock_get(model, cid):
            c = Campaign(id=cid, autopilot=True, status="active", impressions=1000)
            return c
        mock_session.get.side_effect = mock_get
        
        print("Running _process_cycle with mock DB...")
        await optimization_loop._process_cycle()
        print("Scheduler remained alive through cycle.")

if __name__ == '__main__':
    asyncio.run(main())
